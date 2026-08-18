"""Gated checks — runnable, but honest about the access/data they still need.

A *gated* check appears in every audit (so no checklist point is silently missing)
but returns N/A with the specific reason it could not be evaluated on the current
sign-in — "requires Fabric admin access", "requires Git repository access", and so
on. When the platform later obtains that data (an admin sign-in, a service
principal, a portal export, the SQL endpoint), a gated check can be promoted to a
real evaluator.

Underscore-prefixed so the check auto-loader skips it: this module holds the
factory, not checks. The generated ``roadmap.py`` modules import :func:`gated`.
"""
from __future__ import annotations

from collections.abc import Callable

from ..enums import Pillar, StrEnum
from ..models import CheckContext
from .helpers import Verdict, not_applicable


class Requirement(StrEnum):
    """What a not-yet-automatable check needs before it can be evaluated."""

    ADMIN_SCANNER = "admin_scanner"
    ADMIN_ACTIVITY = "admin_activity"
    ADMIN_TENANT = "admin_tenant"
    ITEM_DEFINITION = "item_definition"
    GIT_REPO = "git_repo"
    CAPACITY_METRICS = "capacity_metrics"
    SQL_ENDPOINT = "sql_endpoint"
    DATA_PLANE = "data_plane"
    MANUAL = "manual"


#: Human-readable reason shown as the check's evidence/comment when it is skipped.
REASON: dict[Requirement, str] = {
    Requirement.ADMIN_SCANNER: (
        "Not evaluated on this sign-in: needs Fabric admin access (the Scanner / "
        "detailed-metadata API) to read semantic-model internals, lineage, or labels."
    ),
    Requirement.ADMIN_ACTIVITY: (
        "Not evaluated on this sign-in: needs Fabric admin access to the Activity / "
        "audit-log API (run history, sign-ins, monitoring)."
    ),
    Requirement.ADMIN_TENANT: (
        "Not evaluated on this sign-in: needs Fabric tenant-admin settings "
        "(network, gateway, export/Conditional-Access controls, workspace identity)."
    ),
    Requirement.ITEM_DEFINITION: (
        "Not evaluated on this sign-in: needs the item's source "
        "(notebook / pipeline / model definition) via the Item.ReadWrite scope "
        "(getDefinition) or a portal export."
    ),
    Requirement.GIT_REPO: (
        "Not evaluated on this sign-in: needs access to the workspace's Git "
        "repository (Azure DevOps / GitHub) for branch, PR, and review policies."
    ),
    Requirement.CAPACITY_METRICS: (
        "Not evaluated on this sign-in: needs the Fabric Capacity Metrics app / "
        "capacity metrics API (CU utilization, throttling, bursting)."
    ),
    Requirement.SQL_ENDPOINT: (
        "Not evaluated on this sign-in: needs table column schemas from the "
        "lakehouse/warehouse SQL analytics endpoint."
    ),
    Requirement.DATA_PLANE: (
        "Not evaluated on this sign-in: needs to query the data itself "
        "(SQL endpoint / Spark) — data-plane access, out of scope for a config audit."
    ),
    Requirement.MANUAL: (
        "Manual review required: an organizational, process, or judgement control "
        "with no Fabric API to read."
    ),
}


def gated(requirement: Requirement) -> Callable[[CheckContext], Verdict]:
    """Return a check function that reports N/A with ``requirement``'s reason."""
    reason = REASON[requirement]

    def _gated(_ctx: CheckContext) -> Verdict:
        return not_applicable(reason)

    return _gated


# -- ref -> pillar (checklist taxonomy) ---------------------------------------
# Section rule plus the handful of explicit overrides reproduces the source
# checklist's pillar for every ref. Used by the generated roadmap modules so a
# gated check lands on the same pillar its ref would as a real evaluator.
_SECTION_PILLARS: dict[str, Pillar] = {
    "1": Pillar.ARCHITECTURE, "2": Pillar.DATA_INTEGRATION, "3": Pillar.DATA_PROCESSING,
    "4": Pillar.DATA_MODELING, "5": Pillar.DATA_QUALITY, "6": Pillar.SECURITY_ACCESS,
    "7": Pillar.COMPLIANCE, "8": Pillar.DATA_GOVERNANCE, "9": Pillar.RELIABILITY,
    "10": Pillar.MONITORING, "11": Pillar.DEVOPS, "12": Pillar.COST_MANAGEMENT,
    "14.1": Pillar.ARCHITECTURE, "14.2": Pillar.DATA_PROCESSING, "14.3": Pillar.ARCHITECTURE,
    "14.4": Pillar.SECURITY_ACCESS, "14.5": Pillar.DATA_INTEGRATION,
}
_REF_PILLARS: dict[str, Pillar] = {
    "IMPL-01": Pillar.SECURITY_ACCESS, "IMPL-02": Pillar.SECURITY_ACCESS,
    "IMPL-04": Pillar.SECURITY_ACCESS, "IMPL-06": Pillar.SECURITY_ACCESS,
    "IMPL-15": Pillar.COST_MANAGEMENT, "IMPL-20": Pillar.ARCHITECTURE,
    "IMPL-23": Pillar.DATA_INTEGRATION, "IMPL-24": Pillar.ARCHITECTURE,
    "14.5.3": Pillar.MONITORING, "14.5.4": Pillar.DEVOPS,
}


def pillar_for_ref(ref: str, fallback: Pillar = Pillar.ARCHITECTURE) -> Pillar:
    """Return the checklist pillar for ``ref`` (explicit override, else section rule)."""
    if ref in _REF_PILLARS:
        return _REF_PILLARS[ref]
    parts = ref.split(".")
    section = ".".join(parts[:2]) if parts and parts[0] == "14" else parts[0]
    return _SECTION_PILLARS.get(section, fallback)
