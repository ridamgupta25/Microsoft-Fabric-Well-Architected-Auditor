"""Security · Data Operations — roadmap (gated) checks (auto-generated).

These industry-standard points cannot be evaluated from the data available on the
current sign-in, so each one *runs* and returns N/A with the specific access or
data it needs (admin / Scanner, Git repo, item definition, capacity metrics, ...).
No checklist point is silently missing, and the comment says exactly what unlocks
it. When that data is available a check can be promoted to a real evaluator.

Do not edit by hand — regenerate with build-manual-checks.py.
"""
from auditfast.core.check._gated import Requirement, gated
from auditfast.core.check.registry import check
from auditfast.core.enums import Automation, Layer, Pillar, Resource, Scope

# (id, ref, title, layers, required, requirement)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("R-6-1-6", "6.1.6", "Workspace Identity used for Fabric data connections (preferred over user-delegated or SPN where supported)", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-6-1-7", "6.1.7", "Fabric tenant admin settings reviewed and hardened (export restrictions, external sharing, guest access defaults)", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-6-3-1", "6.3.1", "On-Premises Data Gateway uses encrypted connections", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-6-3-2", "6.3.2", "Private endpoints configured for Fabric capacity (if applicable)", (Layer.OPERATIONS,), False, "ADMIN_TENANT"),
    ("R-6-3-5", "6.3.5", "Conditional Access policies applied to Fabric tenant", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
