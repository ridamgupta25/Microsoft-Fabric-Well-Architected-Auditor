"""Operations & Reliability · Reporting / Semantic — how BI content ships and refreshes.

Two concerns share this module:

* **Shipping** — the reporting layer is the one most often edited in place: a
  measure fixed in the Prod model, a visual moved on the Prod report, neither
  with a history or a way back. ``WS-BI-DEPLOY`` judges the mechanics that stop
  that, both readable from the workspace itself.
* **Refreshing** — whether the model refresh is sequenced behind the load that
  feeds it, and whether large Import tables refresh incrementally. Read from the
  parsed TMSL and the pipeline definitions the provider already fetches. Model
  metadata only — no rows are read from the model or the warehouse behind it.
"""
from __future__ import annotations

from auditfast.core.check._pipeline import walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: The BI content this point governs. ``Dashboard`` is deliberately excluded: a
#: classic Power BI dashboard is not a Git-supported Fabric item, so requiring it
#: to be source-controlled would fail workspaces for something Fabric cannot do.
BI_CONTENT_TYPES: frozenset[str] = frozenset({"SemanticModel", "Report", "PaginatedReport"})


@check(
    id="WS-BI-DEPLOY", ref="14.5.4",
    title="Semantic models and reports are source-controlled and deployed via pipeline (Dev → QA → Prod)",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.REPORTING,),
    requires=[Resource.WORKSPACE, Resource.ITEMS, Resource.GIT], required=True,
)
def bi_content_source_controlled_and_promoted(ctx: CheckContext) -> Verdict:
    """Reporting content has both a version history and a promotion path.

    The point asks for two distinct mechanics and credits them separately,
    because they solve different problems:

    * *source-controlled* — the workspace is Git-connected, so a model or report
      definition has a diff, an author, and a way back to yesterday;
    * *deployed via pipeline* — the workspace is assigned to a Fabric deployment
      pipeline, so content reaches the next tier by promotion rather than by
      being rebuilt or re-published by hand.

    Git alone scores higher than a deployment pipeline alone: a history without a
    promotion path still lets you recover, while promotion without a history
    moves content you cannot review or revert.

    N/A when the workspace holds no semantic model or report — there is no
    reporting content here to ship — or when items or the Git state could not be
    read.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    content = [i for i in ctx.workspace.items if i.type in BI_CONTENT_TYPES]
    if not content:
        return not_applicable("Workspace holds no semantic model, report, or paginated report")

    if not ctx.workspace.has(Resource.GIT):
        return not_applicable(
            f"{len(content)} reporting item(s) present, but the Git connection state "
            f"could not be read, so source control cannot be judged"
        )

    models = sum(1 for i in content if i.type == "SemanticModel")
    reports = len(content) - models
    inventory = (f"Workspace '{ctx.workspace.display_name or ctx.obj_name}' contains "
                 f"{models} semantic model(s) and {reports} report(s)")

    git = ctx.workspace.git_connected
    promoted = ctx.workspace.deployment_pipeline

    if git and promoted:
        return graded(3, f"{inventory}: workspace is Git-connected and assigned to a "
                         f"deployment pipeline — versioned and promoted Dev → QA → Prod")
    if git:
        return graded(2, f"{inventory}: workspace is Git-connected but assigned to no "
                         f"deployment pipeline — content is versioned, but promotion to "
                         f"the next tier is manual")
    if promoted:
        return graded(1, f"{inventory}: workspace is assigned to a deployment pipeline but "
                         f"not Git-connected — content is promoted without a version "
                         f"history to review or revert to")
    return graded(0, f"{inventory}: workspace is neither Git-connected nor assigned to a "
                     f"deployment pipeline — reporting content is edited in place with no "
                     f"history and no promotion path")


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


#: The Fabric item that watches for a condition and raises an alert.
_ALERT_ITEM_TYPES: frozenset[str] = frozenset({"Reflex"})


@check(
    id="SM-REFRESH-ALERT", ref="14.5.3",
    title="Refresh failures alert the owning team",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.REPORTING,),
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
              Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def sm_refresh_failure_alerts(ctx: CheckContext) -> Verdict:
    """A failed semantic-model refresh reaches a human rather than failing silently.

    **Why this is automated and not self-assessed.** The setting exists and is
    readable: a scheduled refresh carries ``notifyOption`` —
    ``MailOnFailure`` or ``NoNotification`` — on
    ``GET …/datasets/{id}/refreshSchedule``. That is an ordinary *delegated*
    Power BI Datasets read (``Dataset.Read.All``), the same shape and token
    audience as the refresh-history call this tool already makes, and it needs no
    tenant-admin scope. So the honest answer is to read it rather than ask.

    **What it can determine.** For every semantic model in the workspace with a
    configured refresh schedule, whether that schedule notifies on failure. Plus
    two workspace-level alternatives that alert just as well and cost no extra
    call: a Data Activator (Reflex) item, which is Fabric's own mechanism for
    watching an item and raising an alert; and a pipeline that refreshes a model
    and has an on-failure path out of that refresh activity.

    **What it cannot.** It cannot see *who* is notified — ``MailOnFailure`` mails
    the model's configured contacts, and the contact list is not in this payload,
    so "the owning team" is taken on trust once a notification exists. It cannot
    read a subscription, an Azure Monitor rule, or a third-party alerting
    integration outside Fabric. A Reflex item's *trigger conditions* are not
    fetched, so its presence is credited as a partial signal, never as proof this
    particular model is watched. Without a Power BI-audience token the schedules
    are unreadable and this reports **N/A, never FAIL** — an absent scope is not
    an absent alert.

    **Sibling.** ``PL-NOTIFY`` (ref 2.4.5) asks whether a *pipeline*
    failure notifies; this asks about the *model refresh*, which is a separate
    mechanism with its own setting and fails independently of any pipeline.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    models = [i for i in ctx.workspace.items if i.type == "SemanticModel"]
    if not models:
        return not_applicable("Workspace holds no semantic model, so no model refresh "
                              "here can fail")

    # A Reflex watches items and raises alerts; its trigger conditions are not
    # fetched, so it is corroborating evidence, never proof on its own.
    reflexes = [i.display_name or i.id for i in ctx.workspace.items
                if i.type in _ALERT_ITEM_TYPES]
    guarded_pipelines = _pipelines_with_guarded_refresh(ctx)

    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE):
        fallback = _describe_fallback(reflexes, guarded_pipelines)
        return not_applicable(
            f"{len(models)} semantic model(s) present, but the refresh schedule "
            f"configuration (notifyOption) could not be read — it needs a Power BI-audience "
            f"token, which this run did not have. Whether refresh failures alert anyone "
            f"cannot be determined{fallback}"
        )

    schedules = ctx.workspace.refresh_schedules or {}
    scheduled = {name: s for name, s in schedules.items() if s.get("enabled")}
    if not scheduled:
        # No model runs on its own schedule. The refresh is then driven by a
        # pipeline (or not at all), so the schedule's notifyOption is the wrong
        # place to look and its absence is not a finding.
        if guarded_pipelines:
            return binary(True, f"No semantic model here refreshes on its own schedule; the "
                                f"refresh is pipeline-driven and the refresh activity has an "
                                f"on-failure path in: {', '.join(sorted(guarded_pipelines))}")
        return not_applicable(
            f"{len(models)} semantic model(s) present but none has an enabled refresh "
            f"schedule ({len(schedules)} schedule(s) read), so there is no scheduled "
            f"refresh whose failure could be notified. A pipeline-driven refresh is "
            f"judged by SM-REFRESH-ORCHESTRATED (ref 14.5.1)"
        )

    alerting = sorted(n for n, s in scheduled.items() if s.get("notifies_on_failure"))
    silent = sorted(n for n in scheduled if n not in set(alerting))
    evidence = (
        f"{len(alerting)} of {len(scheduled)} scheduled semantic model refresh(es) notify "
        f"on failure (notifyOption)"
    )
    if silent:
        evidence += f" — silent: {', '.join(silent[:5])}"
    if not silent:
        return covered(len(alerting), len(scheduled), evidence + ". Who receives the mail is "
                                                      "not readable from this payload")

    fallback = _describe_fallback(reflexes, guarded_pipelines)
    if fallback:
        # Something in this workspace does alert, but it cannot be tied to the
        # silent models, so it lifts the verdict without clearing it.
        return graded(2, evidence + fallback + ". That alternative cannot be tied to the "
                                    "silent model(s), so it corroborates rather than clears them")
    return covered(len(alerting), len(scheduled),
                   evidence + ", and the workspace has neither a Data Activator (Reflex) item "
                              "nor a pipeline refresh with an on-failure path — a failed "
                              "refresh serves stale data with no signal")


