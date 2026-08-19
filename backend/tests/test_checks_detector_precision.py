"""Regression tests for detector-precision fixes to seven checks.

Each check is a pure function of a notebook / pipeline / table definition, so
these build synthetic definitions and assert the verdict directly. Every test
pairs the previously-misjudged case (the bug) with the case that must keep
working, so a future rewrite cannot silently reintroduce the false PASS/FAIL.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from auditfast.core.check._tables import is_audit_column
from auditfast.core.check.cost_resource_optimization.data_operations.automated import (
    no_orphaned_items,
)
from auditfast.core.check.data_management_quality.data_operations.automated import (
    notebook_format_validation,
    notebook_schema_validation,
    notebook_standardization,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    nb_broadcast,
    nb_bronze_metadata,
    nb_cross_recon,
    nb_dedup_verify,
    nb_dq_rules,
    nb_eam_ingest,
    nb_flag_domain,
    nb_language,
    nb_late_arriving,
    nb_no_display,
    nb_no_udf,
    nb_silver_quality,
    nb_source_metadata,
    nb_timeout,
    nb_utf8_encoding,
    parameterized,
    pl_bulk_move,
    pl_historical_separation,
    pl_incremental,
    pl_load_mode,
    pl_metadata_driven,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    table_audit_columns,
    table_date_dimension,
    table_scd2,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    audit_tables_capture_quality_logs,
    pipeline_failure_alert,
)
from auditfast.core.check.operations_reliability.data_prep.automated import (
    explicit_timeouts,
    failure_notification,
    nb_deadletter,
    pipeline_idempotent,
    pl_deadletter,
    restart_from_failure,
    retry_values,
)
from auditfast.core.check.performance_capacity.data_prep.automated import (
    copy_parallelism,
    delta_optimize,
    spark_env,
)
from auditfast.core.check.registry import REGISTRY, CheckRegistry
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext


def _nb(code: str = "", metadata: dict | None = None) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": metadata or {}}


def _ctx(obj) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={}, obj_name="nb", obj=obj)


def _pipe(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


class _WorkspaceProvider:
    def __init__(self, workspace: WorkspaceContext):
        self.workspace = workspace

    def fetch(self, workspace_id, layer, resources):
        self.workspace.layer = layer
        return self.workspace


def _tables_ctx(**tables: dict) -> CheckContext:
    ws = WorkspaceContext(id="w", tables=tables)
    return CheckContext(workspace=ws, settings={}, obj_name="w", obj=None)


#: A check body returns a raw ``Verdict`` whose ``status`` the engine derives
#: from the score later, so a passing/failing verdict is asserted via ``score``
#: (3 = pass, 0 = fail, 1 = partial); only N/A carries a ``status`` at this stage.
_PASS, _FAIL = 3, 0


# -- PL-RETRY-VALUES ---------------------------------------------------------

def test_retry_values_no_pipeline_is_visible_na():
    workspace = WorkspaceContext(id="w", display_name="MLC - CNCR", pipelines={})
    registry = CheckRegistry()
    registry.register(REGISTRY.get("PL-RETRY-VALUES"))

    results = run_audit(
        _WorkspaceProvider(workspace), [("w", Layer.PREP)], {}, registry=registry,
    )

    assert len(results) == 1
    assert results[0].status is Status.NA
    assert results[0].score is None
    assert results[0].evidence == "No data pipelines were found in this workspace"


def test_retry_values_pipeline_without_retry_is_na_and_names_pipeline():
    result = retry_values(CheckContext(
        workspace=WorkspaceContext(id="w"), settings={}, obj_name="PL_No_Retry",
        obj=_pipe({"name": "Copy orders", "type": "Copy", "policy": {"retry": 0}}),
    ))
    assert result.status is Status.NA
    assert "PL_No_Retry" in result.evidence
    assert "PL-RETRY" in result.evidence


def test_retry_values_finds_nested_retry_policy():
    pipeline = _pipe({
        "name": "For each table", "type": "ForEach",
        "typeProperties": {"activities": [{
            "name": "Copy table", "type": "Copy",
            "policy": {"retry": 3, "retryIntervalInSeconds": 60},
        }]},
    })
    result = retry_values(_ctx(pipeline))
    assert result.score == _PASS
    assert "1 of 1" in result.evidence


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"retry": 1000, "retryIntervalInSeconds": 60}, "count 1000 exceeds maximum 10"),
        ({"retry": 3}, "missing or non-positive interval"),
    ],
)
def test_retry_values_invalid_policy_fails_with_activity_evidence(policy, expected):
    pipeline = _pipe({"name": "Copy orders", "type": "Copy", "policy": policy})
    result = retry_values(_ctx(pipeline))
    assert result.score == _FAIL
    assert "Copy orders" in result.evidence
    assert expected in result.evidence


def test_retry_values_unreadable_policy_is_na_not_fail():
    """A retry set by a run-time expression is unknowable, not wrong.

    An earlier implementation ran ``int()`` over the raw value and counted the
    resulting TypeError as a failing activity, which is a false FAIL on a
    pipeline that may be configured perfectly - the value simply cannot be read
    from the definition. It must leave the scored population instead.
    """
    pipeline = _pipe({
        "name": "Copy orders", "type": "Copy",
        "policy": {"retry": {"value": "@pipeline().parameters.n", "type": "Expression"},
                   "retryIntervalInSeconds": 60},
    })
    result = retry_values(_ctx(pipeline))
    assert result.status is Status.NA
    assert result.score is None


# -- NB-LATE-ARRIVING ---------------------------------------------------------

def test_late_arriving_generic_delta_ingest_is_not_dimensional_scope():
    code = """
raw = spark.read.json(source_path)
normalised = raw.withColumn("DATAFIELD", upper(col("DATAFIELD")))
typed = apply_data_types(normalised)
typed.write.format("delta").mode("overwrite").saveAsTable(target_table)
insert_count = typed.count()
update_count = 0
delete_count = 0
"""
    result = nb_late_arriving(_ctx(_nb(code)))
    assert result.status is Status.NA
    assert "no provable dimensional fact load" in result.evidence.lower()


def test_late_arriving_fact_lookup_without_fallback_fails_with_specific_evidence():
    code = """
fact_sales = source.alias("f").join(dim_customer.alias("d"), col("f.customer_id") == col("d.customer_id"), "left")
fact_sales.write.mode("append").saveAsTable("gold.fact_sales")
"""
    result = nb_late_arriving(_ctx(_nb(code)))
    assert result.score == _FAIL
    assert "unknown/inferred-member fallback" in result.evidence
    assert "backfill" in result.evidence


def test_late_arriving_unknown_member_and_backfill_passes():
    code = """
fact_sales = source.alias("f").join(dim_customer.alias("d"), col("f.customer_id") == col("d.customer_id"), "left")
fact_sales = fact_sales.withColumn("customer_key", coalesce(col("d.customer_key"), lit(-1)))
fact_sales.write.mode("append").saveAsTable("gold.fact_sales")
DeltaTable.forName(spark, "gold.dim_customer").alias("target").merge(
    source.alias("source"), "target.customer_id = source.customer_id"
).whenMatchedUpdateAll().execute()
"""
    result = nb_late_arriving(_ctx(_nb(code)))
    assert result.score == _PASS
    assert "fallback" in result.evidence.lower()
    assert "backfill" in result.evidence.lower()


def test_late_arriving_fallback_without_backfill_fails():
    code = """
