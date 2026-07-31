"""Operations & Reliability · Data Prep — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-OPS-ALERTING",
    ref="Q-OPS-2",
    title="Pipeline / job failures trigger proactive alerting",
    pillar=Pillar.OPERATIONS,
    layers=(Layer.PREP, Layer.OPERATIONS),
    question=(
        "When a pipeline or job fails, how is the owner or on-call notified?"
    ),
    options=(
        Option("oncall", "Automated alerting to an on-call / monitored channel", 3),
        Option(
            "email",
            "Automated email notifications only",
            2,
            guidance="Route failures to a monitored on-call channel (Teams/PagerDuty) so "
            "alerts are acknowledged rather than lost in an inbox.",
        ),
        Option(
            "manual",
            "Failures are found by manually checking run history",
            1,
            guidance="Add failure notifications (Activator/Reflex, pipeline on-failure "
            "email, or monitoring) so failures surface without manual polling.",
        ),
        Option(
            "none",
            "No failure notification at all",
            0,
            guidance="Configure on-failure notifications for every scheduled pipeline "
            "and job, delivered to a monitored channel.",
        ),
    ),
)
