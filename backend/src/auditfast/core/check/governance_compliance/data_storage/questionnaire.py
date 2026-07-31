"""Governance & Compliance · Data Storage — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-GOV-CLASSIFICATION",
    ref="Q-GOV-2",
    title="Data classification scheme defined and applied to datasets",
    pillar=Pillar.GOVERNANCE,
    layers=(Layer.STORAGE, Layer.REPORTING),
    question=(
        "Has a data classification scheme (e.g., Public / Internal / Confidential / "
        "Restricted) been defined and applied to the datasets in this workspace?"
    ),
    options=(
        Option("applied", "Scheme defined and applied consistently to datasets", 3),
        Option(
            "defined",
            "Scheme defined but not consistently applied",
            1,
            guidance="Apply the classification to all datasets and reflect it with "
            "sensitivity labels so downstream controls can act on it.",
        ),
        Option(
            "none",
            "No data classification scheme",
            0,
            guidance="Define a classification scheme and apply it, starting with the "
            "datasets that carry sensitive or regulated data.",
        ),
    ),
)
