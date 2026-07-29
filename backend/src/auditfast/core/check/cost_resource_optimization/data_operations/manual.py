"""Cost & Resource Optimization · Data Operations — manual / attestation checks (auto-generated).

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

    ("M-12-1-1", "12.1.1", "Fabric SKU selected based on workload analysis (not guesswork)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-1-2", "12.1.2", "Peak vs off-peak utilization profiled", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-1-3", "12.1.3", "Capacity autoscale/burst configured and understood", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-1", "12.2.1", "Fabric Capacity Metrics App deployed and monitored", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-2", "12.2.2", "Top CU-consuming workloads identified", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-3", "12.2.3", "CU smoothing behavior understood (background vs interactive)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-4", "12.2.4", "Capacity bursting/throttling incidents tracked — frequent throttling indicates undersizing", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-5", "12.2.5", "Workloads distributed to avoid peak-hour contention", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-6", "12.2.6", "Background vs interactive CU consumption analyzed", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-2-7", "12.2.7", "CU consumption alerts configured for proactive throttling prevention", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-3-1", "12.3.1", "ADLS Gen2 access tier appropriate (Hot vs Cool) for Pre-Bronze/Bronze", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-3-2", "12.3.2", "Lifecycle policies on ADLS for old files (move to Cool/Archive)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-12-3-3", "12.3.3", "Spark pool not running idle (Environment settings tuned)", (Layer.OPERATIONS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.COST, layers=list(_layers), required=_required, automation=Automation[_automation])
