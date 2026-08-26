"""Cost & Resource Optimization · Data Operations — roadmap (gated) checks (auto-generated).

These industry-standard points cannot be evaluated from the data available on the
current sign-in, so each one *runs* and returns N/A with the specific access or
data it needs (admin / Scanner, Git repo, item definition, capacity metrics, ...).
No checklist point is silently missing, and the comment says exactly what unlocks
it. When that data is available a check can be promoted to a real evaluator.

Do not edit by hand — regenerate with build-manual-checks.py.
"""
from auditfast.core.check._gated import Requirement, gated, pillar_for_ref
from auditfast.core.check.registry import check
from auditfast.core.enums import Automation, Layer, Resource, Scope

# (id, ref, title, layers, required, requirement)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("R-12-1-1", "12.1.1", "Fabric SKU selected based on workload analysis (not guesswork)", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-1-2", "12.1.2", "Peak vs off-peak utilization profiled", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-1-3", "12.1.3", "Capacity autoscale/burst configured and understood", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-1", "12.2.1", "Fabric Capacity Metrics App deployed and monitored", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-2", "12.2.2", "Top CU-consuming workloads identified", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-3", "12.2.3", "CU smoothing behavior understood (background vs interactive)", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-4", "12.2.4", "Capacity bursting/throttling incidents tracked — frequent throttling indicates undersizing", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-5", "12.2.5", "Workloads distributed to avoid peak-hour contention", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-2-6", "12.2.6", "Background vs interactive CU consumption analyzed", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-3-1", "12.3.1", "ADLS Gen2 access tier appropriate (Hot vs Cool) for Pre-Bronze/Bronze", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-3-2", "12.3.2", "Lifecycle policies on ADLS for old files (move to Cool/Archive)", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
    ("R-12-3-3", "12.3.3", "Spark pool not running idle (Environment settings tuned)", (Layer.OPERATIONS,), True, "CAPACITY_METRICS"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=pillar_for_ref(_ref), scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
