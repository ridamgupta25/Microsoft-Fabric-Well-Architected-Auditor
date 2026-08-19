"""Governance & Compliance · Data Operations — interactive (self-assessed) checks.

What is left here is the part of the lineage/catalog story that genuinely needs a
human: end-to-end lineage *through* systems this tool never sees (the source
system upstream, the Power BI reports downstream), and whether technical metadata
is captured by a scan rather than by hand. Both depend on Microsoft Purview or on
external systems, neither of which the read-only Fabric REST crawl reaches. The
reviewer self-assesses each during the audit and the chosen option's 0-3 score
rolls into the report, per Data Operations workspace — the Azure Well-Architected
Review model. Skipping records N/A and does not score.

Two lineage points that used to be asked here are now measured instead: 8.1.1 and
8.1.5 are answered by ``GOV-LINEAGE-VIEW`` / ``GOV-LINEAGE-CROSSDOMAIN`` in
``automated.py``, which derive the dependency graph from the definitions the crawl
already holds. 7.2.3 moved to ``GOV-FIN-CHANGE-AUDIT`` there too, reading the
Warehouse SQL audit configuration.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.OPERATIONS, Layer.MIXED)

#: Why the lineage / metadata questions are asked rather than measured.
_WHY_LINEAGE = (
    "self-assessed: the systems on either end — the upstream source and Microsoft "
    "Purview — are not exposed by the Fabric REST API this tool reads"
)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — practised consistently and kept current", 3, ""),
        CheckOption("partial", "Partially — some flows or domains are covered", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="GOV-LINEAGE-E2E", ref="8.1.2",
    title="End-to-end lineage visible from source system to Gold Warehouse and Power BI",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Can lineage be followed unbroken from the source system through the Gold Warehouse to the "
        f"Power BI reports that consume it? ({_WHY_LINEAGE})"
    ),
    options=_options(
        "Close the breaks in the chain — typically the source-to-Bronze hop or the "
        "Warehouse-to-report hop — so the lineage runs end to end.",
        "Establish end-to-end lineage from source system to Gold Warehouse to Power BI, in Purview "
        "or a maintained lineage register.",
    ),
)


questionnaire_check(
    id="GOV-TECH-METADATA", ref="8.3.2",
    title="Technical metadata (schema, lineage) automatically captured",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Is technical metadata — table/warehouse schemas and lineage relationships — captured "
        f"automatically by a scan or scheduled job rather than hand-maintained? ({_WHY_LINEAGE})"
    ),
    options=_options(
        "Extend automatic capture to the assets still catalogued by hand, and schedule the scan so "
        "the catalog cannot drift.",
        "Automate technical-metadata capture (e.g. a scheduled Purview scan or a metadata-harvest "
        "job) instead of maintaining schema and lineage manually.",
    ),
)
