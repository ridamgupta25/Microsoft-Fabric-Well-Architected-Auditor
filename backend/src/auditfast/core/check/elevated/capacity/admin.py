"""Capacity-administration checks — ``AdminCategory.CAPACITY``.

**No checks are registered here yet.** This module is the drop-in slot for the
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
