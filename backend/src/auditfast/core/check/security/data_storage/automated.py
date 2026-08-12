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
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return [not_applicable("Semantic model definitions could not be read from Fabric")]
    models = ctx.workspace.semantic_models
    warehouses = [i.display_name for i in ctx.workspace.items if i.type == "Warehouse"]
    if not models and not warehouses:
        return [not_applicable("No semantic models or Warehouses in this workspace")]

    protected: list[str] = []
    failing: list[tuple[str, str]] = []
    for name, defn in models.items():
        filtering, defined = rls_roles(defn)
        if filtering:
            protected.append(name)
        elif defined:
            failing.append((name, f"Defines {defined} role(s) but none carries a filter expression"))
        else:
            failing.append((name, "Defines no RLS role"))

    # Warehouses: assessed only when the SQL analytics endpoint answered.
    security = ctx.workspace.warehouse_security or {}
    assessed_warehouses = 0
    for warehouse in warehouses:
        policies = security.get(warehouse)
        if policies is None:
            continue                      # unreadable - excluded, not failed
        assessed_warehouses += 1
        if any(p.get("enabled") for p in policies):
            protected.append(warehouse)
        else:
            failing.append((warehouse, "Warehouse defines no enabled security policy"))

    total = len(models) + assessed_warehouses
    if not total:
        return [not_applicable(
            "No semantic models, and Warehouse security policies could not be read "
            "from the SQL analytics endpoint"
        )]
    unread = len(warehouses) - assessed_warehouses
    caveat = (f"; {unread} Warehouse(s) were not assessed - the SQL analytics "
              f"endpoint could not be read") if unread else ""
    scope_note = (f"{len(models)} semantic model(s) and {assessed_warehouses} Warehouse(s)"
                  if assessed_warehouses else f"{len(models)} semantic model(s)")
    verdicts = [covered(
        len(protected), total,
        f"{len(protected)} of {total} objects define row-level security "
        f"({scope_note}){caveat}",
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
