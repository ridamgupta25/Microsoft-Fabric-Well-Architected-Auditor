"""Cost & Resource Optimization · Data Operations — capacity and waste."""
from __future__ import annotations

from datetime import datetime, timezone

from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext, Item


@check(
    id="WS-CAPACITY", ref="IMPL-15", title="Workspace is assigned to a Fabric capacity [WS-CAPACITY]",
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
    id="WS-ORPHAN", ref="12.3.4", title="Unused or orphaned Fabric items cleaned up (esp. Dev/QA)",
    pillar=Pillar.COST, scope=Scope.WORKSPACE, severity=Severity.LOW,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def no_orphaned_items(ctx: CheckContext) -> Verdict:
    """Runnable items have run / refreshed within the staleness window.

    The Fabric List Items API carries no last-run/refresh timestamp, so it is
    read per runnable item from the job-scheduler history (``…/jobs/instances``).
    Only items that actually run a job (pipelines, notebooks, semantic models,
    dataflows, Spark jobs) can be stale; reports and dashboards never run and are
    excluded. When the history is unreadable — or no runnable item has ever run —
    staleness cannot be judged and the check is N/A, never a blanket FAIL. "We
    could not read when the item last ran" is not "the item is orphaned".
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    if not ctx.workspace.has(Resource.ITEM_RUN_HISTORY):
        return not_applicable(
            "Per-item run/refresh history could not be read from Fabric "
            "(jobs/instances was forbidden or unavailable)"
        )
    items = ctx.workspace.items
    dated = [i for i in items if i.last_run_utc]
    if not dated:
        return not_applicable(
            f"No run/refresh has been recorded for any of the {len(items)} "
            "item(s) — none of the runnable items (pipeline / notebook / semantic "
            "model / dataflow) has a job-run history yet, so staleness cannot be "
            "assessed"
        )
    cutoff_days = int(ctx.setting("orphan_days", 90))
    now = datetime.now(timezone.utc)
    stale = [i for i in dated if _is_stale(i, cutoff_days=cutoff_days, now=now)]
    return covered(
        len(dated) - len(stale), len(dated),
        f"{len(stale)} of {len(dated)} item(s) with a known run/refresh are stale "
        f"(> {cutoff_days} days)",
    )
