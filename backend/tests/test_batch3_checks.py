"""Tests for the checks added for refs 14.1.2, 14.1.8, 12.3.3, 1.1.5,
4.4.1, 4.4.2, 12.2.1 and 12.2.7.

Every check here is a pure function of metadata the knowledge base already holds,
so each test builds the smallest synthetic context that exercises it. Each check
gets the three cases that matter: the input that must pass, the input that must
fail (or, for the two unscored ``note`` checks, the facts each must report), and
the N/A path — because "we could not read it" must never score as "it is
misconfigured".
"""
from __future__ import annotations

from auditfast.core.check.cost_resource_optimization.data_operations.automated import (
    capacity_metrics_app,
    cu_consumption_alerts,
    spark_pool_not_idle,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    naming_style,
    warehouse_naming_is_internally_consistent,
    warehouse_schema_organization,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    _ambiguous_pairs,
    _directed_filter_graph,
    key_columns_are_hidden,
    relationships_have_no_ambiguous_paths,
)
from auditfast.core.check.operations_reliability.data_operations.automated import (
    medallion_architecture,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

_PASS, _PARTIAL, _FAIL = 3, 1, 0


def _scored(outcome):
    """The scored aggregate verdict a multi-verdict check returns first."""
    return outcome[0] if isinstance(outcome, list) else outcome


def _details(outcome) -> list:
    return outcome[1:] if isinstance(outcome, list) else []


def _ctx(**fields) -> CheckContext:
    workspace = WorkspaceContext(id="w", display_name="w", **fields)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


def _model_ctx(models: dict, **fields) -> CheckContext:
    return _ctx(semantic_models=models, **fields)


def _rel(from_table: str, to_table: str, *, active: bool = True) -> dict:
    return {
        "name": f"{from_table}-{to_table}",
        "from_table": from_table, "from_column": "k",
        "to_table": to_table, "to_column": "k",
        "cross_filter": "oneDirection", "is_active": active,
    }


def _column(table: str, name: str, **flags) -> dict:
    return {
        "table": table, "name": name, "data_type": "string",
        "source_provider_type": "", "source_column": name,
        "is_hidden": flags.get("is_hidden", False),
        "is_key": flags.get("is_key", False),
    }


def _wh_table(*cols: str, store: str = "WH", kind: str = "Warehouse") -> dict:
    return {
        "type": "Managed", "format": "",
        "store": store, "store_kind": kind,
        "columns": [{"name": c, "type": "varchar(50)"} for c in cols],
    }


# =============================================================================
# 14.1.2 — ambiguous filter paths
# =============================================================================

def test_redundant_pairs_finds_a_cycle_and_a_duplicate_edge():
    """The directed detector names the pair a second filter route reaches."""
    # A plain chain dim -> fact -> (nothing) has no second route.
    adjacency = {"date": {"sales"}, "customer": {"sales"}}
    assert _ambiguous_pairs(adjacency, set()) == []
    # A snowflake short-cut (date reaches sales directly and via product) is a diamond.
    diamond = {"date": {"sales", "product"}, "product": {"sales"}}
    assert _ambiguous_pairs(diamond, set()) == [("date", "sales")]
    # Two active relationships between one pair is ambiguous outright.
    assert _ambiguous_pairs({}, {("a", "b")}) == [("a", "b")]


def test_a_galaxy_schema_of_shared_dimensions_is_not_ambiguous():
    """Several facts sharing two dimensions form an undirected cycle but no diamond."""
    models = {
        "m": {"relationships": [
            _rel("BudgetFact", "Date"), _rel("BudgetFact", "Site"),
            _rel("SalesFact", "Date"), _rel("SalesFact", "Site"),
        ]},
    }
    verdict = _scored(relationships_have_no_ambiguous_paths(_model_ctx(models)))
    assert verdict.score == _PASS
    assert "1 of 1" in verdict.evidence
    # The directed graph puts every edge dimension -> fact, so facts are sinks.
    adjacency, duplicates, usable = _directed_filter_graph(models["m"])
    assert usable == 4
    assert _ambiguous_pairs(adjacency, duplicates) == []


def test_ambiguous_paths_pass_when_the_relationship_graph_is_a_tree():
    models = {"m": {"relationships": [_rel("Sales", "Date"), _rel("Sales", "Customer")]}}
    verdict = _scored(relationships_have_no_ambiguous_paths(_model_ctx(models)))
    assert verdict.score == _PASS
    assert "1 of 1" in verdict.evidence
    assert "no direct many-to-many relationship" in verdict.evidence


def _m2m_rel(from_table: str, to_table: str, *, active: bool = True) -> dict:
    """A relationship declared many-to-many on both ends (no bridge)."""
    return {
        "name": f"{from_table}-{to_table}",
        "from_table": from_table, "from_column": "k",
        "to_table": to_table, "to_column": "k",
        "cross_filter": "bothDirections", "is_active": active,
        "from_cardinality": "many", "to_cardinality": "many",
    }


def test_direct_many_to_many_relationship_is_flagged():
    """A relationship declared many/many on both ends has no bridge - a defect."""
    models = {"m": {"relationships": [_m2m_rel("Sales", "Account")]}}
    outcome = relationships_have_no_ambiguous_paths(_model_ctx(models))
    assert _scored(outcome).score == _FAIL
    detail = _details(outcome)[0]
    assert detail.obj == "m"
    assert "direct many-to-many" in detail.evidence
    assert "account <-> sales" in detail.evidence


def test_standard_many_to_one_relationship_is_not_flagged_as_many_to_many():
    """A defaulted (cardinality-omitted) relationship must not read as many-to-many."""
    models = {"m": {"relationships": [_rel("Sales", "Date"), _rel("Sales", "Customer")]}}
    verdict = _scored(relationships_have_no_ambiguous_paths(_model_ctx(models)))
    assert verdict.score == _PASS


def test_a_single_many_to_many_relationship_is_judged_not_skipped():
    """A lone many-to-many relationship is a defect even without a second path."""
    models = {"m": {"relationships": [_m2m_rel("A", "B")]}}
    verdict = _scored(relationships_have_no_ambiguous_paths(_model_ctx(models)))
    assert verdict.score == _FAIL
    assert "0 of 1" in verdict.evidence


def test_ambiguous_paths_fail_when_a_second_active_route_exists():
    models = {
        "m": {"relationships": [
            _rel("Sales", "Customer"), _rel("Customer", "Geography"),
            _rel("Sales", "Geography"),
        ]},
    }
    outcome = relationships_have_no_ambiguous_paths(_model_ctx(models))
    assert _scored(outcome).score == _FAIL
    # The offending pair is named on a detail row, not merely counted.
    detail = _details(outcome)[0]
    assert detail.obj == "m"
    assert "geography <-> sales" in detail.evidence


def test_inactive_relationships_are_reported_but_do_not_create_a_path():
    models = {
        "m": {"relationships": [
            _rel("Sales", "Customer"), _rel("Customer", "Geography"),
            _rel("Sales", "Geography", active=False),
        ]},
    }
    verdict = _scored(relationships_have_no_ambiguous_paths(_model_ctx(models)))
    assert verdict.score == _PASS
    assert "1 inactive relationship(s)" in verdict.evidence


def test_ambiguous_paths_are_na_without_definitions_or_relationships():
    unreadable = _model_ctx({}, unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS})
    assert _scored(relationships_have_no_ambiguous_paths(unreadable)).status is Status.NA
    # One relationship cannot form a second path.
    single = _model_ctx({"m": {"relationships": [_rel("Sales", "Date")]}})
    assert _scored(relationships_have_no_ambiguous_paths(single)).status is Status.NA


