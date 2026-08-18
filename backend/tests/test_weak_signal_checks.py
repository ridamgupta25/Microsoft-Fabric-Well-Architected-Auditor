"""Tests for the six deliberately weak-signal checks (refs 4.5.2, 4.5.11, 4.6.3,
5.2.2, 5.2.3, 10.4.4).

These points are only *partly* readable, so the tests pin the honesty as much as
the detection:

* 4.5.2 scores the "clearly defined" half only — the evidence must say the
  *documented* half is not readable from any API.
* 4.5.11 is unscored on purpose (a ``note``): "where appropriate" is a modelling
  judgement and cardinality needs row data, which is never fetched.
* 4.6.3 is a *workspace-level proxy*: the evidence must say so, and unreadable
  role assignments must be N/A, never a FAIL.
* 5.2.2 / 5.2.3 verify that the safeguard exists, not that a load was complete or
    on time — and 5.2.3 distinguishes a custom pipeline timeout from Fabric's
    stamped multi-hour default.
* 10.4.4 reads trend *mechanics* from the model; a report's visuals are never
  read, so a model with a date axis but no time intelligence is named rather than
  silently judged complete.
"""
from __future__ import annotations

from auditfast.core.check._dax import time_intelligence_calls, uses_time_intelligence
from auditfast.core.check.data_management_quality.data_logs.automated import (
    metadata_store_write_access_is_restricted,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    notebook_has_an_arrival_completeness_control,
    pipeline_has_a_timeliness_control,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    degenerate_and_junk_dimension_candidates,
    fact_grain_is_identifiable,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    monitoring_models_support_trend_analysis,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, RoleAssignment, WorkspaceContext

_PASS, _PARTIAL_LOW, _FAIL = 3, 1, 0


# -- context builders ---------------------------------------------------------

def _table(*cols: tuple[str, str], store: str = "", kind: str = "") -> dict:
    return {
        "type": "Managed", "format": "Delta",
        "store": store, "store_kind": kind,
        "columns": [{"name": name, "type": ctype} for name, ctype in cols],
    }


def _ws_ctx(**kwargs) -> CheckContext:
    workspace = WorkspaceContext(id="w", **kwargs)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


def _tables_ctx(**tables: dict) -> CheckContext:
    return _ws_ctx(tables=tables)


def _nb_ctx(code: str) -> CheckContext:
    definition = {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="nb", obj=definition)


def _pipeline_ctx(*activities: dict) -> CheckContext:
    definition = {"properties": {"activities": list(activities)}}
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="pipeline", obj=definition)


def _refresh_activity(timeout: str | None = None) -> dict:
    activity = {"name": "Refresh_Source", "type": "RefreshDataflow"}
    if timeout is not None:
        activity["policy"] = {"timeout": timeout}
    return activity


def _role(role: str, principal_type: str = "User", name: str = "someone") -> RoleAssignment:
    return RoleAssignment(principal_type=principal_type, display_name=name,
                          role=role, principal_id=name)


# =============================================================================
# 4.5.2 — TB-FACT-GRAIN
# =============================================================================

def test_fact_grain_passes_when_the_schema_declares_at_least_two_grain_keys():
    ctx = _tables_ctx(fact_sales=_table(
        ("sales_sk", "bigint"),          # the fact's own identity, not a grain key
        ("customer_sk", "bigint"),
        ("product_sk", "bigint"),
        ("amount", "decimal(18,2)"),
    ))
    verdict = fact_grain_is_identifiable(ctx)
    assert verdict.score == _PASS
    assert "1 of 1 fact table(s) declare a readable grain" in verdict.evidence


def test_fact_grain_counts_a_timestamp_column_as_the_time_component():
    """"One row per customer per day" is a grain a schema can state."""
    ctx = _tables_ctx(fact_sales=_table(
        ("sales_sk", "bigint"),
        ("customer_sk", "bigint"),
        ("event_time", "timestamp"),
        ("amount", "decimal(18,2)"),
    ))
    assert fact_grain_is_identifiable(ctx).score == _PASS


