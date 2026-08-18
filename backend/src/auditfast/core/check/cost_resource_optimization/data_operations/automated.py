"""Cost & Resource Optimization · Data Operations — capacity and waste."""
from __future__ import annotations

from datetime import datetime, timezone

from auditfast.core.check._recency import parse_stamp
from auditfast.core.check.helpers import (
    Verdict,
    binary,
    covered,
    graded,
    not_applicable,
    note,
)
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext, Item


@check(
    id="WS-CAPACITY", ref="IMPL-15", title="Workspace is assigned to a Fabric capacity [WS-CAPACITY]",
    pillar=Pillar.COST_MANAGEMENT, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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

    The stamp is parsed by the shared :func:`parse_stamp`, so a timestamp one
    recency check can read is never one another silently drops.
    """
    parsed = parse_stamp(item.last_run_utc)
    if parsed is None:
        return True
    return (now - parsed).days > cutoff_days


@check(
    id="WS-ORPHAN", ref="12.3.4", title="Unused or orphaned Fabric items cleaned up (esp. Dev/QA)",
    pillar=Pillar.COST_MANAGEMENT, scope=Scope.WORKSPACE, severity=Severity.LOW,
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


# =============================================================================
# 12.3.3 — Spark pools not left billing while idle
# =============================================================================

#: The Environment definition records its Spark compute settings under these
#: keys (``Sparkcompute.yml``, parsed by the provider). ``environments`` is keyed
#: by *both* item id and display name, so every read must de-duplicate on the id.
_DYNAMIC_ALLOCATION = "dynamic_executor_allocation"


def _distinct_environments(environments: dict[str, dict]) -> list[dict]:
    """One record per Environment item, in a stable order.

    The provider files each Environment under both its id and its display name,
    so iterating the map naively counts every Environment twice.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for key in sorted(environments):
        record = environments.get(key)
        if not isinstance(record, dict):
            continue
        identity = str(record.get("id") or record.get("display_name") or key)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(record)
    return out


@check(
    id="WS-SPARK-IDLE", ref="12.3.3",
    title="Spark pools not running idle (Environment compute settings tuned)",
    pillar=Pillar.COST_MANAGEMENT, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.ENVIRONMENT_DEFINITIONS], required=True,
)
def spark_pool_not_idle(ctx: CheckContext) -> Verdict:
    """Each Fabric Environment scales its executors down instead of holding them.

    Read from the Environment item's own compute definition (``Sparkcompute.yml``),
    which the provider already fetches. Two readable facts decide it:

    * **dynamic executor allocation is enabled** — without it the Environment
      holds a fixed executor fleet for the life of every session, so the
      capacity is billed for executors that are doing nothing between stages;
    * **the minimum executor floor is small** — dynamic allocation cannot
      release below its own floor, so a high ``min_executors`` re-creates the
      fixed fleet it was meant to avoid. The threshold is the project setting
      ``max_idle_spark_min_executors`` (default 2).

    **What it cannot determine.** The Environment definition carries no session
    or idle-*timeout* value — that setting lives in the workspace/tenant Spark
    settings, which are not fetched — so "how long an idle session survives" is
    not assessed and the evidence says so. Nor is any runtime metric read: this
    judges the configuration, never observed utilisation (``SPARK-POOL``,
    ref 3.4.3, covers utilisation per notebook run).

    N/A when Environment definitions could not be read, and N/A — not a
    failure — when the workspace holds no Environment item at all: a workspace
    with no Environment has no pool configuration of its own to tune.
    """
    if not ctx.workspace.has(Resource.ENVIRONMENT_DEFINITIONS):
        return not_applicable("Environment definitions could not be read from Fabric")

    environments = _distinct_environments(ctx.workspace.environments or {})
    if not environments:
        return not_applicable(
            "Workspace holds no Fabric Environment item, so there is no Spark "
            "compute configuration to tune here"
        )

    try:
        floor = int(ctx.setting("max_idle_spark_min_executors", 2))
    except (TypeError, ValueError):
        return not_applicable(
            "Project setting 'max_idle_spark_min_executors' is not an integer, so "
            "the executor floor cannot be judged"
        )

    tuned = 0
    problems: list[str] = []
    for environment in environments:
        name = str(environment.get("display_name") or environment.get("id") or "?")
        allocation = environment.get(_DYNAMIC_ALLOCATION)
        allocation = allocation if isinstance(allocation, dict) else {}
        enabled = bool(allocation.get("enabled"))
        raw_minimum = allocation.get("min_executors")
        try:
            minimum = int(raw_minimum) if raw_minimum is not None else None
        except (TypeError, ValueError):
            minimum = None

        if not enabled:
            problems.append(f"'{name}' does not enable dynamic executor allocation")
        elif minimum is not None and minimum > floor:
            problems.append(f"'{name}' holds a floor of {minimum} executors "
                            f"(above the {floor} allowed)")
        else:
            tuned += 1

    detail = (
        f"{tuned} of {len(environments)} Fabric Environment(s) scale executors down "
        f"(dynamic allocation on, floor <= {floor})"
    )
    if problems:
        detail += "; " + "; ".join(sorted(problems)[:5])
    detail += (". The Environment definition carries no idle-session timeout, so "
               "how long an idle session survives is not assessed here.")
    return covered(tuned, len(environments), detail)


# =============================================================================
# 12.2.1 — Fabric Capacity Metrics App
# =============================================================================

