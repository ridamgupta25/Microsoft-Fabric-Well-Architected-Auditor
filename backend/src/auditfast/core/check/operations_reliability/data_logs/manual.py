"""Operations & Reliability · Data Logs — manual / attestation checks (auto-generated).

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

    ("M-10-1-1", "10.1.1", "Pipeline run history monitored beyond Fabric's default 30-day retention", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-1-2", "10.1.2", "Spark application logs captured in Eventhouse for historical analysis", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-1-3", "10.1.3", "Dashboard shows pipeline status, duration trends, and failure rates", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-1-4", "10.1.4", "Alerting on pipeline failure (Data Activator or equivalent)", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-2-1", "10.2.1", "Log schema designed for queryability (structured, not free-text)", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-2-2", "10.2.2", "Log retention configured per compliance requirements", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-2-3", "10.2.3", "KQL queries exist for common operational investigations", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-2-4", "10.2.4", "Log volume managed — not over-logging or under-logging", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-3-1", "10.3.1", "Dashboard covers all critical pipelines and notebooks", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-3-2", "10.3.2", "Refresh frequency of monitoring data is adequate (near-real-time or hourly)", (Layer.LOGS,), True, "ROADMAP"),
    ("M-10-3-4", "10.3.4", "Historical trend analysis enabled (not just current-state)", (Layer.LOGS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.OPERATIONS, layers=list(_layers), required=_required, automation=Automation[_automation])
