"""Tests for the second batch of checks (refs 4.6.5, 5.1.7, 5.1.9, 5.2.7, 5.5.1,
5.5.2, 5.5.4, 9.2.4, 10.1.1, 11.3.1).

Every check gets three cases at minimum: the input that must pass, the input that
must fail, and the N/A path — the one that matters most, because "we could not
see it" must never be reported as "they did not do it".

Where a check deliberately differs from an existing sibling, the difference is
tested directly rather than described: 5.2.7 must *not* be satisfied by the
key-column evidence that satisfies ``NB-KEY-QUALITY`` (5.5.6), and 5.5.2 must
fail on the ``cast("double")`` that satisfies ``NB-TYPE-CAST`` (5.3.1).
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_logs.automated import (
    notebook_audit_history_is_append_only,
    pipeline_audit_history_is_append_only,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    dq_library_is_standardized,
    notebook_date_quality,
    notebook_dq_failure_halts_run,
    notebook_handles_non_key_nulls,
    notebook_money_precision,
    pipeline_dq_failure_halts_run,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    run_history_is_persisted,
)
from auditfast.core.check.operations_reliability.data_operations.automated import (
    environment_tier_is_declared,
    gold_data_has_a_secondary_copy,
)
from auditfast.core.check.security.data_prep.automated import (
    notebook_pii_is_tokenised,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

_PASS, _PARTIAL_HIGH, _PARTIAL_LOW, _FAIL = 3, 2, 1, 0


# -- context builders ---------------------------------------------------------

def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}


def _nb_ctx(code: str) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="nb", obj=_nb(code))


def _pl_ctx(*activities: dict) -> CheckContext:
    definition = {"properties": {"activities": list(activities)}}
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="pl", obj=definition)


def _ws_ctx(**kwargs) -> CheckContext:
    workspace = WorkspaceContext(id="w", **kwargs)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


def _script(name: str, sql: str) -> dict:
    return {"name": name, "type": "Script",
            "typeProperties": {"scripts": [{"text": sql}]}}


# =============================================================================
# 4.6.5 — audit history is append-only
# =============================================================================

def test_notebook_audit_append_only_passes_when_history_is_only_appended_to():
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        'audit.write.mode("append").saveAsTable("etl_audit_log")\n'
    ))
    assert verdict.score == _PASS
    assert "etl_audit_log" in verdict.evidence


def test_notebook_audit_append_only_ignores_overwrites_of_non_audit_tables():
    """Overwriting staging is normal; only the audit target decides."""
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        'df.write.mode("overwrite").saveAsTable("stg_orders")\n'
        'audit.write.mode("append").saveAsTable("etl_audit_log")\n'
    ))
    assert verdict.score == _PASS


def test_notebook_audit_append_only_fails_when_history_is_deleted():
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        'spark.sql("DELETE FROM audit_log WHERE load_date < \'2024-01-01\'")\n'
        'audit.write.mode("append").saveAsTable("audit_log")\n'
    ))
    assert verdict.score == _FAIL
    assert "audit_log" in verdict.evidence


def test_notebook_audit_append_only_fails_on_overwrite_of_an_audit_table():
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        'audit.write.mode("overwrite").saveAsTable("dq_results")\n'
    ))
    assert verdict.score == _FAIL


def test_notebook_audit_append_only_is_na_when_no_audit_table_is_written():
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        'df.write.mode("overwrite").saveAsTable("dim_customer")\n'
    ))
    assert verdict.status is Status.NA


def test_notebook_audit_append_only_ignores_a_commented_out_delete():
    """A commented-out rewrite is not a live defect — executable_code strips it."""
    verdict = notebook_audit_history_is_append_only(_nb_ctx(
        '# spark.sql("DELETE FROM audit_log")\n'
        'audit.write.mode("append").saveAsTable("audit_log")\n'
    ))
    assert verdict.score == _PASS


def test_pipeline_audit_append_only_passes_on_an_insert():
    verdict = pipeline_audit_history_is_append_only(_pl_ctx(
        _script("Log run", "INSERT INTO dbo.audit_log SELECT @runId, 'OK'")
    ))
    assert verdict.score == _PASS


def test_pipeline_audit_append_only_fails_on_a_truncate_inside_a_foreach():
    """Activities nested in a container must be judged, not skipped."""
    verdict = pipeline_audit_history_is_append_only(_pl_ctx({
        "name": "Per source", "type": "ForEach",
        "typeProperties": {"activities": [
            _script("Reset audit", "TRUNCATE TABLE dbo.audit_log"),
        ]},
    }))
    assert verdict.score == _FAIL
    assert "audit_log" in verdict.evidence


def test_pipeline_audit_append_only_fails_when_a_copy_pre_truncates_the_audit_table():
    verdict = pipeline_audit_history_is_append_only(_pl_ctx({
        "name": "Copy audit", "type": "Copy",
        "typeProperties": {"sink": {"type": "LakehouseTableSink",
                                    "tableName": "run_log",
                                    "preCopyScript": "TRUNCATE TABLE run_log"}},
    }))
    assert verdict.score == _FAIL


def test_pipeline_audit_append_only_is_na_without_an_audit_target():
    verdict = pipeline_audit_history_is_append_only(_pl_ctx(
        _script("Load", "INSERT INTO dbo.fact_sales SELECT * FROM stg_sales")
    ))
    assert verdict.status is Status.NA


# =============================================================================
# 5.1.7 — one DQ library across the solution
# =============================================================================

_GE = "import great_expectations as gx\ncontext = gx.get_context()\n"
_PANDERA = "import pandera as pa\nschema = pa.DataFrameSchema({})\n"
_ASSERTS = "assert df.count() == 5\n"


def test_dq_library_standardized_passes_when_every_notebook_uses_one_library():
    verdict = dq_library_is_standardized(_ws_ctx(notebooks={
        "NB_Bronze": _nb(_GE), "NB_Silver": _nb(_GE),
    }))
    assert verdict.score == _PASS
    assert "great_expectations" in verdict.evidence


def test_dq_library_standardized_fails_when_three_notebooks_use_three_approaches():
    verdict = dq_library_is_standardized(_ws_ctx(notebooks={
        "NB_A": _nb(_GE), "NB_B": _nb(_PANDERA), "NB_C": _nb(_ASSERTS),
    }))
    assert verdict.score == _FAIL
    assert "3 different approaches" in verdict.evidence


def test_dq_library_standardized_is_na_with_only_one_dq_notebook():
    """One notebook cannot be inconsistent with itself."""
    verdict = dq_library_is_standardized(_ws_ctx(notebooks={
        "NB_A": _nb(_GE), "NB_B": _nb("df = spark.read.parquet('/x')\n"),
    }))
    assert verdict.status is Status.NA


def test_dq_library_standardized_is_na_without_notebooks():
    assert dq_library_is_standardized(_ws_ctx()).status is Status.NA


# =============================================================================
# 5.1.9 — a DQ failure must stop the run
# =============================================================================

def test_pipeline_dq_gate_passes_when_the_load_depends_on_validation_succeeding():
    verdict = pipeline_dq_failure_halts_run(_pl_ctx(
        {"name": "Validate row counts", "type": "TridentNotebook"},
        {"name": "Load Gold", "type": "Copy",
         "dependsOn": [{"activity": "Validate row counts",
                        "dependencyConditions": ["Succeeded"]}]},
    ))
    assert verdict.score == _PASS


def test_pipeline_dq_gate_fails_when_the_load_runs_on_completed():
    """``Completed`` runs the load whether validation passed or failed."""
    verdict = pipeline_dq_failure_halts_run(_pl_ctx(
        {"name": "Validate row counts", "type": "TridentNotebook"},
        {"name": "Load Gold", "type": "Copy",
         "dependsOn": [{"activity": "Validate row counts",
                        "dependencyConditions": ["Completed"]}]},
    ))
    assert verdict.score == _FAIL
    assert "Completed" in verdict.evidence


def test_pipeline_dq_gate_fails_when_nothing_depends_on_the_validation():
    verdict = pipeline_dq_failure_halts_run(_pl_ctx(
        {"name": "DQ checks", "type": "TridentNotebook"},
        {"name": "Load Gold", "type": "Copy"},
    ))
    assert verdict.score == _FAIL
    assert "nothing depends on it" in verdict.evidence


def test_pipeline_dq_gate_is_na_without_a_validation_activity():
    verdict = pipeline_dq_failure_halts_run(_pl_ctx(
        {"name": "Load Gold", "type": "Copy"},
    ))
    assert verdict.status is Status.NA


def test_notebook_dq_halt_passes_when_a_bad_result_raises():
    verdict = notebook_dq_failure_halts_run(_nb_ctx(
        'invalid_count = df.filter(col("order_id").isNull()).count()\n'
        'if invalid_count > 0:\n'
        '    raise ValueError("data quality failed")\n'
    ))
    assert verdict.score == _PASS


def test_notebook_dq_halt_is_partial_for_a_soft_notebook_exit():
    verdict = notebook_dq_failure_halts_run(_nb_ctx(
        'invalid_count = df.filter(col("order_id").isNull()).count()\n'
        'notebookutils.notebook.exit(str(invalid_count))\n'
    ))
    assert verdict.score == _PARTIAL_HIGH


def test_notebook_dq_halt_fails_when_the_result_is_only_printed():
    verdict = notebook_dq_failure_halts_run(_nb_ctx(
        'invalid_count = df.filter(col("order_id").isNull()).count()\n'
        'print(invalid_count)\n'
    ))
    assert verdict.score == _FAIL


def test_notebook_dq_halt_is_na_when_no_dq_result_is_computed():
    verdict = notebook_dq_failure_halts_run(_nb_ctx(
        'df = spark.read.parquet("/lh/Files/orders")\n'
        'df.write.saveAsTable("bronze_orders")\n'
    ))
    assert verdict.status is Status.NA


# =============================================================================
# 5.2.7 — nulls outside the key columns
# =============================================================================

def test_null_handling_passes_when_non_key_columns_are_named():
    verdict = notebook_handles_non_key_nulls(_nb_ctx(
        'df = df.fillna({"description": "", "region": "UNKNOWN"})\n'
        'df.write.saveAsTable("silver_customer")\n'
    ))
    assert verdict.score == _PASS
    assert "description" in verdict.evidence


def test_null_handling_passes_when_every_column_is_profiled():
    verdict = notebook_handles_non_key_nulls(_nb_ctx(
        'null_counts = {c: df.filter(col(c).isNull()).count() for c in df.columns}\n'
        'df.write.saveAsTable("silver_customer")\n'
    ))
    assert verdict.score == _PASS


def test_null_handling_is_only_partial_on_key_column_evidence():
    """The evidence that satisfies NB-KEY-QUALITY (5.5.6) must not satisfy 5.2.7."""
    verdict = notebook_handles_non_key_nulls(_nb_ctx(
        'df = df.filter(col("customer_id").isNotNull())\n'
        'df.write.saveAsTable("silver_customer")\n'
    ))
    assert verdict.score == _PARTIAL_LOW
    assert "5.5.6" in verdict.evidence


def test_null_handling_fails_when_no_null_is_examined():
    verdict = notebook_handles_non_key_nulls(_nb_ctx(
        'df = spark.read.csv("/lh/Files/customers.csv")\n'
        'df.write.saveAsTable("silver_customer")\n'
    ))
    assert verdict.score == _FAIL


def test_null_handling_is_na_when_the_notebook_moves_no_data():
    verdict = notebook_handles_non_key_nulls(_nb_ctx("threshold = 1 + 2\n"))
    assert verdict.status is Status.NA


# =============================================================================
# 5.5.1 — date ranges and timezones
# =============================================================================

def test_date_quality_passes_with_a_range_bound_and_utc_handling():
    verdict = notebook_date_quality(_nb_ctx(
        'df = df.filter(to_date(col("order_date")) <= current_date())\n'
        'df = df.withColumn("event_ts", to_utc_timestamp(col("event_ts"), "Asia/Kolkata"))\n'
    ))
    assert verdict.score == _PASS


def test_date_quality_is_partial_when_only_the_range_is_validated():
    verdict = notebook_date_quality(_nb_ctx(
        'df = df.filter(to_date(col("order_date")) <= current_date())\n'
    ))
    assert verdict.score == _PARTIAL_HIGH
    assert "timezone" in verdict.evidence


def test_date_quality_is_partial_when_only_the_timezone_is_handled():
    verdict = notebook_date_quality(_nb_ctx(
        'df = df.withColumn("event_date", to_utc_timestamp(col("event_ts"), "Asia/Kolkata"))\n'
    ))
    assert verdict.score == _PARTIAL_LOW


def test_date_quality_fails_on_naive_parsing():
    verdict = notebook_date_quality(_nb_ctx(
        'df = df.withColumn("order_date", to_date(col("order_date_str")))\n'
    ))
    assert verdict.score == _FAIL


def test_date_quality_is_na_without_dates():
    verdict = notebook_date_quality(_nb_ctx('df.write.saveAsTable("bronze_orders")\n'))
    assert verdict.status is Status.NA


def test_date_quality_is_na_for_date_columns_in_create_table_only():
    verdict = notebook_date_quality(_nb_ctx(
        'spark.sql("CREATE TABLE dim_calendar (calendar_date DATE, label STRING)")\n'
    ))
    assert verdict.status is Status.NA


# =============================================================================
# 5.5.2 — money precision and currency codes
# =============================================================================

def test_money_precision_passes_with_decimal_typing_and_currency_validation():
    verdict = notebook_money_precision(_nb_ctx(
        'df = df.withColumn("amount", col("amount").cast(DecimalType(18, 2)))\n'
        'df = df.filter(col("currency_code").isin("USD", "EUR"))\n'
    ))
    assert verdict.score == _PASS


def test_money_precision_is_partial_when_the_currency_code_is_never_validated():
    verdict = notebook_money_precision(_nb_ctx(
        'df = df.withColumn("price", col("price").cast(DecimalType(18, 2)))\n'
        'df = df.withColumn("currency", lit("USD"))\n'
    ))
    assert verdict.score == _PARTIAL_HIGH


def test_money_precision_fails_on_float_typed_money():
    """``cast("double")`` satisfies NB-TYPE-CAST (5.3.1) and must fail here."""
    verdict = notebook_money_precision(_nb_ctx(
        'df = df.withColumn("total_amount", col("total_amount").cast("double"))\n'
    ))
    assert verdict.score == _FAIL
    assert "float" in verdict.evidence


def test_money_precision_is_partial_when_no_numeric_typing_is_declared():
    verdict = notebook_money_precision(_nb_ctx(
        'df = spark.read.parquet("/lh/Files/invoices")\n'
        'df.write.saveAsTable("fact_invoice_amount")\n'
    ))
    assert verdict.score == _PARTIAL_LOW


def test_money_precision_is_na_without_monetary_values():
    verdict = notebook_money_precision(_nb_ctx('df.write.saveAsTable("dim_customer")\n'))
    assert verdict.status is Status.NA


def test_money_precision_ignores_financial_keywords_in_sql_comments():
    verdict = notebook_money_precision(_nb_ctx(
        'spark.sql("""\n'
        '-- SELECT CAST(amount AS FLOAT), currency_code FROM payments\n'
        'CREATE TABLE dim_customer (customer_id BIGINT)\n'
        '""")\n'
    ))
    assert verdict.status is Status.NA


