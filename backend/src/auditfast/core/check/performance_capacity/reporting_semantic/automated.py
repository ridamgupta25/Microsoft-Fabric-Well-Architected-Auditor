"""Performance & Capacity · Reporting / Semantic — model storage and query cost.

Everything here reads the **parsed TMSL definition** the provider already
fetches: partition modes, refresh policies, aggregation declarations. That is
model *metadata* — no table rows, no column statistics, and no query against the
warehouse. Anything needing actual data volumes (column cardinality, row counts)
is deliberately out of scope: it would have to be measured at check time against
the SQL endpoint, not cached into the knowledge base.
"""
from __future__ import annotations

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
    evidence = (f"{clean} of {len(bound)} table(s) bind directly to a warehouse "
                f"table/view rather than inline query logic")
    if transforming:
        evidence += f" — inline transformation in: {', '.join(transforming)}"
    return covered(clean, len(bound), evidence)
