"""Governance & Compliance · Data Logs — interactive (self-assessed) checks.

The remaining point is the *data access* audit trail: who read which data and
when. That is served by the tenant audit / admin APIs, which need tenant-admin
scope this tool does not request, so the read-only Fabric REST crawl cannot see
it. The reviewer self-assesses it during the audit and the chosen option's 0-3
score rolls into the report, per Data Logs workspace — the Azure Well-Architected
Review model. Skipping records N/A and does not score.

Warehouse-level auditing (7.4.6) is no longer asked here: it is measured by
``GOV-WH-AUDIT`` in ``automated.py``, which reads each Warehouse's
``settings/sqlAudit`` configuration over ordinary delegated Fabric REST.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.LOGS, Layer.MIXED)

#: Why every question in this module is asked rather than measured.
_WHY = (
    "self-assessed: access auditing is only visible through tenant-admin audit "
    "APIs, which this tool does not call"
)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — in place and reviewed", 3, ""),
        CheckOption("partial", "Partially — some data or schemas are covered", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="GOV-ACCESS-AUDIT", ref="7.4.3",
    title="Data access audit trail exists (who accessed what data, when)",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Is there a retained, queryable record of who accessed which data and when — one you "
        f"could produce on request? ({_WHY})"
    ),
    options=_options(
        "Extend the access record to the data stores it does not yet cover, and retain it for the "
        "period your policy requires.",
        "Establish a data-access audit trail (e.g. exported tenant audit activity landed in the "
        "Eventhouse) recording who accessed what data and when.",
    ),
)
