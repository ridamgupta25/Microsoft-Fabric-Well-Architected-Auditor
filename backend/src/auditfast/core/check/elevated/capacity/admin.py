"""Capacity-administration checks — ``AdminCategory.CAPACITY``.

Ported from the reviewed ``Setup2-Capacity`` notebook, which reads the **Fabric
Capacity Metrics** semantic model — the app a capacity administrator installs.
The scoring ladders here are the notebook's ladders.

**Three of these five cap at 2 without a human.** The metrics app shows the
numbers; it cannot show that anyone *read* them. The notebook asks the reviewer
to confirm — ``METRICS_APP_MONITORED``, ``ANALYSIS_DOCUMENTED``,
``THROTTLING_TRACKED`` — and those arrive here as project settings the run screen
collects. Unset means capped at 2, never a failure.

**Three run-time inputs describe where the app is and how to read it**, all
collected by the run screen and all from the notebook's Step 1:

* ``capacity_metrics_workspace`` (``METRICS_WORKSPACE``) — which workspace holds
  the app. It can be installed anywhere and Fabric offers no way to find it other
  than looking. A **hint**: the named workspace is searched first and the rest
  still follow, so a wrong value costs ordering.
* ``capacity_metrics_model`` (``MODEL_NAME_CONTAINS``) — how the app's semantic
  model is recognised, by name. A **filter**, not a hint: the stock app, FUAM and
  a renamed install carry different names, and a value matching nothing makes
  12.2.1 report "not deployed" for an estate that monitors its capacity properly.
  12.2.1's evidence names what it searched for, so the 0 is actionable.
* ``capacity_peak_start_hour``/``capacity_peak_end_hour``
  (``PEAK_START_HOUR``/``PEAK_END_HOUR``) — the client's working day, used by
  12.1.2 to split busy from quiet time. The model's times are **UTC**, so a
  client elsewhere needs these shifted; the window may wrap past midnight, and
  12.1.2's evidence names the window it measured.

Two rules carry over from the standard library and are not negotiable:

* the check is a **pure function** of its ``CheckContext`` — no clock, no
  randomness, no model call. Capacity data is time-series, so the verdict comes
  from values in the snapshot, never from ``datetime.now()``;
* data the token could not read is **N/A, never FAIL**.
"""
from __future__ import annotations

from ....enums import AdminCategory, Pillar, Resource, Severity
from ...helpers import Verdict, graded, not_applicable
from ...registry import admin_check


def _metrics(ctx) -> dict | None:
    if not ctx.workspace.has(Resource.CAPACITY_METRICS):
        return None
    return ctx.workspace.capacity_metrics


def _unreadable() -> Verdict:
    return not_applicable(
        "The Fabric Capacity Metrics model could not be found or read. It needs "
        "the app installed and at least Contributor on the workspace holding it — "
        "name that workspace in the run's 'capacity metrics workspace' input"
    )


@admin_check(
    id="CP-12-2-1", ref="12.2.1", title="Fabric Capacity Metrics App deployed and monitored",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.HIGH, requires=[Resource.CAPACITY_METRICS],
)
def metrics_app(ctx) -> Verdict:
    """The Capacity Metrics app is installed, and somebody reviews it.

    Ladder (from the notebook): no readable metrics model scores 0 — the app is
    how a capacity is monitored at all. Found scores 2, and 3 only when the
    reviewer confirms it is looked at regularly, which no API reports.
    """
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    if not metrics.get("model_found"):
        searched = str(metrics.get("model_name_searched") or "capacity metrics")
        return graded(
            0,
            f"No semantic model whose name contains '{searched}' was found in the "
            f"workspaces searched, so nothing is monitoring this capacity's "
            f"consumption. If the app is installed under another name — FUAM and "
            f"renamed installs are common — set it in the run's 'capacity metrics "
            f"model name' input before treating this as a gap",
        )
    if ctx.setting("metrics_app_monitored", False):
        return graded(3, "Capacity Metrics app installed and confirmed to be reviewed regularly")
    return graded(
        2,
        "Capacity Metrics app is installed, but nobody has confirmed it is "
        "reviewed — an app nobody opens monitors nothing",
    )


