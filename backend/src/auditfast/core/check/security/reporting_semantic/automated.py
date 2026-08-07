"""Security · Reporting / Semantic — automated checks for semantic models."""
from __future__ import annotations

from auditfast.core.check._semantic import restricts_objects, rls_roles
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-RLS-RPT", ref="14.4.1", title="Row-Level Security (RLS) defined on reporting semantic models",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def rls_on_reporting_models(ctx: CheckContext) -> Verdict:
    """Every role on a reporting semantic model carries an RLS filter expression."""
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable("No semantic models in this workspace")

    # "per role": a role with no filter expression restricts nobody, so a model is
    # only covered when every role it defines actually filters.
    complete = 0
    partial = 0
    for defn in models.values():
        filtering, defined = rls_roles(defn)
        if not defined:
            continue
        if filtering == defined:
            complete += 1
        else:
            partial += 1
    gap = (f"; {partial} more define a role that carries no filter expression"
           if partial else "")
    return covered(complete, len(models),
                   f"{complete} of {len(models)} semantic models filter on every role "
                   f"they define{gap}")


@check(
    id="WS-OLS-RPT", ref="14.4.3", title="Object-Level Security (OLS) applied on reporting semantic models",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_reporting_models(ctx: CheckContext) -> Verdict:
    """A role denies a column or a whole table, so object-level security is in force.

    Which audiences a field must be hidden from is a business judgement, so this
    reports whether the control exists — not who it excludes.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable("No semantic models in this workspace")
    with_ols = [name for name, defn in models.items() if restricts_objects(defn)]
    return covered(len(with_ols), len(models),
                   f"{len(with_ols)} of {len(models)} semantic models apply an object-level "
                   f"security permission that hides a column or a whole table; which audiences "
                   f"must be excluded is a business judgement this check does not make")