# =============================================================================
# 14.1.8 — technical keys hidden from consumers
# =============================================================================

def test_hidden_keys_pass_when_every_key_column_is_hidden():
    models = {"m": {"columns": [
        _column("Sales", "OrderId", is_hidden=True),
        _column("Sales", "Amount"),
    ]}}
    verdict = _scored(key_columns_are_hidden(_model_ctx(models)))
    assert verdict.score == _PASS
    assert "1 of 1 key-shaped column(s)" in verdict.evidence
    assert "Display folders are not part of the parsed model definition" in verdict.evidence


def test_hidden_keys_fail_when_a_key_is_visible_to_report_authors():
    models = {"m": {"columns": [_column("Sales", "OrderId")]}}
    outcome = key_columns_are_hidden(_model_ctx(models))
    assert _scored(outcome).score == _FAIL
    assert "Sales[OrderId]" in _details(outcome)[0].evidence


def test_hidden_keys_are_na_when_the_model_declares_no_key_column():
    models = {"m": {"columns": [_column("Sales", "Amount")]}}
    assert _scored(key_columns_are_hidden(_model_ctx(models))).status is Status.NA
    unreadable = _model_ctx({}, unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS})
    assert _scored(key_columns_are_hidden(unreadable)).status is Status.NA


# =============================================================================
# 12.3.3 — Spark Environments not left billing while idle
# =============================================================================

