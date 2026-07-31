"""Cost & Resource Optimization · Data Operations — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-COST-REVIEW",
    ref="Q-COST-1",
    title="Capacity cost is reviewed and right-sized based on usage",
    pillar=Pillar.COST,
    layers=(Layer.ANY,),
    question=(
        "Is Fabric capacity cost reviewed regularly and right-sized (scale up/down, "
        "pause) based on actual usage patterns?"
    ),
    options=(
        Option("optimized", "Reviewed regularly and actively right-sized / scheduled", 3),
        Option(
            "reviewed",
            "Cost is reviewed but capacity is rarely adjusted",
            1,
            guidance="Act on the review: pause idle capacity, schedule scale up/down to "
            "match demand, and consolidate under-used capacities.",
        ),
        Option(
            "never",
            "Capacity cost is not reviewed",
            0,
            guidance="Establish a regular cost review using the Capacity Metrics app and "
            "right-size or schedule capacity to match demand.",
        ),
    ),
)

questionnaire_check(
    id="Q-COST-CHARGEBACK",
    ref="Q-COST-2",
    title="Showback / chargeback attributes capacity cost to owning teams",
    pillar=Pillar.COST,
    layers=(Layer.ANY,),
    question=(
        "Is there a showback or chargeback model that attributes Fabric capacity cost "
        "to the teams that own the workloads?"
    ),
    options=(
        Option("chargeback", "Chargeback — cost is billed back to owning teams", 3),
        Option(
            "showback",
            "Showback — cost is reported to teams but not billed back",
            2,
            guidance="Move from showback to chargeback (or budget accountability) so "
            "teams have a direct incentive to optimize their usage.",
        ),
        Option(
            "none",
            "No cost attribution to owning teams",
            0,
            guidance="Tag/segment capacity by team or workload and report cost back so "
            "consumption is accountable.",
        ),
    ),
)
