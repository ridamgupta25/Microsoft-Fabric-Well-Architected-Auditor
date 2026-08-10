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
from auditfast.core.check._semantic import is_row_identifier, relationship_columns
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


# ---------------------------------------------------------------------------
# 14.1.2 — relationship graph: ambiguous filter paths
# ---------------------------------------------------------------------------


def _table_key(value: object) -> str:
    """A table name reduced to a comparison key ("" when the model named nothing)."""
    return str(value or "").strip().lower()


def _active_edges(model: dict[str, Any]) -> list[tuple[str, str]]:
    """``(table_a, table_b)`` for every *active* relationship, endpoints sorted.

    Self-relationships (both ends on one table) and relationships missing an
    endpoint carry no filter path between two tables, so they are dropped rather
    than guessed at.
    """
    edges: list[tuple[str, str]] = []
    for rel in _relationships(model):
        if not _is_active(rel):
            continue
        left = _table_key(rel.get("from_table") or rel.get("fromTable"))
        right = _table_key(rel.get("to_table") or rel.get("toTable"))
        if not left or not right or left == right:
            continue
        edges.append((left, right) if left <= right else (right, left))
    return edges


def _redundant_pairs(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Table pairs that a second *active* filter path also connects.

    Union-find over the distinct edges, walked in sorted order so the answer is
    the same on every run: an edge whose endpoints are *already* connected by the
    edges accepted before it closes a cycle, and a cycle in a relationship graph
    means at least two active routes exist between some pair of tables. A pair
    joined by more than one active relationship is ambiguous outright and is
    reported too.
    """
    duplicates = sorted({pair for pair, n in Counter(edges).items() if n > 1})

    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    closes_cycle: list[tuple[str, str]] = []
    for left, right in sorted(set(edges)):
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            closes_cycle.append((left, right))
        else:
            parent[root_left] = root_right
    return sorted(set(duplicates) | set(closes_cycle))


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
    took. The relationship graph is built per model from the *active*
    relationships and two defects are named:

    * a pair of tables joined by more than one active relationship;
    * a cycle in the graph — an edge whose endpoints another chain of active
      relationships already connects, so a second route exists.

    Inactive relationships are counted and reported alongside, because a
    modeller who hit ambiguity usually deactivated one leg to escape it: they
    are the fingerprint of the problem, not a defect in themselves (a
    ``USERELATIONSHIP`` role-playing dimension is a legitimate use).

    **What it cannot determine.** Cardinality (``fromCardinality`` /
    ``toCardinality``) is not carried by the parsed TMSL projection, so the
    "many-to-many without a bridge" half of the point is *not* judged here and
    the evidence says so; nothing is inferred about it. Whether a particular
    inactive relationship is deliberate is a modelling judgement and is
    reported, never scored.

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
        edges = _active_edges(defn)
        inactive_total += sum(1 for r in _relationships(defn) if not _is_active(r))
        if len(edges) < 2:
            # One (or no) active relationship cannot form a second path.
            continue
        judged += 1
        ambiguous = _redundant_pairs(edges)
        if not ambiguous:
            clean += 1
            continue
        named = ", ".join(f"{left} <-> {right}" for left, right in ambiguous[:10])
        more = f" (+{len(ambiguous) - 10} more)" if len(ambiguous) > 10 else ""
        offenders[name] = (
            f"{len(ambiguous)} table pair(s) reachable by more than one active "
            f"relationship path: {named}{more}"
        )

    if not judged:
        return [not_applicable(
            "No semantic model defines two or more active relationships, so no "
            "second filter path can exist"
        )]

    detail = (
        f"{clean} of {judged} semantic model(s) have exactly one active filter path "
        f"between any two tables; {inactive_total} inactive relationship(s) across the "
        f"workspace. Relationship cardinality is not part of the parsed model "
        f"definition, so many-to-many without a bridge is not assessed here."
    )
    verdicts: list[Verdict] = [covered(clean, judged, detail)]
    verdicts += [note(reason, obj=model_name) for model_name, reason in sorted(offenders.items())]
    return verdicts


# ---------------------------------------------------------------------------
# 14.1.7 — columns and tables that nothing in the model references
# ---------------------------------------------------------------------------

#: How many candidate names one detail row spells out before summarising.
_MAX_NAMED_COLUMNS = 15


def _referenced_columns(model: dict[str, Any]) -> set[tuple[str, str]]:
    """``(table, column)`` pairs, lower-cased, that the *model* itself uses.

    Two routes count: a relationship endpoint (read via the shared
    ``relationship_columns`` helper) and a mention in a measure's DAX. DAX names
    a column as ``[Column]`` or ``'Table'[Column]``; the table qualifier is
    optional and frequently omitted, so a mention is credited against every
    table that owns a column of that name. Crediting too generously is the safe
    direction here — it can only *shrink* the candidate list.
    """
    referenced: set[tuple[str, str]] = set(relationship_columns(model))

    expressions = " ".join(_normalised(m.get("expression")) for m in model.get("measures") or [])
    mentioned = {m.lower() for m in re.findall(r"\[([^\]\[]+)\]", expressions)}
    if mentioned:
        for column in model.get("columns") or []:
            name = str(column.get("name") or "").strip().lower()
            if name and name in mentioned:
                referenced.add((str(column.get("table") or "").strip().lower(), name))
    return referenced


@check(
    id="R-MODEL-UNUSED", ref="14.1.7",
    title="Unused columns/tables removed from the model to reduce size and confusion",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def unused_model_columns(ctx: CheckContext) -> list[Verdict]:
    """Report the columns and tables nothing *in the model* references.

    A column is a removal **candidate** when no measure expression mentions it
    and no relationship binds it; a table is a candidate when none of its
    columns is referenced, it carries no measure, and it takes part in no
    relationship.

    **The hard limit, stated plainly: report visuals are not fetched.** A column
    dragged straight onto a visual — the single most common way a column earns
    its place — is invisible to this check and will appear in the candidate
    list. That makes the false-positive rate structurally high and unknowable
    from the data available, which is why this check is **unscored**: it emits
    ``note`` rows so a modeller can review the candidates, and never a PASS or a
    FAIL. Scoring it, even generously, would penalise a correct model for a gap
    in the crawler rather than a defect in the model — the opposite of the
    N/A-not-FAIL principle this library is built on.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable(_UNREADABLE)]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable(_NO_MODELS)]

    total_columns = total_candidates = 0
    details: dict[str, str] = {}
    for name, defn in models.items():
        model_columns = defn.get("columns") or []
        if not model_columns:
            continue
        referenced = _referenced_columns(defn)
        candidates: list[str] = []
        used_tables: set[str] = set()
        for column in model_columns:
            table = str(column.get("table") or "").strip()
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            total_columns += 1
            if (table.lower(), column_name.lower()) in referenced:
                used_tables.add(table.lower())
                continue
            candidates.append(f"{table}[{column_name}]" if table else column_name)
        total_candidates += len(candidates)

        measure_tables = {str(m.get("table") or "").strip().lower()
                          for m in defn.get("measures") or []}
        related = {t for edge in _active_edges(defn) for t in edge}
        related |= {_table_key(r.get("from_table")) for r in _relationships(defn)}
        related |= {_table_key(r.get("to_table")) for r in _relationships(defn)}
        orphan_tables = sorted(
            t for t in (defn.get("tables") or [])
            if t and t.strip().lower() not in (used_tables | measure_tables | related)
        )

        if not candidates and not orphan_tables:
            continue
        shown = ", ".join(candidates[:_MAX_NAMED_COLUMNS])
        more = (f" (+{len(candidates) - _MAX_NAMED_COLUMNS} more)"
                if len(candidates) > _MAX_NAMED_COLUMNS else "")
        parts = []
        if candidates:
            parts.append(f"{len(candidates)} of {len(model_columns)} column(s) are "
                         f"referenced by no measure and no relationship: {shown}{more}")
        if orphan_tables:
            parts.append(f"{len(orphan_tables)} table(s) carry no measure, no "
                         f"relationship and no referenced column: "
                         f"{', '.join(orphan_tables[:10])}")
        details[name] = "; ".join(parts)

    if not total_columns:
        return [not_applicable(
            "No semantic model carries column declarations, so removal candidates "
            "cannot be identified"
        )]

    summary = note(
        f"{total_candidates} of {total_columns} column(s) across "
        f"{len(models)} semantic model(s) are referenced by no measure and no "
        f"relationship. Report visuals are not fetched, so a column used only in "
        f"a visual is indistinguishable from an unused one — review before "
        f"removing. Reported, not scored."
    )
    return [summary] + [note(reason, obj=name) for name, reason in sorted(details.items())]


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