def test_fact_grain_fails_when_only_the_facts_own_key_is_present():
    ctx = _tables_ctx(fact_sales=_table(
        ("sales_sk", "bigint"),
        ("amount", "decimal(18,2)"),
    ))
    verdict = fact_grain_is_identifiable(ctx)
    assert verdict.score == _FAIL
    assert "grain not evident on fact_sales" in verdict.evidence


def test_fact_grain_is_partial_when_only_some_facts_declare_one():
    ctx = _tables_ctx(
        fact_sales=_table(("sales_sk", "bigint"), ("customer_sk", "bigint"),
                          ("product_sk", "bigint")),
        fact_budget=_table(("budget_sk", "bigint"), ("amount", "decimal(18,2)")),
    )
    assert fact_grain_is_identifiable(ctx).score == _PARTIAL_LOW


def test_fact_grain_evidence_admits_the_documentation_half_is_unreadable():
    """The point asks for documented *and* defined; only one half is scored."""
    ctx = _tables_ctx(fact_sales=_table(("sales_sk", "bigint"), ("customer_sk", "bigint"),
                                        ("product_sk", "bigint")))
    assert "not readable from any Fabric or SQL" in fact_grain_is_identifiable(ctx).evidence


def test_fact_grain_is_na_without_tables():
    assert fact_grain_is_identifiable(_tables_ctx()).status is Status.NA


def test_fact_grain_is_na_when_no_fact_columns_were_read():
    ctx = _tables_ctx(fact_sales=_table(), dim_customer=_table(("customer_sk", "bigint")))
    assert fact_grain_is_identifiable(ctx).status is Status.NA


# =============================================================================
# 4.5.11 — TB-DEGENERATE-JUNK-DIM (unscored by design)
# =============================================================================

