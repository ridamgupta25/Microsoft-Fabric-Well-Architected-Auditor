"""Security · Data Operations — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-SEC-ACCESS-REVIEW",
    ref="Q-SEC-3",
    title="Workspace access is reviewed on a defined schedule",
    pillar=Pillar.SECURITY,
    layers=(Layer.ANY,),
    question=(
        "Are workspace role assignments reviewed on a regular schedule to remove "
        "stale or over-privileged access?"
    ),
    options=(
        Option("automated", "Reviewed on a schedule via access reviews / IGA tooling", 3),
        Option(
            "manual",
            "Reviewed manually on a defined cadence (e.g., quarterly)",
            2,
            guidance="Automate the review with Entra ID Access Reviews so recertification "
            "is tracked and enforced rather than relying on memory.",
        ),
        Option(
            "ad_hoc",
            "Reviewed only occasionally / when someone remembers",
            1,
            guidance="Set a recurring quarterly access review and record its outcome.",
        ),
        Option(
            "never",
            "Access is never systematically reviewed",
            0,
            guidance="Establish a periodic access review (Entra ID Access Reviews or a "
            "documented manual process) to remove stale access.",
        ),
    ),
)