#: Item names the Microsoft Fabric Capacity Metrics App installs. The app ships a
#: semantic model and a report; Microsoft has shipped both the "Microsoft Fabric
#: Capacity Metrics" and the shorter "Fabric Capacity Metrics" naming, so the
#: match is on the distinctive phrase rather than an exact title.
_CAPACITY_METRICS_PHRASE = "capacity metrics"

#: The item types the app installs. Anything else sharing the phrase (a notebook
#: someone wrote *about* capacity metrics) is not the app.
_CAPACITY_METRICS_TYPES: frozenset[str] = frozenset({"SemanticModel", "Report", "Dashboard"})


@check(
    id="WS-CAPACITY-METRICS", ref="12.2.1",
    title="Fabric Capacity Metrics App deployed and monitored",
    pillar=Pillar.COST_MANAGEMENT, scope=Scope.WORKSPACE, severity=Severity.INFO,
    layers=(Layer.OPERATIONS,), requires=[Resource.ITEMS], required=False,
)
def capacity_metrics_app(ctx: CheckContext) -> Verdict:
    """Report whether the Capacity Metrics App is installed *in this workspace*.

    The app installs a recognisable semantic model and report ("Fabric Capacity
    Metrics" / "Microsoft Fabric Capacity Metrics"), so its presence in the item
    inventory is directly readable.

    **What it cannot determine, and why this is unscored.** Three gaps, each
    fatal to a fair score:

    * the app is normally installed **once**, in a single admin workspace, and
      read from there for the whole tenant — so its absence from *this*
      workspace is not evidence that the tenant lacks it;
    * the tenant-wide install list needs the admin API, which is not called;
    * whether anyone actually **monitors** it — opens it, acts on a throttling
      trend — leaves no trace in any item metadata.

    Scoring presence would therefore fail almost every correctly-run workspace
    for a practice carried out somewhere else. So the check reports the fact and
    stays out of the score; the reviewer confirms the tenant-level install.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    found = [
        item for item in ctx.workspace.items
        if _CAPACITY_METRICS_PHRASE in (item.display_name or "").lower()
        and item.type in _CAPACITY_METRICS_TYPES
    ]
    if found:
        names = ", ".join(sorted(f"{i.display_name} ({i.type})" for i in found)[:3])
        return note(
            f"Fabric Capacity Metrics App content is installed in this workspace: "
            f"{names}. Whether it is regularly reviewed is not readable from item "
            f"metadata — confirm the monitoring routine with the capacity admin."
        )
    return note(
        "No Fabric Capacity Metrics App content in this workspace. The app is "
        "normally installed once in an admin workspace and read from there, so "
        "this is not evidence the tenant lacks it — confirm the tenant-level "
        "install with the capacity admin. Reported, not scored."
    )


# =============================================================================
# 12.2.7 — CU consumption alerting
# =============================================================================

#: Fabric item types for Data Activator. The REST API reports ``Reflex``;
#: ``Activator`` is accepted for forward compatibility with the newer name.
_ACTIVATOR_TYPES: frozenset[str] = frozenset({"Reflex", "Activator"})


@check(
    id="WS-CU-ALERTS", ref="12.2.7",
    title="CU consumption alerts configured for proactive throttling prevention",
    pillar=Pillar.COST_MANAGEMENT, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.WORKSPACE, Resource.ITEMS], required=True,
)
def cu_consumption_alerts(ctx: CheckContext) -> Verdict:
    """Some alerting mechanism exists that *could* warn before the capacity throttles.

    The only readable signal is a Data Activator (Reflex) item in the workspace.
    The trigger conditions inside it — what it watches, at what threshold — are
    **not** fetched, so a Reflex watching capacity CU usage is indistinguishable
    from one watching a pipeline failure or a row count.

    Because of that this check **can never award a full pass**: presence caps at
    a partial credit, and the evidence states outright that the trigger was not
    inspected. Reporting a PASS would assert something the data cannot support.

    Distinct from ``WS-ACTIVATOR`` (ref 10.5.1), which asks whether the
    operational workspace has event-driven alerting *at all* and gates on there
    being pipelines/datasets to raise events about — that check can and does
    pass. This is the capacity-specific variant: it gates on the workspace being
    on a **capacity** (no capacity, no CU to throttle, so N/A) and caps the
    credit because CU-specificity is unverifiable.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    if not ctx.workspace.has(Resource.WORKSPACE):
        return not_applicable("Workspace metadata could not be read from Fabric")
    if not ctx.workspace.capacity_id:
        return not_applicable(
            "Workspace is not assigned to a Fabric capacity, so it consumes no "
            "capacity units to alert on"
        )

    activators = [i for i in ctx.workspace.items if i.type in _ACTIVATOR_TYPES]
    if activators:
        names = ", ".join(sorted(i.display_name or i.id for i in activators)[:3])
        return graded(
            2,
            f"{len(activators)} Data Activator item(s) present ({names}) on capacity "
            f"'{ctx.workspace.capacity_id}' — an alerting mechanism exists, but the "
            f"trigger conditions are not fetched, so whether any of them watches CU "
            f"consumption or a throttling threshold cannot be confirmed. Partial "
            f"credit only: this check can never award a full pass.",
        )
    return graded(
        0,
        f"No Data Activator (Reflex) item in a workspace assigned to capacity "
        f"'{ctx.workspace.capacity_id}' — nothing in the workspace can raise a "
        f"proactive alert before the capacity throttles.",
    )
