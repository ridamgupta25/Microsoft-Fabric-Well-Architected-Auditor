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
    id="WS-LABELS", ref="IMPL-04", title="Sensitivity labels applied across Fabric items",
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
    id="WS-RLS", ref="6.2.1", title="Row-Level Security (RLS) implemented on the Gold Warehouse and/or semantic models where required",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.STORAGE],
    requires=[Resource.SEMANTIC_MODEL_DEFINITIONS, Resource.ITEMS,
              Resource.WAREHOUSE_SECURITY],
    required=True,
)
def rls_on_semantic_models(ctx: CheckContext) -> list[Verdict]:
    """Row-level security restricts data access on semantic models and Warehouses.

    The checklist point names both surfaces. Semantic-model RLS comes from the
    TMSL roles; Warehouse RLS is a T-SQL object (``sys.security_policies``) read
    over the SQL analytics endpoint. When the SQL endpoint could not be reached the
    Warehouses are reported as unassessed rather than counted as failing - "we
    could not look" is not "not configured".

    Returns the scored workspace verdict followed by one unscored detail row per
    object without RLS, so the report names *which* ones fail, not just how many.
    """
    models_available = ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS)
    items_available = ctx.workspace.has(Resource.ITEMS)
    security_available = ctx.workspace.has(Resource.WAREHOUSE_SECURITY)
    models = ctx.workspace.semantic_models if models_available else {}
    warehouses = (
        [i.display_name for i in ctx.workspace.items if i.type == "Warehouse"]
        if items_available else []
    )
    if not models and not warehouses:
        unavailable = []
        if not models_available:
            unavailable.append("semantic model definitions")
        if not items_available:
            unavailable.append("workspace item inventory")
        if unavailable:
            return [not_applicable(
                f"RLS could not be assessed because {', '.join(unavailable)} could not "
                "be read from Fabric"
            )]
        return [not_applicable("No semantic models or Warehouses in this workspace")]

    protected_models: list[str] = []
    failing_models: list[tuple[str, str]] = []
    for name, defn in sorted(models.items()):
        filtering, defined = rls_roles(defn)
        if filtering:
            protected_models.append(name)
        elif defined:
            failing_models.append((name, f"Defines {defined} role(s) but none carries a filter expression"))
        else:
            failing_models.append((name, "Defines no RLS role"))

    # Warehouses: assessed only when the SQL analytics endpoint answered.
    security = ctx.workspace.warehouse_security or {}
    protected_warehouses: list[str] = []
    failing_warehouses: list[tuple[str, str]] = []
    unread_warehouses: list[str] = []
    for warehouse in sorted(warehouses):
        policies = security.get(warehouse) if security_available else None
        if policies is None:
            unread_warehouses.append(warehouse)
            continue
        if any(p.get("enabled") for p in policies):
            protected_warehouses.append(warehouse)
        else:
            failing_warehouses.append((warehouse, "Defines no enabled security policy"))

    assessed_warehouses = len(protected_warehouses) + len(failing_warehouses)
    total = len(models) + assessed_warehouses
    if not total:
        return [not_applicable(
            "No semantic models, and Warehouse security policies could not be read "
            "from the SQL analytics endpoint"
        )]

    def names(values: list[str]) -> str:
        return ", ".join(values) if values else "none"

    def issues(values: list[tuple[str, str]]) -> str:
        return "; ".join(f"{name}: {reason}" for name, reason in values) if values else "none"

    protected = len(protected_models) + len(protected_warehouses)
    not_protected = len(failing_models) + len(failing_warehouses)
    evidence = (
        f"Checked {total} objects: {len(models)} semantic model(s) and "
        f"{assessed_warehouses} Warehouse(s). RLS is enabled on {protected} object(s) "
        f"and not enabled on {not_protected} object(s). "
        f"RLS ENABLED - Semantic models: {names(protected_models)}. "
        f"RLS ENABLED - Warehouses: {names(protected_warehouses)}. "
        f"RLS NOT ENABLED - Semantic models: {issues(failing_models)}. "
        f"RLS NOT ENABLED - Warehouses: {issues(failing_warehouses)}. "
        f"NOT ASSESSED - Warehouses: {names(unread_warehouses)}. "
        f"NOT ASSESSED - Semantic model inventory: "
        f"{'none' if models_available else 'definitions unavailable'}. "
        f"NOT ASSESSED - Warehouse inventory: "
        f"{'none' if items_available else 'workspace items unavailable'}. "
        "A semantic model is marked enabled when at least one defined role contains a "
        "row-filter expression. A Warehouse is marked enabled when it has an enabled "
        "SQL security policy. Reports are not listed separately because they inherit "
        "RLS from their backing semantic model or Warehouse."
    )
    verdicts = [covered(
        protected, total, evidence,
    )]
    verdicts += [
        note(reason, obj=f"Semantic model: {name}")
        for name, reason in failing_models
    ]
    verdicts += [
        note(reason, obj=f"Warehouse: {name}")
        for name, reason in failing_warehouses
    ]
    verdicts += [
        not_applicable(
            "Security policies could not be read from the SQL analytics endpoint",
            obj=f"Warehouse: {name}",
        )
        for name in unread_warehouses
    ]
    if not models_available:
        verdicts.append(not_applicable(
            "Semantic model definitions could not be read from Fabric",
            obj="Semantic model inventory",
        ))
    if not items_available:
        verdicts.append(not_applicable(
            "Workspace items could not be read, so Warehouses could not be enumerated",
            obj="Warehouse inventory",
        ))
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
    title="Dynamic Data Masking applied in the Warehouse for sensitive columns where appropriate",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
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