fact_sales = source.alias("f").join(dim_customer.alias("d"), col("f.customer_id") == col("d.customer_id"), "left")
fact_sales = fact_sales.withColumn("customer_key", coalesce(col("d.customer_key"), lit(-1)))
fact_sales.write.mode("append").saveAsTable("gold.fact_sales")
"""
    result = nb_late_arriving(_ctx(_nb(code)))
    assert result.score == _FAIL
    assert "backfill" in result.evidence.lower()


def test_late_arriving_check_applies_to_data_storage():
    spec = next(spec for spec in REGISTRY if spec.id == "NB-LATE-ARRIVING")
    assert spec.applies_to(Layer.STORAGE)


# -- PL-NOTIFY -----------------------------------------------------------------

def test_notify_nested_in_if_condition_is_found():
    """A notifier nested inside an If Condition must count (was a false FAIL)."""
    pipe = _pipe({
        "name": "Check Threshold", "type": "IfCondition",
        "typeProperties": {
            "ifTrueActivities": [{"name": "Mail Owner", "type": "Office365Outlook"}],
            "ifFalseActivities": [],
        },
    })
    assert failure_notification(_ctx(pipe)).score == _PASS


def test_notify_nested_in_foreach_is_found():
    pipe = _pipe({
        "name": "Loop", "type": "ForEach",
        "typeProperties": {"activities": [{"name": "Teams Ping", "type": "Teams"}]},
    })
    assert failure_notification(_ctx(pipe)).score == _PASS


def test_copy_named_email_is_not_a_notifier():
    """A Copy/Lookup that merely has "email" in its name is not a notifier (was a false PASS)."""
    pipe = _pipe(
        {"name": "Copy_EmailList_To_Stg", "type": "Copy"},
        {"name": "Lookup_Alert_Rows", "type": "Lookup"},
    )
    assert failure_notification(_ctx(pipe)).score == _FAIL


def test_direct_notifier_type_still_passes():
    assert failure_notification(_ctx(_pipe({"name": "n", "type": "Teams"}))).score == _PASS


def test_generic_web_activity_named_alert_is_a_notifier():
    pipe = _pipe({"name": "Send_Teams_Alert", "type": "WebActivity"})
    assert failure_notification(_ctx(pipe)).score == _PASS


# -- NB-NO-UDF -----------------------------------------------------------------

def test_spark_udf_register_is_detected():
    """spark.udf.register(...) is a UDF and must be flagged (was a false PASS)."""
    code = 'def clean(x): return x.upper()\nspark.udf.register("clean_sql", clean, StringType())'
    assert nb_no_udf(_ctx(_nb(code))).score == _FAIL


def test_udf_decorator_is_detected():
    assert nb_no_udf(_ctx(_nb("@udf(StringType())\ndef f(x): return x"))).score == _FAIL


def test_pandas_udf_is_detected():
    assert nb_no_udf(_ctx(_nb("g = pandas_udf(inner, LongType())"))).score == _FAIL


def test_no_udf_still_passes():
    assert nb_no_udf(_ctx(_nb("df = spark.table('t').select('a')"))).score == _PASS


# -- NB-DISPLAY ----------------------------------------------------------------

def test_show_chained_on_spark_sql_is_detected():
    """.show() chained on spark.sql(...) must be flagged (was a false PASS)."""
    code = 'spark.sql("SELECT COUNT(*) FROM t").show()'
    assert nb_no_display(_ctx(_nb(code))).score == _FAIL


def test_count_show_chain_is_detected():
    assert nb_no_display(_ctx(_nb('df.groupBy("c").count().show()'))).score == _FAIL


def test_standalone_display_still_detected():
    assert nb_no_display(_ctx(_nb("display(df)"))).score == _FAIL


def test_no_display_or_show_passes():
    assert nb_no_display(_ctx(_nb("gold.write.saveAsTable('g')"))).score == _PASS


# -- TB-DATEDIM ----------------------------------------------------------------

def test_date_dimension_without_dim_prefix_is_found():
    """A date dimension named `datedimension` counts even without a `dim` prefix (was a false FAIL)."""
    ctx = _tables_ctx(datedimension={"type": "Managed", "format": "Delta"})
    assert table_date_dimension(ctx).score == _PASS


def test_reporting_datedimension_basetable_is_found():
    ctx = _tables_ctx(reporting_datedimension_basetable={"type": "Managed", "format": "Delta"})
    assert table_date_dimension(ctx).score == _PASS


def test_dim_date_prefixed_still_found():
    ctx = _tables_ctx(dim_date={"type": "Managed", "format": "Delta"})
    assert table_date_dimension(ctx).score == _PASS


def test_no_date_dimension_fails_not_matches_fact_with_date_in_name():
    """A fact table with "date" in its name is not a date dimension."""
    ctx = _tables_ctx(
        fact_sales_by_date={"type": "Managed", "format": "Delta"},
        dim_customer={"type": "Managed", "format": "Delta"},
    )
    assert table_date_dimension(ctx).score == _FAIL


def test_date_dimension_is_judged_per_store_not_masked_by_an_unrelated_warehouse():
    ctx = _tables_ctx(
        **{
            "DimDate": {"store": "EMPN2000_101Test", "store_kind": "Warehouse"},
            "WH_Gold.dim_customer": {
                "store": "WH_Gold", "store_kind": "Warehouse",
            },
            "test_Lakehouse.fact_sales": {
                "store": "test_Lakehouse", "store_kind": "Lakehouse",
            },
        },
    )
    verdict = table_date_dimension(ctx)

    assert verdict.score == _FAIL
    assert "1 of 3" in verdict.evidence
    assert "EMPN2000_101Test: DimDate" in verdict.evidence
    assert "WH_Gold" in verdict.evidence
    assert "test_Lakehouse" in verdict.evidence


def test_date_dimension_partial_when_one_of_two_stores_has_one():
    ctx = _tables_ctx(
        **{
            "sales.dim_date": {"store": "WH_Gold", "store_kind": "Warehouse"},
            "bronze.orders": {"store": "LH_Bronze", "store_kind": "Lakehouse"},
        },
    )
    assert table_date_dimension(ctx).score == 1


# -- SPARK-ENV -----------------------------------------------------------------

def test_wheel_url_install_is_flagged():
    """A %pip install of a wheel URL is an inline install (was a false PASS)."""
    code = "%pip install https://aka.ms/chat_magics-0.0.0-py3-none-any.whl"
    assert spark_env(_ctx(_nb(code))).score == 1


def test_os_system_pip_install_is_flagged():
    assert spark_env(_ctx(_nb('os.system("pip install build")'))).score == 1


def test_no_inline_install_still_passes():
    assert spark_env(_ctx(_nb("import pandas as pd"))).score == 3


# -- NOTEBOOK DATA QUALITY VALIDATION -----------------------------------------

def test_notebook_schema_validation_checks_names_and_types():
    code = """
from pyspark.sql.types import StructType, StructField, StringType
schema = StructType([StructField('employee_id', StringType(), True)])
records = spark.read.schema(schema).json('Files/eam.json')
assert records.columns == ['employee_id']
"""
    assert notebook_schema_validation(_ctx(_nb(code))).score == _PASS


def test_notebook_schema_validation_rejects_unchecked_input():
    code = "records = spark.read.json('Files/eam.json')"
    assert notebook_schema_validation(_ctx(_nb(code))).score == _FAIL


def test_notebook_format_validation_covers_utf8_delimiter_and_json():
    code = """
records = (spark.read.option('encoding', 'UTF-8')
    .option('delimiter', '|')
    .json('Files/eam.json'))