def _environment(name: str, *, enabled: bool, minimum: int | None = 1) -> dict:
    return {
        "id": f"id-{name}", "display_name": name,
        "runtime_version": 1.3,
        "dynamic_executor_allocation": {
            "enabled": enabled, "min_executors": minimum, "max_executors": 9,
        },
    }


def test_spark_environment_passes_when_executors_scale_down():
    record = _environment("Env1", enabled=True, minimum=1)
    # The provider files each Environment under both its id and its name.
    ctx = _ctx(environments={"id-Env1": record, "Env1": record})
    verdict = spark_pool_not_idle(ctx)
    assert verdict.score == _PASS
    assert "1 of 1 Fabric Environment(s)" in verdict.evidence, "the id/name double entry was counted twice"
    assert "no idle-session timeout" in verdict.evidence


def test_spark_environment_fails_without_dynamic_allocation_or_with_a_high_floor():
    fixed = spark_pool_not_idle(_ctx(environments={"a": _environment("Fixed", enabled=False)}))
    assert fixed.score == _FAIL
    assert "does not enable dynamic executor allocation" in fixed.evidence

    floored = spark_pool_not_idle(
        _ctx(environments={"a": _environment("Floor", enabled=True, minimum=8)})
    )
    assert floored.score == _FAIL
    assert "holds a floor of 8 executors" in floored.evidence


def test_spark_environment_is_na_without_definitions_or_environments():
    unreadable = _ctx(unavailable={Resource.ENVIRONMENT_DEFINITIONS})
    assert spark_pool_not_idle(unreadable).status is Status.NA
    assert spark_pool_not_idle(_ctx(environments={})).status is Status.NA


# =============================================================================
# 1.1.5 — medallion architecture
# =============================================================================

def _store(name: str, item_type: str) -> Item:
    return Item(id=name, type=item_type, display_name=name)


def test_medallion_passes_when_all_three_tiers_sit_on_the_expected_store_type():
    ctx = _ctx(items=[
        _store("LH_Bronze_Raw", "Lakehouse"),
        _store("LH_Silver_Cleansed", "Lakehouse"),
        _store("WH_Gold_Mart", "Warehouse"),
    ])
    verdict = medallion_architecture(ctx)
    assert verdict.score == _PASS
    assert "Bronze -> Silver -> Gold" in verdict.evidence
    assert "Names are the only readable signal" in verdict.evidence


def test_medallion_is_partial_when_gold_is_a_lakehouse_rather_than_a_warehouse():
    ctx = _ctx(items=[
        _store("LH_Bronze", "Lakehouse"),
        _store("LH_Silver", "Lakehouse"),
        _store("LH_Gold", "Lakehouse"),
    ])
    verdict = medallion_architecture(ctx)
    assert verdict.score == 2
    assert "not a Warehouse" in verdict.evidence


def test_medallion_fails_when_no_name_declares_a_tier():
    ctx = _ctx(items=[_store("LH_One", "Lakehouse"), _store("WH_Two", "Warehouse")])
    verdict = medallion_architecture(ctx)
    assert verdict.score == _FAIL
    assert "names a medallion tier" in verdict.evidence


def test_medallion_partial_when_only_some_tiers_are_named_here():
    ctx = _ctx(items=[_store("LH_Bronze", "Lakehouse"), _store("LH_Silver", "Lakehouse")])
    verdict = medallion_architecture(ctx)
    assert verdict.score == 2
    assert "Gold" in verdict.evidence


def test_medallion_is_na_without_items_or_data_stores():
    assert medallion_architecture(_ctx(unavailable={Resource.ITEMS})).status is Status.NA
    only_notebooks = _ctx(items=[Item(id="nb", type="Notebook", display_name="NB_Gold")])
    assert medallion_architecture(only_notebooks).status is Status.NA


# =============================================================================
# 4.4.1 — Warehouse schema organization
# =============================================================================

