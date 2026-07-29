"""Data Management & Quality · Data Operations — manual / attestation checks (auto-generated).

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

    ("M-8-1-1", "8.1.1", "Fabric lineage view used and accurate for all key data flows", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-8-1-2", "8.1.2", "End-to-end lineage visible from source system to Gold/Power BI", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-8-1-3", "8.1.3", "Microsoft Purview integrated for enterprise cataloging (or equivalent)", (Layer.OPERATIONS,), False, "ROADMAP"),
    ("M-8-1-4", "8.1.4", "Data assets tagged with business domain and data owner", (Layer.OPERATIONS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.DATA, layers=list(_layers), required=_required, automation=Automation[_automation])
