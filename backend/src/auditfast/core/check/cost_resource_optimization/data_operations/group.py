"""Cost & Resource Optimization - Data Operations — cross-workspace (group) check.

Compares the members of a project group (Dev -> UAT -> Prod) for capacity-alert
coverage that should hold in every environment. Registers into the separate
``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than two
members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext


@group_check(
    id="XW-CU-ALERTS", ref="12.2.7",
    title="CU consumption alerts configured for proactive throttling prevention",
    pillar=Pillar.COST_MANAGEMENT, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE, Resource.ACTIVATOR_DEFINITIONS], required=False,
)
def cu_alerts_consistent(ctx: GroupContext) -> Verdict:
    """A CU-consumption alert exists for the *capacity* carrying these workspaces.

    CU / throttling is a **capacity-scoped** concern, not a per-workspace one: one
    alert on a capacity covers every workspace assigned to it. So the group's
    members are de-duplicated by ``capacity_id`` and the verdict is per distinct
    capacity — a single correct capacity alert must never read as "0 of 3
    workspaces".

    A CU alert lives on a surface a workspace-scoped audit does not fetch: a Data
    Activator bound to the Capacity Metrics semantic model, or an Azure Monitor
    alert rule on the Fabric capacity resource. When a member workspace does hold
    an active Data Activator it is credited to its capacity; otherwise, because
    the authoritative capacity-scoped surfaces were not fetched, the result is N/A
    (not FAIL) — "not fetched" is not "absent". Intent classification keeps this
    (capacity) distinct from 9.4.3 (SLA-breach) alerting so one activator is not
    double-counted.
    """
    capacities: dict[str, list[str]] = {}
    unknown_capacity: list[str] = []
    for member in ctx.members:
        cid = member.workspace.capacity_id
        label = _xw.env_label(member)
        if cid:
            capacities.setdefault(cid, []).append(label)
        else:
            unknown_capacity.append(label)

    distinct = len(capacities)
    if distinct == 0:
        return not_applicable(
            "no capacity id could be read for any workspace in this group, so "
            "capacity-level CU alerting cannot be evaluated"
        )

    alerted = [
        cid for cid in capacities
        if any(
            _xw.has_active_activator(member.workspace)
            for member in ctx.members
            if member.workspace.capacity_id == cid
        )
    ]
    shared = "; ".join(
        f"{cid[:8]}\u2026 carries {', '.join(labels)}"
        for cid, labels in capacities.items()
    )

    if alerted and len(alerted) == distinct:
        return covered(
            distinct, distinct,
            f"a Data Activator capacity alert is present for all {distinct} "
            f"capacity(ies) behind this group: {shared}",
        )
    if alerted:
        return covered(
            len(alerted), distinct,
            f"a capacity alert is present for {len(alerted)} of {distinct} "
            f"capacity(ies): {shared}",
        )
    return not_applicable(
        f"the {len(ctx.members)} workspace(s) in this group share {distinct} "
        f"capacity(ies) ({shared}); CU-consumption / throttling alerts are "
        "configured at capacity scope — a Data Activator on the Capacity Metrics "
        "app, or an Azure Monitor alert on the Fabric capacity resource — which a "
        "workspace-scoped audit does not fetch, so it cannot be determined here. "
        "Create one CU alert on the shared capacity and route it to the capacity "
        "admin (a single alert covers all of its workspaces)."
    )
