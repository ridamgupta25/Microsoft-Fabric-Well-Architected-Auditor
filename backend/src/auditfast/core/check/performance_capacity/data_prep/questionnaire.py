"""Performance & Capacity · Data Prep — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-PERF-CAPACITY-MONITOR",
    ref="Q-PERF-1",
    title="Capacity utilization is monitored and reviewed",
    pillar=Pillar.PERFORMANCE,
    layers=(Layer.ANY,),
    question=(
        "Is Fabric capacity utilization (CU %, throttling, bursting) monitored and "
        "reviewed regularly via the Capacity Metrics app or an equivalent?"
    ),
    options=(
        Option("alerts", "Monitored with alerting on sustained high utilization", 3),
        Option(
            "reviewed",
            "Reviewed occasionally, but no alerting",
            1,
            guidance="Add alerts on sustained CU / throttling so capacity pressure is "
            "caught before it degrades workloads.",
        ),
        Option(
            "none",
            "Capacity utilization is not monitored",
            0,
            guidance="Install the Fabric Capacity Metrics app and review CU usage, "
            "throttling, and overages on a regular cadence.",
        ),
    ),
)
