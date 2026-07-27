"""The auditing core — the business logic, and the innermost layer.

Nothing here imports a web framework, an HTTP client, or a database. It depends
on nothing outside itself, and everything else depends on it. That is what lets
the same engine serve the REST API, the CLI, the MCP server, and a future
background worker without any of them being able to disagree.

* :mod:`.enums`    — the vocabulary: pillars, layers, scopes, statuses.
* :mod:`.models`   — data models: workspace context, check spec, check result.
* :mod:`.scoring`  — 0-3 bands, rating bands, and the weighted roll-up.
* :mod:`.engine`   — runs selected checks across workspaces.
* :mod:`.checks`   — the deterministic rule library.
"""
from __future__ import annotations

from .enums import (
    ITEM_TYPE_SCOPE,
    LAYER_ITEM_TYPES,
    SEVERITY_RANK,
    Layer,
    Pillar,
    Resource,
    Scope,
    Severity,
    Status,
)
from .models import (
    MAX_SCORE,
    CheckContext,
    CheckResult,
    CheckSpec,
    Item,
    RoleAssignment,
    WorkspaceContext,
)
from .scoring import (
    aggregate,
    band_from_coverage,
    percentage,
    rating,
    scored_only,
    status_from_score,
)

__all__ = [
    "ITEM_TYPE_SCOPE",
    "LAYER_ITEM_TYPES",
    "MAX_SCORE",
    "SEVERITY_RANK",
    "CheckContext",
    "CheckResult",
    "CheckSpec",
    "Item",
    "Layer",
    "Pillar",
    "Resource",
    "RoleAssignment",
    "Scope",
    "Severity",
    "Status",
    "WorkspaceContext",
    "aggregate",
    "band_from_coverage",
    "percentage",
    "rating",
    "scored_only",
    "status_from_score",
]
