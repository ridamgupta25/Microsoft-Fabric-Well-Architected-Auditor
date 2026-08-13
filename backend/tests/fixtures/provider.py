"""A recorded-tenant provider — test infrastructure, not a product feature.

The application ships exactly one provider, :class:`~auditfast.clients.live.LiveFabricProvider`.
This class exists solely so the test suite can exercise the engine, the check
library, and the API deterministically and offline, without a real Fabric tenant,
network access, or credentials in CI. It satisfies the same
:class:`~auditfast.clients.base.Provider` protocol the live provider does, so
every test runs against the exact code path production uses — only the data
source differs.

It is never imported by ``auditfast.*`` and is not part of the installed
package.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from auditfast.clients.base import ALL_RESOURCES
from auditfast.clients.errors import WorkspaceAccessError
from auditfast.core.enums import Layer, Resource
from auditfast.core.models import Item, RoleAssignment, WorkspaceContext

FIXTURE_FILE = Path(__file__).parent / "tenant.json"


class RecordedProvider:
    """Serves workspace contexts recorded in a JSON file, for tests only."""

    def __init__(self, fixture_file: str | Path = FIXTURE_FILE):
        self.fixture_file = Path(fixture_file)
        with self.fixture_file.open(encoding="utf-8") as fh:
            data = json.load(fh)
        self._workspaces: dict[str, dict] = {w["id"]: w for w in data.get("workspaces", [])}

    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        raw = self._workspaces.get(workspace_id)
        if raw is None:
            raise WorkspaceAccessError(workspace_id, 404)

        return WorkspaceContext(
            id=raw["id"],
            display_name=raw.get("displayName", raw["id"]),
            layer=layer if layer is not Layer.MIXED else Layer.parse(raw.get("role")),
            capacity_id=raw.get("capacityId"),
            git_connected=bool(raw.get("gitConnected")),
            deployment_pipeline=bool(raw.get("deploymentPipeline")),
            role_assignments=[RoleAssignment.from_api(r) for r in raw.get("roleAssignments", [])],
            items=[Item.from_api(i) for i in raw.get("items", [])],
            pipelines=dict(raw.get("pipelines") or {}),
            notebooks=dict(raw.get("notebooks") or {}),
            environments=dict(raw.get("environments") or {}),
            tables=dict(raw.get("tables") or {}),
            shortcuts=dict(raw.get("shortcuts") or {}),
            semantic_models=dict(raw.get("semanticModels") or {}),
            reports=list(raw.get("reports") or []),
            lakehouse_files=dict(raw.get("lakehouseFiles") or {}),
        )

    def list_workspaces(self) -> list[dict]:
        return [
            {
                "id": w["id"],
                "name": w.get("displayName", w["id"]),
                "layer": w.get("role", ""),
                "items": len(w.get("items", [])),
                "pipelines": len(w.get("pipelines", {})),
            }
            for w in self._workspaces.values()
        ]
