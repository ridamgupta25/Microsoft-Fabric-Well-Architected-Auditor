"""Security · Reporting / Semantic — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-SEC-RLS",
    ref="Q-SEC-2",
    title="Row-Level Security defined and tested on confidential semantic models",
    pillar=Pillar.SECURITY,
    layers=(Layer.REPORTING,),
    question=(
        "For semantic models that expose confidential or role-restricted data, is "
        "Row-Level Security (RLS) defined and validated against test users?"
    ),
    options=(
        Option("tested", "RLS defined and verified with role/test-user validation", 3),
        Option(
            "defined",
            "RLS defined but not routinely tested",
            2,
            guidance="Add a validation step ('View as role') to the release checklist "
            "so RLS filters are proven before each publish.",
        ),
        Option(
            "partial",
            "RLS on some models but not all that need it",
            1,
            guidance="Inventory models exposing restricted data and apply RLS to each.",
        ),
        Option(
            "none",
            "No RLS where confidential data is exposed",
            0,
            guidance="Define RLS roles and DAX filters on models exposing restricted "
            "data, then assign members via the workspace or app.",
        ),
    ),
)
