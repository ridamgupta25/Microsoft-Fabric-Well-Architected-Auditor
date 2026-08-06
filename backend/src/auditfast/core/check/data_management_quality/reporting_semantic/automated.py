"""Data Management & Quality · Reporting / Semantic — semantic model design.

Reads the parsed TMSL definition of every semantic model in the workspace to judge
star-schema filter direction, measure re-use, and DAX readability. Each check is
workspace-scoped and aggregates across every model found.
"""
from __future__ import annotations

import re
from collections import Counter

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_UNREADABLE = "Semantic model definitions could not be read from Fabric"
_NO_MODELS = "No semantic models in this workspace"

#: Below this an expression is boilerplate (``0``, ``BLANK()``, a bare column sum),
#: so repeating it is convention rather than duplicated calculation logic.
_MIN_MEASURE_CHARS = 60

#: Above this a measure is complex enough that VAR is the readable way to write it.
_COMPLEX_MEASURE_CHARS = 400

_WHITESPACE = re.compile(r"\s+")

#: A real DAX variable declaration. A loose ``VAR`` substring also matches a column
#: named "Var Amount".
_VAR_DECLARATION = re.compile(r"\bVAR\s+\w+\s*=", re.IGNORECASE)


def _normalised(expression: object) -> str:
    """Expression with runs of whitespace collapsed, so pretty-printing adds no length."""
    return _WHITESPACE.sub(" ", str(expression or "")).strip()


@check(
    id="R-BIDI-REL", ref="14.1.1", title="Star schema: relationships filter in a single direction",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def single_direction_relationships(ctx: CheckContext) -> Verdict:
    """Relationships filter one way, so filter propagation stays predictable.

    A bidirectional filter is legitimate for a bridge table, so this reports the
    count for review rather than judging whether each one is necessary.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable(_UNREADABLE)
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable(_NO_MODELS)

    with_relationships = 0
    single_direction = 0
    bidirectional = 0
    for defn in models.values():
        rels = defn.get("relationships") or []
        if not rels:
            continue
        with_relationships += 1
        both = [r for r in rels
                if str(r.get("cross_filter") or "").strip().lower() == "bothdirections"]
        bidirectional += len(both)
        if not both:
            single_direction += 1

    if not with_relationships:
        return not_applicable("No semantic models define relationships")
    return covered(
        single_direction, with_relationships,
        f"{single_direction} of {with_relationships} semantic models filter in a single "
        f"direction; {bidirectional} bidirectional relationship(s) found",
    )


@check(
    id="R-MEASURE-DUP", ref="14.1.3", title="Measures centralized: no duplicated calculation logic",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def measures_not_duplicated(ctx: CheckContext) -> Verdict:
    """Each substantial calculation is defined once and re-used, not copy-pasted.

    Duplication is counted across every model in the workspace, which is where
    copy-pasted logic usually lands: the same measure cloned into many models.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable(_UNREADABLE)
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable(_NO_MODELS)

    expressions = [
        normalised
        for defn in models.values()
        for measure in defn.get("measures") or []
        if len(normalised := _normalised(measure.get("expression"))) > _MIN_MEASURE_CHARS
    ]
    if not expressions:
        return not_applicable("No semantic model measures long enough to assess for duplication")

    counts = Counter(expressions)
    duplicated = {expr for expr, n in counts.items() if n > 1}
    unique = sum(1 for expr in expressions if expr not in duplicated)
    return covered(
        unique, len(expressions),
        f"{unique} of {len(expressions)} substantial measures carry an expression used "
        f"nowhere else; {len(duplicated)} distinct expression(s) are repeated across the "
        f"workspace's {len(models)} semantic model(s)",
    )


@check(
    id="R-DAX-VAR", ref="14.1.4", title="Complex DAX measures use variables",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def complex_measures_use_variables(ctx: CheckContext) -> Verdict:
    """Long DAX measures declare VAR, so intermediate steps are named and evaluated once."""
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable(_UNREADABLE)
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable(_NO_MODELS)

    complex_expressions = [
        normalised
        for defn in models.values()
        for measure in defn.get("measures") or []
        if len(normalised := _normalised(measure.get("expression"))) > _COMPLEX_MEASURE_CHARS
    ]
    if not complex_expressions:
        return not_applicable("No semantic model measures are complex enough to require variables")

    using_var = sum(1 for expr in complex_expressions if _VAR_DECLARATION.search(expr))
    return covered(
        using_var, len(complex_expressions),
        f"{using_var} of {len(complex_expressions)} measures longer than "
        f"{_COMPLEX_MEASURE_CHARS} characters use VAR",
    )