def test_degenerate_candidate_is_reported_as_an_unscored_note():
    """``order_number`` on a fact with no ``dim_order`` is the classic shape."""
    ctx = _tables_ctx(
        fact_sales=_table(("sales_sk", "bigint"), ("customer_sk", "bigint"),
                          ("order_number", "varchar(20)")),
        dim_customer=_table(("customer_sk", "bigint"), ("customer_name", "varchar(50)")),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.status is Status.INFO
    assert verdict.scored is False
    assert verdict.score is None
    assert "order_number" in verdict.evidence
    assert "degenerate-dimension candidates" in verdict.evidence


def test_a_key_resolving_to_a_dimension_is_not_a_degenerate_candidate():
    ctx = _tables_ctx(
        fact_sales=_table(("sales_sk", "bigint"), ("customer_sk", "bigint")),
        dim_customer=_table(("customer_sk", "bigint"), ("customer_name", "varchar(50)")),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.status is Status.INFO
    assert "No degenerate or junk dimension candidate found" in verdict.evidence


def test_junk_dimension_candidate_needs_a_cluster_of_flag_columns():
    ctx = _tables_ctx(fact_sales=_table(
        ("sales_sk", "bigint"),
        ("is_active", "boolean"),
        ("paid_flag", "boolean"),
        ("order_status", "varchar(10)"),
    ))
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.status is Status.INFO
    assert "junk-dimension candidates" in verdict.evidence
    assert "3 flag column(s)" in verdict.evidence


def test_two_flag_columns_are_not_a_junk_dimension_candidate():
    ctx = _tables_ctx(fact_sales=_table(
        ("sales_sk", "bigint"), ("is_active", "boolean"), ("paid_flag", "boolean"),
    ))
    assert "junk-dimension" not in degenerate_and_junk_dimension_candidates(ctx).evidence


def test_degenerate_junk_is_na_when_no_fact_columns_were_read():
    assert degenerate_and_junk_dimension_candidates(
        _tables_ctx(fact_sales=_table())
    ).status is Status.NA


def test_degenerate_junk_is_na_without_tables():
    assert degenerate_and_junk_dimension_candidates(_tables_ctx()).status is Status.NA


# =============================================================================
# 4.6.3 — WS-METADATA-WRITE
# =============================================================================

_META = {"control_table": _table(("job_name", "varchar(50)"), ("watermark", "timestamp"))}


def test_metadata_write_passes_when_only_non_personal_identities_can_write():
    ctx = _ws_ctx(tables=dict(_META), role_assignments=[
        _role("Contributor", "ServicePrincipal", "etl-framework-spn"),
        _role("Member", "Group", "DataPlatform-Owners"),
        _role("Viewer", "User", "analyst"),
    ])
    verdict = metadata_store_write_access_is_restricted(ctx)
    assert verdict.score == _PASS
    assert "2 of 2 write-capable grant(s)" in verdict.evidence


def test_metadata_write_fails_when_a_named_user_holds_a_write_role():
    ctx = _ws_ctx(tables=dict(_META), role_assignments=[
        _role("Contributor", "User", "jane.doe"),
    ])
    verdict = metadata_store_write_access_is_restricted(ctx)
    assert verdict.score == _FAIL
    assert "jane.doe" in verdict.evidence


def test_metadata_write_evidence_states_it_is_a_workspace_level_proxy():
    ctx = _ws_ctx(tables=dict(_META), role_assignments=[
        _role("Contributor", "User", "jane.doe"),
    ])
    evidence = metadata_store_write_access_is_restricted(ctx).evidence
    assert "Workspace-level proxy only" in evidence
    assert "no per-table or per-database grant" in evidence


def test_metadata_write_is_na_when_role_assignments_could_not_be_read():
    """The NOIDA snapshot's shape: tables read, roleAssignments unavailable."""
    ctx = _ws_ctx(tables=dict(_META), unavailable={Resource.ROLE_ASSIGNMENTS})
    verdict = metadata_store_write_access_is_restricted(ctx)
    assert verdict.status is Status.NA
    assert "not the same as unrestricted" in verdict.evidence


def test_metadata_write_is_na_when_the_workspace_holds_no_metadata_store():
    ctx = _ws_ctx(tables={"fact_sales": _table(("sales_sk", "bigint"))},
                  role_assignments=[_role("Contributor", "User", "jane.doe")])
    assert metadata_store_write_access_is_restricted(ctx).status is Status.NA


def test_metadata_write_is_na_without_tables():
    assert metadata_store_write_access_is_restricted(_ws_ctx()).status is Status.NA


# =============================================================================
# 5.2.2 — NB-COMPLETENESS-CONTROL
# =============================================================================

def test_completeness_passes_when_the_missing_input_set_is_computed():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'arrived = notebookutils.fs.ls(landing_path)\n'
        'missing_files = set(expected) - {f.name for f in arrived}\n'
        'if missing_files:\n'
        '    raise ValueError(missing_files)\n'
    ))
    assert verdict.score == _PASS
    assert "missing/unreceived inputs" in verdict.evidence


def test_completeness_passes_on_an_expected_set_that_is_asserted_against():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df = spark.read.parquet(path)\n'
        'expected_partitions = build_partition_list(run_date)\n'
        'assert set(expected_partitions).difference(loaded_partitions) == set()\n'
    ))
    assert verdict.score == _PASS


def test_completeness_is_partial_when_the_expectation_is_never_acted_on():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df = spark.read.parquet(path)\n'
        'expected_file_count = 12\n'
        'print(expected_file_count)\n'
    ))
    assert verdict.score == _PARTIAL_LOW
    assert "nothing compares or" in verdict.evidence


def test_completeness_fails_when_nothing_would_notice_an_absent_batch():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df = spark.read.parquet(path)\n'
        'df.write.mode("overwrite").saveAsTable("bronze.sales")\n'
    ))
    assert verdict.score == _FAIL


