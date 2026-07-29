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
import logging
import time
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ..core.enums import Layer, Resource
from ..core.models import Item, RoleAssignment, WorkspaceContext
from .base import ALL_RESOURCES
from .errors import WorkspaceAccessError
from .tmsl import parse_tmsl

log = logging.getLogger("auditfast.live")


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

    def _get_url(self, url: str) -> tuple[int | None, Any]:
        """GET an absolute URL (used to follow a pagination continuation link)."""
        try:
            response = self._session.get(url, timeout=self._timeout)
        except Exception:
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _values(self, path: str) -> tuple[list, bool]:
        """GET a collection endpoint, following continuation to the last page.

        Returns ``(rows, known)``. ``known`` is False only when the *first* call
        failed outright, so the caller can tell "no rows" from "could not ask".
        Fabric list endpoints page with ``continuationUri``; every page is
        gathered so a large workspace is never silently truncated.
        """
        status, body = self._get(path)
        if status != 200 or not isinstance(body, dict):
            return [], False
        rows = list(body.get("value") or [])
        next_uri = body.get("continuationUri")
        pages = 0
        while next_uri and pages < 1000:
            pages += 1
            status, body = self._get_url(next_uri)
            if status != 200 or not isinstance(body, dict):
                break
            rows.extend(body.get("value") or [])
            next_uri = body.get("continuationUri")
        return rows, True

    @staticmethod
    def _json(response) -> Any:
        """Parse a response body as JSON, or ``None`` if it is not JSON."""
        try:
            return response.json()
        except ValueError:
            return None

    def _definition_parts(
        self, workspace_id: str, item_id: str, fmt: str | None = None
    ) -> list[dict]:
        """Read an item's definition parts via the read-only getDefinition LRO.

        getDefinition is a long-running operation: it answers 200 with the body
        inline for small items, or 202 with a ``Location`` to poll until the
        operation completes and its result carries the parts. A read-only token
        is rejected with 401 — getDefinition requires an Item.ReadWrite scope.
        """
        url = f"{self.BASE}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
        if fmt:
            url += f"?format={fmt}"
        try:
            response = self._session.post(url, timeout=self._timeout)
        except Exception as exc:
            log.warning("item %s getDefinition transport error: %s", item_id, exc)
            return []
        if response.status_code == 200:
            body = self._json(response)
        elif response.status_code == 202:
            body = self._await_operation(response)
        else:
            log.warning("item %s getDefinition -> HTTP %s", item_id, response.status_code)
            return []
        parts = ((body or {}).get("definition") or {}).get("parts") or []
        if not parts:
            log.warning("item %s getDefinition returned no definition parts", item_id)
        return parts

    def _await_operation(self, response) -> Any:
        """Poll a 202 long-running operation to completion, returning its result body."""
        location = response.headers.get("Location")
        if not location:
            log.warning("getDefinition 202 without a Location header")
            return None
        delay = 1.0
        try:
            delay = min(float(response.headers.get("Retry-After") or 1.0), 10.0)
        except ValueError:
            pass
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            time.sleep(min(delay, 5.0))
            try:
                op = self._session.get(location, timeout=self._timeout)
            except Exception:
                return None
            state = str((self._json(op) or {}).get("status", "")).lower()
            if state == "succeeded":
                try:
                    result = self._session.get(
                        location.rstrip("/") + "/result", timeout=self._timeout
                    )
                except Exception:
                    return None
                return self._json(result)
            if state == "failed":
                log.warning("getDefinition operation failed: %s", location)
                return None
        log.warning("getDefinition operation timed out: %s", location)
        return None

    def _pipeline_definition(self, workspace_id: str, item_id: str) -> dict | None:
        """Read a pipeline's content (a read-only getDefinition) as parsed JSON."""
        for part in self._definition_parts(workspace_id, item_id):
            if part.get("path", "").endswith(("pipeline-content.json", "pipelineContent.json")):
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    return json.loads(payload)
                except Exception:
                    return None
        return None

    def _notebook_definition(self, workspace_id: str, item_id: str) -> dict | None:
        """Read a notebook's content (a read-only getDefinition) as an .ipynb dict."""
        for part in self._definition_parts(workspace_id, item_id, fmt="ipynb"):
            path = part.get("path", "")
            if not path.endswith((".ipynb", "notebook-content.py")):
                continue
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
            except Exception:
                return None
            if path.endswith(".ipynb"):
                try:
                    return json.loads(payload)
                except Exception:
                    return None
            # A .py export: wrap the raw source as a single code cell.
            return {"cells": [{"cell_type": "code", "source": payload}]}
        return None

    def _lakehouse_tables(self, workspace_id: str, item_id: str) -> list[dict]:
        """List a lakehouse's tables via REST (name/type/format; no columns).

        The Fabric *List Tables* endpoint returns rows under ``data`` (not the
        usual ``value`` collection key), so it is read directly here.
        """
        status, body = self._get(
            f"/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
        )
        if status != 200 or not isinstance(body, dict):
            log.warning("lakehouse %s list-tables -> HTTP %s", item_id, status)
            return []
        return body.get("data") or []

    def _item_shortcuts(self, workspace_id: str, item_id: str) -> list[dict]:
        """List an item's OneLake shortcuts (name/path/target type), all pages."""
        rows, _known = self._values(
            f"/workspaces/{workspace_id}/items/{item_id}/shortcuts"
        )
        shortcuts = []
        for row in rows:
            target = row.get("target") or {}
            shortcuts.append({
                "name": row.get("name", ""),
                "path": row.get("path", ""),
                "target_type": target.get("type", ""),
            })
        return shortcuts

    def _semantic_model_definition(self, workspace_id: str, item_id: str) -> dict | None:
        """Fetch a semantic model's TMSL definition and reduce it to model facts."""
        for part in self._definition_parts(workspace_id, item_id, fmt="TMSL"):
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
                document = json.loads(payload)
            except Exception:
                continue
            if isinstance(document, dict) and ("model" in document or "tables" in document):
                return parse_tmsl(document)
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

        # Pipeline, notebook, and table reads all walk the item list, so fetch it
        # whenever any item-derived resource was asked for.
        if wanted & {
            Resource.ITEMS,
            Resource.PIPELINE_DEFINITIONS,
            Resource.NOTEBOOK_DEFINITIONS,
            Resource.TABLE_SCHEMAS,
            Resource.SHORTCUTS,
            Resource.SEMANTIC_MODEL_DEFINITIONS,
        }:
            rows, known = self._values(f"/workspaces/{workspace_id}/items")
            ctx.items = [Item.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ITEMS)
            log.info("fetch %s: %d items by type %s", workspace_id,
                     len(ctx.items), dict(Counter(i.type for i in ctx.items)))

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

        # Notebook definitions: same one-call-per-item getDefinition pattern.
        if Resource.NOTEBOOK_DEFINITIONS in wanted:
            found = [i for i in ctx.items if i.type == "Notebook"]
            for item in found:
                definition = self._notebook_definition(workspace_id, item.id)
                if definition:
                    ctx.notebooks[item.display_name or item.id] = definition
            log.info("fetch %s: %d notebooks found, %d definitions read",
                     workspace_id, len(found), len(ctx.notebooks))

        # Lakehouse table listing (name/type/format). Column schemas need the SQL
        # analytics endpoint and are left empty here; column-level checks report
        # N/A rather than failing when they are absent.
        if Resource.TABLE_SCHEMAS in wanted:
            lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
            for item in lakehouses:
                for tbl in self._lakehouse_tables(workspace_id, item.id):
                    name = tbl.get("name")
                    if name:
                        ctx.tables[name] = {
                            "type": tbl.get("type", ""),
                            "format": tbl.get("format", ""),
                            "columns": [],
                        }
            log.info("fetch %s: %d lakehouses, %d tables read",
                     workspace_id, len(lakehouses), len(ctx.tables))

        # OneLake shortcuts per lakehouse (governance/lineage: external references).
        if Resource.SHORTCUTS in wanted:
            total = 0
            for item in ctx.items:
                if item.type != "Lakehouse":
                    continue
                shortcuts = self._item_shortcuts(workspace_id, item.id)
                if shortcuts:
                    ctx.shortcuts[item.display_name or item.id] = shortcuts
                    total += len(shortcuts)
            log.info("fetch %s: %d shortcuts read", workspace_id, total)

        # Semantic-model measures + relationships, parsed from the TMSL definition.
        if Resource.SEMANTIC_MODEL_DEFINITIONS in wanted:
            parsed = 0
            for item in ctx.items:
                if item.type != "SemanticModel":
                    continue
                model = self._semantic_model_definition(workspace_id, item.id)
                if model:
                    ctx.semantic_models[item.display_name or item.id] = model
                    parsed += 1
            log.info("fetch %s: %d semantic models parsed", workspace_id, parsed)

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
