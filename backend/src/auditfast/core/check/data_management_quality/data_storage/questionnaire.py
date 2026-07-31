"""Data Management & Quality · Data Storage — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-DATA-MASTER",
    ref="Q-DATA-3",
    title="Master / reference data is centrally managed and reused",
    pillar=Pillar.DATA,
    layers=(Layer.STORAGE,),
    question=(
        "Is master and reference data (shared dimensions, code lists) centrally "
        "managed and reused across the estate rather than duplicated per project?"
    ),
    options=(
        Option("central", "Centrally managed and reused via shared dimensions/shortcuts", 3),
        Option(
            "partial",
            "Partly shared, but some duplication exists",
            1,
            guidance="Consolidate duplicated dimensions/code lists into shared, governed "
            "tables and reference them (OneLake shortcuts) instead of copying.",
        ),
        Option(
            "duplicated",
            "Reference data is duplicated across projects",
            0,
            guidance="Establish governed master/reference datasets and reuse them across "
            "workspaces to avoid conflicting copies.",
        ),
    ),
)
