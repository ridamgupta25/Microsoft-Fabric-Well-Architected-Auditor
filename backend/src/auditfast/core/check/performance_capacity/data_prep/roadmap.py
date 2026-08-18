"""Performance & Capacity · Data Prep — roadmap (gated) checks (auto-generated).

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

    ("R-3-5-7", "3.5.7", "Gold-layer tables optimized for common query patterns (Z-ORDER on filter columns)", (Layer.PREP,), True, "ITEM_DEFINITION"),
    ("R-3-5-8", "3.5.8", "Predicate pushdown verified for shortcut/external reads", (Layer.PREP,), True, "ITEM_DEFINITION"),
]

for _id, _ref, _title, _layers, _required, _requirement in _CHECKS:
    check(
        id=_id, ref=_ref, title=_title,
        pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE,
        layers=list(_layers), requires=[Resource.WORKSPACE], required=_required,
        automation=Automation.ROADMAP,
    )(gated(Requirement[_requirement]))
