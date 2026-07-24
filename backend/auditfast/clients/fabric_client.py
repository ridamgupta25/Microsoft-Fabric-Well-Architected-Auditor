"""Fabric data access.

Two implementations expose the SAME normalized "workspace context" so the rule
engine, scoring, and reports are identical whether data comes from a live tenant
or offline fixtures:

  * MockFabricClient - reads sample_data/tenant.json (offline demo / tests).
  * LiveFabricClient - calls the Fabric REST API read-only with a delegated
    OAuth2 token (see auth.py). Best-effort skeleton for Phase 1.

Normalized workspace context shape:
{
  "id", "displayName", "role",
  "capacityId", "gitConnected", "deploymentPipeline",
  "roleAssignments": [{"principalType","displayName","role"}],
  "items": [{"id","type","displayName","sensitivityLabel","lastRunUtc"}],
  "pipelines": {"<name>": <definition dict>}
}
"""
from __future__ import annotations

import base64
import json
from typing import Optional


class WorkspaceAccessError(Exception):
    """Raised when a workspace cannot be read (missing, no access, or bad token).

    This is what stops the auditor from silently "passing" a workspace that does
    not exist or that the signed-in user cannot see: instead of scoring an empty
    context, the run reports exactly why the workspace was skipped.
    """

    def __init__(self, ws_id: str, status: Optional[int] = None):
        self.ws_id = ws_id
        self.status = status
        super().__init__(self._friendly())

    def _friendly(self) -> str:
        s = self.status
        if s == 401:
            return ("Sign-in/token problem (HTTP 401): the token was rejected or has "
                    "expired. Sign in again, then retry.")
        if s == 403:
            return ("Access denied (HTTP 403): the signed-in user does not have access "
                    "to this workspace. Ask for at least a Viewer role, then retry.")
        if s == 404:
            return ("Not found (HTTP 404): no workspace with this name/ID is visible to "
                    "you. Use \u201cLoad my Fabric workspaces\u201d to pick the right one.")
        if s is None:
            return "Could not reach Fabric to read this workspace (network/connection error)."
        return f"Fabric returned HTTP {s} for this workspace, so it was not audited."


class FabricClient:
    def get_workspace_context(self, ws_id: str, role: str = "") -> dict:
        raise NotImplementedError


class MockFabricClient(FabricClient):
    """Reads a tenant fixture file and returns its workspace contexts as-is."""

    def __init__(self, tenant_file: str):
        with open(tenant_file, encoding="utf-8") as fh:
            data = json.load(fh)
        self._ws = {w["id"]: w for w in data.get("workspaces", [])}

    def get_workspace_context(self, ws_id: str, role: str = "") -> dict:
        if ws_id not in self._ws:
            raise KeyError(ws_id)
        ctx = dict(self._ws[ws_id])
        if role:
            ctx["role"] = role
        ctx.setdefault("pipelines", {})
        ctx.setdefault("items", [])
        ctx.setdefault("roleAssignments", [])
        return ctx


