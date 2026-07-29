"""Governance & Compliance · Data Logs — manual / attestation checks (auto-generated).

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

    ("M-7-5-1", "7.5.1", "Fabric Activity Log / Unified Audit Log enabled and exported", (Layer.LOGS,), True, "ROADMAP"),
    ("M-7-5-2", "7.5.2", "Admin audit log captures workspace changes, permission changes, item deletions", (Layer.LOGS,), True, "ROADMAP"),
    ("M-7-5-3", "7.5.3", "Data access audit trail exists (who accessed what data, when)", (Layer.LOGS,), True, "ROADMAP"),
    ("M-7-5-4", "7.5.4", "Audit logs retained per compliance requirement (6–7 years for HIPAA/SOX)", (Layer.LOGS,), True, "ROADMAP"),
    ("M-7-5-5", "7.5.5", "Logs stored in tamper-resistant location (Eventhouse + backup)", (Layer.LOGS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.GOVERNANCE, layers=list(_layers), required=_required, automation=Automation[_automation])
