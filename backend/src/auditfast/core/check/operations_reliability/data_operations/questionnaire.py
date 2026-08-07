"""Operations & Reliability · Data Operations — interactive (self-assessed) checks.

Points a machine cannot read from Fabric metadata — SLA targets/monitoring,
alerting, folder-based domain governance, and CI/CD tests. The reviewer
self-assesses each during the audit and the chosen option's 0-3 score rolls into
the report, per Data Operations workspace — the Azure Well-Architected Review
model. Skipping records N/A and does not score.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.OPERATIONS, Layer.MIXED)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — implemented consistently", 3, ""),
        CheckOption("partial", "Partially — some gaps remain", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="WS-DOMAIN-FOLDERS", ref="1.1.4",
    title="Domain segregation via folders (Finance, Sales, etc.) is consistent and applied uniformly across Prep and Store workspaces",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are workspace folders used to segregate domains (Finance, Sales, …) consistently and uniformly across the Prep and Store workspaces?",
    options=_options(
        "Extend the same domain-folder taxonomy to every Prep and Store workspace so the structure is uniform.",
        "Adopt a consistent domain-folder structure (Finance, Sales, …) across all Prep and Store workspaces.",
    ),
)


questionnaire_check(
    id="PL-SLA-MONITORED", ref="9.4.2",
    title="Pipeline completion SLAs set and monitored",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are completion SLAs defined for critical pipelines and actively monitored against actual run durations?",
    options=_options(
        "Define SLAs for the remaining critical pipelines and wire them into monitoring.",
        "Set completion SLAs for critical pipelines and monitor actual durations against them (e.g. via Data Activator or the Metadata DB).",
    ),
)


questionnaire_check(
    id="PL-SLA-ALERTS", ref="9.4.3",
    title="SLA breach triggers alerts (Data Activator, email, Teams)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Does an SLA breach automatically raise an alert (Data Activator, email, or Teams) to the owning team?",
    options=_options(
        "Extend SLA-breach alerting to all critical pipelines and confirm the alerts reach the owning team.",
        "Configure SLA-breach alerts via Data Activator, email, or Teams so late/failed runs notify the owning team.",
    ),
)


questionnaire_check(
    id="OPS-INTEGRATION-TESTS", ref="11.5.2",
    title="Integration tests validate end-to-end pipeline execution",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Do integration tests validate end-to-end pipeline execution (source → Gold) before release?",
    options=_options(
        "Broaden integration-test coverage to the remaining end-to-end pipeline paths.",
        "Add integration tests that run the pipelines end-to-end and assert the run succeeds before promotion.",
    ),
)


questionnaire_check(
    id="OPS-DATA-VALIDATION-TESTS", ref="11.5.3",
    title="Data validation tests run post-deployment (record counts, schema checks)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Do automated data-validation tests (record counts, schema checks) run after each deployment?",
    options=_options(
        "Extend post-deployment validation to all critical tables and schemas.",
        "Add post-deployment data-validation tests (record counts and schema checks) to the release process.",
    ),
)