class LiveFabricClient(FabricClient):
    """Read-only Fabric REST client using a delegated OAuth2 bearer token.

    Phase 1 best-effort: all calls are GET (or the read-only getDefinition POST)
    and every call is wrapped so a missing/failed endpoint degrades gracefully
    rather than aborting the audit.
    """

    BASE = "https://api.fabric.microsoft.com/v1"

    def __init__(self, token: str, timeout: int = 60):
        import requests  # local import so mock mode needs no dependency
        self._requests = requests
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})
        self._timeout = timeout

    # -- low-level helpers -----------------------------------------------------
    def _get(self, path: str) -> Optional[dict]:
        try:
            r = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            return None
        return None

    def _get_status(self, path: str):
        """Like _get but also returns the HTTP status so callers can tell a real
        404/403 apart from an empty-but-successful response."""
        try:
            r = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
            try:
                body = r.json()
            except Exception:
                body = None
            return r.status_code, body
        except Exception:
            return None, None

    def _get_definition(self, ws_id: str, item_id: str) -> Optional[dict]:
        """POST getDefinition (read-only) and decode the pipeline content part."""
        try:
            url = f"{self.BASE}/workspaces/{ws_id}/items/{item_id}/getDefinition"
            r = self._session.post(url, timeout=self._timeout)
            if r.status_code not in (200, 202):
                return None
            parts = (r.json() or {}).get("definition", {}).get("parts", [])
            for part in parts:
                if part.get("path", "").endswith(("pipeline-content.json", "pipelineContent.json")):
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    return json.loads(payload)
        except Exception:
            return None
        return None

    # -- list workspaces the signed-in user can access -------------------------
    def list_workspaces(self) -> list:
        data = self._get("/workspaces") or {}
        rows = []
        for w in data.get("value", []):
            rows.append({
                "id": w.get("id"),
                "name": w.get("displayName", w.get("id")),
                "role": "",
                "items": None,
                "pipelines": None,
            })
        return rows

    def probe(self, max_ws: int = 3) -> dict:
        """Quick connectivity probe: what does Fabric actually return for this token?"""
        result = {"list_status": None, "count": 0, "samples": [], "error": None}
        try:
            r = self._session.get(f"{self.BASE}/workspaces", timeout=self._timeout)
            result["list_status"] = r.status_code
            if r.status_code == 200:
                wss = (r.json() or {}).get("value", [])
                result["count"] = len(wss)
                for w in wss[:max_ws]:
                    wid = w.get("id")
                    ri = self._session.get(f"{self.BASE}/workspaces/{wid}/items", timeout=self._timeout)
                    items = (ri.json() or {}).get("value", []) if ri.status_code == 200 else []
                    rr = self._session.get(f"{self.BASE}/workspaces/{wid}/roleAssignments", timeout=self._timeout)
                    result["samples"].append({
                        "name": w.get("displayName", wid),
                        "items_status": ri.status_code,
                        "items": len(items),
                        "pipelines": sum(1 for it in items if it.get("type") == "DataPipeline"),
                        "roles_status": rr.status_code,
                    })
            else:
                result["error"] = (r.text or "")[:300]
        except Exception as exc:
            result["error"] = str(exc)
        return result

    # -- normalized context ----------------------------------------------------
    def get_workspace_context(self, ws_id: str, role: str = "") -> dict:
        # Read the workspace itself FIRST and check the HTTP status. If Fabric
        # says 404/403/401 (or the call fails), the workspace is not auditable -
        # raise so the run reports it instead of scoring an empty context.
        status, ws = self._get_status(f"/workspaces/{ws_id}")
        if status != 200 or not isinstance(ws, dict):
            raise WorkspaceAccessError(ws_id, status)
        items_raw = (self._get(f"/workspaces/{ws_id}/items") or {}).get("value", [])
        roles_raw = (self._get(f"/workspaces/{ws_id}/roleAssignments") or {}).get("value", [])
        git = self._get(f"/workspaces/{ws_id}/git/connection")

        items = [{
            "id": it.get("id"),
            "type": it.get("type"),
            "displayName": it.get("displayName"),
            "sensitivityLabel": (it.get("sensitivityLabel") or {}).get("labelId"),
            "lastRunUtc": it.get("lastUpdatedDate"),
        } for it in items_raw]

        role_assignments = [{
            "principalType": (ra.get("principal") or {}).get("type"),
            "displayName": (ra.get("principal") or {}).get("displayName"),
            "role": ra.get("role"),
        } for ra in roles_raw]

        pipelines = {}
        for it in items_raw:
            if it.get("type") == "DataPipeline":
                definition = self._get_definition(ws_id, it["id"])
                if definition:
                    pipelines[it.get("displayName", it["id"])] = definition

        return {
            "id": ws_id,
            "displayName": ws.get("displayName", ws_id),
            "role": role,
            "capacityId": ws.get("capacityId"),
            "gitConnected": bool(git),
            "deploymentPipeline": bool(ws.get("assignedToDeploymentPipeline")),
            "roleAssignments": role_assignments,
            "items": items,
            "pipelines": pipelines,
        }
