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

from auditfast.core.check._dax import (
    expensive_iterator,
    repeated_subexpressions,
    uses_variables,
)
from auditfast.core.check._dax import normalised as dax_normalised
from auditfast.core.check.helpers import Verdict, covered, not_applicable, note
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
    return dax_normalised(expression)


#: How many offending measure names a detail row spells out before summarising. A
#: model with hundreds of them is a model-level problem, not a list to work through.
_MAX_NAMED_MEASURES = 25


def _measure_detail(names: list[str], verb: str) -> str:
    """``N measures <verb>: a, b, c`` - capped so one model cannot fill the report."""
    shown = ", ".join(names[:_MAX_NAMED_MEASURES])
    more = (f" (+{len(names) - _MAX_NAMED_MEASURES} more)"
            if len(names) > _MAX_NAMED_MEASURES else "")
    return f"{len(names)} measure(s) {verb}: {shown}{more}"


# ---------------------------------------------------------------------------
# Structure checks — referential integrity
# ---------------------------------------------------------------------------


@check(
    id="SM-FK-SURROGATE",
    ref="5.4.1",
    title="Fact-dimension referential integrity: all FKs in fact tables match dimension surrogate keys",
    pillar=Pillar.DATA,
    scope=Scope.SEMANTIC_MODEL,
    severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def sm_fk_surrogate(ctx: CheckContext) -> Verdict:
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definition could not be read")

    relationships = _relationships(model)
    relationships = _relationships(model)
    if not relationships:
        table_count = len(model.get("tables") or [])
        if table_count <= 1:
        table_count = len(model.get("tables") or [])
        if table_count <= 1:
            return not_applicable("Single-table model — no relationships to define")
        return covered(0, 1, f"Model has {table_count} tables but no relationships defined — facts are not wired to dimensions",)

    on_keys: list[dict[str, Any]] = []
    inactive_names: list[str] = []

    on_keys: list[dict[str, Any]] = []
    inactive_names: list[str] = []

    for rel in relationships:
        from_col = _norm_col(str(rel.get("from_column") or rel.get("fromColumn") or ""))
        to_col = _norm_col(str(rel.get("to_column") or rel.get("toColumn") or ""))

        if _KEY_HINT_RE.search(from_col) or _KEY_HINT_RE.search(to_col):
        from_col = _norm_col(str(rel.get("from_column") or rel.get("fromColumn") or ""))
        to_col = _norm_col(str(rel.get("to_column") or rel.get("toColumn") or ""))

        if _KEY_HINT_RE.search(from_col) or _KEY_HINT_RE.search(to_col):
            on_keys.append(rel)

        if not _is_active(rel):
            inactive_names.append(str(rel.get("name") or rel.get("id") or "?"))

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
    title="Fact-dimension referential integrity: all FKs in fact tables match dimension surrogate keys",
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
    id="R-BIDI-REL", ref="14.1.1", title="Star schema followed in the semantic model (single-direction relationships, no unnecessary bidirectional filters)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def single_direction_relationships(ctx: CheckContext) -> list[Verdict]:
    """Relationships filter one way, so filter propagation stays predictable.

    A bidirectional filter is legitimate for a bridge table, so this reports the
    count for review rather than judging whether each one is necessary. The scored
    workspace verdict is followed by one unscored detail row per model carrying a
    bidirectional relationship, naming the relationship.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    with_relationships = 0
    single_direction = 0
    bidirectional = 0
    failing: list[tuple[str, str]] = []
    for name, defn in models.items():
        rels = defn.get("relationships") or []
        if not rels:
            continue
        with_relationships += 1
        both = [r for r in rels
                if str(r.get("cross_filter") or "").strip().lower() == "bothdirections"]
        bidirectional += len(both)
        if not both:
            single_direction += 1
        else:
            named = ", ".join(
                f"{r.get('from_table') or '?'} <-> {r.get('to_table') or '?'}" for r in both[:10]
            )
            more = f" (+{len(both) - 10} more)" if len(both) > 10 else ""
            failing.append((name, f"{len(both)} bidirectional relationship(s): {named}{more}"))

    if not with_relationships:
        return [not_applicable("No semantic models define relationships")]
    verdicts = [covered(
        single_direction, with_relationships,
        f"{single_direction} of {with_relationships} semantic models filter in a single "
        f"direction; {bidirectional} bidirectional relationship(s) found",
    )]
    verdicts += [note(reason, obj=name) for name, reason in sorted(failing)]
    return verdicts


@check(
    id="R-MEASURE-DUP", ref="14.1.3", title="Measures centralized (no duplicated calculation logic across reports)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def measures_not_duplicated(ctx: CheckContext) -> list[Verdict]:
    """Each substantial calculation is defined once and re-used, not copy-pasted.

    Duplication is counted across every model in the workspace, which is where
    copy-pasted logic usually lands: the same measure cloned into many models. The
    scored workspace verdict is followed by one unscored detail row per model that
    carries a repeated expression, naming the measures.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    # (model, measure name, normalised expression) for every substantial measure.
    entries: list[tuple[str, str, str]] = []
    for model_name, defn in models.items():
        for measure in defn.get("measures") or []:
            expr = _normalised(measure.get("expression"))
            if len(expr) > _MIN_MEASURE_CHARS:
                entries.append((model_name, measure.get("name") or "?", expr))
    if not entries:
        return [not_applicable("No semantic model measures long enough to assess for duplication")]

    counts = Counter(expr for _, _, expr in entries)
    duplicated = {expr for expr, n in counts.items() if n > 1}
    unique = sum(1 for _, _, expr in entries if expr not in duplicated)

    offenders: dict[str, list[str]] = {}
    for model_name, measure_name, expr in entries:
        if expr in duplicated:
            offenders.setdefault(model_name, []).append(measure_name)

    verdicts = [covered(
        unique, len(entries),
        f"{unique} of {len(entries)} substantial measures carry an expression used "
        f"nowhere else; {len(duplicated)} distinct expression(s) are repeated across the "
        f"workspace's {len(models)} semantic model(s)",
    )]
    verdicts += [
        note(_measure_detail(names, "repeat an expression defined elsewhere"), obj=model_name)
        for model_name, names in sorted(offenders.items())
    ]
    return verdicts


@check(
    id="R-DAX-VAR", ref="14.1.4", title="DAX follows good practices (variables, no repeated sub-expressions, avoids expensive iterators where avoidable)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def complex_measures_use_variables(ctx: CheckContext) -> list[Verdict]:
    """Substantial measures are readable and cheap to evaluate.

    Three mechanically checkable practices, judged per measure:

    * a long measure declares ``VAR``, so intermediate steps are named and
      evaluated once;
    * no substantial sub-expression is written twice — the duplication a ``VAR``
      exists to remove;
    * no iterator pattern with a cheaper equivalent — an iterator nested inside
      another, or ``CALCULATE(..., FILTER(<table>, <table>[col] = x))`` where a
      plain boolean argument does the same job.

    Whether a *particular* iterator is truly avoidable is a modelling judgement,
    so only the two unambiguous anti-patterns above count against a measure. The
    scored workspace verdict is followed by one unscored detail row per model,
    naming the offending measures and which practice each breaks.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    total = compliant = 0
    no_var = repeated = iterators = 0
    offenders: dict[str, list[str]] = {}
    for model_name, defn in models.items():
        for measure in defn.get("measures") or []:
            expr = _normalised(measure.get("expression"))
            if len(expr) <= _MIN_MEASURE_CHARS:
                continue
            total += 1
            faults = []
            if len(expr) > _COMPLEX_MEASURE_CHARS and not uses_variables(expr):
                faults.append("no VAR")
                no_var += 1
            if repeated_subexpressions(expr):
                faults.append("repeated sub-expression")
                repeated += 1
            if expensive_iterator(expr):
                faults.append("avoidable iterator")
                iterators += 1
            if faults:
                name = measure.get("name") or "?"
                offenders.setdefault(model_name, []).append(f"{name} ({', '.join(faults)})")
            else:
                compliant += 1

    if not total:
        return [not_applicable("No semantic model measures substantial enough to assess")]

    verdicts = [covered(
        compliant, total,
        f"{compliant} of {total} substantial measures follow all three DAX practices — "
        f"{no_var} measure(s) longer than {_COMPLEX_MEASURE_CHARS} characters declare no VAR, "
        f"{repeated} repeat a substantial sub-expression, "
        f"{iterators} use an iterator pattern with a cheaper equivalent",
    )]
    verdicts += [
        note(_measure_detail(names, "break a DAX practice"), obj=model_name)
        for model_name, names in sorted(offenders.items())
    ]
    return verdicts
