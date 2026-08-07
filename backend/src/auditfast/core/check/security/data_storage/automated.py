"""Security · Data Storage — how the data at rest is classified and protected."""
from __future__ import annotations

import re

from auditfast.core.check._semantic import hidden_columns, rls_roles
from auditfast.core.check._tables import TABLE_LAYERS, columns
from auditfast.core.check.helpers import Verdict, covered, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


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
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.STORAGE], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def rls_on_semantic_models(ctx: CheckContext) -> list[Verdict]:
    """Semantic models define RLS roles with table permissions to restrict data access.

    Returns the scored workspace verdict followed by one unscored detail row per
    model without RLS, so the report names *which* models fail, not just how many.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable("No semantic models in this workspace")]

    with_rls = []
    failing = []
    for name, defn in models.items():
        filtering, defined = rls_roles(defn)
        if filtering:
            with_rls.append(name)
        elif defined:
            failing.append((name, f"Defines {defined} role(s) but none carries a filter expression"))
        else:
            failing.append((name, "Defines no RLS role"))

    warehouses = sum(1 for i in ctx.workspace.items if i.type == "Warehouse")
    caveat = (f"; the {warehouses} Warehouse(s) were not assessed — Warehouse RLS policies "
              f"are not readable through the Fabric REST API") if warehouses else ""
    verdicts = [covered(
        len(with_rls), len(models),
        f"{len(with_rls)} of {len(models)} semantic models define RLS roles{caveat}",
    )]
    verdicts += [note(reason, obj=name) for name, reason in sorted(failing)]
    return verdicts


@check(
    id="WS-OLS", ref="6.2.2", title="Column-Level Security / Object-Level Security applied for sensitive fields",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.STORAGE], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_semantic_models(ctx: CheckContext) -> list[Verdict]:
    """A column permission denies a column, so column-level security is in force.

    Which fields count as sensitive is a business classification, so this reports
    whether the control exists — not whether it covers the right columns. The
    scored workspace verdict is followed by one unscored detail row per model
    applying no column-level security.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable("No semantic models in this workspace")]

    restricted = [name for name, defn in models.items() if hidden_columns(defn)]
    failing = [name for name in models if name not in set(restricted)]
    verdicts = [covered(
        len(restricted), len(models),
        f"{len(restricted)} of {len(models)} semantic models deny at least one column "
        f"through a column-level security permission; which fields need protecting is a "
        f"business judgement this check does not make",
    )]
    verdicts += [
        note("No column-level security permission denies any column", obj=name)
        for name in sorted(failing)
    ]
    return verdicts



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
        # Two very different reasons produce an empty list; say which one it was.
        if not any(columns(t) for t in tables.values()):
            return not_applicable(
                f"No column metadata available for the {len(tables)} table(s) read "
                f"— Fabric's table listing does not return columns"
            )
        return not_applicable(
            f"No sensitive-looking column names found across {len(tables)} table(s)"
        )

    return covered(
        len(masked_cols), len(sensitive_cols),
        f"{len(masked_cols)} of {len(sensitive_cols)} sensitive columns "
        f"have Dynamic Data Masking applied",
    )
