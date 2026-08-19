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
from auditfast.core.check._semantic import is_row_identifier
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


def _name_list(names: list[str], limit: int = _MAX_NAMED_MEASURES) -> str:
    """Comma-joined names, capped so a large estate cannot fill the evidence."""
    shown = ", ".join(names[:limit])
    more = f" (+{len(names) - limit} more)" if len(names) > limit else ""
    return f"{shown}{more}"


def _offender_breakdown(offenders: dict[str, list[str]], limit: int = _MAX_NAMED_MEASURES) -> str:
    """``Model: m1 (fault), m2 (fault); Model2: m3 (fault)`` — the offending measures
    grouped under the model they belong to, capped at ``limit`` measures total so one
    workspace cannot flood the scored row's evidence.
    """
    total_named = sum(len(names) for names in offenders.values())
    parts: list[str] = []
    shown = 0
    for model_name, names in sorted(offenders.items()):
        if shown >= limit:
            break
        picked = names[: limit - shown]
        shown += len(picked)
        parts.append(f"{model_name}: {', '.join(picked)}")
    text = "; ".join(parts)
    if total_named > limit:
        text += f" (+{total_named - limit} more)"
    return text


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
        return covered(0, 1, f"Model has {table_count} tables but no relationships defined — facts are not wired to dimensions",)

    on_keys: list[dict[str, Any]] = []
    inactive_names: list[str] = []

    for rel in relationships:
        from_col = _norm_col(str(rel.get("from_column") or rel.get("fromColumn") or ""))
        to_col = _norm_col(str(rel.get("to_column") or rel.get("toColumn") or ""))

        if _KEY_HINT_RE.search(from_col) or _KEY_HINT_RE.search(to_col):
            on_keys.append(rel)

        if not _is_active(rel):
            inactive_names.append(str(rel.get("name") or rel.get("id") or "?"))

    detail = f"{len(on_keys)} of {len(relationships)} relationships join on a surrogate-key-shaped column"
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
    scored workspace verdict names every model it assessed and the measures that
    break a practice, and is followed by one unscored detail row per model
    repeating that model's offenders.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    total = compliant = 0
    no_var = repeated = iterators = 0
    assessed_models: list[str] = []
    offenders: dict[str, list[str]] = {}
    for model_name, defn in models.items():
        model_assessed = False
        for measure in defn.get("measures") or []:
            expr = _normalised(measure.get("expression"))
            if len(expr) <= _MIN_MEASURE_CHARS:
                continue
            total += 1
            model_assessed = True
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
        if model_assessed:
            assessed_models.append(model_name)

    if not total:
        return [not_applicable("No semantic model measures substantial enough to assess")]

    headline = (
        f"{compliant} of {total} substantial measure(s) follow all three DAX practices "
        f"across {len(assessed_models)} semantic model(s) "
        f"({_name_list(assessed_models)}) — "
        f"{no_var} measure(s) longer than {_COMPLEX_MEASURE_CHARS} characters declare no VAR, "
        f"{repeated} repeat a substantial sub-expression, "
        f"{iterators} use an iterator pattern with a cheaper equivalent"
    )
    if offenders:
        headline += ". Measures needing attention — " + _offender_breakdown(offenders)
    verdicts = [covered(compliant, total, headline)]
    verdicts += [
        note(_measure_detail(names, "break a DAX practice"), obj=model_name)
        for model_name, names in sorted(offenders.items())
    ]
    return verdicts


# ---------------------------------------------------------------------------
# 14.1.2 — relationship graph: ambiguous filter paths
# ---------------------------------------------------------------------------


def _table_key(value: object) -> str:
    """A table name reduced to a comparison key ("" when the model named nothing)."""
    return str(value or "").strip().lower()


