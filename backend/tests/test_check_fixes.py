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
    nb_bronze_metadata,
    nb_broadcast,
    nb_cross_recon,
    nb_dedup_verify,
    nb_dq_rules,
    nb_eam_ingest,
    nb_flag_domain,
    nb_language,
    nb_no_display,
    nb_no_udf,
    nb_silver_quality,
    nb_source_metadata,
    nb_timeout,
    nb_utf8_encoding,
    parameterized,
    pl_bulk_move,
    pl_incremental,
    pl_load_mode,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    table_audit_columns,
    table_date_dimension,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    audit_tables_capture_quality_logs,
    pipeline_failure_alert,
)
from auditfast.core.check.operations_reliability.data_prep.automated import (
    explicit_timeouts,
    failure_notification,
    pipeline_idempotent,
    restart_from_failure,
)
from auditfast.core.check.performance_capacity.data_prep.automated import (
    copy_parallelism,
    delta_optimize,
    spark_env,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext


def _nb(code: str = "", metadata: dict | None = None) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": metadata or {}}


def _ctx(obj) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={}, obj_name="nb", obj=obj)


def _pipe(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


def _tables_ctx(**tables: dict) -> CheckContext:
    ws = WorkspaceContext(id="w", tables=tables)
    return CheckContext(workspace=ws, settings={}, obj_name="w", obj=None)


#: A check body returns a raw ``Verdict`` whose ``status`` the engine derives
#: from the score later, so a passing/failing verdict is asserted via ``score``
#: (3 = pass, 0 = fail, 1 = partial); only N/A carries a ``status`` at this stage.
_PASS, _FAIL = 3, 0


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


def test_notebook_standardization_covers_dates_codes_and_reference_mapping():
    code = """
records = spark.read.json('Files/eam.json')
records = records.withColumn('event_date', to_date('event_date'))
records = records.withColumn('employee_code', upper(trim('employee_code')))
records = records.join(reference_mapping, 'employee_code')
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


def test_silver_quality_passes_with_dedup_and_type_conformance():
    code = """
silver = spark.read.table('bronze_events')
silver = silver.dropDuplicates(['event_id']).withColumn('event_date', to_date('event_date'))
silver.write.saveAsTable('silver_events')
"""
    assert nb_silver_quality(_ctx(_nb(code))).score == _PASS


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
    assert pl_bulk_move(pipeline).score == _FAIL


# -- DQ RULES / RESTART / AUDIT LOG / FAILURE ALERT ---------------------------

def test_dq_rules_are_codified():
    code = "df = spark.read.json('Files/input.json')\nassert df.filter(df.id.isNull()).count() == 0"
    assert nb_dq_rules(_ctx(_nb(code))).score == _PASS


def test_restart_boundary_is_detected():
    pipeline = _pipe({
        "name": "Load batch", "type": "Copy",
        "typeProperties": {"watermark": "control_table.last_loaded"},
    })
    assert restart_from_failure(_ctx(pipeline)).score == _PASS


def test_run_id_logging_is_not_restart_boundary():
    """A failure logger carrying Fabric's run id is not proof of restart-from-failure."""
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
    assert restart_from_failure(_ctx(pipeline)).score == _FAIL


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
