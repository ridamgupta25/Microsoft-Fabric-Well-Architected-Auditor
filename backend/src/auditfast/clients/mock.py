"""Offline provider backed by a tenant fixture file.

Reads ``sample_data/tenant.json`` and serves it as workspace contexts. This is
what makes the whole test suite deterministic and network-free, and what lets the
UI demo without a Fabric tenant.

Unlike the live provider it ignores ``resources`` and always populates
everything: reading one local JSON file is free, so there is nothing to defer.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..core.enums import Layer, Resource
from ..core.models import Item, RoleAssignment, WorkspaceContext
from .base import ALL_RESOURCES
from .errors import WorkspaceAccessError


class MockProvider:
    """Serves workspace contexts from a tenant fixture."""

    def __init__(self, tenant_file: str | Path):
        self.tenant_file = Path(tenant_file)
        with self.tenant_file.open(encoding="utf-8") as fh:
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
            # 404 gives the same "not visible to you" guidance the live provider
            # produces, which is the accurate description for a fixture too.
            raise WorkspaceAccessError(workspace_id, 404)

        return WorkspaceContext(
            id=raw["id"],
            display_name=raw.get("displayName", raw["id"]),
            # An explicit layer from the caller wins; the fixture's own role is
            # the fallback so the file stays usable on its own.
            layer=layer if layer is not Layer.MIXED else Layer.parse(raw.get("role")),
            capacity_id=raw.get("capacityId"),
            git_connected=bool(raw.get("gitConnected")),
            deployment_pipeline=bool(raw.get("deploymentPipeline")),
            role_assignments=[RoleAssignment.from_api(r) for r in raw.get("roleAssignments", [])],
            items=[Item.from_api(i) for i in raw.get("items", [])],
            pipelines=dict(raw.get("pipelines") or {}),
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
