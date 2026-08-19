"""Operations & Reliability · Data Logs — roadmap (gated) checks (auto-generated).

These industry-standard points cannot be evaluated from the data available on the
current sign-in, so each one *runs* and returns N/A with the specific access or
data it needs (admin / Scanner, Git repo, item definition, capacity metrics, ...).
No checklist point is silently missing, and the comment says exactly what unlocks
it. When that data is available a check can be promoted to a real evaluator.

Do not edit by hand — regenerate with build-manual-checks.py.

Promoted points are removed from this list as they become real evaluators. Gone
from here: 10.1.1 (``WS-RUN-HISTORY-EXPORT``), 10.1.2 (``OPS-SPARK-LOGS``,
interactive), 10.3.1 (``WS-EVENTHOUSE-TELEMETRY``), 10.3.2 (``WS-KQL-QUERIES``),
10.3.4 (``WS-INGEST-VOLUME``).
"""
from auditfast.core.check._gated import Requirement, gated, pillar_for_ref
from auditfast.core.check.registry import check
from auditfast.core.enums import Automation, Layer, Resource, Scope

# (id, ref, title, layers, required, requirement)
_CHECKS: list[tuple[str, str, str, tuple[str, ...], bool, str]] = [

    ("R-10-1-3", "10.1.3", "Dashboard shows pipeline status, duration trends, and failure rates", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-10-1-4", "10.1.4", "Alerting on pipeline failure (Data Activator or equivalent)", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-10-2-1", "10.2.1", "Log schema designed for queryability (structured, not free-text)", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-10-2-2", "10.2.2", "Log retention configured per compliance requirements", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-10-2-3", "10.2.3", "KQL queries exist for common operational investigations", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
    ("R-10-2-4", "10.2.4", "Log volume managed — not over-logging or under-logging", (Layer.LOGS,), True, "ADMIN_ACTIVITY"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=pillar_for_ref(_ref), scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
