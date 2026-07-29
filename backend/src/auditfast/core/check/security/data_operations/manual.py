"""Security · Data Operations — manual / attestation checks (auto-generated).

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

    ("M-6-1-6", "6.1.6", "Workspace Identity used for Fabric data connections (preferred over user-delegated or SPN where supported)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-6-1-7", "6.1.7", "Fabric tenant admin settings reviewed and hardened (export restrictions, external sharing, guest access defaults)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-6-3-1", "6.3.1", "On-Premises Data Gateway uses encrypted connections", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-6-3-2", "6.3.2", "Private endpoints configured for Fabric capacity (if applicable)", (Layer.OPERATIONS,), False, "ROADMAP"),
    ("M-6-3-4", "6.3.4", "API source connections use TLS 1.2+", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-6-3-5", "6.3.5", "Conditional Access policies applied to Fabric tenant", (Layer.OPERATIONS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.SECURITY, layers=list(_layers), required=_required, automation=Automation[_automation])