def test_warehouse_schemas_pass_with_domain_schemas_plus_a_staging_schema():
    ctx = _ctx(tables={
        "sales.fact_orders": _wh_table("order_id"),
        "finance.dim_account": _wh_table("account_id"),
        "stg.orders_landing": _wh_table("order_id"),
    })
    verdict = warehouse_schema_organization(ctx)
    assert verdict.score == _PASS
    assert "stg (1 table(s))" in verdict.evidence


def test_warehouse_schemas_fail_when_everything_sits_in_dbo():
    ctx = _ctx(tables={
        "dbo.fact_orders": _wh_table("order_id"),
        "dbo.dim_account": _wh_table("account_id"),
    })
    assert warehouse_schema_organization(ctx).score == _FAIL


def test_warehouse_schemas_are_partial_without_a_staging_schema():
    ctx = _ctx(tables={
        "sales.fact_orders": _wh_table("order_id"),
        "finance.dim_account": _wh_table("account_id"),
    })
    assert warehouse_schema_organization(ctx).score == 2


def test_warehouse_schemas_exclude_fabric_system_schemas():
    ctx = _ctx(tables={
        "mtd.fact_orders": _wh_table("order_id", store="WH_Gold"),
        "mtd.dim_account": _wh_table("account_id", store="WH_Gold"),
        "sys.managed_delta_tables": _wh_table("name", store="WH_Gold"),
        "sys.external_delta_tables": _wh_table("name", store="WH_Gold"),
        "INFORMATION_SCHEMA.COLUMNS": _wh_table("TABLE_NAME", store="WH_Gold"),
    })
    verdict = warehouse_schema_organization(ctx)

    assert verdict.score == 1
    assert "mtd (2 table(s))" in verdict.evidence
    assert "sys (" not in verdict.evidence.lower()
    assert "information_schema (" not in verdict.evidence.lower()
    assert "Excluded 3 Fabric system-schema table(s)" in verdict.evidence


def test_warehouse_schemas_are_na_when_only_fabric_system_schemas_exist():
    ctx = _ctx(tables={
        "sys.managed_delta_tables": _wh_table("name", store="WH_Gold"),
        "INFORMATION_SCHEMA.COLUMNS": _wh_table("TABLE_NAME", store="WH_Gold"),
    })
    verdict = warehouse_schema_organization(ctx)

    assert verdict.status is Status.NA
    assert "belong to Fabric system schemas" in verdict.evidence


def test_warehouse_schemas_are_na_when_no_schema_qualifier_was_read():
    """The SQL reader records TABLE_NAME without TABLE_SCHEMA — unknown, not a defect."""
    ctx = _ctx(tables={"fact_orders": _wh_table("order_id")})
    verdict = warehouse_schema_organization(ctx)
    assert verdict.status is Status.NA
    assert "TABLE_SCHEMA" in verdict.evidence


def test_warehouse_schemas_are_na_without_warehouse_tables():
    assert warehouse_schema_organization(_ctx(tables={})).status is Status.NA
    lakehouse_only = _ctx(tables={"t": _wh_table("c", store="LH", kind="Lakehouse")})
    assert warehouse_schema_organization(lakehouse_only).status is Status.NA


def test_the_store_prefix_of_a_colliding_table_key_is_not_read_as_a_schema():
    """``<store>.<table>`` is how a name collision is filed — not a schema."""
    ctx = _ctx(tables={"WH.fact_orders": _wh_table("order_id", store="WH")})
    assert warehouse_schema_organization(ctx).status is Status.NA


# =============================================================================
# 4.4.2 — Warehouse naming consistency (any one convention)
# =============================================================================

def test_naming_style_classifies_each_convention():
    assert naming_style("fact_orders") == "snake_case"
    assert naming_style("FactOrders") == "PascalCase"
    assert naming_style("factOrders") == "camelCase"
    assert naming_style("FACT_ORDERS") == "UPPER_CASE"
    assert naming_style("Order Header") == "mixed"
    assert naming_style("Customer_ID") == "mixed"


def test_warehouse_naming_passes_on_a_consistent_non_snake_convention():
    """PascalCase throughout is consistent — this check mandates no one convention."""
    ctx = _ctx(tables={
        "FactOrders": _wh_table("OrderId", "OrderDate"),
        "DimCustomer": _wh_table("CustomerId"),
    })
    verdict = warehouse_naming_is_internally_consistent(ctx)
    assert verdict.score == _PASS
    assert "PascalCase" in verdict.evidence


