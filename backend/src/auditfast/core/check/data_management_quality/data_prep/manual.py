"""Data Management & Quality · Data Prep — manual / attestation checks (auto-generated).

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

    ("M-1-2-1", "1.2.1", "Data flow lineage is traceable end-to-end from source to Gold layer", (Layer.PREP, Layer.STORAGE, Layer.LOGS), True, "ROADMAP"),
    ("M-1-3-3", "1.3.3", "API ingestion has proper authentication, pagination, throttling, and error handling", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-1-3", "2.1.3", "Master/orchestrator pipeline pattern used for coordinating dependent pipelines", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-1-5", "2.1.5", "Parallel execution used where possible (no unnecessary sequential execution)", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-2-1", "2.2.1", "Incremental load implemented where applicable (watermark, CDC, delta detection)", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-2-2", "2.2.2", "Full load reserved only for small reference/dimension tables or initial loads", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-2-4", "2.2.4", "Initial load vs. incremental load clearly separated or parameterized", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-5-1", "2.5.1", "Dataflows used appropriately (light transformations, not heavy compute)", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-5-2", "2.5.2", "Dataflow refresh strategy aligns with pipeline orchestration", (Layer.PREP,), True, "ROADMAP"),
    ("M-2-5-3", "2.5.3", "Staging enabled for Dataflows Gen2 where performance benefits apply", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-1-1", "3.1.1", "Notebooks follow consistent structure (parameters → imports → config → logic → output)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-1-4", "3.1.4", "Cell-level documentation (markdown cells) explains business logic, not just code", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-1-5", "3.1.5", "Functions are modular and reusable — not monolithic single-cell scripts", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-1-7", "3.1.7", "All notebooks have meaningful, consistent names", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-1-8", "3.1.8", "Notebook execution timeout / max runtime configured to prevent runaway Spark sessions", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-2-1", "3.2.1", "Consistent language approach used (PySpark vs Spark SQL — one primary, not mixed)", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-2-2", "3.2.2", "DataFrame API used over RDD API", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-2-4", "3.2.4", "Broadcast joins used for small-large table joins", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-2-5", "3.2.5", "UDFs avoided where native Spark functions exist", (Layer.PREP,), True, "ROADMAP"),
    ("M-3-2-6", "3.2.6", "Schema explicitly defined at read time for external sources (not inferred on CSV/JSON)", (Layer.PREP,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.DATA, layers=list(_layers), required=_required, automation=Automation[_automation])
