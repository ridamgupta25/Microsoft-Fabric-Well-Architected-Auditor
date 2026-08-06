"""Data Management & Quality · Reporting / Semantic — model structure and design checks.

Read from the semantic model's TMSL definition. The model is where the
fact-to-dimension wiring is declared explicitly, which makes it the one place
referential structure is machine-readable without querying the warehouse.

The same parsed TMSL also carries the design signals this module judges:
star-schema filter direction, measure re-use, and DAX readability. Those checks
are workspace-scoped and aggregate across every model found.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

SEMANTIC_LAYERS = (Layer.REPORTING, Layer.STORAGE, Layer.MIXED)

SURROGATE_KEY_RE = re.compile(r"(?:_sk|_key|_id)$", re.IGNORECASE)

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

_KEY_HINT_RE = re.compile(
    r"(?:^id$|_id$|^key$|_key$|^sk$|_sk$|code$|number$|num$|date$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model(ctx: CheckContext) -> dict[str, Any] | None:
    """Return parsed TMSL for the current semantic model object."""
    return (ctx.workspace.semantic_models or {}).get(ctx.obj_name)


def _relationships(model: dict[str, Any]) -> list[dict[str, Any]]:
    return model.get("relationships") or []


def _is_active(rel: dict[str, Any]) -> bool:
    """Normalize active flag variants."""
    return bool(rel.get("is_active", rel.get("isActive", True)))


def _norm_col(name: str) -> str:
    return re.sub(r"[\s\-\.]+", "_", (name or "").strip().lower())


def _query_results(ctx: CheckContext) -> dict[str, Any]:
    return getattr(ctx.workspace, "query_results", {}) or {}


def _normalised(expression: object) -> str:
    """Expression with runs of whitespace collapsed, so pretty-printing adds no length."""
    return _WHITESPACE.sub(" ", str(expression or "")).strip()


# ---------------------------------------------------------------------------
# Structure checks — referential integrity
# ---------------------------------------------------------------------------


@check(
    id="SM-FK-SURROGATE",
    ref="5.4.1",
    title="Fact-dimension relationships join on surrogate keys",
    pillar=Pillar.DATA,
    scope=Scope.SEMANTIC_MODEL,
    severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def sm_fk_surrogate(ctx: CheckContext) -> Verdict:
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definition could not be read")

    relationships = _relationships(model)
    if not relationships:
        table_count = len(model.get("tables") or [])
        if table_count <= 1:
            return not_applicable("Single-table model — no relationships to define")
        return covered(
            0,
            1,
            f"Model has {table_count} tables but no relationships defined — "
            f"facts are not wired to dimensions",
        )

    on_keys: list[dict[str, Any]] = []
    inactive_names: list[str] = []

    for rel in relationships:
        from_col = _norm_col(str(rel.get("from_column") or rel.get("fromColumn") or ""))
        to_col = _norm_col(str(rel.get("to_column") or rel.get("toColumn") or ""))

        if _KEY_HINT_RE.search(from_col) or _KEY_HINT_RE.search(to_col):
            on_keys.append(rel)

        if not _is_active(rel):
            inactive_names.append(str(rel.get("name") or rel.get("id") or "?"))

    detail = (
        f"{len(on_keys)} of {len(relationships)} relationships join on a "
        f"surrogate-key-shaped column"
    )
    if inactive_names:
        detail += f"; {len(inactive_names)} inactive ({', '.join(sorted(inactive_names))})"

    return covered(len(on_keys), len(relationships), detail)


@check(
    id="SM-FK-RI-DATA",
    ref="5.4.1",
    title="Fact FK values resolve to dimension surrogate keys (no orphans)",
    pillar=Pillar.DATA,
    scope=Scope.WORKSPACE,
    severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def sm_fk_ri_data(ctx: CheckContext) -> Verdict:
    rows = _query_results(ctx).get("5.4.1.ri") or []
    if not rows:
        return not_applicable("No Lakehouse/Warehouse SQL RI evidence found for 5.4.1")

    total = len(rows)
    passed = 0
    failing: list[str] = []

    for row in rows:
        orphan_count = int(row.get("orphan_count") or 0)
        rel_name = str(row.get("relationship") or "?")
        if orphan_count == 0:
            passed += 1
        else:
            failing.append(f"{rel_name} (orphans={orphan_count})")

    detail = f"{passed} of {total} FK relationships have zero orphans"
    if failing:
        detail += "; failing: " + ", ".join(failing[:10])
        if len(failing) > 10:
            detail += f" (+{len(failing) - 10} more)"

    return covered(passed, total, detail)


# ---------------------------------------------------------------------------
# Design checks — star schema, measure re-use, DAX readability
# ---------------------------------------------------------------------------


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