def test_warehouse_naming_fails_on_a_mixture_of_conventions():
    ctx = _ctx(tables={
        "FactOrders": _wh_table("OrderId"),
        "dim_customer": _wh_table("customer_id"),
        "Order Header": _wh_table("Order Date"),
    })
    assert warehouse_naming_is_internally_consistent(ctx).score == _FAIL


def test_warehouse_naming_is_na_without_warehouse_tables():
    assert warehouse_naming_is_internally_consistent(_ctx(tables={})).status is Status.NA
    lakehouse_only = _ctx(tables={"t": _wh_table("c", store="LH", kind="Lakehouse")})
    assert warehouse_naming_is_internally_consistent(lakehouse_only).status is Status.NA


def test_warehouse_naming_says_so_when_no_column_metadata_was_read():
    ctx = _ctx(tables={"FactOrders": _wh_table()})
    verdict = warehouse_naming_is_internally_consistent(ctx)
    assert "no column metadata was read" in verdict.evidence


# =============================================================================
# 12.2.1 — Capacity Metrics App (unscored by design)
# =============================================================================

def test_capacity_metrics_app_reports_an_install_without_scoring_it():
    ctx = _ctx(items=[
        Item(id="sm", type="SemanticModel", display_name="Fabric Capacity Metrics"),
    ])
    verdict = capacity_metrics_app(ctx)
    assert verdict.status is Status.INFO and verdict.score is None
    assert "installed in this workspace" in verdict.evidence


def test_capacity_metrics_absence_is_reported_as_inconclusive_not_as_a_failure():
    verdict = capacity_metrics_app(_ctx(items=[Item(id="a", type="Notebook", display_name="NB")]))
    assert verdict.status is Status.INFO and verdict.score is None
    assert "not evidence the tenant lacks it" in verdict.evidence


def test_capacity_metrics_is_na_without_items():
    assert capacity_metrics_app(_ctx(unavailable={Resource.ITEMS})).status is Status.NA


# =============================================================================
# 12.2.7 — CU consumption alerting
# =============================================================================

def test_cu_alerts_credit_an_activator_partially_and_never_fully():
    ctx = _ctx(capacity_id="CAP-1",
               items=[Item(id="rf", type="Reflex", display_name="AlertRule")])
    verdict = cu_consumption_alerts(ctx)
    assert verdict.score == 2, "presence of a Reflex must never award a full pass"
    assert "never award a full pass" in verdict.evidence


def test_cu_alerts_fail_on_a_capacity_with_no_activator():
    ctx = _ctx(capacity_id="CAP-1",
               items=[Item(id="pl", type="DataPipeline", display_name="PL")])
    assert cu_consumption_alerts(ctx).score == _FAIL


def test_cu_alerts_are_na_off_capacity_or_without_items():
    off_capacity = _ctx(items=[Item(id="rf", type="Reflex", display_name="AlertRule")])
    assert cu_consumption_alerts(off_capacity).status is Status.NA
    assert cu_consumption_alerts(_ctx(unavailable={Resource.ITEMS})).status is Status.NA


# =============================================================================
# registration + remediation
# =============================================================================

def test_every_new_ref_is_registered_once_and_has_remediation_text():
    from auditfast.core.check.registry import REGISTRY
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    book = load_remediation(load_project(PROJECT_FILE))
    new = {
        "14.1.2": "R-REL-AMBIGUOUS",
        "14.1.8": "R-MODEL-HIDDEN-KEYS",
        "12.3.3": "WS-SPARK-IDLE",
        "1.1.5": "WS-MEDALLION",
        "4.4.1": "TB-WH-SCHEMAS",
        "4.4.2": "TB-WH-NAME-CONSISTENCY",
        "12.2.1": "WS-CAPACITY-METRICS",
        "12.2.7": "WS-CU-ALERTS",
    }
    for ref, check_id in new.items():
        spec = REGISTRY.get(check_id)
        assert spec is not None, f"{check_id} is not registered"
        assert spec.ref == ref
        assert [s.id for s in REGISTRY if s.ref == ref] == [check_id], \
            f"ref {ref} is claimed by more than one check"
        assert book.get(ref), f"ref {ref} has no remediation text"
