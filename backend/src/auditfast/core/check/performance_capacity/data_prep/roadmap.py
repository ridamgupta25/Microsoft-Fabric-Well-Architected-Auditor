"""Performance & Capacity · Data Prep — roadmap (gated) checks (auto-generated).

These industry-standard points cannot be evaluated from the data available on the
current sign-in, so each one *runs* and returns N/A with the specific access or
data it needs (admin / Scanner, Git repo, item definition, capacity metrics, ...).
No checklist point is silently missing, and the comment says exactly what unlocks
it. When that data is available a check can be promoted to a real evaluator.

Do not edit by hand — regenerate with build-manual-checks.py.
"""
from auditfast.core.check._gated import Requirement, gated
from auditfast.core.check.registry import check
from auditfast.core.enums import Automation, Layer, Pillar, Resource, Scope

# (id, ref, title, layers, required, requirement)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("R-2-6-2", "2.6.2", "Copy activities use appropriate parallelism (DIU, degree of copy parallelism)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-1", "3.3.1", "Single `MERGE INTO` statement handles all three operations (Insert/Update/Delete) atomically — not separate DELETE, INSERT, UPDATE statements executed sequentially", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-2", "3.3.2", "`OPTIMIZE` (bin-compaction) runs on tables after write-heavy operations", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-3", "3.3.3", "`VACUUM` scheduled to clean up old Delta files", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-4", "3.3.4", "Z-ORDER applied on high-cardinality filter columns", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-5", "3.3.5", "V-Order enabled where Fabric recommends for read-optimized workloads", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-6", "3.3.6", "Table properties set appropriately (autoOptimize, optimizeWrite, autoCompaction)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-3-7", "3.3.7", "Delta table history retention configured and monitored", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-4-1", "3.4.1", "Fabric Environments used to manage Spark dependencies", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-4-2", "3.4.2", "Custom library versions pinned (not latest/floating)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-4-4", "3.4.4", "Spark configuration tuned from defaults where justified (shuffle partitions, memory)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-4-5", "3.4.5", "Python/Spark version is current and supported", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-2", "3.5.2", "Partition count appropriate (not 200 default for small/medium data)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-3", "3.5.3", "Caching (`persist`/`cache`) used judiciously, not indiscriminately", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-4", "3.5.4", "Write operations use appropriate partition strategy (coalesce vs repartition)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-5", "3.5.5", "No full-table scans when partition pruning is possible", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-7", "3.5.7", "Gold-layer tables optimized for common query patterns (Z-ORDER on filter columns)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-8", "3.5.8", "Predicate pushdown verified for shortcut/external reads", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-9", "3.5.9", "Unnecessary columns eliminated in reads (explicit select, not `SELECT *`)", (Layer.PREP,), True, "ITEM_DEFINITION"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.PERFORMANCE, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