# =============================================================================
# 5.5.4 — PII tokenisation (the notebook half; WS-DDM covers the Warehouse half)
# =============================================================================

def test_pii_tokenised_passes_with_hashing_and_format_validation():
    verdict = notebook_pii_is_tokenised(_nb_ctx(
        'df = df.withColumn("email", sha2(col("email"), 256))\n'
        'df = df.filter(col("email").rlike("^[^@]+@[^@]+$"))\n'
    ))
    assert verdict.score == _PASS


def test_pii_tokenised_is_partial_with_masking_but_no_format_validation():
    verdict = notebook_pii_is_tokenised(_nb_ctx(
        'df = df.withColumn("ssn", sha2(col("ssn"), 256))\n'
    ))
    assert verdict.score == _PARTIAL_HIGH


def test_pii_tokenised_is_partial_with_format_validation_but_no_masking():
    verdict = notebook_pii_is_tokenised(_nb_ctx(
        'df = df.filter(col("email").rlike("^[^@]+@[^@]+$"))\n'
    ))
    assert verdict.score == _PARTIAL_LOW


def test_pii_tokenised_fails_when_raw_pii_is_carried_through():
    verdict = notebook_pii_is_tokenised(_nb_ctx(
        'out = df.select("email", "phone_number")\n'
        'out.write.saveAsTable("silver_customer")\n'
    ))
    assert verdict.score == _FAIL


