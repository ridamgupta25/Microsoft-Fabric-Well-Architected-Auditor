"""Data Management & Quality · Data Storage — interactive (self-assessed) checks.

End-to-end data-flow lineage is carried by Microsoft Purview and the Fabric
lineage view. Neither is integrated with this tool: the read-only Fabric REST
crawl returns items, definitions and table schemas, but no lineage graph and no
Purview asset. The reviewer therefore self-assesses the point during the audit
and the chosen option's 0-3 score rolls into the report, per Data Storage
workspace — the Azure Well-Architected Review model. Skipping records N/A and
does not score.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.STORAGE, Layer.MIXED)

#: Why the question below is asked rather than measured.
_WHY = (
    "self-assessed: lineage lives in Microsoft Purview and the Fabric lineage "
    "view, neither of which the Fabric REST API this tool reads exposes"
)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — practised consistently and kept current", 3, ""),
        CheckOption("partial", "Partially — some flows are covered", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="WS-LINEAGE-E2E", ref="1.2.1",
    title="Data flow lineage traceable end-to-end from source to Gold Warehouse and downstream semantic models",
    pillar=Pillar.DATA, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Can you trace a data flow end-to-end — source system, Bronze/Silver, Gold Warehouse, and "
        f"the semantic models built on it — from documented or tooled lineage? ({_WHY})"
    ),
    options=_options(
        "Complete the lineage record for the flows that stop short of the Gold Warehouse or the "
        "downstream semantic models.",
        "Establish end-to-end lineage (Purview, the Fabric lineage view, or a maintained lineage "
        "register) covering source system through Gold Warehouse to the semantic models.",
    ),
)
