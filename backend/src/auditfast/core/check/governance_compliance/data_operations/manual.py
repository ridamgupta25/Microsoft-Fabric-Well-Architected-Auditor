"""Governance & Compliance · Data Operations — manual / attestation checks (auto-generated).

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

    ("M-1-3-6", "1.3.6", "All source connections inventoried (even if only in pipeline metadata)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-13-1-1", "13.1.1", "Architecture overview document exists (or can be generated from workspace)", (Layer.OPERATIONS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.GOVERNANCE, layers=list(_layers), required=_required, automation=Automation[_automation])
