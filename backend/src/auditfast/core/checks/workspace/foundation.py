"""Workspace checks — Foundation (cross-cutting, informational).

These describe the estate rather than judging it, so they are never scored. They
still appear in reports, because "what is actually in here" is the context every
other finding is read against.
"""
from __future__ import annotations

from collections import Counter

from ...enums import Pillar, Resource, Scope
from ...models import CheckContext
from ..helpers import Verdict, note
from ..registry import check


@check(
    id="WS-INVENTORY", ref="1.1", title="Item inventory",
    pillar=Pillar.FOUNDATION, scope=Scope.WORKSPACE,
    requires=[Resource.ITEMS],
)
def item_inventory(ctx: CheckContext) -> Verdict:
    """Counts of every Fabric item type present in the workspace."""
    counts = Counter(i.type or "Unknown" for i in ctx.workspace.items)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no items"
    return note(summary)