@admin_check(
    id="CP-12-1-2", ref="12.1.2", title="Peak vs off-peak utilization profiled",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def peak_profile(ctx) -> Verdict:
    """Busy and quiet hours are separable, and the profile is written down.

    Ladder (from the notebook): a readable model with no usable time column
    scores 1 — the data is there but the day cannot be split. A split scores 2,
    and 3 when the analysis is documented.
    """
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    split = metrics.get("peak_split")
    if not split:
        return graded(
            1,
            "Metrics model readable, but no usable time column was found, so busy "
            "and quiet hours cannot be separated",
        )
    detail = f"peak CU={split.get('peak', 0)}, off-peak CU={split.get('off_peak', 0)}"
    window = str(split.get("window") or "")
    if window:
        # Naming the window matters: the model's times are UTC, so a reader in
        # another timezone needs to see which half of the day was measured.
        detail += f", working day {window}"
    if ctx.setting("analysis_documented", False):
        return graded(3, f"Utilization profiled ({detail}) and the analysis is documented")
    return graded(
        2,
        f"Utilization profiled ({detail}), but no written profile was confirmed — "
        f"an SKU decision needs the shape of the day recorded, not just visible",
    )


@admin_check(
    id="CP-12-2-2", ref="12.2.2", title="Top CU-consuming workloads identified",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def top_consumers(ctx) -> Verdict:
    """Consumption breaks down by item, and somebody has acted on it.

    Ladder (from the notebook): readable but no per-item breakdown scores 1; a
    breakdown scores 2; documented findings score 3.
    """
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    consumers = metrics.get("top_consumers") or []
    if not consumers:
        return graded(
            1,
            "Metrics model readable, but consumption could not be broken down by "
            "item or operation, so the heaviest workloads cannot be named",
        )
    if ctx.setting("analysis_documented", False):
        return graded(
            3,
            f"{len(consumers)} top capacity consumer(s) identified and the findings "
            f"are documented",
        )
    return graded(
        2,
        f"{len(consumers)} top capacity consumer(s) identified, but there is no "
        f"evidence anyone has acted on them",
    )


@admin_check(
    id="CP-12-2-4", ref="12.2.4", title="Capacity bursting and throttling incidents tracked",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.HIGH, requires=[Resource.CAPACITY_METRICS],
)
def throttling(ctx) -> Verdict:
    """Throttling and overload are visible, and incidents are tracked.

    Ladder (from the notebook): no throttling/overload signal in the model scores
    1 — incidents cannot be counted at all. Otherwise a clean capacity, or one
    whose incidents are tracked, scores 3; untracked incidents score 2.
    """
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    if not metrics.get("throttling_readable", False):
        return graded(
            1,
            "No throttling, overload or burndown column was found in the model, so "
            "incidents cannot be counted",
        )
    count = int(metrics.get("throttling_events") or 0)
    if count == 0:
        return graded(3, "No throttling or overload event was recorded in the readable window")
    if ctx.setting("throttling_tracked", False):
        return graded(
            3,
            f"{count} throttling/overload event(s) observed, and incident tracking "
            f"is confirmed",
        )
    return graded(
        2,
        f"{count} throttling/overload event(s) observed with no confirmation they "
        f"are logged and acted on — repeated throttling is the signal a capacity "
        f"is undersized",
    )


@admin_check(
    id="CP-12-2-6", ref="12.2.6",
    title="Warehouse and semantic-model query load included in capacity analysis",
    pillar=Pillar.COST_MANAGEMENT, category=AdminCategory.CAPACITY,
    severity=Severity.MEDIUM, requires=[Resource.CAPACITY_METRICS],
)
def query_load(ctx) -> Verdict:
    """Query load is separable from pipeline load in the capacity picture.

    Ladder (from the notebook): no separable query load scores 1; separable
    scores 2; documented analysis scores 3.
    """
    metrics = _metrics(ctx)
    if metrics is None:
        return _unreadable()
    rows = metrics.get("query_load") or []
    if not rows:
        return graded(
            1,
            "No Warehouse or semantic-model query load could be separated from "
            "overall capacity usage, so sizing cannot account for it",
        )
    if ctx.setting("analysis_documented", False):
        return graded(
            3,
            f"{len(rows)} Warehouse/semantic query-load row(s) identified and "
            f"included in a documented analysis",
        )
    return graded(
        2,
        f"{len(rows)} Warehouse/semantic query-load row(s) identified, but no "
        f"written analysis was confirmed — sizing may be counting pipeline load alone",
    )
