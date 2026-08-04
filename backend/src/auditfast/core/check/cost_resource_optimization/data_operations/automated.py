"""Cost & Resource Optimization · Data Operations — capacity and waste."""
from __future__ import annotations

from datetime import datetime, timezone

from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext, Item


@check(
    id="WS-CAPACITY", ref="12.1", title="Capacity assigned",
    pillar=Pillar.COST, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.WORKSPACE], required=True,
)
def capacity_assigned(ctx: CheckContext) -> Verdict:
    """The workspace runs on an explicitly assigned Fabric capacity."""
    capacity = ctx.workspace.capacity_id
    return binary(bool(capacity), f"capacityId={capacity}" if capacity
                  else "No capacity assigned")


def _is_stale(item: Item, *, cutoff_days: int, now: datetime) -> bool:
    """True when the item's last run/refresh is older than the staleness window.

    Only called for items that carry a timestamp; a present-but-unparseable stamp
    is treated as stale (a value we cannot read is suspect). A *missing* timestamp
    is handled by the caller as N/A, not stale — unknown is not the same as unused.
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
    requires=[Resource.ITEMS], required=False,
)
def no_orphaned_items(ctx: CheckContext) -> Verdict:
    """Items with a known run/refresh have run within the staleness window.

    The Fabric List Items API does not expose a last-run/refresh timestamp, so
    when *no* item carries one the data needed to judge staleness is unavailable
    and the check is N/A — never a blanket FAIL of every item. "We could not read
    when the item last ran" is not the same finding as "the item is orphaned".
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    items = ctx.workspace.items
    dated = [i for i in items if i.last_run_utc]
    if not dated:
        return not_applicable(
            f"No run/refresh timestamp is available for any of the {len(items)} "
            "item(s) — the Fabric List Items API does not expose last-run/refresh, "
            "so staleness cannot be assessed (needs per-item run/refresh history)"
        )
    cutoff_days = int(ctx.setting("orphan_days", 90))
    now = datetime.now(timezone.utc)
    stale = [i for i in dated if _is_stale(i, cutoff_days=cutoff_days, now=now)]
    return covered(
        len(dated) - len(stale), len(dated),
        f"{len(stale)} of {len(dated)} item(s) with a known run/refresh are stale "
        f"(> {cutoff_days} days)",
    )
