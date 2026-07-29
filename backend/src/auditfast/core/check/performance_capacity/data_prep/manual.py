"""Performance & Capacity · Data Prep — manual / attestation checks (auto-generated).

These industry-standard points are not verified from Fabric data today, so the
engine never runs them (manual=True); they exist so the catalogue is a complete,
attestable checklist. Each carries:
  - `required`   — expected in every project (True) or situational (False);
  - `automation` — ROADMAP (automatable once the provider integrates the needed
    Fabric API) or MANUAL (only a human can attest).

Do not edit by hand — regenerate with build-manual-checks.py.
"""
from auditfast.core.check.registry import manual_check
from auditfast.core.enums import Automation, Layer, Pillar

# (id, ref, title, layers, required, automation)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("M-2-6-2", "2.6.2", "Copy activities use appropriate parallelism (DIU, degree of copy parallelism)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-1", "3.3.1", "Single `MERGE INTO` statement handles all three operations (Insert/Update/Delete) atomically — not separate DELETE, INSERT, UPDATE statements executed sequentially", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-2", "3.3.2", "`OPTIMIZE` (bin-compaction) runs on tables after write-heavy operations", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-3", "3.3.3", "`VACUUM` scheduled to clean up old Delta files", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-4", "3.3.4", "Z-ORDER applied on high-cardinality filter columns", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-5", "3.3.5", "V-Order enabled where Fabric recommends for read-optimized workloads", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-6", "3.3.6", "Table properties set appropriately (autoOptimize, optimizeWrite, autoCompaction)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-3-7", "3.3.7", "Delta table history retention configured and monitored", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-4-1", "3.4.1", "Fabric Environments used to manage Spark dependencies", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-4-2", "3.4.2", "Custom library versions pinned (not latest/floating)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-4-4", "3.4.4", "Spark configuration tuned from defaults where justified (shuffle partitions, memory)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-4-5", "3.4.5", "Python/Spark version is current and supported", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-2", "3.5.2", "Partition count appropriate (not 200 default for small/medium data)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-3", "3.5.3", "Caching (`persist`/`cache`) used judiciously, not indiscriminately", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-4", "3.5.4", "Write operations use appropriate partition strategy (coalesce vs repartition)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-5", "3.5.5", "No full-table scans when partition pruning is possible", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-7", "3.5.7", "Gold-layer tables optimized for common query patterns (Z-ORDER on filter columns)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-8", "3.5.8", "Predicate pushdown verified for shortcut/external reads", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-5-9", "3.5.9", "Unnecessary columns eliminated in reads (explicit select, not `SELECT *`)", (Layer.PREP,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.PERFORMANCE, layers=list(_layers), required=_required, automation=Automation[_automation])
