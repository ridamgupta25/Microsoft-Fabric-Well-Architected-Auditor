"""Performance & Capacity · Reporting / Semantic — model storage and query cost.

Everything here reads the **parsed TMSL definition** the provider already
fetches: partition modes, refresh policies, aggregation declarations, column
declarations. That is model *metadata* — no table rows, no column statistics,
and no query against the warehouse.

Anything needing actual data volumes stays out of scope: it would have to be
measured at check time against the SQL endpoint, not cached into the knowledge
base. ``SM-COLUMN-SHAPE`` (ref 14.2.3) is the deliberate near-miss — it scores a
*shape* proxy (GUID / full-precision datetime / free-text / unused identity
columns) and says plainly in its own evidence that it has not measured
cardinality, because measuring it would need the rows.
"""
from __future__ import annotations

import re

from auditfast.core.check._semantic import (
    high_cardinality_shape,
    is_row_identifier,
    relationship_columns,
)
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Workspaces that hold semantic models.
MODEL_LAYERS = (Layer.REPORTING, Layer.MIXED)

#: Modes a partition can declare. ``directLake`` reads Delta files straight from
#: OneLake; ``import`` copies into the model; ``directQuery`` pushes every query
#: to the source. ``dual`` lets the engine pick per query.
_REAL_MODES = {"directlake", "import", "directquery", "dual"}

#: Power Query functions that reshape data inside the partition — a merge, an
#: append, a group-by, a pivot, or a manual ``#table`` entry — the transforms a
#: warehouse view would otherwise own.
_TRANSFORM_M = re.compile(
    r"Table\.(Combine|Join|NestedJoin|Group|Pivot|Unpivot|FromRows|FromList|"
    r"FromRecords|FromColumns)\b|#table\s*\(",
    re.IGNORECASE,
)
#: A native SQL partition query that itself joins, groups, unions or pivots is
#: doing the reshape the warehouse should. A plain ``SELECT ... FROM`` is not.
_TRANSFORM_SQL = re.compile(
    r"\bJOIN\b|\bGROUP\s+BY\b|\bUNION\b|\bPIVOT\b|\bUNPIVOT\b|\bHAVING\b",
    re.IGNORECASE,
)


def _is_transforming_query(expression: str) -> bool:
    """True when a partition query reshapes data rather than just reading a source."""
    text = expression or ""
    return bool(_TRANSFORM_M.search(text) or _TRANSFORM_SQL.search(text))


#: Metadata proxies for a "performance-critical" (large) model in SM-AGGREGATIONS.
#: Real size (rows, VertiPaq footprint, cardinality) is never in the KB, so a
#: model counts as large if it carries an incremental-refresh policy, is wide
#: (this many column declarations), or has at least this many tables.
_WIDE_MODEL_COLUMNS = 100
_LARGE_MODEL_TABLES = 5


def _model(ctx: CheckContext) -> dict | None:
    """The parsed TMSL for the model under inspection, if it was read."""
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return None
    return (ctx.workspace.semantic_models or {}).get(ctx.obj_name)


