"""Governance & Compliance · Data Operations — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-GOV-OWNERSHIP",
    ref="Q-GOV-1",
    title="Documented owner / data steward for each workspace and critical dataset",
    pillar=Pillar.GOVERNANCE,
    layers=(Layer.ANY,),
    question=(
        "Is there a documented, discoverable owner or data steward accountable for "
        "each workspace and its business-critical datasets?"
    ),
    options=(
        Option("catalog", "Ownership documented in a catalog and kept current", 3),
        Option(
            "partial",
            "Ownership known informally but not documented everywhere",
            1,
            guidance="Record an accountable owner/steward per workspace and dataset in "
            "a catalog (Purview) or a maintained register.",
        ),
        Option(
            "none",
            "No clear ownership",
            0,
            guidance="Assign and document an owner and data steward for each workspace "
            "and its critical datasets.",
        ),
    ),
)