def _directed_filter_graph(
    model: dict[str, Any],
) -> tuple[dict[str, set[str]], set[tuple[str, str]], int]:
    """The model's filter-propagation graph, as *directed* edges.

    A relationship propagates a filter from its "one" side to its "many" side —
    from the ``to`` endpoint (the dimension key) to the ``from`` endpoint (the
    fact's foreign key). A both-directions relationship propagates each way. That
    direction is exactly what separates a genuine ambiguity from an ordinary star
    or galaxy schema: two fact tables sharing a dimension form an *undirected*
    cycle, but every edge still points dimension -> fact, so no table is reachable
    by two routes and there is nothing for the engine to disambiguate.

    Returns the adjacency map, the set of unordered table pairs joined by more
    than one active relationship (ambiguous outright), and the count of usable
    active relationships (to decide whether a second path is even possible).
    """
    adjacency: dict[str, set[str]] = {}
    pair_counts: Counter[tuple[str, str]] = Counter()
    usable = 0
    for rel in _relationships(model):
        if not _is_active(rel):
            continue
        many = _table_key(rel.get("from_table") or rel.get("fromTable"))
        one = _table_key(rel.get("to_table") or rel.get("toTable"))
        if not many or not one or many == one:
            continue
        usable += 1
        adjacency.setdefault(one, set()).add(many)
        behaviour = str(
            rel.get("cross_filter") or rel.get("crossFilteringBehavior") or ""
        ).strip().lower()
        if behaviour == "bothdirections":
            adjacency.setdefault(many, set()).add(one)
        pair_counts[(many, one) if many <= one else (one, many)] += 1
    duplicates = {pair for pair, seen in pair_counts.items() if seen > 1}
    return adjacency, duplicates, usable


