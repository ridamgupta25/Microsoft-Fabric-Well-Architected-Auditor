"""Operations & Reliability · Data Operations — roadmap (gated) checks (auto-generated).

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

    ("R-9-1-1", "9.1.1", "Failed pipelines can be restarted from point of failure (not full re-run)", (Layer.OPERATIONS,), True, "ITEM_DEFINITION"),
    ("R-9-1-2", "9.1.2", "Transient failure handling: retries with exponential backoff", (Layer.OPERATIONS,), True, "ITEM_DEFINITION"),
    ("R-9-1-4", "9.1.4", "Schema drift between environments is detectable and reconciled", (Layer.OPERATIONS,), True, "SQL_ENDPOINT"),
    ("R-10-4-1", "10.4.1", "Data Activator triggers configured for critical events", (Layer.OPERATIONS,), True, "ADMIN_ACTIVITY"),
    ("R-11-1-6", "11.1.6", "Pull request reviews required before merge to main branch", (Layer.OPERATIONS,), True, "GIT_REPO"),
    ("R-11-1-7", "11.1.7", "Minimum reviewer count enforced via branch policies", (Layer.OPERATIONS,), True, "GIT_REPO"),
    ("R-11-1-8", "11.1.8", "Secret-scanning / credential-detection enabled on the source repository (ADO / GitHub)", (Layer.OPERATIONS,), True, "GIT_REPO"),
    ("R-11-2-2", "11.2.2", "Deployment rules configured for environment-specific parameters", (Layer.OPERATIONS,), True, "ITEM_DEFINITION"),
    ("R-11-2-3", "11.2.3", "No manual deployments to production — all go through pipeline", (Layer.OPERATIONS,), True, "ITEM_DEFINITION"),
    ("R-11-2-5", "11.2.5", "Rollback procedure defined and tested", (Layer.OPERATIONS,), True, "ITEM_DEFINITION"),
    ("R-11-3-1", "11.3.1", "Separate workspaces for Dev, Test/QA, and Production", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-11-3-2", "11.3.2", "Production workspace has restricted access (no developer write)", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-11-3-3", "11.3.3", "Test environment representative of production (data, scale)", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
    ("R-11-3-4", "11.3.4", "Environment parity maintained — no “works on dev” surprises", (Layer.OPERATIONS,), True, "ADMIN_TENANT"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
