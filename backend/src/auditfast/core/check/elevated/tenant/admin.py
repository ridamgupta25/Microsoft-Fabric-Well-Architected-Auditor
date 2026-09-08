"""Tenant-administration checks — ``AdminCategory.TENANT``.

**No checks are registered here yet.** This module is the drop-in slot for the
tenant family: tenant settings, the scanner API (endorsement / sensitivity
labels), and tenant audit logs — data that genuinely needs a Fabric tenant
administrator.

To populate it, register with ``category=AdminCategory.TENANT``::

    from ...registry import admin_check
    from ....enums import AdminCategory, Pillar, Resource, Scope, Severity
    from ..helpers import Verdict, binary, not_applicable

    @admin_check(
        id="TN-EXPORT", ref="7.2.1",
        title="Tenant export settings are restricted",
        pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
        scope=Scope.WORKSPACE, severity=Severity.HIGH,
        requires=[Resource.TENANT_SETTINGS],
    )
    def export_restricted(ctx) -> Verdict:
        if not ctx.workspace.has(Resource.TENANT_SETTINGS):
            return not_applicable("Tenant settings could not be read")
        ...

Nothing else needs editing: the catalog endpoint, the selection screen and the
run mode are all driven from the registry, so the category lights up with a live
count as soon as a check lands here.

Two rules carry over from the standard library and are not negotiable:

* the check is a **pure function** of its ``CheckContext`` — no clock, no
  randomness, no model call;
* data the token could not read is **N/A, never FAIL**. A tenant-admin API that
  returns 403 means "we could not determine this", which is not the same claim
  as "this is misconfigured".
"""
from __future__ import annotations