def _ambiguous_pairs(
    adjacency: dict[str, set[str]], duplicates: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Table pairs a second *active* filter route can reach.

    A pair joined by more than one active relationship is ambiguous outright. For
    the rest, a bounded depth-first walk counts distinct *simple* directed paths
    out of each source; a target reached by two different routes — the diamond of
    a snowflake short-cut, or a bidirectional loop — is the second filter path the
    engine would have to choose between. A single-direction star or galaxy schema
    forms no such diamond, so it is not flagged.

    The walk is capped so a pathological bidirectional graph cannot run away;
    exhausting the cap can only *under*-report, never invent an ambiguity.
    """
    pairs: set[tuple[str, str]] = set(duplicates)
    nodes = sorted(set(adjacency) | {t for outs in adjacency.values() for t in outs})
    budget = 200_000
    for source in nodes:
        reached: dict[str, int] = {}
        stack: list[tuple[str, frozenset[str]]] = [(source, frozenset((source,)))]
        while stack and budget > 0:
            node, visited = stack.pop()
            for nxt in sorted(adjacency.get(node, ())):
                if nxt in visited:
                    continue
                budget -= 1
                reached[nxt] = reached.get(nxt, 0) + 1
                if reached[nxt] == 2:
                    pairs.add((source, nxt) if source <= nxt else (nxt, source))
                stack.append((nxt, visited | frozenset((nxt,))))
    return sorted(pairs)


def _many_to_many_pairs(model: dict[str, Any]) -> list[tuple[str, str]]:
    """Active relationships declared many-to-many on both ends — i.e. no bridge.

    A conformed bridge uses two many-to-one relationships through an intermediate
    table; a relationship whose *own* endpoints are both ``many`` is a direct
    many-to-many, the "many-to-many without a bridge" the checklist names. TMSL
    omits the cardinality fields for a standard many-to-one relationship, so only
    an *explicit* ``many``/``many`` is flagged — a defaulted relationship is not.
    Inactive relationships propagate no filter, so only active ones are judged.
    """
    pairs: set[tuple[str, str]] = set()
    for rel in _relationships(model):
        if not _is_active(rel):
            continue
        frm = str(rel.get("from_cardinality") or rel.get("fromCardinality") or "").strip().lower()
        to = str(rel.get("to_cardinality") or rel.get("toCardinality") or "").strip().lower()
        if frm == "many" and to == "many":
            many = _table_key(rel.get("from_table") or rel.get("fromTable"))
            one = _table_key(rel.get("to_table") or rel.get("toTable"))
            if many and one and many != one:
                pairs.add((many, one) if many <= one else (one, many))
    return sorted(pairs)


@check(
    id="R-REL-AMBIGUOUS", ref="14.1.2",
    title="Relationships correctly defined (cardinality, active/inactive) with no ambiguous filter paths",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def relationships_have_no_ambiguous_paths(ctx: CheckContext) -> list[Verdict]:
    """Exactly one active filter path connects any two tables in the model.

    Two or more active routes between the same pair of tables make filter
    propagation non-deterministic: the engine picks a path, and a measure that
    looks correct returns a silently different number depending on which one it
    took. A *directed* filter-propagation graph is built per model from the
    *active* relationships (dimension -> fact, both ways for a bidirectional
    relationship) and two defects are named:

    * a pair of tables joined by more than one active relationship;
    * a pair a second *directed* filter route also reaches — the diamond of a
      snowflake short-cut or a bidirectional loop. Because direction is honoured,
      a star or galaxy schema where several facts share a dimension is **not**
      flagged: its relationships form an undirected cycle, yet every edge points
      dimension -> fact, so no table is reachable two ways.

    Inactive relationships are counted and reported alongside, because a
    modeller who hit ambiguity usually deactivated one leg to escape it: they
    are the fingerprint of the problem, not a defect in themselves (a
    ``USERELATIONSHIP`` role-playing dimension is a legitimate use).

    **Cardinality is assessed structurally.** The parsed TMSL now carries each
    relationship's declared ``fromCardinality`` / ``toCardinality`` (definition
    metadata, never row data). A relationship declared ``many`` on both ends is a
    direct many-to-many with no bridge table — the "cardinality" half of this
    point — and is flagged as a defect alongside ambiguous paths. TMSL omits the
    fields for an ordinary many-to-one relationship, so only an *explicit*
    many/many is flagged; a defaulted relationship is left alone.

    **What it cannot determine.** Whether a particular inactive relationship is
    deliberate is a modelling judgement and is reported, never scored. Row-level
    cardinality (actual distinct-value counts) still needs the rows and is never
    read.

    Distinct from ``R-BIDI-REL`` (ref 14.1.1), which judges only the *direction*
    of cross-filtering on each relationship in isolation. A model can filter in
    a single direction everywhere and still carry two active single-direction
    routes between the same tables — the defect this check finds. Workspace
    scope (not ``Scope.SEMANTIC_MODEL``) matches the sibling 14.1.x checks, so
    the reporting workspace gets one scored roll-up plus one named detail row
    per offending model.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    judged = clean = 0
    inactive_total = 0
    offenders: dict[str, str] = {}
    for name, defn in models.items():
        adjacency, duplicates, usable = _directed_filter_graph(defn)
        inactive_total += sum(1 for r in _relationships(defn) if not _is_active(r))
        m2m = _many_to_many_pairs(defn)
        # A model is judged when there is something to assess: a second filter
        # path is only *possible* with two active relationships, but a direct
        # many-to-many relationship is a defect on its own, so a model carrying
        # one is judged even with a single relationship.
        if usable < 2 and not m2m:
            continue
        judged += 1
        ambiguous = _ambiguous_pairs(adjacency, duplicates) if usable >= 2 else []
        reasons: list[str] = []
        if ambiguous:
            named = ", ".join(f"{left} <-> {right}" for left, right in ambiguous[:10])
            more = f" (+{len(ambiguous) - 10} more)" if len(ambiguous) > 10 else ""
            reasons.append(
                f"{len(ambiguous)} table pair(s) reachable by more than one active "
                f"filter path: {named}{more}"
            )
        if m2m:
            named = ", ".join(f"{left} <-> {right}" for left, right in m2m[:10])
            more = f" (+{len(m2m) - 10} more)" if len(m2m) > 10 else ""
            reasons.append(
                f"{len(m2m)} direct many-to-many relationship(s) with no bridge "
                f"table: {named}{more}"
            )
        if not reasons:
            clean += 1
            continue
        offenders[name] = "; ".join(reasons)

    if not judged:
        return [not_applicable(
            "No semantic model defines two or more active relationships or a "
            "many-to-many relationship, so there is no filter path or cardinality "
            "defect to assess"
        )]

    detail = (
        f"{clean} of {judged} semantic model(s) have exactly one active filter path "
        f"between any two tables and no direct many-to-many relationship; "
        f"{inactive_total} inactive relationship(s) across the workspace"
    )
    verdicts: list[Verdict] = [covered(clean, judged, detail)]
    verdicts += [note(reason, obj=model_name) for model_name, reason in sorted(offenders.items())]
    return verdicts


