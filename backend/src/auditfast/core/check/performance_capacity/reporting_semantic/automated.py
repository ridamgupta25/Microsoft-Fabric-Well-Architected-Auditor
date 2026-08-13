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
    # Absence is only meaningful once the model is big enough to care about.
    if len(model.get("tables") or []) < 5:
        return not_applicable(f"Small model ({len(model.get('tables') or [])} tables) — "
                              f"aggregations are not warranted")
    return binary(False, f"Import/DirectQuery model with {len(model.get('tables') or [])} "
                         f"tables and no aggregation tables — every visual scans detail rows")


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
    * *free text* — an unbounded ``varchar(max)``/``text``/``json`` source type,
      or a name saying description/comment/address/url. Not something a user
      slices by, and expensive to keep.
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
    """Tables bind to a warehouse table or view, not to inline query logic.

    A partition carrying its own native SQL or Power Query expression re-runs
    that transformation on every refresh, and it hides the logic from the
    warehouse where it could be tested and reused. Binding to a table or view
    pushes the work upstream and makes the model a thin serving layer.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definitions could not be read from Fabric")

    storage = model.get("storage") or {}
    bound = {n: f for n, f in storage.items()
             if any(m.lower() in _REAL_MODES for m in f.get("modes") or [])}
    if not bound:
        return not_applicable("Model has no data-bearing partitions to assess")

    transforming = sorted(n for n, f in bound.items() if f.get("native_query_partitions"))
    clean = len(bound) - len(transforming)
    evidence = (f"Semantic model '{ctx.obj_name}': {clean} of {len(bound)} table(s) "
                "bind directly to a warehouse table/view rather than inline query logic")
    if transforming:
        evidence += f" — inline transformation in: {', '.join(transforming)}"
    evidence += (". This verdict inspects saved semantic-model partition metadata; it "
                 "does not measure SQL, DAX, refresh, or interactive query performance")
    return covered(clean, len(bound), evidence)
