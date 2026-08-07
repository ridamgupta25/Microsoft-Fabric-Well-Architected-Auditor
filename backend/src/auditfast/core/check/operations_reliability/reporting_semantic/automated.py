"""Operations & Reliability · Reporting / Semantic — how BI content ships.

The reporting layer is the one most often edited in place: a measure is fixed in
the Prod semantic model, a visual is moved on the Prod report, and neither change
has a history or a path back. This module judges the two mechanics that stop
that, both readable from the workspace itself.
"""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, graded, not_applicable
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
    inventory = f"{models} semantic model(s) and {reports} report(s)"

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
