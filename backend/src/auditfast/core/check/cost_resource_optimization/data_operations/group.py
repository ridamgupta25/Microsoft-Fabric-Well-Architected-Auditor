"""Cost & Resource Optimization - Data Operations — cross-workspace (group) check.

Compares the members of a project group (Dev -> UAT -> Prod) for capacity-alert
coverage that should hold in every environment. Registers into the separate
``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than two
members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext


@group_check(
    id="XW-CU-ALERTS", ref="12.2.7",
    title="CU consumption alerts configured for proactive throttling prevention",
    pillar=Pillar.COST_MANAGEMENT, severity=Severity.MEDIUM,
    requires=[Resource.ACTIVATOR_DEFINITIONS], required=False,
)
def cu_alerts_consistent(ctx: GroupContext) -> Verdict:
    """A Data Activator (for capacity alerting) exists in every environment.

    An environment "implements" proactive throttling prevention when it holds at
    least one Data Activator with a configured rule — the in-Fabric alerting
    surface for capacity/CU events. N/A when fewer than two members' Activator
    definitions could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ACTIVATOR_DEFINITIONS),
        implements=_xw.has_active_activator,
        practice="configures a Data Activator for capacity alerting",
        data_name="Data Activator definitions",
    )
