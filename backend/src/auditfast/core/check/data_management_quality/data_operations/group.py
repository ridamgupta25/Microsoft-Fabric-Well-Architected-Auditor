"""Data Management & Quality · Data Operations — cross-workspace (group) checks.

Compares the members of a project group (Dev → UAT → Prod) for the estate-level
separation-of-concerns practice that a single-workspace check can only judge one
workspace at a time. Registers into the separate ``GROUP_REGISTRY`` via
:func:`group_check`; N/A-not-FAIL when fewer than two members can be read.

The per-workspace angle already ships as ``WS-LAYER-CONTENT`` / ``WS-LAYER-SEP``
(refs 1.1.1 / 1.1.9) in :mod:`.automated`; this module adds the *across the
estate* angle without changing either of them.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import LAYER_ITEM_TYPES, Layer, Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext

from ._layer import effective_layer


def _stays_within_layer(ws: WorkspaceContext) -> bool:
    """True when expected items exist and no items belong to another layer."""
    layer = effective_layer(ws)
    expected = LAYER_ITEM_TYPES.get(layer)
    if not expected:
        return False
    present = ws.item_types()
    foreign_types: set[str] = set()
    for other_layer, types in LAYER_ITEM_TYPES.items():
        if other_layer is not layer:
            foreign_types |= types
    return bool(present & expected) and not (present & (foreign_types - expected))


@group_check(
    id="XW-LAYER-SEP", ref="1.1.1",
    title="Separation of concerns maintained consistently across the project group "
          "(Data Prep / Data Store / Data Consumption × Dev / QA / Prod)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.MEDIUM, requires=[Resource.ITEMS],
    required=False,
)
def layer_separation_consistent(ctx: GroupContext) -> Verdict:
    """Every environment keeps its workspaces within their layer's item types.

    Where ``WS-LAYER-SEP`` judges one workspace, this compares across the group:
    an environment "implements" the practice when its workspace holds no item
    types that belong to another layer. Surfacing it per group catches a clean
    separation in Prod that was never carried back to Dev/UAT (or vice versa).
    N/A when fewer than two members' item inventories could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: (
            ws.has(Resource.ITEMS) and effective_layer(ws) is not Layer.MIXED
        ),
        implements=_stays_within_layer,
        practice="contains expected items and no items from another layer",
        data_name="item inventories with identifiable layer roles",
    )
