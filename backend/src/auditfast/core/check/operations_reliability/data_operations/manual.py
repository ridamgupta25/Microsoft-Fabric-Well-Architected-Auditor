"""Operations & Reliability · Data Operations — manual / attestation checks (auto-generated).

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

    ("M-9-1-1", "9.1.1", "Failed pipelines can be restarted from point of failure (not full re-run)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-9-1-2", "9.1.2", "Transient failure handling: retries with exponential backoff", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-10-4-1", "10.4.1", "Data Activator triggers configured for critical events", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-1-4", "11.1.4", "Branching strategy defined (feature branches, main, release)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-1-6", "11.1.6", "Pull request reviews required before merge to main branch", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-1-7", "11.1.7", "Minimum reviewer count enforced via branch policies", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-1-8", "11.1.8", "Secret-scanning / credential-detection enabled on the source repository (ADO / GitHub)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-2-2", "11.2.2", "Deployment rules configured for environment-specific parameters", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-2-3", "11.2.3", "No manual deployments to production — all go through pipeline", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-2-5", "11.2.5", "Rollback procedure defined and tested", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-3-1", "11.3.1", "Separate workspaces for Dev, Test/QA, and Production", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-3-2", "11.3.2", "Production workspace has restricted access (no developer write)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-3-3", "11.3.3", "Test environment representative of production (data, scale)", (Layer.OPERATIONS,), True, "ROADMAP"),
    ("M-11-3-4", "11.3.4", "Environment parity maintained — no “works on dev” surprises", (Layer.OPERATIONS,), True, "ROADMAP"),
]

for _id, _ref, _title, _layers, _required, _automation in _CHECKS:
    manual_check(id=_id, ref=_ref, title=_title, pillar=Pillar.OPERATIONS, layers=list(_layers), required=_required, automation=Automation[_automation])
