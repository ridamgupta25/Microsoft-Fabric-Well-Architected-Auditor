"""Data Management & Quality · Data Operations — roadmap (gated) checks (auto-generated).

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

    ("R-8-1-1", "8.1.1", "Fabric lineage view used and accurate for all key data flows", (Layer.OPERATIONS,), True, "ADMIN_SCANNER"),
    ("R-8-1-2", "8.1.2", "End-to-end lineage visible from source system to Gold/Power BI", (Layer.OPERATIONS,), True, "ADMIN_SCANNER"),
    ("R-8-1-3", "8.1.3", "Microsoft Purview integrated for enterprise cataloging (or equivalent)", (Layer.OPERATIONS,), False, "ADMIN_SCANNER"),
    ("R-8-1-4", "8.1.4", "Data assets tagged with business domain and data owner", (Layer.OPERATIONS,), True, "ADMIN_SCANNER"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.DATA, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
