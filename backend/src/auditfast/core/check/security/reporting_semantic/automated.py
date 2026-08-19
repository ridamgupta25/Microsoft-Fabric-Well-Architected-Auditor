"""Security · Reporting / Semantic — automated checks for semantic models."""
from __future__ import annotations

from auditfast.core.check._semantic import restricts_objects, rls_roles
from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-RLS-RPT", ref="14.4.1", title="RLS defined on semantic models and tested per role",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def rls_on_reporting_models(ctx: CheckContext) -> list[Verdict]:
    """One PASS/FAIL/N/A per model: every role it defines must carry an RLS filter.

    A role with no filter expression restricts nobody, so a model passes only
    when every role it defines actually filters. A model defining no RLS role at
    all is reported as N/A — there is nothing to filter per role.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable("No semantic models in this workspace")]

    verdicts: list[Verdict] = []
    for name, defn in sorted(models.items()):
        filtering, defined = rls_roles(defn)
        if not defined:
            verdicts.append(not_applicable(
                "Defines no RLS role, so there is nothing to filter per role", obj=name))
        elif filtering == defined:
            verdicts.append(binary(
                True,
                f"All {defined} defined role(s) carry an RLS filter expression",
                obj=name))
        else:
            verdicts.append(binary(
                False,
                f"Defines {defined} role(s) but only {filtering} carry a filter expression",
                obj=name))
    return verdicts


@check(
    id="WS-OLS-RPT", ref="14.4.3", title="Object-Level Security applied where fields must be hidden from some audiences",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_reporting_models(ctx: CheckContext) -> list[Verdict]:
    """A role denies a column or a whole table, so object-level security is in force.

    Which audiences a field must be hidden from is a business judgement, so this
    reports whether the control exists — not who it excludes. The scored workspace
    verdict is followed by one unscored detail row per model hiding nothing.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable("No semantic models in this workspace")]
    with_ols = [name for name, defn in models.items() if restricts_objects(defn)]
    failing = [name for name in models if name not in set(with_ols)]
    verdicts = [covered(
        len(with_ols), len(models),
        f"{len(with_ols)} of {len(models)} semantic models apply an object-level "
        f"security permission that hides a column or a whole table; which audiences "
        f"must be excluded is a business judgement this check does not make",
    )]
    verdicts += [
        note("No role hides a column or a table", obj=name)
        for name in sorted(failing)
    ]
    return verdicts