# ---------------------------------------------------------------------------
# 14.1.8 — consumer-friendly model: technical keys hidden
# ---------------------------------------------------------------------------


@check(
    id="R-MODEL-HIDDEN-KEYS", ref="14.1.8",
    title="Model naming and organization are consumer-friendly (display folders, hidden keys)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def key_columns_are_hidden(ctx: CheckContext) -> list[Verdict]:
    """Key and technical columns are hidden from the report field list.

    A surrogate key, a GUID, or an ``…ID`` column carries no meaning to a report
    author: leaving it visible invites someone to drag it onto a visual and
    aggregate a key. TMSL states ``isHidden`` per column and ``isKey`` where the
    modeller marked one, so the *hidden-keys* half of this point is directly
    readable and is what this check scores — the share of key-shaped columns
    that are hidden.

    **What it cannot determine: display folders.** ``displayFolder`` is present
    in the raw TMSL but is *not* carried by this project's parsed projection
    (``clients/tmsl.py``), and extending that parser would invalidate every
    semantic-model snapshot already in the knowledge base until a re-crawl. So
    the folder-organisation half of the point is deliberately **not** scored
    here, and the evidence says so rather than implying a model without folders
    was assessed and passed.

    Key-shaped is judged by ``is_row_identifier`` — the model's own ``isKey``
    flag, or a key-shaped name — so it is the same vocabulary the table checks
    use. A model with no key-shaped column has nothing to hide and is N/A.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    total = hidden = 0
    offenders: dict[str, list[str]] = {}
    for name, defn in models.items():
        for column in defn.get("columns") or []:
            if not is_row_identifier(column):
                continue
            total += 1
            if column.get("is_hidden"):
                hidden += 1
            else:
                table = str(column.get("table") or "").strip()
                column_name = str(column.get("name") or "").strip()
                offenders.setdefault(name, []).append(
                    f"{table}[{column_name}]" if table else column_name
                )

    if not total:
        return [not_applicable(
            "No semantic model declares a key-shaped column, so there is no "
            "technical column to hide from report consumers"
        )]

    verdicts: list[Verdict] = [covered(
        hidden, total,
        f"{hidden} of {total} key-shaped column(s) across {len(models)} semantic "
        f"model(s) are hidden from the report view. Display folders are not part "
        f"of the parsed model definition, so folder organisation is not assessed.",
    )]
    for model_name, names in sorted(offenders.items()):
        shown = ", ".join(sorted(names)[:_MAX_NAMED_COLUMNS])
        more = (f" (+{len(names) - _MAX_NAMED_COLUMNS} more)"
                if len(names) > _MAX_NAMED_COLUMNS else "")
        verdicts.append(note(
            f"{len(names)} key-shaped column(s) visible to report authors: {shown}{more}",
            obj=model_name,
        ))
    return verdicts


# ---------------------------------------------------------------------------
# Shared report/model helpers (14.3.4)
# ---------------------------------------------------------------------------

#: How many names to list in evidence before summarising.
_MAX_NAMED_MODELS = 5

#: How many visible key columns to name per model before summarising the rest.
_MAX_NAMED_COLUMNS = 10

#: Stated wherever a check would otherwise be read as judging *certification*.
#: Verified against Microsoft's *Get Datasets In Group* reference: the standard
#: response carries no ``endorsementDetails`` — endorsement (Promoted /
#: Certified) is only available from the admin/scanner API, which this read-only
#: auditor never calls.
_ENDORSEMENT_LIMIT = (
    "Endorsement is not readable (the standard datasets API carries no "
    "endorsementDetails; Promoted/Certified needs the admin/scanner API), so this "
    "scores sharing and reuse, never certification."
)


def _reports_by_model(reports: list[dict]) -> dict[str, list[dict]]:
    """Group the workspace's reports by the semantic model they are built on.

    Reports with no ``dataset_id`` — paginated (RDL) reports bind to no semantic
    model — are excluded rather than grouped under an empty key: they cannot
    evidence either reuse or a private extract.
    """
    grouped: dict[str, list[dict]] = {}
    for report in reports:
        dataset_id = str(report.get("dataset_id") or "")
        if dataset_id:
            grouped.setdefault(dataset_id, []).append(report)
    return grouped


# ---------------------------------------------------------------------------
# 14.3.4 — reports built on a shared model, not a private ad-hoc extract
# ---------------------------------------------------------------------------


@check(
    id="R-REPORT-SHARED-MODEL", ref="14.3.4",
    title="Reports use the shared certified model rather than private ad-hoc extracts",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.REPORTING],
    requires=[Resource.REPORTS], required=False,
)
def reports_are_built_on_a_shared_model(ctx: CheckContext) -> Verdict:
    """Each report is built on a model other reports also use, not on its own extract.

    **What it can determine.** Every report's ``datasetId`` (Power BI *Get
    Reports In Group*, delegated ``Report.Read.All``), grouped by model. A report
    counts as reuse when either the model it binds to also serves another report
    in this workspace, **or** the model lives in a *different* workspace — that
    second case is the central-hub pattern, where the report deliberately
    consumes a shared model published elsewhere. Everything else is a model with
    exactly one report and no wider audience: the readable shape of a private
    ad-hoc extract.

    **What it cannot determine — stated plainly, because the point says
    "certified".** Endorsement is *not* readable: the standard *Get Datasets In
    Group* response carries no ``endorsementDetails``, and Promoted/Certified
    needs the admin/scanner API this read-only auditor never calls. So this check
    scores **sharing and reuse only**. A workspace can score 3 here with a
    thoroughly uncertified model.

    It also sees only *this* workspace's reports, so a model whose other
    consumers live elsewhere is under-counted — which is why a cross-workspace
    binding is credited as reuse outright. Paginated (RDL) reports carry no
    ``datasetId`` at all; they are excluded from both sides of the ratio, never
    counted as a private extract.

    **Scope limit — this scores sharing, never certification.** Ref 14.1.5
    ("shared/**certified** semantic model reused across domains") was removed
    from this library: endorsement (Promoted / Certified) is readable only from
    the admin/scanner API, so that point cannot be fully automated on a
    normal delegated sign-in and is tracked as an admin-scoped check. What is
    readable — and what this judges — is whether reports actually share a model
    or each sit on their own private extract.
    """
    if not ctx.workspace.has(Resource.REPORTS):
        return not_applicable(
            "Report → semantic-model bindings could not be read (the Power BI reports "
            "listing was unavailable or the sign-in yielded no Power BI token)"
        )
    reports = ctx.workspace.reports
    if not reports:
        return not_applicable("This workspace holds no report, so none can be judged")

    grouped = _reports_by_model(reports)
    bound = sum(len(rows) for rows in grouped.values())
    unbound = len(reports) - bound
    if not bound:
        return not_applicable(
            f"None of the {len(reports)} report(s) declares a semantic-model binding — "
            "paginated (RDL) reports carry no datasetId, so no model reuse is observable"
        )
    if bound < 2:
        return not_applicable(
            f"Only {bound} report in this workspace declares a model binding, so whether a "
            "model is shared between reports cannot be observed from one report"
        )

    workspace_id = ctx.workspace.id
    shared = 0
    private: list[str] = []
    for dataset_id, rows in grouped.items():
        external = any(
            row.get("dataset_workspace_id") and row.get("dataset_workspace_id") != workspace_id
            for row in rows
        )
        if len(rows) > 1 or external:
            shared += len(rows)
        else:
            private.append(rows[0].get("name") or rows[0].get("id") or dataset_id)

    detail = (
        f"{shared} of {bound} report(s) with a model binding are built on a model that is "
        f"shared — used by another report here, or published in another workspace — across "
        f"{len(grouped)} distinct model(s)"
    )
    if private:
        detail += (f"; {len(private)} report(s) sit on a model no other report uses: "
                   f"{', '.join(sorted(private)[:_MAX_NAMED_MODELS])}")
    if unbound:
        detail += (f". {unbound} paginated/unbound report(s) are excluded, not counted as "
                   "private extracts")
    detail += f". {_ENDORSEMENT_LIMIT}"
    return covered(shared, bound, detail)
