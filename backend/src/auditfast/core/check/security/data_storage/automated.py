"""Security · Data Storage — how the data at rest is classified and protected."""
from __future__ import annotations

import re

from auditfast.core.check._semantic import hidden_columns, rls_roles
from auditfast.core.check._tables import TABLE_LAYERS, columns, in_warehouse
from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-LABELS", ref="13.2.3", title="Sensitivity labels applied across Fabric items",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def sensitivity_labels(ctx: CheckContext) -> Verdict:
    """Every item carries a sensitivity label, especially those holding PII.

    **What it can determine.** The Fabric Core items API documents a
    ``sensitivityLabel`` on each item and returns it to any caller with a viewer
    workspace role, so this needs no admin access.

    **What it cannot determine - and why a bare zero is not reported.** When the
    response carries a label for *no* item at all, two very different situations
    look identical from here: nothing in the workspace is labelled, or labels
    were not returned to this caller (the tenant does not use MIP labelling, the
    information-protection tenant setting is off, or the sign-in did not surface
    them). On a real 1,076-item workspace every item came back with no label,
    which the old ratio scored as ``0 of 1076`` - a confident FAIL built on
    unreadable data. "We could not determine this" is not "this is
    misconfigured", so that case is now N/A.

    Once *any* item carries a label, labelling is demonstrably in use here and
    the ratio becomes meaningful - so the unlabelled remainder is scored.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    items = ctx.workspace.items
    if not items:
        return not_applicable("Workspace holds no items to label")
    labeled = [i for i in items if i.sensitivity_label]
    if not labeled:
        return not_applicable(
            f"No sensitivity label was returned for any of the {len(items)} item(s). "
            "That reads the same whether nothing is labelled or labels are not "
            "exposed to this sign-in (information-protection settings), so labelling "
            "is reported as unassessed rather than failed - confirm in the Fabric portal"
        )
    return covered(
        len(labeled), len(items),
        f"{len(labeled)} of {len(items)} items carry a sensitivity label",
    )


@check(
    id="WS-RLS", ref="6.2.1", title="Row-Level Security (RLS) implemented on the Gold Warehouse and/or semantic models where required",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
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

    # Semantic models are read one definition at a time, so the crawl can return
    # *some* of them. Those that could not be read are unknown, never failing -
    # the same rule the Warehouse branch below already applied.
    model_stat = ctx.workspace.read_failures.get(Resource.SEMANTIC_MODEL_DEFINITIONS.value, {})
    unread_models = max(0, int(model_stat.get("attempted", 0)) - int(model_stat.get("read", 0)))

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
        f"{'definitions unavailable' if not models_available else (f'{unread_models} definition(s) could not be read' if unread_models else 'none')}. "
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
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    layers=[Layer.STORAGE], requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def ols_on_semantic_models(ctx: CheckContext) -> list[Verdict]:
    """One PASS/FAIL per model: a column permission must deny at least one column.

    Which fields count as sensitive is a business classification, so this reports
    whether the control exists — not whether it covers the right columns. Each
    model is reported as its own scored result: it passes when it denies at least
    one column and fails when it denies none.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    if not models:
        return [not_applicable("No semantic models in this workspace")]

    verdicts: list[Verdict] = []
    for name, defn in sorted(models.items()):
        if hidden_columns(defn):
            verdicts.append(binary(
                True,
                "Denies at least one column through a column-level security permission; "
                "which fields need protecting is a business judgement this check does not make",
                obj=name,
            ))
        else:
            verdicts.append(binary(
                False,
                "No column-level security permission denies any column",
                obj=name,
            ))
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

#: N/A reason when no lakehouse/warehouse table was read at all.
_NO_TABLES_READ = "No lakehouse/warehouse tables were read for this workspace"

#: How many column names to list in evidence before summarising.
_SAMPLE_LIMIT = 8


@check(
    id="WS-DDM", ref="6.2.3",
    title="Dynamic Data Masking applied in the Warehouse for sensitive columns where appropriate",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def dynamic_data_masking(ctx: CheckContext) -> Verdict:
    """Warehouse tables apply Dynamic Data Masking on columns that hold sensitive data.

    **What it can determine.** ``sys.columns.is_masked``, read over the SQL
    analytics endpoint alongside the column schema, records whether DDM is
    applied to a column. Sensitivity is inferred from the column *name*
    (``email``, ``ssn``, ``phone``), so the population is a name guess - a
    sensitive column named ``col_17`` is invisible here, and the evidence says so.

    **Masking is a Warehouse feature.** Lakehouse Delta tables are surfaced
    read-only through the SQL analytics endpoint and are not the place DDM is
    applied, so only Warehouse-owned tables are judged. When none of the tables
    read belong to a Warehouse the answer is N/A, not a finding.
    """
    if not ctx.workspace.has(Resource.TABLE_SCHEMAS):
        return not_applicable(_NO_TABLES_READ)
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES_READ)
    if not ctx.workspace.has(Resource.TABLE_COLUMNS):
        return not_applicable(
            "Column metadata could not be read over the SQL analytics endpoint, "
            "so masking cannot be assessed"
        )

    warehouse_tables = {n: t for n, t in tables.items() if in_warehouse(t)}
    if not warehouse_tables:
        return not_applicable(
            f"No table among the {len(tables)} read is known to live in a Warehouse. "
            "Dynamic Data Masking is a Warehouse feature, so there is nothing to assess"
        )

    sensitive_cols: list[str] = []
    masked_cols: list[str] = []
    for table_name, table in warehouse_tables.items():
        for col in columns(table):
            col_name = (col.get("name") or "").lower()
            if not _SENSITIVE_PATTERNS.search(col_name):
                continue
            qualified = f"{table_name}.{col.get('name', '')}"
            sensitive_cols.append(qualified)
            if col.get("is_masked") or col.get("masking_function") or col.get("data_mask"):
                masked_cols.append(qualified)

    if not sensitive_cols:
        if not any(columns(t) for t in warehouse_tables.values()):
            return not_applicable(
                f"No column metadata available for the {len(warehouse_tables)} "
                f"Warehouse table(s) read"
            )
        return not_applicable(
            f"No sensitive-looking column names found across "
            f"{len(warehouse_tables)} Warehouse table(s). Sensitivity is judged "
            f"from the column name, so a sensitive column named opaquely is not seen"
        )

    unmasked = sorted(set(sensitive_cols) - set(masked_cols))
    evidence = (f"{len(masked_cols)} of {len(sensitive_cols)} sensitive-looking "
                f"Warehouse column(s) have Dynamic Data Masking applied")
    if unmasked:
        evidence += f". Unmasked: {', '.join(unmasked[:_SAMPLE_LIMIT])}"
        if len(unmasked) > _SAMPLE_LIMIT:
            evidence += f", \u2026(+{len(unmasked) - _SAMPLE_LIMIT} more)"
    return covered(len(masked_cols), len(sensitive_cols), evidence)