def test_completeness_is_not_satisfied_by_a_commented_out_control():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df = spark.read.parquet(path)\n'
        '# missing_files = set(expected_files) - arrived\n'
    ))
    assert verdict.score == _FAIL


def test_completeness_is_not_satisfied_by_a_missing_file_mode_name():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df = spark.read.csv(path)\n'
        'if mode == "missing_file":\n'
        '    spark.read.csv("Files/does_not_exist.csv").count()\n'
    ))
    assert verdict.score == _FAIL


def test_completeness_evidence_does_not_claim_the_load_was_complete():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'arrived = notebookutils.fs.ls(p)\nmissing_partitions = expected - arrived\n'
    ))
    assert "runtime outcome this check does not read" in verdict.evidence


def test_completeness_is_na_when_the_notebook_reads_no_source():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx("x = 1 + 1\n"))
    assert verdict.status is Status.NA


def test_completeness_fails_when_a_write_has_no_recognisable_source_or_control():
    verdict = notebook_has_an_arrival_completeness_control(_nb_ctx(
        'df.write.mode("overwrite").saveAsTable("bronze.sales")\n'
    ))
    assert verdict.score == _FAIL
    assert "no recognisable source read" in verdict.evidence


def test_completeness_is_na_when_notebook_definitions_are_unavailable():
    workspace = WorkspaceContext(id="w", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    ctx = CheckContext(workspace=workspace, settings={}, obj_name="nb",
                       obj={"cells": []})
    assert notebook_has_an_arrival_completeness_control(ctx).status is Status.NA


# =============================================================================
# 5.2.3 — NB-TIMELINESS-CONTROL (pipeline-scoped for compatibility)
# =============================================================================

def test_timeliness_passes_when_every_refresh_activity_has_a_custom_timeout():
    verdict = pipeline_has_a_timeliness_control(_pipeline_ctx(
        _refresh_activity("0.01:00:00"),
        {"name": "Copy_Source", "type": "Copy", "policy": {"timeout": "0.02:00:00"}},
    ))
    assert verdict.score == _PASS
    assert "custom timeout" in verdict.evidence


def test_timeliness_is_partial_when_only_some_activities_have_custom_timeouts():
    verdict = pipeline_has_a_timeliness_control(_pipeline_ctx(
        _refresh_activity("0.01:00:00"),
        {"name": "Copy_Source", "type": "Copy", "policy": {"timeout": "0.12:00:00"}},
    ))
    assert verdict.score == 2
    assert "only partially configured" in verdict.evidence


def test_timeliness_is_partial_when_only_fabric_default_timeout_is_present():
    verdict = pipeline_has_a_timeliness_control(_pipeline_ctx(
        _refresh_activity("0.12:00:00")
    ))
    assert verdict.score == _PARTIAL_LOW
    assert "Fabric defaults" in verdict.evidence


def test_timeliness_fails_when_refresh_activity_has_no_timeout():
    verdict = pipeline_has_a_timeliness_control(_pipeline_ctx(_refresh_activity()))
    assert verdict.score == _FAIL
    assert "sets an execution timeout" in verdict.evidence


def test_timeliness_descends_into_container_activities():
    nested = {
        "name": "ForEach_Source",
        "type": "ForEach",
        "typeProperties": {"activities": [_refresh_activity("0.01:00:00")]},
    }
    assert pipeline_has_a_timeliness_control(_pipeline_ctx(nested)).score == _PASS


def test_timeliness_ignores_non_refresh_container_timeout_absence():
    container = {"name": "ForEach_Source", "type": "ForEach", "typeProperties": {}}
    assert pipeline_has_a_timeliness_control(_pipeline_ctx(container)).status is Status.NA


def test_timeliness_is_na_when_pipeline_has_no_refresh_or_data_activity():
    assert pipeline_has_a_timeliness_control(_pipeline_ctx()).status is Status.NA


def test_timeliness_is_na_when_pipeline_definitions_are_unavailable():
    workspace = WorkspaceContext(id="w", unavailable={Resource.PIPELINE_DEFINITIONS})
    ctx = CheckContext(workspace=workspace, settings={}, obj_name="pipeline",
                       obj={"properties": {"activities": []}})
    assert pipeline_has_a_timeliness_control(ctx).status is Status.NA


# =============================================================================
# 10.4.4 — WS-MONITOR-TREND (and the shared DAX helper it reuses)
# =============================================================================

def test_time_intelligence_helper_needs_a_call_not_a_mention():
    assert uses_time_intelligence("CALCULATE([Runs], SAMEPERIODLASTYEAR('Date'[Date]))")
    assert uses_time_intelligence("TOTALYTD ( [Runs], 'Date'[Date] )")
    # A measure merely *named* after a period comparison shifts no date filter.
    assert not uses_time_intelligence("[Runs SAMEPERIODLASTYEAR]")
    assert time_intelligence_calls("DATEADD('Date'[Date], -1, YEAR)") == {"DATEADD"}


def _model(tables: list[str], *measures: tuple[str, str]) -> dict:
    return {
        "tables": tables,
        "measures": [{"name": name, "expression": expr} for name, expr in measures],
        "relationships": [], "roles": [],
    }


def test_trend_passes_when_the_model_has_a_date_table_and_time_intelligence():
    ctx = _ws_ctx(semantic_models={"Ops Monitoring": _model(
        ["Date", "pipeline_runs"],
        ("Runs LY", "CALCULATE([Runs], SAMEPERIODLASTYEAR('Date'[Date]))"),
    )})
    verdict = monitoring_models_support_trend_analysis(ctx)
    assert verdict.score == _PASS
    assert "Runs LY" in verdict.evidence
    assert "SAMEPERIODLASTYEAR" in verdict.evidence


def test_trend_fails_when_the_model_is_current_state_only():
    ctx = _ws_ctx(semantic_models={"Ops Monitoring": _model(
        ["pipeline_runs"], ("Runs", "COUNTROWS(pipeline_runs)"),
    )})
    verdict = monitoring_models_support_trend_analysis(ctx)
    assert verdict.score == _FAIL
    assert "never a historical trend" in verdict.evidence


def test_trend_names_a_date_axis_without_time_intelligence_rather_than_hiding_it():
    ctx = _ws_ctx(semantic_models={"Ops Monitoring": _model(
        ["Date", "pipeline_runs"], ("Runs", "COUNTROWS(pipeline_runs)"),
    )})
    verdict = monitoring_models_support_trend_analysis(ctx)
    assert verdict.score == _PARTIAL_LOW
    assert "date/calendar table" in verdict.evidence
    assert "not readable here" in verdict.evidence


def test_trend_passes_when_any_model_carries_the_mechanics():
    """The estate can express a trend as soon as one model has both mechanics."""
    ctx = _ws_ctx(semantic_models={
        "Ops Monitoring": _model(["Date", "runs"],
                                 ("Runs LY", "CALCULATE([Runs], DATEADD('Date'[Date], -1, YEAR))")),
        "Ops Current": _model(["runs"], ("Runs", "COUNTROWS(runs)")),
    })
    assert monitoring_models_support_trend_analysis(ctx).score == _PASS


def test_trend_evidence_admits_report_visuals_are_never_read():
    ctx = _ws_ctx(semantic_models={"Ops Monitoring": _model(
        ["Date", "runs"], ("Runs LY", "TOTALYTD([Runs], 'Date'[Date])"),
    )})
    assert "report visuals are not fetched" in \
        monitoring_models_support_trend_analysis(ctx).evidence


def test_trend_is_na_when_no_semantic_model_was_read():
    assert monitoring_models_support_trend_analysis(_ws_ctx()).status is Status.NA


def test_trend_is_na_when_semantic_model_definitions_are_unavailable():
    ctx = _ws_ctx(unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS})
    verdict = monitoring_models_support_trend_analysis(ctx)
    assert verdict.status is Status.NA
    assert "could not be read" in verdict.evidence
