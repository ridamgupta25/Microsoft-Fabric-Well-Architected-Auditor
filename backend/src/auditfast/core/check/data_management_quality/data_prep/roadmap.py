"""Data Management & Quality · Data Prep — roadmap (gated) checks (auto-generated).

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

    ("R-1-2-1", "1.2.1", "Data flow lineage is traceable end-to-end from source to Gold layer", (Layer.PREP, Layer.STORAGE, Layer.LOGS), True, "ADMIN_SCANNER"),
    ("R-1-3-3", "1.3.3", "API ingestion has proper authentication, pagination, throttling, and error handling", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-1-3", "2.1.3", "Master/orchestrator pipeline pattern used for coordinating dependent pipelines", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-1-5", "2.1.5", "Parallel execution used where possible (no unnecessary sequential execution)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-2-1", "2.2.1", "Incremental load implemented where applicable (watermark, CDC, delta detection)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-2-2", "2.2.2", "Full load reserved only for small reference/dimension tables or initial loads", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-2-4", "2.2.4", "Initial load vs. incremental load clearly separated or parameterized", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-5-1", "2.5.1", "Dataflows used appropriately (light transformations, not heavy compute)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-5-2", "2.5.2", "Dataflow refresh strategy aligns with pipeline orchestration", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-2-5-3", "2.5.3", "Staging enabled for Dataflows Gen2 where performance benefits apply", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-1-1", "3.1.1", "Notebooks follow consistent structure (parameters → imports → config → logic → output)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-1-4", "3.1.4", "Cell-level documentation (markdown cells) explains business logic, not just code", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-1-5", "3.1.5", "Functions are modular and reusable — not monolithic single-cell scripts", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-1-7", "3.1.7", "All notebooks have meaningful, consistent names", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-1-8", "3.1.8", "Notebook execution timeout / max runtime configured to prevent runaway Spark sessions", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-2-1", "3.2.1", "Consistent language approach used (PySpark vs Spark SQL — one primary, not mixed)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-2-2", "3.2.2", "DataFrame API used over RDD API", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-2-4", "3.2.4", "Broadcast joins used for small-large table joins", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-2-5", "3.2.5", "UDFs avoided where native Spark functions exist", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-2-6", "3.2.6", "Schema explicitly defined at read time for external sources (not inferred on CSV/JSON)", (Layer.PREP,), True, "ITEM_DEFINITION"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.DATA, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
