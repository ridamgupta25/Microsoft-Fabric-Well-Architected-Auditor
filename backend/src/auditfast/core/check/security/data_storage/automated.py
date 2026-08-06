"""Security · Data Storage — how the data at rest is classified and protected."""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

import re

from auditfast.core.check._tables import TABLE_LAYERS, columns


@check(
    id="WS-LABELS", ref="6.2.4", title="Sensitivity labels applied to items",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def sensitivity_labels(ctx: CheckContext) -> Verdict:
    """Every item carries a sensitivity label, especially those holding PII."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    items = ctx.workspace.items
    labeled = [i for i in items if i.sensitivity_label]
    return covered(
        len(labeled), len(items),
        f"{len(labeled)} of {len(items)} items carry a sensitivity label",
    )


@check(
    id="WS-RLS", ref="6.2.1", title="Row-Level Security (RLS) defined on semantic models",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=[Layer.STORAGE], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def rls_on_semantic_models(ctx: CheckContext) -> Verdict:
    """Semantic models define RLS roles with table permissions to restrict data access."""
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
    id="WS-OLS", ref="6.2.2", title="Column-Level Security / Object-Level Security applied for sensitive fields",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=[Layer.STORAGE], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_semantic_models(ctx: CheckContext) -> Verdict:
    """Semantic models restrict sensitive columns via OLS/CLS column permissions."""
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
                   f"{len(with_ols)} of {len(models)} semantic models define OLS/CLS column permissions")



# ---------------------------------------------------------------------------
# Dynamic Data Masking
# ---------------------------------------------------------------------------

#: Column name patterns that suggest sensitive/PII data warranting DDM.
_SENSITIVE_PATTERNS = re.compile(
    r"(email|phone|ssn|social_security|credit_card|card_number|account_number"
    r"|passport|salary|wage|compensation|dob|date_of_birth|birth_date"
    r"|national_id|tax_id|iban|bank_account|address|zip_code|postal_code)",
    re.IGNORECASE,
)


@check(
    id="WS-DDM", ref="6.2.3",
    title="Dynamic Data Masking applied in the Warehouse for sensitive columns",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def dynamic_data_masking(ctx: CheckContext) -> Verdict:
    """Warehouse tables apply Dynamic Data Masking on columns that hold sensitive data."""
    if not ctx.workspace.has(Resource.TABLE_SCHEMAS):
        return not_applicable(
            "No lakehouse/warehouse tables were read for this workspace"
        )
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(
            "No lakehouse/warehouse tables were read for this workspace"
        )

    sensitive_cols: list[str] = []
    masked_cols: list[str] = []

    for table_name, table in tables.items():
        for col in columns(table):
            col_name = (col.get("name") or "").lower()
            if not _SENSITIVE_PATTERNS.search(col_name):
                continue
            qualified = f"{table_name}.{col.get('name', '')}"
            sensitive_cols.append(qualified)
            # DDM metadata: provider exposes masking_function when a mask is defined.
            masking = col.get("masking_function") or col.get("data_mask")
            if masking:
                masked_cols.append(qualified)

    if not sensitive_cols:
        return not_applicable("No table column metadata available")

    return covered(
        len(masked_cols), len(sensitive_cols),
        f"{len(masked_cols)} of {len(sensitive_cols)} sensitive columns "
        f"have Dynamic Data Masking applied",
    )