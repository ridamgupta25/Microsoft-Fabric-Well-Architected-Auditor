"""Operations & Reliability · Data Logs — interactive (self-assessed) checks.

Observability points that live in Power BI report content, Warehouse/Metadata-DB
schema, or operational access — none of which the read-only crawl can judge. The
reviewer self-assesses each during the audit and the chosen option's 0-3 score
rolls into the report, per Data Logs workspace — the Azure Well-Architected
Review model. Skipping records N/A and does not score.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.LOGS, Layer.MIXED)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — implemented consistently", 3, ""),
        CheckOption("partial", "Partially — some gaps remain", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="OPS-DASH-PIPELINE", ref="10.1.3",
    title="Dashboard shows pipeline status, duration trends, and failure rates",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Is there an operational dashboard showing pipeline status, duration trends, and failure rates?",
    options=_options(
        "Add the missing views (duration trends and/or failure rates) to the operational dashboard.",
        "Build an operational dashboard covering pipeline status, duration trends, and failure rates.",
    ),
)


questionnaire_check(
    id="OPS-AUDIT-SCHEMA", ref="10.2.1",
    title="Audit Tables schema designed for queryability (structured, not free-text)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are the Audit Tables modelled as structured, queryable columns (not free-text blobs)?",
    options=_options(
        "Refactor free-text audit columns into typed, queryable fields.",
        "Redesign the Audit Tables with a structured, queryable schema instead of free-text.",
    ),
)


questionnaire_check(
    id="OPS-DQ-LOGS", ref="10.2.3",
    title="DQ logs, row counts, null checks, and exceptions captured consistently across domains",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are DQ logs, row counts, null checks, and exceptions captured consistently across every domain?",
    options=_options(
        "Standardise DQ logging so every domain captures the same metrics (row counts, null checks, exceptions).",
        "Capture DQ logs — row counts, null checks, and exceptions — consistently across all domains.",
    ),
)


questionnaire_check(
    id="OPS-METADATA-DB", ref="10.2.4",
    title="Metadata DB captures every notebook run, data source changes, and lineage",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Does the Metadata DB record every notebook run, data-source change, and lineage relationship?",
    options=_options(
        "Extend the Metadata DB to capture the missing signals (runs, source changes, or lineage).",
        "Record notebook runs, data-source changes, and lineage in the Metadata DB.",
    ),
)


questionnaire_check(
    id="OPS-AUDIT-QUERYABLE", ref="10.2.5",
    title="Audit Tables and Metadata DB are queryable by operations (not just developers)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Can the operations team query the Audit Tables and Metadata DB directly (not only developers)?",
    options=_options(
        "Grant and document operations-team read access to the Audit Tables and Metadata DB.",
        "Give the operations team a supported, read query path into the Audit Tables and Metadata DB.",
    ),
)


questionnaire_check(
    id="OPS-DASH-COVERAGE", ref="10.4.1",
    title="Dashboard covers all critical pipelines, notebooks, and Warehouse loads",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Does the monitoring dashboard cover all critical pipelines, notebooks, and Warehouse loads?",
    options=_options(
        "Add the uncovered critical pipelines, notebooks, or Warehouse loads to the dashboard.",
        "Expand the dashboard to cover every critical pipeline, notebook, and Warehouse load.",
    ),
)
