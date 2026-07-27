"""Workspace checks — Operational Excellence.

Naming, source control, promotion, and layer hygiene. Governance findings roll up
into this pillar, matching the Area 8 mapping in the design specification.
"""
from __future__ import annotations

import re

from ...enums import LAYER_ITEM_TYPES, Pillar, Resource, Scope, Severity
from ...models import CheckContext
from ..helpers import Verdict, binary, not_applicable, note
from ..registry import check


@check(
    id="WS-NAME", ref="1.1.7", title="Workspace naming convention",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE, severity=Severity.LOW,
    requires=[Resource.WORKSPACE],
)
def naming_convention(ctx: CheckContext) -> Verdict:
    """The workspace name matches the org convention configured for the project."""
    pattern = ctx.setting("naming_convention")
    name = ctx.workspace.display_name
    ok = bool(pattern) and re.match(pattern, name) is not None
    return binary(
        ok,
        f"'{name}' matches convention" if ok
        else f"'{name}' does not match convention {pattern!r}",
    )


@check(
    id="WS-GIT", ref="11.1.2", title="Git integration enabled",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.GIT],
)
def git_connected(ctx: CheckContext) -> Verdict:
    """The workspace is connected to Git so its items are source-controlled."""
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable("Git connection state could not be read from Fabric")
    ok = ctx.workspace.git_connected
    return binary(ok, "Workspace is connected to Git" if ok
                  else "Workspace is not connected to Git")


@check(
    id="WS-DEPLOY", ref="11.2", title="Deployment pipeline configured",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE],
)
def deployment_pipeline(ctx: CheckContext) -> Verdict:
    """The workspace is assigned to a deployment pipeline gating promotion."""
    ok = ctx.workspace.deployment_pipeline
    return binary(ok, "Assigned to a deployment pipeline" if ok
                  else "No deployment pipeline assigned")


@check(
    id="WS-LAYER-CONTENT", ref="1.1.2", title="Contains the expected items for its layer",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
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
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
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