validated_json = json.loads(payload)
"""
    assert notebook_format_validation(_ctx(_nb(code))).score == _PASS


def test_notebook_format_validation_rejects_missing_format_controls():
    assert notebook_format_validation(_ctx(_nb("records = spark.read.json('Files/eam.json')"))).score == _FAIL


def test_notebook_standardization_covers_dates_and_codes():
    code = """
records = spark.read.json('Files/eam.json')
records = records.withColumn('event_date', to_date('event_date'))
records = records.withColumn('employee_code', upper(trim('employee_code')))
"""
    assert notebook_standardization(_ctx(_nb(code))).score == _PASS


def test_notebook_standardization_rejects_missing_categories():
    code = "records = spark.read.json('Files/eam.json')\nrecords = records.withColumn('event_date', to_date('event_date'))"
    assert notebook_standardization(_ctx(_nb(code))).score == _FAIL


def test_notebook_quality_checks_are_na_without_input():
    notebook = _ctx(_nb("print('no incoming data')"))
    assert notebook_schema_validation(notebook).status is Status.NA
    assert notebook_format_validation(notebook).status is Status.NA
    assert notebook_standardization(notebook).status is Status.NA


# -- DATA QUALITY INGESTION CONTROLS ------------------------------------------

def test_source_metadata_requires_ingestion_timestamp():
    code = """
df = spark.read.json('Files/events.json')
df = df.withColumn('ingestion_timestamp', current_timestamp())
df.write.saveAsTable('bronze_events')
"""
    assert nb_source_metadata(_ctx(_nb(code))).score == _PASS


def test_deduplication_verification_detects_duplicate_keys():
    code = """
df = spark.read.json('Files/events.json')
duplicates = df.groupBy('event_id').count().filter('count > 1')
assert duplicates.count() == 0
df.write.saveAsTable('bronze_events')
"""
    assert nb_dedup_verify(_ctx(_nb(code))).score == _PASS


def test_utf8_encoding_validation_passes():
    code = "df = spark.read.option('encoding', 'UTF-8').json('Files/events.json')"
    assert nb_utf8_encoding(_ctx(_nb(code))).score == _PASS


def test_flag_domain_validation_passes():
    code = "df = spark.read.json('Files/events.json')\nvalid = df.filter(df.active.isin(True, False))"
    assert nb_flag_domain(_ctx(_nb(code))).score == _PASS


def test_flag_domain_passes_when_a_flag_is_normalised_by_when_otherwise():
    code = (
        "df = spark.read.json('Files/events.json')\n"
        "df = df.withColumn('is_active', when(col('is_active') == 'Y', True).otherwise(False))"
    )
    assert nb_flag_domain(_ctx(_nb(code))).score == _PASS


def test_flag_domain_fails_on_a_boolean_type_declaration_alone():
    """Declaring a BooleanType column sets a type; it does not restrict values."""
    code = (
        "df = spark.read.json('Files/events.json')\n"
        "schema = StructType([StructField('amount', BooleanType())])"
    )
    assert nb_flag_domain(_ctx(_nb(code))).score == _FAIL


def test_flag_domain_fails_on_a_generic_when_otherwise():
    """A when/otherwise with no flag column and no flag literal is generic logic."""
    code = (
        "df = spark.read.csv('Files/events.csv')\n"
        "df = df.withColumn('bucket', when(col('amount') > 100, 'big').otherwise('small'))"
    )
    assert nb_flag_domain(_ctx(_nb(code))).score == _FAIL


def test_ingestion_controls_are_na_without_relevant_input_or_write():
    context = _ctx(_nb("print('no input or write')"))
    assert nb_source_metadata(context).status is Status.NA
    assert nb_dedup_verify(context).status is Status.NA
    assert nb_utf8_encoding(context).status is Status.NA
    assert nb_flag_domain(context).status is Status.NA


# -- BRONZE / SILVER / BULK / EAM CONTROLS -----------------------------------

def test_bronze_metadata_passes_with_audit_fields():
    code = """
raw = spark.read.json('Files/raw.json')
raw = raw.withColumn('ingestion_timestamp', current_timestamp())
raw = raw.withColumn('source_file', input_file_name()).withColumn('batch_id', lit('b1'))
raw.write.saveAsTable('bronze_events')
"""
    assert nb_bronze_metadata(_ctx(_nb(code))).score == _PASS


def test_silver_quality_scores_each_aspect_separately():
    """Two of the three scored aspects is a partial, not a pass.

    The point scores cleansing, conforming and type standardization. Dedup is
    detected but reported as unscored context, so it never lifts or lowers the
    band; scoring the ratio of the three is what makes the evidence checkable.
    """
    code = """
silver = spark.read.table('bronze_events')
silver = (silver.dropDuplicates(['event_id'])
          .withColumn('event_date', to_date('event_date'))
          .withColumn('name', trim(col('name'))))
silver.write.saveAsTable('silver_events')
"""
    verdict = nb_silver_quality(_ctx(_nb(code)))
    assert verdict.score is not None and verdict.score < _PASS
    assert "2 of 3" in verdict.evidence
    assert "Not found: conforming" in verdict.evidence
    assert "deduplication also applied" in verdict.evidence


def test_silver_quality_passes_when_every_aspect_is_present():
    """All three scored aspects present - cast, trim (cleansing), rename (conforming).

    Dedup is also applied here, but it is reported as context, not scored.
    """
    code = """
silver = spark.read.table('bronze_events')
silver = (silver.dropDuplicates(['event_id'])
          .withColumn('event_date', to_date('event_date'))
          .withColumn('name', trim(col('name')))
          .withColumnRenamed('src_id', 'source_id'))
silver.write.saveAsTable('silver_events')
"""
    verdict = nb_silver_quality(_ctx(_nb(code)))
    assert verdict.score == _PASS
    assert "3 of 3" in verdict.evidence


def test_bulk_pipeline_passes_with_parallel_copy():
    pipeline = _pipe({
        "name": "Bulk copy", "type": "Copy",
        "typeProperties": {"parallelCopies": 8},
    })
    assert pl_bulk_move(_ctx(pipeline)).score == _PASS


def test_eam_ingestion_passes_with_bounded_streaming_json():
    code = """
events = (spark.readStream.option('maxFilesPerTrigger', 10)
          .json('Files/eam'))
