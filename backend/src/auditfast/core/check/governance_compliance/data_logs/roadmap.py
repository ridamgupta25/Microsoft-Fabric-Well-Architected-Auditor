"""Governance & Compliance · Data Logs — roadmap (gated) checks (auto-generated).

These industry-standard points cannot be evaluated from the data available on the
current sign-in, so each one *runs* and returns N/A with the specific access or
data it needs (admin / Scanner, Git repo, item definition, capacity metrics, ...).
No checklist point is silently missing, and the comment says exactly what unlocks
it. When that data is available a check can be promoted to a real evaluator.

Do not edit by hand — regenerate with build-manual-checks.py.
"""
from auditfast.core.check._gated import Requirement, gated, pillar_for_ref
from auditfast.core.check.registry import check
from auditfast.core.enums import Automation, Layer, Resource, Scope

# (id, ref, title, layers, required, requirement)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("R-7-5-1", "7.5.1", "Fabric Activity Log / Unified Audit Log enabled and exported", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-7-5-2", "7.5.2", "Admin audit log captures workspace changes, permission changes, item deletions", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-7-5-3", "7.5.3", "Data access audit trail exists (who accessed what data, when)", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-7-5-4", "7.5.4", "Audit logs retained per compliance requirement (6–7 years for HIPAA/SOX)", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-7-5-5", "7.5.5", "Logs stored in tamper-resistant location (Eventhouse + backup)", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=pillar_for_ref(_ref), scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
