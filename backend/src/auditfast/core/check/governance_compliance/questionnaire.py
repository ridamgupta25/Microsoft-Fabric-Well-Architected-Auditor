"""Governance & Compliance - CAT3 client-evidence questionnaire checks."""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.ANY,)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    return [
        CheckOption("yes", "Yes - implemented and evidenced", 3, ""),
        CheckOption("partial", "Partially - some gaps remain", 1, partial_guidance),
        CheckOption("no", "No - not implemented", 0, no_guidance),
    ]


questionnaire_check(
    id="Q-CAT3-REPORT-KPI",
    ref="14.3.3",
    title="Report KPI integrity and validation",
    pillar=Pillar.DATA,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Are KPI definitions consistent across domains, report values validated "
        "against trusted sources, and report interactions tested?"
    ),
    options=_options(
        "Complete KPI reconciliation and interaction testing for the remaining reports or metrics.",
        "Define common KPI ownership and validation, then test report filters, calculations, and interactions.",
    ),
)

questionnaire_check(
    id="Q-CAT3-DATA-CHANGE",
    ref="2.3.5",
    title="Data change and merge strategy",
    pillar=Pillar.DATA,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Are update, delete, and merge-conflict strategies defined and tested "
        "for the data ingestion process?"
    ),
    options=_options(
        "Document and test the missing update, delete, or conflict-resolution cases.",
        "Define table-level update, delete, and conflict-resolution rules and test them with representative records.",
    ),
)

questionnaire_check(
    id="Q-CAT3-TRANSFORMATION",
    ref="3.2.19",
    title="Transformation logic governance",
    pillar=Pillar.DATA,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Is transformation logic documented, reproducible, and verified against "
        "functional requirements before production deployment?"
    ),
    options=_options(
        "Add missing business-rule documentation, reproducibility evidence, or functional sign-off.",
        "Document transformation rules and require functional verification before production deployment.",
    ),
)

questionnaire_check(
    id="Q-CAT3-DQ-FRAMEWORK",
    ref="5.1.1",
    title="Data quality framework and KPIs",
    pillar=Pillar.DATA,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Is there a governed data-quality framework with defined rules, owners, "
        "scoring, and measurable quality KPIs?"
    ),
    options=_options(
        "Complete the missing DQ rules, ownership, scoring method, or KPI definitions.",
        "Establish a formal DQ framework covering rules, ownership, scoring, and completeness, accuracy, timeliness, consistency, uniqueness, and validity KPIs.",
    ),
)

questionnaire_check(
    id="Q-CAT3-ACCESS-REVIEWS",
    ref="6.1.7",
    title="Access review governance",
    pillar=Pillar.SECURITY,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question="Are workspace and domain-folder access reviews scheduled, completed, and documented?",
    options=_options(
        "Complete the overdue workspace or domain-folder reviews and record their approvals.",
        "Establish recurring access reviews for every workspace and domain folder with documented approvals and remediation.",
    ),
)

questionnaire_check(
    id="Q-CAT3-REGULATORY-SCOPE",
    ref="7.1.1",
    title="Regulatory scope and regional processing",
    pillar=Pillar.GOVERNANCE,
    severity=Severity.CRITICAL,
    layers=_LAYERS,
    question=(
        "Are applicable regulations, regulated data categories, and data-residency "
        "requirements identified and met?"
    ),
    options=_options(
        "Complete the missing regulatory inventory, regulated-data classification, or residency assessment.",
        "Document applicable regimes and regulated data, map processing locations, and approve any residency exceptions.",
    ),
)

questionnaire_check(
    id="Q-CAT3-AGREEMENTS-INCIDENTS",
    ref="7.1.5",
    title="Compliance agreements and incident notification",
    pillar=Pillar.GOVERNANCE,
    severity=Severity.CRITICAL,
    layers=_LAYERS,
    question=(
        "Are required service agreements in place and is there a customer-owned "
        "breach and incident notification process?"
    ),
    options=_options(
        "Obtain missing agreements or complete the customer-side notification process and contacts.",
        "Maintain agreements covering all Fabric and Azure services and document customer-owned breach notification steps.",
    ),
)

