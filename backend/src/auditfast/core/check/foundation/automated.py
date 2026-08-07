"""Foundation — cross-cutting, informational (never scored).

Describes the estate rather than judging it. Kept out of the six scored pillars
but still reported, because "what is actually in here" is the context every other
finding is read against.
"""
from __future__ import annotations

from collections import Counter

from auditfast.core.check.helpers import Verdict, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope
from auditfast.core.models import CheckContext


@check(
    id="WS-INVENTORY", ref="IMPL-20", title="Workspace item inventory captured (informational — enumerates all items; never fails) [WS-INVENTORY]",
    pillar=Pillar.FOUNDATION, scope=Scope.WORKSPACE,
    requires=[Resource.ITEMS], required=False,
)
def item_inventory(ctx: CheckContext) -> Verdict:
    """Counts of every Fabric item type present in the workspace."""
    counts = Counter(i.type or "Unknown" for i in ctx.workspace.items)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no items"
    return note(summary)
