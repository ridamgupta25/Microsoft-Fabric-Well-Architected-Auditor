"""Workspace checks — Cost Optimization.

Capacity assignment and waste from items nobody runs any more.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ...enums import Pillar, Resource, Scope, Severity
from ...models import CheckContext, Item
from ..helpers import Verdict, binary, covered, not_applicable
from ..registry import check


@check(
    id="WS-CAPACITY", ref="12.1", title="Capacity assigned",
    pillar=Pillar.COST, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.WORKSPACE],
)
def capacity_assigned(ctx: CheckContext) -> Verdict:
    """The workspace runs on an explicitly assigned Fabric capacity."""
    capacity = ctx.workspace.capacity_id
    return binary(bool(capacity), f"capacityId={capacity}" if capacity
                  else "No capacity assigned")


def _is_stale(item: Item, *, cutoff_days: int, now: datetime) -> bool:
    """An item is stale when it has no parseable recent run/refresh timestamp.

    A missing or unreadable timestamp counts as stale: we cannot show the item is
    in use, and unused items are the cost problem this check exists to surface.
    """
    stamp = item.last_run_utc
    if not stamp:
        return True
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).days > cutoff_days


@check(
    id="WS-ORPHAN", ref="12.3.4", title="No orphaned / stale items",
    pillar=Pillar.COST, scope=Scope.WORKSPACE, severity=Severity.LOW,
    requires=[Resource.ITEMS],
)
def no_orphaned_items(ctx: CheckContext) -> Verdict:
    """Every item has run or refreshed within the project's staleness window."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    cutoff_days = int(ctx.setting("orphan_days", 90))
    now = datetime.now(timezone.utc)
    items = ctx.workspace.items
    stale = [i for i in items if _is_stale(i, cutoff_days=cutoff_days, now=now)]
    return covered(
        len(items) - len(stale), len(items),
        f"{len(stale)} of {len(items)} items have no run/refresh in {cutoff_days} days",
    )