def test_pii_tokenised_is_na_without_a_pii_column():
    verdict = notebook_pii_is_tokenised(_nb_ctx('df.write.saveAsTable("fact_sales")\n'))
    assert verdict.status is Status.NA


# =============================================================================
# 9.2.4 — a secondary copy of the Gold layer
# =============================================================================

_WAREHOUSE = Item(id="1", type="Warehouse", display_name="WH_Gold")


def test_gold_secondary_copy_passes_with_a_mirrored_item():
    verdict = gold_data_has_a_secondary_copy(_ws_ctx(items=[
        _WAREHOUSE, Item(id="2", type="MirroredDatabase", display_name="Mirror_Gold"),
    ]))
    assert verdict.score == _PASS
    assert "Mirror_Gold" in verdict.evidence


def test_gold_secondary_copy_passes_with_a_pipeline_export_to_external_storage():
    verdict = gold_data_has_a_secondary_copy(_ws_ctx(
        items=[_WAREHOUSE],
        pipelines={"PL_Export_Gold": {"properties": {"activities": [{
            "name": "Export", "type": "Copy",
            "typeProperties": {"sink": {
                "type": "ParquetSink",
                "storeSettings": {"type": "AzureBlobFSWriteSettings"},
            }},
        }]}}},
    ))
    assert verdict.score == _PASS
    assert "PL_Export_Gold" in verdict.evidence