def _describe_fallback(reflexes: list[str], guarded_pipelines: list[str]) -> str:
    """Phrase the workspace-level alerting alternatives, or ``""`` when there are none."""
    parts: list[str] = []
    if reflexes:
        parts.append(f"a Data Activator (Reflex) item ({', '.join(sorted(reflexes)[:3])}) "
                     f"is present, though its trigger conditions are not readable")
    if guarded_pipelines:
        parts.append(f"pipeline(s) {', '.join(sorted(guarded_pipelines))} refresh a model "
                     f"with an on-failure path")
    return f"; {', and '.join(parts)}" if parts else ""


def _pipelines_with_guarded_refresh(ctx: CheckContext) -> list[str]:
    """Pipelines whose semantic-model refresh activity has an on-failure path.

    A refresh activity with a ``Failed``/``Completed`` dependant is handled: the
    pipeline notices the failure and runs something because of it. Without
    pipeline definitions this is simply unknown, and returns nothing.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return []
    found: list[str] = []
    for name, defn in (ctx.workspace.pipelines or {}).items():
        acts = walk_activities(defn)
        refresh_names = {
            a.get("name") for a in acts
            if (a.get("type") or "") in _REFRESH_ACTIVITY_TYPES and a.get("name")
        }
        if not refresh_names:
            continue
        handled = any(
            dep.get("activity") in refresh_names
            and bool({"Failed", "Completed"} & set(dep.get("dependencyConditions") or []))
            for a in acts
            for dep in (a.get("dependsOn") or [])
        )
        if handled:
            found.append(name)
    return found


@check(
    id="SM-INCREMENTAL-REFRESH", ref="14.5.2",
    title="Incremental refresh configured for large Import models",
    pillar=Pillar.OPERATIONS, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=MODEL_LAYERS,
    requires=[Resource.SEMANTIC_MODEL_DEFINITIONS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE],
    required=False,
)
def sm_incremental_refresh(ctx: CheckContext) -> Verdict:
    """An Import model refreshes on a schedule; a Direct Lake model is N/A.

    Direct Lake tables have no refresh to schedule, so their absence of one is
    correct rather than a finding. An Import model with an enabled refresh
    schedule passes; one with no enabled schedule fails.
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
        return not_applicable("Model has no Import tables (Direct Lake / non-import) — "
                              "scheduled refresh does not apply")

    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE):
        return not_applicable(
            "Refresh schedule configuration could not be read — it needs a Power BI-audience "
            "token, which this run did not have, so whether the model refreshes on a schedule "
            "cannot be determined"
        )

    schedule = (ctx.workspace.refresh_schedules or {}).get(ctx.obj_name) or {}
    if schedule.get("enabled"):
        return binary(True, f"{len(import_tables)} Import table(s) and an enabled refresh "
                            f"schedule is configured")
    return binary(False, f"{len(import_tables)} Import table(s) and no enabled refresh "
                         f"schedule — the model does not refresh on its own schedule")
