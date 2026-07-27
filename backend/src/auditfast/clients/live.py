"""Read-only Fabric REST provider.

Every call is a GET, except the read-only ``getDefinition`` POST. Nothing here
ever writes.

Two behaviours matter and are easy to get wrong:

1. **The workspace itself is read first, and its HTTP status is checked.** A
   403 must raise, not yield an empty context — otherwise an inaccessible
   workspace scores zeros and looks like a badly configured one.
2. **A failed sub-resource call is recorded as *unknown*, not as *absent*.**
   ``git/connection`` returning a network error does not mean "Git is not
   connected"; the affected checks report N/A instead of failing.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

from ..core.enums import Layer, Resource
from ..core.models import Item, RoleAssignment, WorkspaceContext
from .base import ALL_RESOURCES
from .errors import WorkspaceAccessError


class LiveFabricProvider:
    """Reads a live Fabric tenant with a delegated, read-only OAuth2 token."""

    BASE = "https://api.fabric.microsoft.com/v1"

    def __init__(self, token: str, timeout: int = 60):
        import requests  # imported lazily so offline mode needs no HTTP stack

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})
        self._timeout = timeout

    # -- transport -------------------------------------------------------------
    def _get(self, path: str) -> tuple[int | None, Any]:
        """GET a path, returning ``(status, body)``.

        A status of ``None`` means the request never completed — a transport
        failure, which callers must treat as *unknown* rather than *empty*.
        """
        try:
            response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
        except Exception:
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _values(self, path: str) -> tuple[list, bool]:
        """GET a collection endpoint. Returns ``(rows, known)``.

        ``known`` is False when the call failed outright, so the caller can tell
        "no rows" apart from "could not ask".
        """
        status, body = self._get(path)
        if status != 200 or not isinstance(body, dict):
            return [], False
        return body.get("value") or [], True

    def _pipeline_definition(self, workspace_id: str, item_id: str) -> dict | None:
        """POST getDefinition (read-only) and decode the pipeline content part."""
        url = f"{self.BASE}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
        try:
            response = self._session.post(url, timeout=self._timeout)
            if response.status_code not in (200, 202):
                return None
            parts = (response.json() or {}).get("definition", {}).get("parts", [])
        except Exception:
            return None
        for part in parts:
            if part.get("path", "").endswith(("pipeline-content.json", "pipelineContent.json")):
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    return json.loads(payload)
                except Exception:
                    return None
        return None

    # -- the provider contract -------------------------------------------------
    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        wanted = set(resources)

        # The workspace itself is always read: it establishes both identity and
        # access, and its status is how we detect an unreadable workspace.
        status, workspace = self._get(f"/workspaces/{workspace_id}")
        if status != 200 or not isinstance(workspace, dict):
            raise WorkspaceAccessError(workspace_id, status)

        ctx = WorkspaceContext(
            id=workspace_id,
            display_name=workspace.get("displayName", workspace_id),
            layer=layer,
            capacity_id=workspace.get("capacityId"),
            deployment_pipeline=bool(workspace.get("assignedToDeploymentPipeline")),
        )

        # Pipeline definitions are derived from the item list, so fetch items if
        # either resource was asked for.
        if wanted & {Resource.ITEMS, Resource.PIPELINE_DEFINITIONS}:
            rows, known = self._values(f"/workspaces/{workspace_id}/items")
            ctx.items = [Item.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ITEMS)

        if Resource.ROLE_ASSIGNMENTS in wanted:
            rows, known = self._values(f"/workspaces/{workspace_id}/roleAssignments")
            ctx.role_assignments = [RoleAssignment.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ROLE_ASSIGNMENTS)

        if Resource.GIT in wanted:
            git_status, _ = self._get(f"/workspaces/{workspace_id}/git/connection")
            if git_status == 200:
                ctx.git_connected = True
            elif git_status in (400, 404):
                ctx.git_connected = False  # genuinely not connected
            else:
                # 401/403/500/transport failure: we could not determine it.
                ctx.unavailable.add(Resource.GIT)

        # The expensive one — one call per pipeline. Only paid for when a
        # selected check actually reads a pipeline definition.
        if Resource.PIPELINE_DEFINITIONS in wanted:
            for item in ctx.items:
                if item.type != "DataPipeline":
                    continue
                definition = self._pipeline_definition(workspace_id, item.id)
                if definition:
                    ctx.pipelines[item.display_name or item.id] = definition

        return ctx

    def list_workspaces(self) -> list[dict]:
        rows, _known = self._values("/workspaces")
        return [
            {
                "id": row.get("id"),
                "name": row.get("displayName", row.get("id")),
                "layer": "",
                "items": None,
                "pipelines": None,
            }
            for row in rows
        ]

    # -- diagnostics -----------------------------------------------------------
    def probe(self, max_workspaces: int = 3) -> dict:
        """Report what this token can actually read, per sub-resource.

        Used by the Diagnose button when a live run returns less than expected —
        it surfaces partial permissions (for example: items readable, role
        assignments forbidden) that would otherwise look like clean passes.
        """
        result: dict[str, Any] = {"list_status": None, "count": 0, "samples": [], "error": None}
        status, body = self._get("/workspaces")
        result["list_status"] = status
        if status != 200 or not isinstance(body, dict):
            result["error"] = f"Listing workspaces returned HTTP {status}."
            return result

        workspaces = body.get("value") or []
        result["count"] = len(workspaces)
        for workspace in workspaces[:max_workspaces]:
            workspace_id = workspace.get("id")
            items_status, items_body = self._get(f"/workspaces/{workspace_id}/items")
            roles_status, _ = self._get(f"/workspaces/{workspace_id}/roleAssignments")
            items = (items_body or {}).get("value", []) if items_status == 200 else []
            result["samples"].append({
                "name": workspace.get("displayName", workspace_id),
                "items_status": items_status,
                "items": len(items),
                "pipelines": sum(1 for i in items if i.get("type") == "DataPipeline"),
                "roles_status": roles_status,
            })
        return result
