"""Data Management & Quality · Data Operations — medallion layer hygiene.

Whether each workspace holds the item types its layer role calls for, and none
that belong to another layer.
"""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, binary, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import LAYER_ITEM_TYPES, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-LAYER-CONTENT", ref="1.1.2", title="Contains the expected items for its layer",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def layer_content(ctx: CheckContext) -> Verdict:
    """The workspace holds at least one item type its layer role calls for."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    layer = ctx.workspace.layer
    expected = LAYER_ITEM_TYPES.get(layer)
    if not expected:
        return note(f"role '{layer.value}' has no layer-specific expectation")
    present = ctx.workspace.item_types()
    return binary(
        bool(present & expected),
        f"expected any of {sorted(expected)}; found {sorted(present) or ['none']}",
    )


@check(
    id="WS-LAYER-SEP", ref="1.1.2", title="Free of other layers' concerns",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def layer_separation(ctx: CheckContext) -> Verdict:
    """The workspace does not hold item types that belong to a different layer."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    layer = ctx.workspace.layer
    expected = LAYER_ITEM_TYPES.get(layer)
    if not expected:
        return note(f"role '{layer.value}' has no separation rule")
    foreign_types: set[str] = set()
    for other_layer, types in LAYER_ITEM_TYPES.items():
        if other_layer is not layer:
            foreign_types |= types
    foreign = ctx.workspace.item_types() & (foreign_types - expected)
    return binary(not foreign, f"foreign item types found: {sorted(foreign) or ['none']}")