events.writeStream.partitionBy('event_date').start()
"""
    assert nb_eam_ingest(_ctx(_nb(code))).score == _PASS


def test_layer_specific_checks_return_na_when_not_applicable():
    notebook = _ctx(_nb("print('unrelated notebook')"))
    pipeline = _ctx(_pipe({"name": "copy", "type": "Copy"}))
    assert nb_bronze_metadata(notebook).status is Status.NA
    assert nb_silver_quality(notebook).status is Status.NA
    assert nb_eam_ingest(notebook).status is Status.NA
    assert pl_bulk_move(pipeline).score == 1


def test_bulk_move_is_na_for_notebook_only_pipeline():
    # A pipeline that only runs a notebook moves no bulk data itself, so the
    # bulk-vs-row-by-row question is judged in the notebook, not here.
    pipeline = _pipe(
        {"name": "Run notebook", "type": "TridentNotebook",
         "typeProperties": {"notebookId": "abc"}},
        {"name": "Read control table", "type": "Lookup"},
    )
    assert pl_bulk_move(_ctx(pipeline)).status is Status.NA


def test_bulk_move_evidence_names_the_activity_and_reason():
    # Default Copy settings are a warning, not proof of row-by-row movement.
    pipeline = _pipe({"name": "Move rows", "type": "Copy"})
    verdict = pl_bulk_move(_ctx(pipeline))
    assert verdict.score == 1
    assert "1 Copy" in verdict.evidence
    assert "parallelCopies" in verdict.evidence


def test_bulk_move_ignores_foreach_batch_count_as_copy_evidence():
    pipeline = _pipe({
        "name": "Load metadata tables", "type": "ForEach",
        "typeProperties": {
            "batchCount": 50,
            "items": {"value": "@activity('Lookup').output.value", "type": "Expression"},
            "activities": [{"name": "Copy table", "type": "Copy", "typeProperties": {}}],
        },
    })
    verdict = pl_bulk_move(_ctx(pipeline))
    assert verdict.score == 1
    assert "batchCount" not in verdict.evidence


def test_bulk_move_passes_with_staging_and_copy_command():
    pipeline = _pipe({
        "name": "Warehouse bulk load", "type": "Copy",
        "typeProperties": {
            "enableStaging": True,
            "sink": {"type": "DataWarehouseSink", "allowCopyCommand": True},
        },
    })
    verdict = pl_bulk_move(_ctx(pipeline))
    assert verdict.score == _PASS
    assert "enableStaging=true" in verdict.evidence
    assert "allowCopyCommand=true" in verdict.evidence


def test_bulk_move_fails_only_explicit_row_by_row_logic():
    pipeline = _pipe({
        "name": "Insert row by row", "type": "Script",
        "typeProperties": {"script": "-- row-by-row load\nINSERT INTO target VALUES (1)"},
    })
    verdict = pl_bulk_move(_ctx(pipeline))
    assert verdict.score == _FAIL
    assert "explicit row-by-row" in verdict.evidence


# -- DQ RULES / RESTART / AUDIT LOG / FAILURE ALERT ---------------------------

def test_dq_rules_are_scored_per_discipline():
    """One assertion is not a rule framework, so it is a partial, not a pass.

    An earlier version passed on any single token from a bundled pattern, which
    scored full marks for "DQ rules codified in code/config" on a notebook whose
    only quality logic was ``drop_duplicates``.
    """
    code = "df = spark.read.json('Files/input.json')\nassert df.filter(df.id.isNull()).count() == 0"
    verdict = nb_dq_rules(_ctx(_nb(code)))
    assert verdict.score is not None and verdict.score < _PASS
    assert "assertions / expectations" in verdict.evidence
    assert "null / domain checks" in verdict.evidence


def test_dq_rules_do_not_pass_on_deduplication_alone():
    """Deduplication is housekeeping, not a rule that judges a record."""
    code = "df = spark.read.json('Files/in.json')\ndf = df.drop_duplicates()\ndf.write.saveAsTable('t')"
    verdict = nb_dq_rules(_ctx(_nb(code)))
    assert verdict.score is not None and verdict.score < _PASS


def test_dq_rules_pass_when_every_discipline_is_codified():
    code = (
        "from pyspark.sql.types import StructType, StructField\n"
        "df = spark.read.schema(StructType([])).json('Files/in.json')\n"
        "assert df.count() > 0\n"
        "clean = df.filter(df.id.isNotNull() & df.status.isin(['A', 'B']))\n"
        "rejected = df.join(clean, 'id', 'left_anti')\n"
        "rejected.write.saveAsTable('quarantine_rows')\n"
    )
    assert nb_dq_rules(_ctx(_nb(code))).score == _PASS


def test_restart_reports_a_progress_marker_without_scoring_it():
    """9.1.1 is unscored: Fabric reruns from the failed activity for every pipeline.

    Microsoft documents the capability as a run-history action needing no
    configuration ("rerun the entire pipeline, or rerun only from the failed
    activity"), so a pipeline cannot fail this point by omitting a marker. What
    the note still reports is whether a *durable progress marker* exists, because
    that decides whether a rerun resumes or repeats.
    """
    pipeline = _pipe({
        "name": "Load batch", "type": "Copy",
        "typeProperties": {"watermark": "control_table.last_loaded"},
    })
    verdict = restart_from_failure(_ctx(pipeline))
    assert verdict.score is None, "the platform provides this, so nothing is scored"
    assert verdict.status is Status.INFO
    assert "durable progress marker" in verdict.evidence


def test_restart_note_says_when_no_progress_marker_is_present():
    """Still unscored - but it points at 2.4.6, which judges whether a rerun is safe."""
    pipeline = _pipe(
        {"name": "Notebook1", "type": "TridentNotebook"},
        {
            "name": "Error Notebook", "type": "TridentNotebook",
            "dependsOn": [{"activity": "Notebook1", "dependencyConditions": ["Failed"]}],
            "typeProperties": {
                "parameters": {
                    "run_id": {
                        "value": {"value": "@pipeline().RunId", "type": "Expression"},
                        "type": "string",
                    },
                    "error_message": {
                        "value": {"value": "@activity('Notebook1').Error.Message", "type": "Expression"},
                        "type": "string",
                    },
                },
            },
        },
    )
    verdict = restart_from_failure(_ctx(pipeline))
    assert verdict.score is None
    assert "2.4.6" in verdict.evidence, "the reader is pointed at the check that scores"


def test_audit_quality_log_writer_is_detected():
    code = """
quality_log = df.select('batch_id', 'row_count', 'null_count', 'exception_count')
quality_log.write.mode('append').saveAsTable('dq_audit_log')
"""
    context = CheckContext(
        workspace=WorkspaceContext(id="w", notebooks={"dq": _nb(code)}),
        settings={}, obj_name="w", obj=None,
    )
    assert audit_tables_capture_quality_logs(context).score == _PASS


def test_failure_alert_requires_failed_link():
    pipeline = _pipe(
        {"name": "Load", "type": "Copy"},
        {"name": "Send Teams Alert", "type": "Teams",
         "dependsOn": [{"activity": "Load", "dependencyConditions": ["Failed"]}]},
    )
    assert pipeline_failure_alert(_ctx(pipeline)).score == _PASS


def test_new_checks_return_na_when_required_artifact_is_missing():
    notebook = _ctx(_nb("print('no data')"))
    pipeline = _ctx(_pipe())
    assert nb_dq_rules(notebook).status is Status.NA
    assert restart_from_failure(pipeline).status is Status.NA
    assert pipeline_failure_alert(pipeline).status is Status.NA


# -- PL-PARAM parameterization (design-aware) ---------------------------------

def test_declared_pipeline_parameters_pass():
    pipeline = {"properties": {"parameters": {"p_load_date": {"type": "string"}},
                               "activities": []}}
    verdict = parameterized(_ctx(pipeline))
    assert verdict.score == _PASS
    assert "pipeline parameters" in verdict.evidence


def test_metadata_driven_lookup_passes_without_declared_parameters():
    """A control-table framework resolves source/target from a lookup + item, not
    from a declared ``parameters`` block — it must score PASS, not PARTIAL."""
    pipeline = _pipe({
        "name": "Copy_From_Control_Table", "type": "Copy",
        "typeProperties": {"source": {
            "schemaName": "@item().source_schema_name",
            "query": "@activity('LKP_control').output.value[0].source_table_name",
        }},
    })
    assert parameterized(_ctx(pipeline)).score == _PASS


def test_managed_connection_reference_counts_as_parameterized():
    pipeline = _pipe({
        "name": "Copy", "type": "Copy",
        "typeProperties": {"source": {"type": "OracleSource"}},
        "externalReferences": {"connection": "52b0fafd-1111-2222-3333-444455556666"},
    })
    assert parameterized(_ctx(pipeline)).score == _PASS


def test_static_pipeline_without_any_parameterization_is_partial():
    pipeline = _pipe({"name": "Run_NB", "type": "TridentNotebook"})
    assert parameterized(_ctx(pipeline)).score == 1


def test_hardcoded_endpoint_still_fails():
    pipeline = _pipe({
        "name": "Copy", "type": "Script",
        "typeProperties": {"scripts": [{"text": "Server=tcp:prod.database.windows.net;"}]},
    })
    assert parameterized(_ctx(pipeline)).score == _FAIL


# -- PL-INCREMENTAL / PL-LOADMODE (dedicated full-load pipelines) --------------

_COPY = {"name": "Copy", "type": "Copy"}
_MERGE = {"name": "Merge", "type": "Script",
          "typeProperties": {"scripts": [{"text": "MERGE INTO t USING s ON t.id = s.id"}]}}


def _named_pipe_ctx(name: str, *acts: dict) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name=name, obj=_pipe(*acts))


def test_incremental_full_load_pipeline_is_na_by_name():
    verdict = pl_incremental(_named_pipe_ctx("PL_IN_WHITM_TBL_FullLoad", _COPY))
    assert verdict.status is Status.NA
    assert "full-load" in verdict.evidence.lower()


def test_incremental_non_full_load_pipeline_still_fails():
    assert pl_incremental(_named_pipe_ctx("PL_Bronze_Load", _COPY)).score == _FAIL


def test_incremental_pattern_wins_over_a_full_load_name():
    # An explicit watermark/merge means incremental *is* implemented — PASS beats N/A.
    assert pl_incremental(_named_pipe_ctx("PL_Something_FullLoad", _MERGE)).score == _PASS


def test_incremental_notebook_only_pipeline_is_na():
    # Only data-movement is a notebook — the load logic is inside code this
    # pipeline-scoped check cannot read, so it is N/A rather than a full-reload FAIL.
    verdict = pl_incremental(_named_pipe_ctx("PL_Auditing", {"name": "Run NB", "type": "TridentNotebook"}))
    assert verdict.status is Status.NA
    assert "notebook" in verdict.evidence.lower()


def test_incremental_copy_plus_notebook_is_still_assessed():
    # A real Copy means the pipeline moves data itself — still FAIL without a pattern.
    ctx = _named_pipe_ctx("PL_Bronze_Load", _COPY, {"name": "NB", "type": "TridentNotebook"})
    assert pl_incremental(ctx).score == _FAIL


def test_load_mode_notebook_only_pipeline_is_na():
    ctx = _named_pipe_ctx("PL_Auditing", {"name": "Run NB", "type": "TridentNotebook"})
    verdict = pl_load_mode(ctx)
    assert verdict.status is Status.NA
    assert "notebook" in verdict.evidence.lower()


def test_idempotent_notebook_only_pipeline_is_na():
    ctx = _named_pipe_ctx("PL_Auditing", {"name": "Run NB", "type": "TridentNotebook"})
    verdict = pipeline_idempotent(ctx)
    assert verdict.status is Status.NA
    assert "notebook" in verdict.evidence.lower()


# -- PL-TIMEOUT (default timeout → partial, not fail) --------------------------

def test_timeout_default_value_is_partial():
    verdict = explicit_timeouts(_ctx(_pipe(
        {"name": "Copy", "type": "Copy", "policy": {"timeout": "0.12:00:00"}})))
    assert verdict.score == 1
    assert "default timeout" in verdict.evidence.lower()


def test_timeout_custom_value_passes():
    assert explicit_timeouts(_ctx(_pipe(
        {"name": "Copy", "type": "Copy", "policy": {"timeout": "0.02:00:00"}}))).score == _PASS


def test_timeout_no_timeout_declared_is_na():
    verdict = explicit_timeouts(_ctx(_pipe(
        {"name": "Invoke", "type": "ExecutePipeline", "policy": {"secureInput": False}})))
    assert verdict.status is Status.NA


def test_load_mode_passes_when_the_name_declares_a_dedicated_mode():
    assert pl_load_mode(_named_pipe_ctx("PL_IN_WHITM_TBL_FullLoad", _COPY)).score == _PASS
    assert pl_load_mode(_named_pipe_ctx("PL_DynamicIngestionPipelineIncrmLoad", _COPY)).score == _PASS


def test_load_mode_without_name_param_or_branch_still_fails():
    assert pl_load_mode(_named_pipe_ctx("PL_Generic_Copy", _COPY)).score == _FAIL


# -- PL-METADATA-DRIVEN ------------------------------------------------------

def _metadata_lookup(name: str = "Lookup_Ingestion_Config", query: str | None = None) -> dict:
    return {
        "name": name,
        "type": "Lookup",
        "typeProperties": {
            "source": {
                "type": "DataWarehouseSource",
                "sqlReaderQuery": query or (
                    "SELECT SourceName, LoadType, ScheduleName, TargetTable "
                    "FROM dbo.ETL_Ingestion_Config"
                ),
            },
        },
    }


def _lookup_loop(source: str, name: str = "ForEach_Config_Row") -> dict:
    return {
        "name": name,
        "type": "ForEach",
        "typeProperties": {
            "items": {
                "value": f"@activity('{source}').output.value",
                "type": "Expression",
            },
            "activities": [{"name": "Run_Configured_Load", "type": "TridentNotebook"}],
        },
    }


def test_metadata_driven_passes_only_when_complete_metadata_lookup_feeds_loop():
    verdict = pl_metadata_driven(_named_pipe_ctx(
        "PL_Metadata_Driven_PASS",
        _metadata_lookup(),
        _lookup_loop("Lookup_Ingestion_Config"),
    ))
    assert verdict.score == _PASS
    assert "PL_Metadata_Driven_PASS" in verdict.evidence
    assert "Lookup_Ingestion_Config" in verdict.evidence
    assert "ForEach_Config_Row" in verdict.evidence
    assert "source list, load type, schedule and target mapping" in verdict.evidence


def test_metadata_driven_ignores_metadata_named_connection_for_generic_query():
    lookup = {
        "name": "Lookup_Runtime_Rows",
        "type": "Lookup",
        "typeProperties": {
            "source": {
                "type": "DataWarehouseSource",
                "sqlReaderQuery": "SELECT ItemName FROM dbo.RuntimeRows",
            },
            "datasetSettings": {
                "linkedService": {"name": "WH_Metadata"},
            },
        },
    }
    loop = {
        "name": "ForEach_Runtime_Row",
        "type": "ForEach",
        "typeProperties": {
            "items": {
                "value": "@activity('Lookup_Runtime_Rows').output.value",
                "type": "Expression",
            },
            "activities": [{"name": "Run_Runtime_Row", "type": "TridentNotebook"}],
        },
    }
    verdict = pl_metadata_driven(
        _named_pipe_ctx("PL_Generic_Lookup_Loop_PARTIAL", lookup, loop)
    )
    assert verdict.score == 1
    assert "not a metadata/config source" in verdict.evidence


def test_metadata_driven_does_not_pair_unrelated_metadata_lookup_and_loop():
    generic_lookup = {
        "name": "Lookup_Runtime_Rows",
        "type": "Lookup",
        "typeProperties": {
            "source": {"sqlReaderQuery": "SELECT ItemName FROM dbo.RuntimeRows"},
        },
    }
    verdict = pl_metadata_driven(_named_pipe_ctx(
        "PL_Mismatched_Metadata_Link_TEST",
        _metadata_lookup(),
        generic_lookup,
        _lookup_loop("Lookup_Runtime_Rows", "ForEach_Runtime_Row"),
    ))
    assert verdict.score == 1
    assert "iterates 'Lookup_Runtime_Rows' instead" in verdict.evidence
    assert "does not drive ingestion" in verdict.evidence


def test_metadata_driven_lookup_without_foreach_is_partial():
    verdict = pl_metadata_driven(_named_pipe_ctx(
        "PL_Metadata_Lookup_Only_PARTIAL",
        _metadata_lookup(),
        {"name": "Run_Fixed_Load", "type": "TridentNotebook"},
    ))
    assert verdict.score == 1
    assert "no ForEach iterates its output" in verdict.evidence


def test_metadata_driven_link_missing_required_dimensions_is_partial():
    lookup = _metadata_lookup(query=(
        "SELECT SourceName, TargetTable FROM dbo.ETL_Ingestion_Config"
    ))
    verdict = pl_metadata_driven(_named_pipe_ctx(
        "PL_Metadata_Missing_Fields_PARTIAL",
        lookup,
        _lookup_loop("Lookup_Ingestion_Config"),
    ))
    assert verdict.score == 2
    assert "load type, schedule" in verdict.evidence


def test_metadata_driven_hardcoded_activity_fails_with_pipeline_evidence():
    verdict = pl_metadata_driven(_named_pipe_ctx(
        "PL_Hardcoded_Ingestion_FAIL",
        {"name": "Load_AdageCustomers", "type": "TridentNotebook"},
    ))
    assert verdict.score == _FAIL
    assert "PL_Hardcoded_Ingestion_FAIL" in verdict.evidence


def test_metadata_driven_empty_pipeline_is_na_with_pipeline_evidence():
    verdict = pl_metadata_driven(_named_pipe_ctx("PL_No_Data_NA"))
    assert verdict.status is Status.NA
    assert "PL_No_Data_NA" in verdict.evidence


# -- PL-HIST-SEPARATION -------------------------------------------------------

def test_historical_separation_named_empty_pipeline_is_na():
    verdict = pl_historical_separation(_named_pipe_ctx("PL_Adage_Historical_Backfill"))
    assert verdict.status is Status.NA
    assert "no activities" in verdict.evidence


def test_historical_separation_dedicated_pipeline_passes_with_activity_evidence():
    ctx = _named_pipe_ctx(
        "PL_Adage_Historical_Backfill",
        {"name": "Run_Historical_Backfill", "type": "TridentNotebook"},
    )
    verdict = pl_historical_separation(ctx)
    assert verdict.score == _PASS
    assert "Run_Historical_Backfill" in verdict.evidence


def test_historical_separation_named_pipeline_still_fails_when_paths_are_inline():
    ctx = _named_pipe_ctx(
        "PL_Adage_Historical_Backfill",
        {"name": "Run_Historical_Backfill", "type": "TridentNotebook"},
        {"name": "Run_Daily_Incremental", "type": "TridentNotebook"},
    )
    assert pl_historical_separation(ctx).score == _FAIL


def test_historical_separation_inline_combined_pipeline_fails():
    ctx = _named_pipe_ctx(
        "PL_Combined_Load_Test",
        {"name": "Run_Historical_Backfill", "type": "TridentNotebook"},
        {"name": "Run_Daily_Incremental", "type": "TridentNotebook"},
    )
    verdict = pl_historical_separation(ctx)
    assert verdict.score == _FAIL
    assert "ungated" in verdict.evidence


def test_historical_separation_if_condition_passes_and_names_gate():
    branch = {
        "name": "Choose_Load_Mode",
        "type": "IfCondition",
        "typeProperties": {
            "expression": {"value": "@equals('Historical', 'Incremental')"},
            "ifTrueActivities": [
                {"name": "Run_Historical_Backfill", "type": "TridentNotebook"},
            ],
            "ifFalseActivities": [
                {"name": "Run_Daily_Incremental", "type": "TridentNotebook"},
            ],
        },
    }
    verdict = pl_historical_separation(_named_pipe_ctx("PL_Load_Mode_Orchestrator", branch))
    assert verdict.score == _PASS
    assert "IfCondition activity 'Choose_Load_Mode'" in verdict.evidence


def test_historical_separation_historical_only_pipeline_is_partial():
    ctx = _named_pipe_ctx(
        "PL_Load_Investigation",
        {"name": "Run_Historical_Backfill", "type": "TridentNotebook"},
    )
    assert pl_historical_separation(ctx).score == 1


def test_historical_separation_no_historical_signal_is_na():
    verdict = pl_historical_separation(_named_pipe_ctx("PL_Daily_Load", _COPY))
    assert verdict.status is Status.NA
    assert "PL_Daily_Load" in verdict.evidence


@pytest.mark.parametrize("name", [
    "PL_IN_WHITMPK_TBL_FullLoad",
    "PL_IN_WHITM_TBL_FullLoad",
])
def test_historical_separation_full_load_name_alone_is_na(name):
    verdict = pl_historical_separation(_named_pipe_ctx(
        name,
        {"name": "Copy_IN_WHITM_INCROQ", "type": "Copy"},
    ))
    assert verdict.status is Status.NA
    assert "no historical/backfill load signal" in verdict.evidence


def test_historical_separation_ignores_project_schema_literal_in_definition():
    # Regression (real MLC_ADAGE data): PL_IN_WHITMPK_TBL_FullLoad was scored
    # PARTIAL only because the historical detector matched the project's own
    # 'ADAGE' schema name buried in a Copy sink's typeProperties - an incidental
    # data value, not a load-intent signal - while its structurally identical
    # twin PL_IN_WHITM_TBL_FullLoad (which names the schema via an expression)
    # was N/A. A full-load pipeline with no historical/backfill naming must be
    # N/A regardless of the schema / table names it writes to.
    copy_to_adage_schema = {
        "name": "ACT_MT_Copy_ingestBlobdataForFullLoad",
        "type": "Copy",
        "typeProperties": {
            "sink": {
                "type": "LakehouseTableSink",
                "tableActionOption": "OverwriteSchema",
                "datasetSettings": {
                    "typeProperties": {
                        "schema": {"value": "ADAGE", "type": "Expression"},
                        "table": {"value": "IN_WHITM", "type": "Expression"},
                    }
                },
            }
        },
    }
    verdict = pl_historical_separation(
        _named_pipe_ctx("PL_IN_WHITMPK_TBL_FullLoad", copy_to_adage_schema)
    )
    assert verdict.status is Status.NA
    assert "no historical/backfill load signal" in verdict.evidence


# -- PL-DEADLETTER structural routing ----------------------------------------

def test_deadletter_ignores_error_words_inside_copy_column_mappings():
    pipeline = _pipe({
        "name": "Copy IFS rows",
        "type": "Copy",
        "typeProperties": {
            "translator": {"mappings": [
                {"source": {"name": "ERROR_DESC"}, "sink": {"name": "ERROR_DESC"}},
                {"source": {"name": "REJECT_CODE"}, "sink": {"name": "REJECT_CODE"}},
            ]},
        },
    })
    verdict = pl_deadletter(_named_pipe_ctx("PL_Copy_Business_Error_Columns", *pipeline["properties"]["activities"]))
    assert verdict.score == _FAIL
    assert "no structural failed-record route" in verdict.evidence


def test_deadletter_passes_copy_redirect_incompatible_rows():
    copy = {
        "name": "Copy with incompatible-row redirect",
        "type": "Copy",
        "typeProperties": {
            "enableSkipIncompatibleRow": True,
            "redirectIncompatibleRowSettings": {"linkedServiceName": "RejectStore"},
        },
    }
    verdict = pl_deadletter(_named_pipe_ctx("PL_Copy_Redirect", copy))
    assert verdict.score == _PASS
    assert "Incompatible rows are redirected" in verdict.evidence


def test_deadletter_passes_failed_dependency_to_quarantine_activity():
    pipeline = _named_pipe_ctx(
        "PL_Failed_Dependency_Route",
        {"name": "Copy source", "type": "Copy"},
        {
            "name": "Write quarantine log", "type": "Script",
            "dependsOn": [{"activity": "Copy source", "dependencyConditions": ["Failed"]}],
            "typeProperties": {"scripts": [{"text": "INSERT INTO quarantine.failed_rows SELECT 1"}]},
        },
    )
    verdict = pl_deadletter(pipeline)
    assert verdict.score == _PASS
    # The activity's own name marks it as the quarantine step, which is the
    # stronger signal and is reported ahead of the [Failed] dependency.
    assert "quarantine/reject step is present" in verdict.evidence
    assert "Write quarantine log" in verdict.evidence


def test_deadletter_is_na_without_any_failed_record_signal():
    verdict = pl_deadletter(_named_pipe_ctx(
        "PL_Ordinary_Copy",
        {"name": "Copy customer rows", "type": "Copy"},
    ))
    assert verdict.score == _FAIL
    assert "no structural failed-record route" in verdict.evidence


# -- NB-DEADLETTER rejected-row persistence ----------------------------------

def test_notebook_deadletter_fails_when_rejected_dataframe_is_not_written():
    code = """
rejected_df = source.filter("is_valid = false")
clean_df = source.filter("is_valid = true")
clean_df.write.mode("append").saveAsTable("silver.clean_orders")
"""
    verdict = nb_deadletter(_ctx(_nb(code)))

    assert verdict.score == _FAIL
    assert "rejected_df" in verdict.evidence
    assert "not written" in verdict.evidence


def test_notebook_deadletter_passes_when_rejected_dataframe_is_written():
    code = """
rejected_df = source.filter("is_valid = false")
rejected_df.write.mode("append").saveAsTable("ops.records")
"""
    verdict = nb_deadletter(_ctx(_nb(code)))

    assert verdict.score == _PASS
    assert "rejected_df" in verdict.evidence


def test_notebook_deadletter_passes_when_sink_name_identifies_quarantine():
    code = """
failed_rows = source.filter("is_valid = false")
failed_rows.write.mode("append").saveAsTable("dq.order_quarantine")
"""
    verdict = nb_deadletter(_ctx(_nb(code)))

    assert verdict.score == _PASS
    assert "order_quarantine" in verdict.evidence


def test_notebook_deadletter_ignores_error_words_in_table_schema_columns():
    code = '''
spark.sql("""
CREATE OR REPLACE TABLE mlc_mapping_source (
    sale_id BIGINT,
    ERROR_DESC STRING,
    REJECT_CODE STRING
) USING DELTA
""")
spark.sql("""
CREATE OR REPLACE TABLE mlc_mapping_sink (
    sale_id BIGINT,
    ERROR_DESC STRING,
    REJECT_CODE STRING
) USING DELTA
""")
spark.sql("INSERT INTO mlc_mapping_source VALUES (1, 'message', 'R01')")
'''
    verdict = nb_deadletter(_ctx(_nb(code)))

    assert verdict.status is Status.NA
    assert "no structural failed-record" in verdict.evidence


# -- TB-SCD2 aliases ---------------------------------------------------------

def test_scd2_detects_alias_trio_and_flags_non_standard_names():
    table = {
        "columns": [
            {"name": "customer_key"}, {"name": "effective_date"},
            {"name": "end_date"}, {"name": "active_flag"},
            {"name": "customer_name", "type": "varchar"},
            {"name": "city", "type": "varchar"},
        ],
    }
    verdict = table_scd2(_tables_ctx(dim_customer=table))
    assert verdict.score == _PASS          # the trio is complete
    assert "Non-standard column names in use" in verdict.evidence
    assert "effective_date" in verdict.evidence


def test_scd2_no_pattern_reason_acknowledges_readable_column_metadata():
    """The N/A must say the columns *were* read, so the gap is not read as a failure."""
    table = {
        "columns": [
            {"name": "customer_key"},
            {"name": "customer_name", "type": "varchar"},
            {"name": "city", "type": "varchar"},
        ],
    }
    verdict = table_scd2(_tables_ctx(dim_customer=table))
    assert verdict.status is Status.NA
    assert "Column metadata was read" in verdict.evidence


def test_scd2_detects_the_trio_on_a_non_dimension_table():
    """SCD2 history tables are often Silver tables the role classifier reads as
    unknown/fact, so the scan must cover every table, not only dimensions."""
    table = {
        "columns": [
            {"name": "product_code", "type": "varchar"},
            {"name": "price", "type": "decimal"},
            {"name": "valid_from"}, {"name": "valid_until"}, {"name": "active_flag"},
        ],
    }
    verdict = table_scd2(_tables_ctx(price_versions=table))
    assert verdict.score == _PASS
    assert "Non-standard column names in use" in verdict.evidence


def test_scd2_bare_start_end_pair_without_a_flag_is_not_scd2():
    """A start/end date pair with no current-flag is a validity period, not SCD2."""
    table = {
        "columns": [
            {"name": "product_code", "type": "varchar"},
            {"name": "effective_date"}, {"name": "expiration_date"},
        ],
    }
    verdict = table_scd2(_tables_ctx(prod_price=table))
    assert verdict.status is Status.NA
    assert "no table is versioned as SCD Type 2" in verdict.evidence


# -- PL-COPY-PARALLEL (lone copy → N/A) ----------------------------------------

def test_copy_parallel_single_untuned_copy_is_na():
    verdict = copy_parallelism(_ctx(_pipe({"name": "Copy", "type": "Copy"})))
    assert verdict.status is Status.NA
    assert "Only 1 Copy activity" in verdict.evidence


def test_copy_parallel_single_tuned_copy_passes():
    copy = {"name": "Copy", "type": "Copy", "typeProperties": {"parallelCopies": 4}}
    assert copy_parallelism(_ctx(_pipe(copy))).score == _PASS


def test_copy_parallel_multiple_untuned_copies_still_fail():
    copy = {"name": "Copy", "type": "Copy"}
    assert copy_parallelism(_ctx(_pipe(copy, dict(copy, name="Copy2")))).score == _FAIL


def test_copy_parallel_no_copy_activity_is_na():
    verdict = copy_parallelism(_ctx(_pipe({"name": "NB", "type": "TridentNotebook"})))
    assert verdict.status is Status.NA
    assert "no Copy activities" in verdict.evidence


# -- DELTA-OPTIMIZE ------------------------------------------------------------

def test_optimize_word_in_string_is_not_a_command():
    """The English word "Optimize" in a string must not count as the SQL command (was a false PASS)."""
    code = 'df.write.saveAsTable("t")\nmcem_level = "Manage and Optimize"'
    assert delta_optimize(_ctx(_nb(code))).score == _FAIL


def test_real_optimize_command_passes():
    code = 'df.write.saveAsTable("t")\nspark.sql("OPTIMIZE t")'
    assert delta_optimize(_ctx(_nb(code))).score == _PASS


def test_delta_optimize_api_call_passes():
    code = 'df.write.saveAsTable("t")\nDeltaTable.forName(spark, "t").optimize().executeCompaction()'
    assert delta_optimize(_ctx(_nb(code))).score == _PASS


# -- NB-TIMEOUT ----------------------------------------------------------------

def test_zero_keepalive_timeout_is_na_not_pass():
    """sessionKeepAliveTimeout=0 means "unset" — N/A, not a false PASS."""
    nb = _nb(metadata={"sessionKeepAliveTimeout": 0})
    assert nb_timeout(_ctx(nb)).status is Status.NA


def test_missing_timeout_metadata_is_na():
    assert nb_timeout(_ctx(_nb(metadata={"language_info": {"name": "python"}}))).status is Status.NA


def test_positive_timeout_passes():
    nb = _nb(metadata={"sessionKeepAliveTimeout": 1800})
    assert nb_timeout(_ctx(nb)).score == _PASS


def test_default_session_timeout_is_partial_not_pass():
    """Fabric stamps spark.synapse.nbs.session.timeout=600000 (10 min) on every
    notebook; carrying only that default is a PARTIAL (tune it), not a PASS."""
    nb = _nb(metadata={"spark_compute": {"session_options": {"conf": {
        "spark.synapse.nbs.session.timeout": "600000"}}}})
    v = nb_timeout(_ctx(nb))
    assert v.score == 1
    assert "default" in v.evidence.lower()


def test_custom_session_timeout_passes():
    """A non-default session timeout is a deliberately configured cap -> PASS."""
    nb = _nb(metadata={"spark_compute": {"session_options": {"conf": {
        "spark.synapse.nbs.session.timeout": "300000"}}}})
    assert nb_timeout(_ctx(nb)).score == _PASS


# -- WS-ORPHAN -----------------------------------------------------------------

def _ws_items_ctx(*items: Item) -> CheckContext:
    ws = WorkspaceContext(id="w", items=list(items))
    return CheckContext(workspace=ws, settings={"orphan_days": 90}, obj_name="w", obj=None)


def test_orphan_all_missing_timestamps_is_na_not_fail():
    """No item exposes a run/refresh timestamp -> N/A, not a 0% FAIL of every item."""
    ctx = _ws_items_ctx(
        Item(id="1", type="Notebook", display_name="A"),
        Item(id="2", type="DataPipeline", display_name="B"),
    )
    assert no_orphaned_items(ctx).status is Status.NA


def test_orphan_recent_items_pass():
    now_iso = datetime.now(timezone.utc).isoformat()
    ctx = _ws_items_ctx(Item(id="1", type="Notebook", display_name="A", last_run_utc=now_iso))
    assert no_orphaned_items(ctx).score == _PASS


def test_orphan_stale_item_is_flagged():
    ctx = _ws_items_ctx(
        Item(id="1", type="Notebook", display_name="A", last_run_utc="2000-01-01T00:00:00Z")
    )
    assert no_orphaned_items(ctx).score == _FAIL  # 0 of 1 fresh


def test_orphan_scores_only_items_with_a_timestamp():
    """An item with no timestamp is excluded, not counted as stale."""
    now_iso = datetime.now(timezone.utc).isoformat()
    ctx = _ws_items_ctx(
        Item(id="1", type="Notebook", display_name="A", last_run_utc=now_iso),
        Item(id="2", type="Notebook", display_name="B"),  # no timestamp -> excluded
    )
    assert no_orphaned_items(ctx).coverage == 1.0  # 1 dated item, and it is fresh


# =============================================================================
# Review fixes: 4.2.5 audit-column matching, 3.2.1 vacuous pass,
# 3.2.4 evidence, 5.3.6 cross-source reconciliation.
# =============================================================================

@pytest.mark.parametrize("name", [
    "created_date", "CreatedDate", "createdDate", "_CREATED_DT", "created_at",
    "modified_at", "LastModified", "date_created", "load_date", "load_ts",
    "ingestion_timestamp", "createdOnBehalfBy", "SinkModifiedOn",
    "ETLInsertedDateTime", "batch_id", "BatchId", "etl_batch_id",
    "collection_batch_id", "root_batch_id", "run_id", "source_system",
    "SourceSystem", "source_file", "source_table",
])
def test_audit_column_spellings_are_recognised(name: str):
    """Exact-tuple matching saw only ``created_date`` and reported 7 of 502 tables."""
    assert is_audit_column(name), name


@pytest.mark.parametrize("name", [
    "order_date", "birth_date", "start_date", "end_date", "due_date",
    "ship_date", "customer_id", "product_key", "amount", "invoice_date",
    # Dataverse business-process metadata, not lineage. A first cut matched all
    # of these through a "process" event word and inflated the result.
    "processid", "processname", "process", "processversion", "processmapversion",
    # As often a business attribute as a lineage one.
    "SourceName", "SourceID",
    # Batch *sizing* settings, which are configuration rather than lineage.
    "syncbulkoperationbatchsize", "recurrenceexpansionjobbatchinterval",
])
def test_business_columns_are_not_audit_columns(name: str):
    """A business event is not lineage metadata - the vocabulary stays narrow."""
    assert not is_audit_column(name), name


def test_audit_columns_check_accepts_camel_case():
    ctx = _tables_ctx(
        dim_customer={"columns": [{"name": "CreatedDate"}, {"name": "CustomerId"}]},
        fact_sales={"columns": [{"name": "order_date"}, {"name": "amount"}]},
    )
    assert table_audit_columns(ctx).coverage == 0.5


def test_a_notebook_using_neither_spark_dialect_is_na():
    """Pure pandas has no Spark language choice - it must not score a vacuous pass."""
    ctx = _ctx(_nb("import pandas as pd\ndf = pd.read_csv('x.csv')\nprint(df.head())\n"))
    assert nb_language(ctx).status is Status.NA


def test_a_single_spark_dialect_still_passes():
    ctx = _ctx(_nb('df = spark.table("t").withColumn("a", lit(1))\n'))
    verdict = nb_language(ctx)
    assert verdict.score == _PASS
    assert "DataFrame API only" in verdict.evidence


def test_mixed_spark_dialects_are_partial():
    ctx = _ctx(_nb('df = spark.table("t").withColumn("a", lit(1))\nspark.sql("SELECT 1")\n'))
    assert nb_language(ctx).score == 1


def test_broadcast_evidence_does_not_claim_per_join_analysis():
    """The check searches the whole notebook, so the evidence must say so."""
    ctx = _ctx(_nb('a.join(b, "k")\nc.join(d, "k")\n'))
    evidence = nb_broadcast(ctx).evidence
    assert "no broadcast() hint anywhere in the notebook" in evidence
    assert "join(s) without a broadcast() hint" not in evidence


def test_a_lone_row_count_assert_is_not_cross_source_reconciliation():
    """5.3.6 shared 5.2.5's regex, so one count assertion satisfied both."""
    code = ('a = spark.read.parquet("/a")\nb = spark.read.parquet("/b")\n'
            'assert a.count() == 100\n')
    verdict = nb_cross_recon(_ctx(_nb(code)))
    assert verdict.score == 1
    assert "nothing compares the sources" in verdict.evidence


def test_two_counts_compared_is_cross_source_reconciliation():
    code = ('a = spark.read.parquet("/a")\nb = spark.read.parquet("/b")\n'
            'assert a.count() == b.count()\n')
    assert nb_cross_recon(_ctx(_nb(code))).score == _PASS


def test_a_set_difference_is_cross_source_reconciliation():
    code = ('a = spark.read.parquet("/a")\nb = spark.read.parquet("/b")\n'
            'missing = a.subtract(b)\n')
    assert nb_cross_recon(_ctx(_nb(code))).score == _PASS


def test_multi_source_with_no_validation_at_all_still_fails():
    code = ('a = spark.read.parquet("/a")\nb = spark.read.parquet("/b")\n'
            'a.join(b, "k").write.saveAsTable("t")\n')
    assert nb_cross_recon(_ctx(_nb(code))).score == _FAIL
