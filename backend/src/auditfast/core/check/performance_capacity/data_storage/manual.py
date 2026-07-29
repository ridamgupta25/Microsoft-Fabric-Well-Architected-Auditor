"""Performance & Capacity · Data Storage — manual / attestation checks (auto-generated).

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

    ("M-4-3-1", "4.3.1", "Delta format used for all analytical tables (not Parquet files in Files section for analytics)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-3-2", "4.3.2", "Raw files in Files section organized by source/date hierarchy", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-3-3", "4.3.3", "File sizes avoid small-file problem (target 128MB–1GB per file)", (Layer.STORAGE,), True, "ROADMAP"),
    ("M-4-3-4", "4.3.4", "Orphaned files cleaned up periodically", (Layer.STORAGE,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.PERFORMANCE, layers=list(_layers), required=_required, automation=Automation[_automation])
