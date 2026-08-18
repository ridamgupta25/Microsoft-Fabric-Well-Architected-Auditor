"""Tests for the store-aware and dimensional-purity checks.

Refs covered: 1.2.6, 1.2.8, 4.4.9, 4.5.3, 4.5.4, 4.5.8, 4.6.2, 4.6.8, 5.1.4, 10.3.4.

Every check here is a pure function of table/column metadata, so each test builds
a synthetic ``tables`` dict and asserts the verdict directly. Each check gets a
passing case, a failing case, and the N/A path — in particular the one that
matters most: a table whose owning store could not be read is *unknown*, and must
never be scored as a mismatch.
"""
from __future__ import annotations

import pytest

from auditfast.core.check._tables import (
    is_audit_table,
    is_audit_table_name,
    is_config_table_name,
    is_key_column,
    is_platform_table,
    key_referent,
    name_words,
    purpose_tokens,
)
from auditfast.core.check.data_management_quality.data_logs.automated import (
    audit_tables_are_queryable,
    config_lives_in_one_store,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    dq_scores_are_trended,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    audit_tables_separated,
    conformed_dimensions,
    dimensions_are_denormalized,
    fact_tables_have_no_descriptive_attributes,
    scd_strategy_per_dimension,
    warehouse_is_modeled,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    ingestion_volume_monitored,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, WorkspaceContext

_PASS, _PARTIAL, _FAIL = 3, 1, 0


def _table(*cols: tuple[str, str], store: str = "", kind: str = "") -> dict:
    """A table schema entry: columns plus the owning store the crawler recorded."""
    return {
        "type": "Managed", "format": "Delta",
        "store": store, "store_kind": kind,
        "columns": [{"name": name, "type": ctype} for name, ctype in cols],
    }


def _ctx(**tables: dict) -> CheckContext:
    workspace = WorkspaceContext(id="w", tables=tables)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


_EMPTY = _ctx()

# Reusable shapes.
_FACT = ("sales_sk", "bigint"), ("customer_sk", "bigint"), ("amount", "decimal(18,2)")
_DIM = ("customer_sk", "bigint"), ("customer_name", "varchar(100)")


# -- shared vocabulary helpers ------------------------------------------------

def test_name_words_splits_separators_and_camel_case():
    assert name_words("audit_log") == frozenset({"audit", "log"})
    assert name_words("AuditLog") == frozenset({"audit", "log"})
    assert name_words("DQ-Results") == frozenset({"dq", "results"})


def test_purpose_tokens_strips_container_tier_and_version_noise():
    """The same concept under three names must reduce to one purpose."""
    assert purpose_tokens("dim_customer") == ("customer",)
    assert purpose_tokens("DimCustomer_v2") == ("customer",)
    assert purpose_tokens("GOLD_dim_customer_PROD") == ("customer",)
    assert purpose_tokens("dim") == ()          # nothing but noise — not comparable


def test_is_key_column_does_not_match_words_that_merely_end_in_id():
    assert is_key_column("customer_sk") is True
    assert is_key_column("OrderID") is True
    assert is_key_column("valid") is False      # the classic false positive
    assert is_key_column("customer_name") is False


def test_key_referent_strips_the_key_word():
    assert key_referent("category_sk") == ("category",)
    assert key_referent("categoryid") == ("category",)
    assert key_referent("customer_name") == ()  # not a key at all


def test_is_audit_table_by_name_or_by_column_dominance():
    assert is_audit_table("audit_log", _table()) is True
    assert is_audit_table("dq_results", _table()) is True
    # Columns dominated by lineage metadata, whatever the name says.
    assert is_audit_table("run_history", _table(("batch_id", "bigint"),
                                                ("load_date", "timestamp"))) is True
    # Two lineage columns on a business table is not an audit table.
    assert is_audit_table("dim_customer", _table(("customer_sk", "bigint"),
                                                 ("customer_name", "varchar(50)"),
                                                 ("created_date", "timestamp"))) is False


def test_is_config_table_name():
    assert is_config_table_name("control_table") is True
    assert is_config_table_name("watermark_config") is True
    assert is_config_table_name("fact_sales") is False


# -- 1.2.6 · TB-WH-MODELED -----------------------------------------------------

def test_warehouse_modeled_passes_when_one_warehouse_holds_facts_and_dimensions():
    ctx = _ctx(
        fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
        dim_customer=_table(*_DIM, store="WH_Gold", kind="Warehouse"),
    )
    verdict = warehouse_is_modeled(ctx)
    assert verdict.score == _PASS
    assert "WH_Gold" in verdict.evidence


def test_warehouse_modeled_fails_when_the_warehouse_has_no_dimensions():
    ctx = _ctx(
        fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
        fact_returns=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
    )
    assert warehouse_is_modeled(ctx).score == _FAIL


def test_warehouse_modeled_is_na_when_no_table_is_known_to_be_in_a_warehouse():
    """A star split across a Lakehouse and an unknown store is not a modelled Warehouse."""
    ctx = _ctx(
        fact_sales=_table(*_FACT, store="LH_Gold", kind="Lakehouse"),
        dim_customer=_table(*_DIM),                       # store unreadable
    )
    assert warehouse_is_modeled(ctx).status is Status.NA


def test_warehouse_modeled_is_na_without_tables():
    assert warehouse_is_modeled(_EMPTY).status is Status.NA


# -- 1.2.8 · TB-AUDIT-SEPARATED ------------------------------------------------

def test_audit_tables_separated_passes_with_a_dedicated_audit_store():
    ctx = _ctx(
        audit_log=_table(("batch_id", "bigint"), ("load_date", "timestamp"),
                         store="LH_Audit", kind="Lakehouse"),
        dq_results=_table(("batch_id", "bigint"), ("dq_score", "decimal(5,2)"),
                          store="LH_Audit", kind="Lakehouse"),
        fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
        dim_customer=_table(*_DIM, store="WH_Gold", kind="Warehouse"),
    )
    verdict = audit_tables_separated(ctx)
    assert verdict.score == _PASS
    assert "LH_Audit" in verdict.evidence


def test_audit_tables_separated_fails_when_audit_sits_beside_business_data():
    ctx = _ctx(
        audit_log=_table(("batch_id", "bigint"), ("load_date", "timestamp"),
                         store="WH_Gold", kind="Warehouse"),
        fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
        dim_customer=_table(*_DIM, store="WH_Gold", kind="Warehouse"),
    )
    verdict = audit_tables_separated(ctx)
    assert verdict.score == _FAIL
    assert "mixed" in verdict.evidence


def test_audit_tables_separated_is_na_when_no_store_could_be_read():
    """An unreadable endpoint means unknown membership — never a mismatch."""
    ctx = _ctx(
        audit_log=_table(("batch_id", "bigint")),
        fact_sales=_table(*_FACT),
    )
    assert audit_tables_separated(ctx).status is Status.NA


def test_audit_tables_separated_is_na_without_any_audit_table():
    ctx = _ctx(fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"))
    assert audit_tables_separated(ctx).status is Status.NA


# -- 4.4.9 · TB-CONFORMED-DIM --------------------------------------------------

def test_conformed_dimensions_passes_when_each_concept_lives_in_one_store():
    ctx = _ctx(
        dim_customer=_table(*_DIM, store="WH_Sales", kind="Warehouse"),
        dim_product=_table(("product_sk", "bigint"), store="WH_Finance", kind="Warehouse"),
    )
    assert conformed_dimensions(ctx).score == _PASS


def test_conformed_dimensions_fails_when_the_same_dimension_is_copied_per_domain():
    """``dim_customer`` and ``DimCustomer_v2`` in two stores are one concept, twice."""
    ctx = _ctx(
        dim_customer=_table(*_DIM, store="WH_Sales", kind="Warehouse"),
        DimCustomer_v2=_table(*_DIM, store="WH_Finance", kind="Warehouse"),
    )
    verdict = conformed_dimensions(ctx)
    assert verdict.score == _FAIL
    assert "customer" in verdict.evidence


def test_conformed_dimensions_is_na_with_a_single_store():
    ctx = _ctx(dim_customer=_table(*_DIM, store="WH_Sales", kind="Warehouse"))
    assert conformed_dimensions(ctx).status is Status.NA


def test_conformed_dimensions_is_na_when_stores_are_unknown():
    ctx = _ctx(dim_customer=_table(*_DIM), dim_product=_table(("product_sk", "bigint")))
    assert conformed_dimensions(ctx).status is Status.NA


# -- 4.5.3 · TB-FACT-PURITY ----------------------------------------------------

def test_fact_purity_passes_with_only_keys_measures_and_audit_columns():
    ctx = _ctx(fact_sales=_table(("customer_sk", "bigint"), ("amount", "decimal(18,2)"),
                                 ("created_date", "timestamp")))
    assert fact_tables_have_no_descriptive_attributes(ctx).score == _PASS


def test_fact_purity_fails_on_a_descriptive_text_attribute():
    ctx = _ctx(fact_sales=_table(("customer_sk", "bigint"), ("amount", "decimal(18,2)"),
                                 ("customer_name", "varchar(100)")))
    verdict = fact_tables_have_no_descriptive_attributes(ctx)
    assert verdict.score == _FAIL
    assert "customer_name" in verdict.evidence


def test_fact_purity_is_na_when_no_fact_column_types_were_read():
    ctx = _ctx(fact_sales=_table(), dim_customer=_table(*_DIM))
    assert fact_tables_have_no_descriptive_attributes(ctx).status is Status.NA


# -- 4.5.4 · TB-DIM-DENORM -----------------------------------------------------

def test_dimensions_denormalized_passes_when_dimensions_are_flat():
    ctx = _ctx(
        dim_customer=_table(("customer_sk", "bigint"), ("customer_name", "varchar(50)")),
        dim_date=_table(("date_sk", "int"), ("full_date", "date")),
    )
    assert dimensions_are_denormalized(ctx).score == _PASS


def test_dimensions_denormalized_flags_a_snowflake_link_to_another_dimension():
    ctx = _ctx(
        dim_product=_table(("product_sk", "bigint"), ("category_sk", "bigint")),
        dim_category=_table(("category_sk", "bigint"), ("category_name", "varchar(50)")),
    )
    verdict = dimensions_are_denormalized(ctx)
    assert verdict.score == _PARTIAL          # 1 of 2 dimensions is flat
    assert "dim_product -> dim_category" in verdict.evidence


def test_dimensions_denormalized_is_na_without_dimensions():
    assert dimensions_are_denormalized(_ctx(fact_sales=_table(*_FACT))).status is Status.NA


# -- 4.5.8 · TB-SCD-STRATEGY ---------------------------------------------------

def test_scd_strategy_full_credit_when_every_dimension_declares_one():
    ctx = _ctx(
        dim_customer=_table(("customer_sk", "bigint"), ("valid_from", "timestamp"),
                            ("valid_to", "timestamp"), ("is_current", "boolean")),
        dim_product=_table(("product_sk", "bigint"), ("row_hash", "varchar(64)")),
    )
    assert scd_strategy_per_dimension(ctx).score == _PASS


def test_scd_strategy_partial_when_only_some_dimensions_declare_one():
    ctx = _ctx(
        dim_customer=_table(("customer_sk", "bigint"), ("valid_from", "timestamp")),
        dim_product=_table(("product_sk", "bigint"), ("product_name", "varchar(50)")),
    )
    assert scd_strategy_per_dimension(ctx).score == 2


def test_scd_strategy_is_never_a_hard_fail_for_plain_type_1_dimensions():
    """Type 1 is a legitimate strategy — it scores partial, never zero."""
    ctx = _ctx(dim_product=_table(("product_sk", "bigint"), ("product_name", "varchar(50)")))
    verdict = scd_strategy_per_dimension(ctx)
    assert verdict.score == _PARTIAL
    assert verdict.score > _FAIL


def test_scd_strategy_is_na_without_dimensions():
    assert scd_strategy_per_dimension(_ctx(fact_sales=_table(*_FACT))).status is Status.NA


# -- 4.6.2 · TB-CONFIG-SINGLE-STORE -------------------------------------------

def test_config_single_store_passes_when_all_config_is_in_one_store():
    ctx = _ctx(
        control_table=_table(("job_name", "varchar(50)"), store="LH_Meta", kind="Lakehouse"),
        watermark_config=_table(("table_name", "varchar(50)"), store="LH_Meta",
                                kind="Lakehouse"),
        fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"),
    )
    verdict = config_lives_in_one_store(ctx)
    assert verdict.score == _PASS
    assert "single configuration store" in verdict.evidence


def test_config_single_store_fails_when_config_is_spread_across_stores():
    ctx = _ctx(
        control_table=_table(("job_name", "varchar(50)"), store="LH_Meta", kind="Lakehouse"),
        watermark_config=_table(("table_name", "varchar(50)"), store="WH_Gold",
                                kind="Warehouse"),
        job_schedule=_table(("cron", "varchar(20)"), store="LH_Ops", kind="Lakehouse"),
    )
    verdict = config_lives_in_one_store(ctx)
    assert verdict.score == _FAIL
    assert "spread across 3 stores" in verdict.evidence


def test_config_single_store_is_na_without_config_tables():
    ctx = _ctx(fact_sales=_table(*_FACT, store="WH_Gold", kind="Warehouse"))
    assert config_lives_in_one_store(ctx).status is Status.NA


def test_config_single_store_is_na_when_stores_are_unknown():
    assert config_lives_in_one_store(_ctx(control_table=_table())).status is Status.NA


# -- 4.6.8 · TB-AUDIT-QUERYABLE ------------------------------------------------

def test_audit_queryable_passes_with_a_structured_schema():
    ctx = _ctx(audit_log=_table(("batch_id", "bigint"), ("run_timestamp", "timestamp"),
                                ("rows_written", "bigint"), ("status", "varchar(20)")))
    assert audit_tables_are_queryable(ctx).score == _PASS


def test_audit_queryable_fails_on_an_id_plus_blob_schema():
    ctx = _ctx(audit_log=_table(("log_id", "bigint"), ("payload", "json")))
    verdict = audit_tables_are_queryable(ctx)
    assert verdict.score == _FAIL
    assert "no timestamp column" in verdict.evidence


def test_audit_queryable_is_na_without_readable_audit_columns():
    ctx = _ctx(audit_log=_table(), fact_sales=_table(*_FACT))
    assert audit_tables_are_queryable(ctx).status is Status.NA


# -- 5.1.4 · TB-DQ-TREND -------------------------------------------------------

def test_dq_trend_passes_when_a_score_is_stored_with_a_timestamp():
    ctx = _ctx(dq_results=_table(("table_name", "varchar(50)"), ("dq_score", "decimal(5,2)"),
                                 ("run_date", "timestamp")))
    assert dq_scores_are_trended(ctx).score == _PASS


def test_dq_trend_is_partial_when_the_score_has_no_run_timestamp():
    """A score with nowhere to sit on a timeline is a reading, not a trend."""
    ctx = _ctx(dq_results=_table(("table_name", "varchar(50)"),
                                 ("dq_score", "decimal(5,2)")))
    verdict = dq_scores_are_trended(ctx)
    assert verdict.score == _PARTIAL
    assert "cannot be trended" in verdict.evidence


def test_dq_trend_fails_when_no_audit_table_carries_a_score():
    ctx = _ctx(audit_log=_table(("batch_id", "bigint"), ("load_date", "timestamp")))
    assert dq_scores_are_trended(ctx).score == _FAIL


def test_dq_trend_is_na_without_an_audit_table():
    ctx = _ctx(fact_sales=_table(*_FACT))
    assert dq_scores_are_trended(ctx).status is Status.NA


# -- 10.3.4 · WS-INGEST-VOLUME -------------------------------------------------

def test_ingestion_volume_passes_when_row_counts_are_stamped_with_a_run_time():
    ctx = _ctx(ingest_log=_table(("batch_id", "bigint"), ("row_count", "bigint"),
                                 ("load_ts", "timestamp")))
    assert ingestion_volume_monitored(ctx).score == _PASS


def test_ingestion_volume_partial_when_counts_carry_no_run_timestamp():
    ctx = _ctx(ingest_log=_table(("source_name", "varchar(50)"),
                                 ("records_read", "bigint")))
    verdict = ingestion_volume_monitored(ctx)
    assert verdict.score == _PARTIAL
    assert "silent drop" in verdict.evidence


def test_ingestion_volume_fails_when_no_row_count_is_recorded():
    ctx = _ctx(audit_log=_table(("batch_id", "bigint"), ("load_date", "timestamp"),
                                ("status", "varchar(20)")))
    assert ingestion_volume_monitored(ctx).score == _FAIL


def test_ingestion_volume_is_na_without_an_audit_table():
    ctx = _ctx(fact_sales=_table(*_FACT))
    assert ingestion_volume_monitored(ctx).status is Status.NA


def test_ingestion_volume_is_na_without_tables():
    assert ingestion_volume_monitored(_EMPTY).status is Status.NA


# =============================================================================
# Real-workspace validation findings (2026-08-10, "Explore Fabric - NOIDA").
#
# Every case below is a FALSE PASS the recorded fixture could not surface: each
# was found only by running the checks against a real estate and reading the
# ground truth behind the verdict.
# =============================================================================

def test_a_business_date_range_is_not_an_scd_strategy():
    """start_date/end_date/version must not read as SCD machinery.

    They are ordinary business columns - a contract term, a promotion window, a
    product revision - and is_audit_column already excludes them for exactly this
    reason. The vocabularies must agree.
    """
    ctx = _ctx(
        dim_city=_table(("city_key", "int"), ("start_date", "date"),
                        ("end_date", "date"), ("version", "int")),
    )
    verdict = scd_strategy_per_dimension(ctx)
    assert verdict.score == 1, "no real SCD marker present, so this is Type-1-by-default"


def test_a_business_state_flag_is_not_an_scd_strategy():
    """is_active/is_deleted describe the row's state, not whether it is versioned."""
    ctx = _ctx(
        dim_user=_table(("user_key", "int"), ("is_active", "bit"),
                        ("is_deleted", "bit")),
    )
    assert scd_strategy_per_dimension(ctx).score == 1


def test_a_real_scd2_dimension_still_scores():
    ctx = _ctx(
        dim_customer=_table(("customer_key", "int"), ("valid_from", "date"),
                            ("valid_to", "date"), ("is_current", "bit")),
    )
    assert scd_strategy_per_dimension(ctx).score == 3


def test_separatorless_validity_columns_still_score():
    """Real estates spell it ``ValidFrom``/``ValidTo`` as often as ``valid_from``."""
    ctx = _ctx(
        dimension_city=_table(("city_key", "int"), ("ValidFrom", "datetime"),
                              ("ValidTo", "datetime")),
    )
    assert scd_strategy_per_dimension(ctx).score == 3


@pytest.mark.parametrize("name", [
    "managed_delta_table_log_files",   # Delta's own transaction log - every lakehouse
    "dm_db_external_tables_log_status",  # SQL dynamic-management view
    "msdyn_salesforcestructuredqnaconfig",  # Dynamics system table
    "adx_setting",
    "syncerror",
    "ontology",
])
def test_platform_tables_are_not_the_teams_audit_practice(name: str):
    """WS-INGEST-VOLUME passed on managed_delta_table_log_files, which exists in
    every lakehouse and carries rows_inserted + commit_time by construction."""
    assert is_platform_table(name), name
    assert not is_audit_table_name(name), name
    assert not is_config_table_name(name), name


@pytest.mark.parametrize("name", [
    "etl_audit_log", "dq_results", "error_log", "load_exception", "audit_rowcounts",
])
def test_real_audit_tables_still_match(name: str):
    assert not is_platform_table(name), name
    assert is_audit_table_name(name), name


def test_a_platform_table_cannot_qualify_on_column_shape_either():
    """A Dynamics system table is mostly createdon/modifiedby, so the column route
    would let it back in through the side door."""
    table = _table(("createdon", "datetime"), ("modifiedon", "datetime"),
                   ("createdby", "nvarchar"), ("modifiedby", "nvarchar"))
    assert not is_audit_table("msdyn_something", table)
    assert is_audit_table("etl_audit", table), "a real audit table still qualifies"
