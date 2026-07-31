"""Governance & Compliance · Data Logs — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-GOV-RETENTION",
    ref="Q-GOV-3",
    title="Data retention and lifecycle policies defined and implemented",
    pillar=Pillar.GOVERNANCE,
    layers=(Layer.STORAGE, Layer.LOGS),
    question=(
        "Are data retention and lifecycle policies (archival / deletion of aged or "
        "log data) defined and actually implemented for this workspace's data?"
    ),
    options=(
        Option("implemented", "Policies defined and automatically enforced", 3),
        Option(
            "defined",
            "Policies defined on paper but enforced manually / inconsistently",
            1,
            guidance="Automate retention (lifecycle rules, scheduled purge pipelines, "
            "KQL retention policies) so aged data is removed reliably.",
        ),
        Option(
            "none",
            "No retention or lifecycle policy",
            0,
            guidance="Define retention periods per dataset/log and implement automated "
            "archival or deletion to meet compliance and cost goals.",
        ),
    ),
)