@check(
    id="SM-STORAGE-MODE", ref="14.2.1",
    title="Storage mode chosen deliberately (Direct Lake / Import / DirectQuery)",
    pillar=Pillar.PERFORMANCE, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_storage_mode(ctx: CheckContext) -> Verdict:
    """One storage mode is applied consistently rather than drifting per table.

    Cannot read the *rationale* — that lives in a design document. What is
    verifiable is consistency: a model whose tables are split across modes with
    no dual/hybrid intent is usually accidental, and it costs a refresh path and
    a query path instead of one.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    storage = model.get("storage") or {}
    data_tables = {
        name: facts for name, facts in storage.items()
        if any(m.lower() in _REAL_MODES for m in facts.get("modes") or [])
    }
    if not data_tables:
        return not_applicable("Model declares no data-bearing partitions "
                              "(calculated / calculation-group tables only)")

    modes: set[str] = set()
    for facts in data_tables.values():
        modes.update(m.lower() for m in facts.get("modes") or [] if m.lower() in _REAL_MODES)

    if len(modes) == 1:
        only = sorted(modes)[0]
        return binary(True, f"All {len(data_tables)} data table(s) use one storage mode: {only}")
    if modes <= {"directquery", "import", "dual"} and "dual" in modes:
        return graded(2, f"Mixed storage ({', '.join(sorted(modes))}) including 'dual' — "
                         f"looks like a deliberate composite model; confirm the rationale "
                         f"is documented")
    return graded(
        1,
        f"{len(data_tables)} data table(s) split across {len(modes)} storage modes "
        f"({', '.join(sorted(modes))}) with no dual/hybrid table — usually unintended "
        f"drift rather than a composite-model decision",
    )


@check(
    id="SM-DIRECTLAKE-FALLBACK", ref="14.2.2",
    title="Direct Lake fallback behaviour is set deliberately",
    pillar=Pillar.PERFORMANCE, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_directlake_fallback(ctx: CheckContext) -> Verdict:
    """A Direct Lake model states whether it may silently fall back to DirectQuery.

    Reads ``directLakeBehavior`` from the model. Left unset, Fabric's default is
    ``automatic``: a query that exceeds a guardrail silently becomes DirectQuery
    and gets much slower with no signal to the user. Setting it to
    ``directLakeOnly`` turns that into a visible error instead. Whether fallback
    was *monitored* needs the Activity API and is not judged here.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    storage = model.get("storage") or {}
    direct_lake = [n for n, f in storage.items()
                   if any(m.lower() == "directlake" for m in f.get("modes") or [])]
    if not direct_lake:
        return not_applicable("Model uses no Direct Lake tables — fallback does not apply")

    behavior = (model.get("direct_lake_behavior") or "").strip()
    if not behavior or behavior.lower() == "automatic":
        stated = behavior or "unset (defaults to automatic)"
        return binary(False, f"{len(direct_lake)} Direct Lake table(s) with "
                             f"directLakeBehavior {stated} — queries can silently fall "
                             f"back to DirectQuery and slow down with no error raised")
    return binary(True, f"{len(direct_lake)} Direct Lake table(s) with an explicit "
                        f"directLakeBehavior: {behavior}")


@check(
    id="SM-AGGREGATIONS", ref="14.2.4",
    title="Aggregations used for performance-critical models",
    pillar=Pillar.PERFORMANCE, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_aggregations(ctx: CheckContext) -> Verdict:
    """A large Import/DirectQuery model summarizes its detail with aggregations.

    Gated to the models where aggregations actually pay: Direct Lake reads
    columnar Delta directly, so an aggregation table is usually redundant there
    and its absence must not be reported as a finding.

    Presence is credited (PASS); absence is **N/A, never a FAIL**. Whether a
    model genuinely needs aggregations depends on fact-table row counts and
    VertiPaq footprint, and rows are never read into the knowledge base — so the
    tool cannot assert a model *should* have them.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    storage = model.get("storage") or {}
    modes = {m.lower() for f in storage.values() for m in f.get("modes") or []}
    if not modes & {"import", "directquery", "dual"}:
        return not_applicable("Model is Direct Lake only — aggregation tables are not "
                              "the relevant optimization")

    aggregations = model.get("aggregations") or []
    if aggregations:
        tables = sorted({a.get("table", "") for a in aggregations if a.get("table")})
        return binary(True, f"{len(aggregations)} aggregation column(s) declared on: "
                            f"{', '.join(tables)}")
    # Absence is not a defect this tool can assert. Whether a model *needs*
    # aggregations depends on fact-table row counts and VertiPaq footprint, and
    # rows are never read into the knowledge base — so a missing aggregation
    # table is "cannot determine", never a FAIL. The metadata size is reported
    # for context, but it does not turn absence into a finding.
    table_count = len(model.get("tables") or [])
    column_count = len(model.get("columns") or [])
    incremental = model.get("refresh_policies") or []
    signals: list[str] = []
    if incremental:
        signals.append(f"{len(incremental)} incremental-refresh policy(ies)")
    if column_count >= _WIDE_MODEL_COLUMNS:
        signals.append(f"{column_count} columns")
    if table_count >= _LARGE_MODEL_TABLES:
        signals.append(f"{table_count} tables")
    context = f" (metadata size: {'; '.join(signals)})" if signals else ""
    return not_applicable(
        f"Model '{ctx.obj_name}' declares no aggregation tables{context}. Aggregations "
        f"are an optional optimization that only pays on very large fact tables; whether "
        f"this model needs them depends on row counts, which are never read into the "
        f"knowledge base, so their absence is not scored"
    )


@check(
    id="SM-COLUMN-SHAPE", ref="14.2.3",
    title="Model size and column cardinality optimized (reduce high-cardinality columns where possible)",
    pillar=Pillar.PERFORMANCE, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_column_shape(ctx: CheckContext) -> Verdict:
    """Columns whose declared shape is one-value-per-row are kept out of the model.

    **This check does not measure cardinality, and cannot.** True cardinality is
    the number of *distinct values* in a column, which can only be known by
    reading the rows — and rows are never fetched into the knowledge base. What
    it scores is a **shape proxy** taken from the TMSL column declarations: types
    and names that are inherently one-value-per-row (or close to it) whatever
    data sits behind them. Every verdict says so in its own evidence, so nobody
    reads this as a measurement.

    **What it can determine.** Four readable shapes, each a well-known VertiPaq
    dictionary-size problem:

    * *GUID* — a ``uniqueidentifier`` source type, or a name saying guid/uuid.
      One distinct value per row by construction.
    * *full-precision datetime* — a temporal column carrying a time of day rather
      than a date. Split into a date key plus a time key it costs two small
      dictionaries instead of one enormous one.
    * *free text* — an unbounded ``varchar(max)``/``text``/``json`` source type.
      A column is **not** judged free text on its name alone: a ``Description``
      column mapped one-to-one to a low-cardinality key carries that key's
      cardinality, which the name cannot reveal and the rows are never read.
    * *row identifier* — a column the model marks ``isKey``, or whose name is
      key-shaped, that **no relationship uses**. That is an identity column
      imported for no modelling reason: the "unnecessary columns imported" half
      of this point.

    It also reports the model's total column count, the other half of "model
    size" that is readable without rows.

    **What it cannot.** It cannot say a flagged column is *actually* large (a
    GUID column on a 40-row dimension costs nothing), nor that an unflagged
    column is small — a ``string`` column named ``customer_name`` may well have
    a million distinct values and will never be flagged here. It cannot see
    memory footprint, dictionary size, or compression, none of which are in any
    definition. Columns a relationship binds are exempt on every shape rule:
    those are load-bearing keys, not accidents.

    **Sibling.** ``SM-AGGREGATIONS`` (ref 14.2.4) asks whether the model
    summarises its detail; this asks whether the detail it keeps is shaped to
    compress. A model can pass either and fail the other.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    columns = model.get("columns") or []
    if not columns:
        return not_applicable(
            f"Model '{ctx.obj_name}' has no column declarations in the captured "
            f"definition ({len(model.get('tables') or [])} table(s) read), so no column "
            f"shape can be judged. Snapshots taken before column metadata was parsed "
            f"need a re-crawl before this can be answered"
        )

    bound = relationship_columns(model)

    def _is_bound(column: dict) -> bool:
        return (str(column.get("table") or "").lower(),
                str(column.get("name") or "").lower()) in bound

    # Relationship columns are exempt: the model cannot join without them, so
    # their shape is a required cost rather than an avoidable one.
    judged = [c for c in columns if not _is_bound(c)]
    if not judged:
        return not_applicable(
            f"All {len(columns)} column(s) in '{ctx.obj_name}' are bound by a "
            f"relationship, so every one is a load-bearing key with no discretionary "
            f"shape to judge"
        )

    flagged: list[tuple[str, str]] = []
    for column in judged:
        label = f"{column.get('table', '')}[{column.get('name', '')}]"
        reason = high_cardinality_shape(column)
        if not reason and is_row_identifier(column):
            reason = "unused row identifier"
        if reason:
            flagged.append((label, reason))

    clean = len(judged) - len(flagged)
    caveat = (
        " This is a *shape* proxy read from the column declarations — type and name — "
        "not a measured distinct-value count; rows are never read"
    )
    summary = (
        f"Model '{ctx.obj_name}': {len(model.get('tables') or [])} table(s), "
        f"{len(columns)} column(s) declared. {clean} of {len(judged)} discretionary "
        f"column(s) carry no inherently high-cardinality shape "
        f"({len(columns) - len(judged)} relationship key(s) exempt)"
    )
    if flagged:
        shown = "; ".join(f"{label} ({reason})" for label, reason in flagged[:5])
        more = f" and {len(flagged) - 5} more" if len(flagged) > 5 else ""
        summary += f" — flagged: {shown}{more}"
    return covered(clean, len(judged), summary + "." + caveat)


@check(
    id="SM-QUERY-TRANSFORM", ref="14.2.6",
    title="Warehouse serves the model directly (no per-refresh transformation)",
    pillar=Pillar.PERFORMANCE, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_query_transform(ctx: CheckContext) -> Verdict:
    """Tables read a warehouse table or view, not reshape data inline.

    Reading a warehouse table or view — a source navigation, or a plain
    ``SELECT ... FROM`` — pushes the work upstream where it can be tested and
    reused, and makes the model a thin serving layer. What re-runs an expensive
    reshape on every refresh, and hides the logic from the warehouse, is a
    partition that *transforms* inline: a Power Query merge, append, group-by,
    pivot or manual ``#table`` entry, or a native SQL query that itself joins,
    groups or unions. Only those are flagged; a partition that merely navigates
    to a source object or runs a plain query is treated as warehouse-served.

    **What it cannot determine.** It classifies the *saved query text*, not query
    performance: a plain ``SELECT`` from a view that hides heavy logic reads as
    warehouse-served here, which is the intended verdict — the work lives in the
    warehouse. Snapshots taken before the partition query text was captured carry
    only the coarse "has a native query" count, and fall back to it.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    storage = model.get("storage") or {}
    bound = {n: f for n, f in storage.items()
             if any(m.lower() in _REAL_MODES for m in f.get("modes") or [])}
    if not bound:
        return not_applicable("Model has no data-bearing partitions to assess")

    transforming: list[str] = []
    for name, facts in bound.items():
        expressions = facts.get("native_query_expressions")
        if expressions is None:
            # Pre-capture snapshot: only the count survived, so fall back to it.
            if facts.get("native_query_partitions"):
                transforming.append(name)
            continue
        if any(_is_transforming_query(expr) for expr in expressions):
            transforming.append(name)
    transforming.sort()

    clean = len(bound) - len(transforming)
    evidence = (f"Semantic model '{ctx.obj_name}': {clean} of {len(bound)} table(s) "
                "read a warehouse table/view or run a plain source query rather than "
                "transforming data inline")
    if transforming:
        evidence += (" — inline transformation (merge/append/group-by/pivot/manual entry) "
                     f"in: {', '.join(transforming)}")
    evidence += (". A partition that only navigates to a source object or runs a plain "
                 "SELECT is treated as warehouse-served; this reads saved partition "
                 "metadata, not SQL, DAX, refresh, or interactive query performance")
    return covered(clean, len(bound), evidence)
