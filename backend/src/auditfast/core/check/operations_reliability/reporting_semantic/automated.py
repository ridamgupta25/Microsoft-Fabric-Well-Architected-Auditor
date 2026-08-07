"""Operations & Reliability · Reporting / Semantic — how the model gets refreshed.

Reads the parsed TMSL (refresh policies) and, for the orchestration question,
the pipeline definitions the provider already fetches. Model metadata only —
no rows are read from the model or the warehouse behind it.
"""
from __future__ import annotations

from auditfast.core.check._pipeline import walk_activities
from auditfast.core.check.helpers import Verdict, binary, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Workspaces that hold semantic models.
MODEL_LAYERS = (Layer.REPORTING, Layer.MIXED)
#: …and the ones that also hold the pipelines which load them.
REFRESH_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.REPORTING, Layer.MIXED)

#: The pipeline activity that refreshes a Power BI / Fabric semantic model.
_REFRESH_ACTIVITY_TYPES = {
    "PBISemanticModelRefresh", "RefreshDataflow", "DatasetRefresh",
}
#: Activities that constitute "the Gold load" — the work a refresh should follow.
_LOAD_ACTIVITY_TYPES = {
    "Copy", "Script", "TridentNotebook", "SqlServerStoredProcedure",
}


@check(
    id="SM-REFRESH-ORCHESTRATED", ref="14.5.1",
    title="Refresh strategy aligned with upstream load completion",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=REFRESH_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.ITEMS], required=False,
)
def sm_refresh_orchestrated(ctx: CheckContext) -> Verdict:
    """The pipeline that loads Gold also triggers the model refresh.

    A refresh driven by the loading pipeline is aligned *by construction* — it
    cannot start before the load finishes. A model left on its own clock can
    refresh mid-load and publish a half-written Gold layer.

    Read from the pipeline definitions rather than from schedule times: the
    Fabric job-schedule API is not among the resources the provider fetches, and
    an orchestrated refresh is the stronger signal anyway.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    models = [i for i in ctx.workspace.items if i.type == "SemanticModel"]
    if not models:
        return not_applicable("Workspace holds no semantic model to refresh")
    pipelines = ctx.workspace.pipelines or {}
    if not pipelines:
        return not_applicable("Workspace holds no pipeline that could drive a refresh")

    driving: list[str] = []
    refresh_only: list[str] = []
    for name, defn in pipelines.items():
        acts = walk_activities(defn)
        refreshes = [a for a in acts if (a.get("type") or "") in _REFRESH_ACTIVITY_TYPES]
        if not refreshes:
            continue
        loads = [a for a in acts if (a.get("type") or "") in _LOAD_ACTIVITY_TYPES]
        # A refresh that depends on something upstream runs after the load;
        # a refresh with no dependency is just a scheduled trigger in disguise.
        sequenced = any(a.get("dependsOn") for a in refreshes)
        (driving if loads and sequenced else refresh_only).append(name)

    if driving:
        return binary(True, f"Model refresh runs downstream of the load in: "
                            f"{', '.join(sorted(driving))}")
    if refresh_only:
        return graded(1, f"Pipeline(s) {', '.join(sorted(refresh_only))} refresh a model but "
                         f"the refresh is not sequenced after a load activity — it can "
                         f"start while the load is still running")
    return binary(False, f"{len(models)} semantic model(s) and {len(pipelines)} pipeline(s), "
                         f"but no pipeline triggers a refresh — the model runs on its own "
                         f"clock with no guarantee the Gold load has finished")


@check(
    id="SM-INCREMENTAL-REFRESH", ref="14.5.2",
    title="Incremental refresh configured for large Import models",
    pillar=Pillar.OPERATIONS, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS, requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_incremental_refresh(ctx: CheckContext) -> Verdict:
    """Import tables carry a refresh policy instead of reloading in full.

    Gated to Import: Direct Lake tables have no refresh to make incremental, so
    their absence of a policy is correct rather than a finding.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    model = (ctx.workspace.semantic_models or {}).get(ctx.obj_name)
    if model is None:
        return not_applicable("Semantic model definition could not be read from Fabric")

    storage = model.get("storage") or {}
    import_tables = [n for n, f in storage.items()
                     if any(m.lower() == "import" for m in f.get("modes") or [])]
    if not import_tables:
        return not_applicable("Model has no Import tables — incremental refresh does not apply")

    policies = model.get("refresh_policies") or []
    covered_tables = {p.get("table") for p in policies if p.get("table")}
    if covered_tables:
        return binary(True, f"Incremental refresh policy on "
                            f"{len(covered_tables & set(import_tables))} of "
                            f"{len(import_tables)} Import table(s): "
                            f"{', '.join(sorted(covered_tables))}")
    if len(import_tables) < 3:
        return not_applicable(f"Only {len(import_tables)} Import table(s) — too small for "
                              f"incremental refresh to be warranted")
    return binary(False, f"{len(import_tables)} Import table(s) and no incremental refresh "
                         f"policy — every refresh reloads the full history")
