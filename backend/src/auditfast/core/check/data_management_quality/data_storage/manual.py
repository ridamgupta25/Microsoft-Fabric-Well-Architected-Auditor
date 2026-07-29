"""Data Management & Quality · Data Storage — manual / attestation checks (auto-generated).

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

    ("M-4-1-2", "4.1.2", "Clear separation between Lakehouse and Warehouse workloads", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-1-3", "4.1.3", "OneLake used as the single data lake — no shadow storage outside OneLake (except justified ADLS)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-1-4", "4.1.4", "Shortcuts don't create circular references or ungoverned data access paths", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-2-2", "4.2.2", "Partitioning strategy defined for large tables (by date, region, etc.)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-2-3", "4.2.3", "Column naming is consistent and self-documenting", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-2-4", "4.2.4", "Data types are appropriate (no stringly-typed dates, no oversized varchars)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-3", "4.4.3", "Fact tables contain only foreign keys and measures (no descriptive attributes)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-5", "4.4.5", "Dimension tables are denormalized appropriately (star over snowflake unless justified)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-6", "4.4.6", "Conformed dimensions shared across fact tables (no duplicate dimension versions)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-9", "4.4.9", "SCD strategy defined and implemented per dimension (Type 1 / Type 2 / Type 3 / Hybrid)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-10", "4.4.10", "SCD Type 2 includes valid_from, valid_to, and is_current flag correctly maintained", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-4-17", "4.4.17", "Referential integrity enforced: every FK in fact tables has a matching dimension record", (Layer.STORAGE,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.DATA, layers=list(_layers), required=_required, automation=Automation[_automation])
