"""Capacity-administration checks ported from the Capacity Metrics notebook.

This module is the drop-in slot for the
capacity family: capacity metrics, SKU sizing, throttling and overload events —
data that needs capacity administration rights.

To populate it, register with ``category=AdminCategory.CAPACITY``::

    from ...registry import admin_check
    from ....enums import AdminCategory, Pillar, Resource, Scope, Severity
    from ..helpers import Verdict, graded, not_applicable

    @admin_check(
        id="CP-THROTTLE", ref="12.2.1",
        title="Capacity is not sustained in throttling",
        pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
        scope=Scope.WORKSPACE, severity=Severity.HIGH,
        requires=[Resource.CAPACITY_METRICS],
    )
    def not_throttled(ctx) -> Verdict:
        if not ctx.workspace.has(Resource.CAPACITY_METRICS):
            return not_applicable("Capacity metrics could not be read")
        ...

Nothing else needs editing: the catalog endpoint, the selection screen and the
run mode are all driven from the registry, so the category lights up with a live
count as soon as a check lands here.

Two rules carry over from the standard library and are not negotiable:

* the check is a **pure function** of its ``CheckContext`` — no clock, no
  randomness, no model call. Capacity data is time-series, so derive the verdict
  from values in the snapshot, never from ``datetime.now()``;
* data the token could not read is **N/A, never FAIL**.
"""
from __future__ import annotations

from ...helpers import Verdict, graded, not_applicable
from ...registry import admin_check
from ....enums import AdminCategory, Pillar, Resource, Severity


def _metrics(ctx) -> dict | None:
    if not ctx.workspace.has(Resource.CAPACITY_METRICS):
        return None
    return ctx.workspace.capacity_metrics


def _unreadable() -> Verdict:
    return not_applicable("The Fabric Capacity Metrics model could not be found or read")


@admin_check(
    id="CP-12-2-1", ref="12.2.1", title="Fabric Capacity Metrics App deployed and monitored",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.HIGH, requires=[Resource.CAPACITY_METRICS],
)
def metrics_app(ctx) -> Verdict:
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    if not metrics.get("model_found"):
        return graded(0, "No readable Fabric Capacity Metrics semantic model was found")
    score = 3 if bool(ctx.setting("metrics_app_monitored", False)) else 2
    return graded(score, f"Capacity Metrics model found; regular review confirmed={score == 3}")


@admin_check(
    id="CP-12-1-2", ref="12.1.2", title="Peak vs off-peak utilization profiled",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def peak_profile(ctx) -> Verdict:
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    split = metrics.get("peak_split")
    if not split:
        return graded(1, "Capacity metrics were readable, but no hourly peak/off-peak split was available")
    score = 3 if bool(ctx.setting("analysis_documented", False)) else 2
    return graded(score, f"Peak CU={split.get('peak', 0)}; off-peak CU={split.get('off_peak', 0)}; analysis documented={score == 3}")


@admin_check(
    id="CP-12-2-2", ref="12.2.2", title="Top CU-consuming workloads identified",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def top_consumers(ctx) -> Verdict:
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    consumers = metrics.get("top_consumers") or []
    if not consumers:
        return graded(1, "Capacity metrics were readable, but no top-consumer rows were available")
    score = 3 if bool(ctx.setting("analysis_documented", False)) else 2
    return graded(score, f"{len(consumers)} top capacity consumer(s) identified; analysis documented={score == 3}")


@admin_check(
    id="CP-12-2-4", ref="12.2.4", title="Capacity bursting and throttling incidents tracked",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.HIGH, requires=[Resource.CAPACITY_METRICS],
)
def throttling(ctx) -> Verdict:
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    if not metrics.get("throttling_readable", False):
        return graded(1, "The metrics model exposed no recognizable throttling or overload signal")
    count = int(metrics.get("throttling_events") or 0)
    tracked = bool(ctx.setting("throttling_tracked", False))
    score = 3 if count == 0 or tracked else 2
    return graded(score, f"{count} throttling/overload event(s) observed; incident tracking confirmed={tracked}")


@admin_check(
    id="CP-12-2-6", ref="12.2.6",
    title="Warehouse and semantic-model query load included in capacity analysis",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def query_load(ctx) -> Verdict:
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    rows = metrics.get("query_load") or []
    if not rows:
        return graded(1, "No Warehouse or semantic-model query load could be separated from capacity usage")
    score = 3 if bool(ctx.setting("analysis_documented", False)) else 2
    return graded(score, f"{len(rows)} Warehouse/semantic query-load row(s) identified; analysis documented={score == 3}")