def test_gold_secondary_copy_fails_when_the_only_sink_stays_in_onelake():
    verdict = gold_data_has_a_secondary_copy(_ws_ctx(
        items=[_WAREHOUSE],
        pipelines={"PL_Load_Gold": {"properties": {"activities": [{
            "name": "Load", "type": "Copy",
            "typeProperties": {"sink": {"type": "LakehouseTableSink"}},
        }]}}},
        shortcuts={"LH_Gold": []},
    ))
    assert verdict.score == _FAIL
    assert "exactly one place" in verdict.evidence


def test_gold_secondary_copy_is_na_without_a_store():
    verdict = gold_data_has_a_secondary_copy(_ws_ctx(items=[
        Item(id="1", type="Notebook", display_name="NB_Build"),
    ]))
    assert verdict.status is Status.NA


# =============================================================================
# 10.1.1 — run history persisted past Fabric's retention window
# =============================================================================

def test_run_history_export_passes_with_identity_status_and_timing():
    verdict = run_history_is_persisted(_ws_ctx(notebooks={"NB_Log": _nb(
        'row = [(run_id, "Succeeded", start_time, end_time)]\n'
        'spark.createDataFrame(row).write.mode("append").saveAsTable("pipeline_run_log")\n'
    )}))
    assert verdict.score == _PASS


