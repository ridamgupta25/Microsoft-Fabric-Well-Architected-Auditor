"""Security · Reporting / Semantic — automated checks for semantic models."""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-RLS-RPT", ref="6.2.7", title="Row-Level Security (RLS) defined on reporting semantic models",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def rls_on_reporting_models(ctx: CheckContext) -> Verdict:
    """Semantic models define RLS roles with table-level filter expressions."""
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable("No semantic models in this workspace")
    with_rls = [name for name, defn in models.items()
                if any(r.get("table_permissions") for r in defn.get("roles", []))]
    return covered(len(with_rls), len(models),
                   f"{len(with_rls)} of {len(models)} semantic models define RLS roles")


@check(
    id="WS-OLS-RPT", ref="6.2.8", title="Object-Level Security (OLS) applied on reporting semantic models",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=[Layer.REPORTING], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_reporting_models(ctx: CheckContext) -> Verdict:
    """Semantic models restrict sensitive columns via OLS column permissions."""
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable("No semantic models in this workspace")
    with_ols = [
        name for name, defn in models.items()
        if any(
            cp
            for r in defn.get("roles", [])
            for tp in r.get("table_permissions", [])
            for cp in tp.get("column_permissions", [])
        )
    ]
    return covered(len(with_ols), len(models),
                   f"{len(with_ols)} of {len(models)} semantic models define OLS column permissions")
