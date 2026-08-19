"""Data Management & Quality · Data Storage — roadmap (gated) checks (auto-generated).

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

    ("R-4-1-2", "4.1.2", "Clear separation between Lakehouse and Warehouse workloads", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-1-3", "4.1.3", "OneLake used as the single data lake — no shadow storage outside OneLake (except justified ADLS)", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-1-4", "4.1.4", "Shortcuts don't create circular references or ungoverned data access paths", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-4-6", "4.4.6", "Conformed dimensions shared across fact tables (no duplicate dimension versions)", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-4-17", "4.4.17", "Referential integrity enforced: every FK in fact tables has a matching dimension record", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=pillar_for_ref(_ref), scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
