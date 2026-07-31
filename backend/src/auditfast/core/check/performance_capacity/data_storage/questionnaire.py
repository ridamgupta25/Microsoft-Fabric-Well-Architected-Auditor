"""Performance & Capacity · Data Storage — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-PERF-LOADTEST",
    ref="Q-PERF-2",
    title="Reports and models are performance-tested before release",
    pillar=Pillar.PERFORMANCE,
    layers=(Layer.STORAGE, Layer.REPORTING),
    question=(
        "Are reports and semantic models validated against representative data "
        "volumes for query/refresh performance before they are released?"
    ),
    options=(
        Option("routine", "Performance-tested at representative scale before each release", 3),
        Option(
            "sometimes",
            "Tested only for major releases or when a problem is suspected",
            1,
            guidance="Make performance validation a standard release gate for models and "
            "high-traffic reports at production data volumes.",
        ),
        Option(
            "never",
            "No performance testing before release",
            0,
            guidance="Add a performance test step (Performance Analyzer, DAX query "
            "timings, refresh duration) against representative volumes.",
        ),
    ),
)