questionnaire_check(
    id="Q-CAT3-RELEASE-GOVERNANCE",
    ref="7.2.2",
    title="Change and release governance",
    pillar=Pillar.OPERATIONS,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Do production changes follow formal change management with tested rollback "
        "and schema-regression controls?"
    ),
    options=_options(
        "Add approvals, rollback tests, or schema-regression coverage for the remaining release paths.",
        "Enforce approved change records, test rollback procedures, and run regression tests for schema changes.",
    ),
)

questionnaire_check(
    id="Q-CAT3-RETENTION",
    ref="7.2.7",
    title="Financial and audit data retention",
    pillar=Pillar.GOVERNANCE,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question=(
        "Are financial history and audit logs retained according to documented "
        "policy and compliance requirements?"
    ),
    options=_options(
        "Align the affected retention settings or evidence with the approved policy and compliance requirements.",
        "Define and enforce retention schedules for financial history and audit logs, including disposal and exception handling.",
    ),
)

questionnaire_check(
    id="Q-CAT3-PRIVACY-LIFECYCLE",
    ref="7.3.1",
    title="Privacy governance and data lifecycle",
    pillar=Pillar.GOVERNANCE,
    severity=Severity.CRITICAL,
    layers=_LAYERS,
    question=(
        "Is personal data governed through inventory and legal basis, minimization, "
        "retention, consent or purpose tracking, and cross-border assessment?"
    ),
    options=_options(
        "Complete the missing personal-data inventory, legal basis, minimization, retention, consent, or transfer evidence.",
        "Maintain a personal-data inventory with legal basis and enforce minimization, lifecycle, consent or purpose, and transfer controls.",
    ),
)

questionnaire_check(
    id="Q-CAT3-PRIVACY-RIGHTS",
    ref="7.3.3",
    title="Privacy rights implementation",
    pillar=Pillar.GOVERNANCE,
    severity=Severity.CRITICAL,
    layers=_LAYERS,
    question=(
        "Can personal-data erasure and rectification requests be executed and "
        "verified across all applicable data layers?"
    ),
    options=_options(
        "Test and close the gaps in downstream deletion, correction, verification, or exception handling.",
        "Implement an end-to-end erasure and rectification process with traceability across source, derived, and reporting layers.",
    ),
)

questionnaire_check(
    id="Q-CAT3-DATA-OWNERSHIP",
    ref="8.2.1",
    title="Data ownership and accountability",
    pillar=Pillar.DATA,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question="Does every dataset and table have a named accountable business or data owner?",
    options=_options(
        "Assign owners to the datasets or tables that are currently missing accountable ownership.",
        "Maintain a current ownership register with an accountable owner for every dataset and table.",
    ),
)

questionnaire_check(
    id="Q-CAT3-DR-READINESS",
    ref="9.2.1",
    title="Disaster recovery readiness",
    pillar=Pillar.OPERATIONS,
    severity=Severity.CRITICAL,
    layers=_LAYERS,
    question=(
        "Are RTO and RPO defined, is the DR plan tested, and is annual DR testing "
        "scheduled and completed?"
    ),
    options=_options(
        "Define missing RTO/RPO targets or complete and evidence the required DR test cadence.",
        "Maintain a tested DR plan with data-product RTO/RPO targets and at least annual recovery exercises.",
    ),
)

questionnaire_check(
    id="Q-CAT3-DATA-FRESHNESS",
    ref="9.4.1",
    title="Data freshness SLA",
    pillar=Pillar.OPERATIONS,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question="Are data-freshness SLAs defined, owned, monitored, and escalated for each data product or Gold table?",
    options=_options(
        "Define or monitor freshness SLAs for the data products or Gold tables that have gaps.",
        "Maintain an owned freshness-SLA register with monitoring, breach handling, and approved exceptions.",
    ),
)

questionnaire_check(
    id="Q-CAT3-INCIDENT-RESPONSE",
    ref="10.5.4",
    title="Operational incident response",
    pillar=Pillar.OPERATIONS,
    severity=Severity.HIGH,
    layers=_LAYERS,
    question="Are runbooks available and maintained for common pipeline, notebook, Warehouse, and data-quality failures?",
    options=_options(
        "Complete the missing failure runbooks or validate them through an operational walkthrough.",
        "Maintain tested incident runbooks for common failure scenarios with clear ownership and escalation paths.",
    ),
)
