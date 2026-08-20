"""Performance & Capacity · Data Storage — roadmap (gated) checks (auto-generated).

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

    ("R-4-3-1", "4.3.1", "Delta format used for all analytical tables (not Parquet files in Files section for analytics)", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-3-2", "4.3.2", "Raw files in Files section organized by source/date hierarchy", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-3-3", "4.3.3", "File sizes avoid small-file problem (target 128MB–1GB per file)", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
    ("R-4-3-4", "4.3.4", "Orphaned files cleaned up periodically", (Layer.STORAGE,), True, "SQL_ENDPOINT"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=pillar_for_ref(_ref), scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