def test_run_history_export_is_partial_when_the_row_carries_no_outcome():
    verdict = run_history_is_persisted(_ws_ctx(notebooks={"NB_Log": _nb(
        'spark.createDataFrame([(run_id,)]).write.mode("append").saveAsTable("run_history")\n'
    )}))
    assert verdict.score == _PARTIAL_LOW
    assert "status" in verdict.evidence


def test_run_history_export_fails_when_nothing_persists_the_outcome():
    verdict = run_history_is_persisted(_ws_ctx(
        pipelines={"PL_Load": {"properties": {"activities": [
            {"name": "Load", "type": "Copy"},
        ]}}},
    ))
    assert verdict.score == _FAIL
    assert "retention window" in verdict.evidence


def test_run_history_export_is_na_without_pipelines_or_notebooks():
    assert run_history_is_persisted(_ws_ctx()).status is Status.NA


# =============================================================================
# 11.3.1 — the tier a single workspace declares (reported, never scored)
# =============================================================================

def test_tier_declaration_reports_the_tier_without_scoring_it():
    verdict = environment_tier_is_declared(_ws_ctx(display_name="MLC_DATAPREP_PROD"))
    assert verdict.status is Status.INFO
    assert verdict.scored is False
    assert "Prod" in verdict.evidence


def test_tier_declaration_reports_a_missing_tier_without_failing_the_workspace():
    """A shared store legitimately carries no tier — this must not score 0."""
    verdict = environment_tier_is_declared(_ws_ctx(display_name="Shared_Gold_Lakehouse"))
    assert verdict.status is Status.INFO
    assert verdict.scored is False
    assert "no environment tier" in verdict.evidence
