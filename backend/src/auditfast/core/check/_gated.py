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

from ..enums import StrEnum
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
