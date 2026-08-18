"""Tests for the Gold-layer control, freshness and structure checks.

Refs 5.3.8 (a control check — "does the *code* implement the control", never "is
the data right"), 5.4.7 (a measurement check — were the serving items actually
refreshed), and 2.1.4, 4.4.7, 14.1.5, 14.3.4 (structure checks).

The dedup tests matter as much as the detection tests: several of these points
sit next to an existing check that is easy to mistake them for, so each of those
pairs is pinned here.

* 5.3.8 vs ``NB-RECON-COUNT`` (5.2.5) — same-run source reconciliation must
  **not** satisfy the run-over-run point.
* 2.1.4 vs ``PL-DESC`` (2.1.6) — fully described activities with Fabric-default
  names pass 2.1.6 and fail 2.1.4.
* 14.3.4 — the report→model binding is scored from the report side only. Ref
  14.1.5 (shared/certified model reuse) was removed: "certified" is readable
  only from the admin/scanner API, so the point cannot be fully automated
  without tenant-admin and is tracked as an admin-scoped check instead.

Refs 5.4.2, 5.4.3 and 5.4.8 previously lived here as notebook-scoped control
checks. They were removed: the source-of-truth checklist scopes them to
Warehouse and Semantic Model artifacts, and answering them needs a SQL/DAX query
at check time, which ``CheckContext`` cannot carry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from auditfast.core.check.data_management_quality.data_prep.automated import (
    descriptions,
    nb_recon_count,
    notebook_has_a_run_over_run_volume_control,
    pipeline_activities_are_self_documenting,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    gold_items_refreshed_within_sla,
    workspace_defines_a_view_layer_over_its_tables,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    reports_are_built_on_a_shared_model,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

_PASS, _PARTIAL_HIGH, _PARTIAL_LOW, _FAIL = 3, 2, 1, 0


# -- context builders ---------------------------------------------------------

def _nb_ctx(code: str, **workspace) -> CheckContext:
    definition = {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}
    return CheckContext(workspace=WorkspaceContext(id="w", **workspace), settings={},
                        obj_name="nb", obj=definition)


def _pl_ctx(definition: dict, **workspace) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w", **workspace), settings={},
                        obj_name="pl", obj=definition)


def _ws_ctx(**workspace) -> CheckContext:
    ws = WorkspaceContext(id="w", **workspace)
    return CheckContext(workspace=ws, settings={}, obj_name="w", obj=ws)


def _table(*cols: tuple[str, str], store: str = "", kind: str = "") -> dict:
    return {
        "type": "Managed", "format": "Delta",
        "store": store, "store_kind": kind,
        "columns": [{"name": name, "type": ctype} for name, ctype in cols],
    }


def _script_pipeline(sql: str) -> dict:
    return {"properties": {"activities": [
        {"name": "Deploy Objects", "type": "Script",
         "typeProperties": {"scripts": [{"text": sql}]}},
    ]}}


# =============================================================================
# 5.3.8 — NB-VOLUME-TREND
# =============================================================================

_RUN_OVER_RUN = """
prev_row_count = spark.sql("select row_count from audit.load_log order by run_ts desc limit 1").collect()[0][0]
current = df.count()
if current < prev_row_count * 0.9:
    raise ValueError("volume shrank")
df.write.mode("append").saveAsTable("silver.orders")
"""

_COUNT_ONLY_LOGGED = """
row_count = df.count()
spark.sql("INSERT INTO audit.load_log VALUES ('b1', " + str(row_count) + ")")
df.write.mode("append").saveAsTable("silver.orders")
"""

_NO_VOLUME_CONTROL = """
df = spark.read.parquet("abfss://x@y/z")
df.write.mode("overwrite").saveAsTable("silver.orders")
"""


def test_volume_trend_passes_when_this_run_is_compared_against_a_previous_run():
    verdict = notebook_has_a_run_over_run_volume_control(_nb_ctx(_RUN_OVER_RUN))
    assert verdict.score == _PASS
    assert "previous run" in verdict.evidence


def test_volume_trend_evidence_says_the_outcome_itself_is_not_read():
    verdict = notebook_has_a_run_over_run_volume_control(_nb_ctx(_RUN_OVER_RUN))
    assert "runtime outcome this check does not read" in verdict.evidence


def test_volume_trend_is_partial_when_the_count_is_only_persisted():
    verdict = notebook_has_a_run_over_run_volume_control(_nb_ctx(_COUNT_ONLY_LOGGED))
    assert verdict.score == _PARTIAL_LOW
    assert "nothing reads a previous run's count back" in verdict.evidence


def test_volume_trend_fails_when_nothing_looks_at_volume_at_all():
    assert notebook_has_a_run_over_run_volume_control(
        _nb_ctx(_NO_VOLUME_CONTROL)).score == _FAIL


def test_volume_trend_is_na_when_the_notebook_writes_nothing():
    verdict = notebook_has_a_run_over_run_volume_control(
        _nb_ctx("df = spark.read.table('silver.orders')\ndisplay(df)"))
    assert verdict.status is Status.NA


def test_volume_trend_is_na_when_notebook_definitions_were_unreadable():
    ctx = _nb_ctx(_RUN_OVER_RUN, unavailable={Resource.NOTEBOOK_DEFINITIONS})
    assert notebook_has_a_run_over_run_volume_control(ctx).status is Status.NA


def test_volume_trend_ignores_a_commented_out_previous_count():
    code = ("# prev_row_count = 100 and if current < prev_row_count: raise\n"
            "df.write.mode('append').saveAsTable('silver.orders')")
    assert notebook_has_a_run_over_run_volume_control(_nb_ctx(code)).score == _FAIL


# -- the 5.2.5 dedup pin ------------------------------------------------------

_SAME_RUN_RECONCILIATION = """
source_count = src.count()
target_count = df.count()
assert target_count == source_count, "row count mismatch"
df.write.mode("append").saveAsTable("silver.orders")
"""


def test_same_run_source_reconciliation_satisfies_5_2_5():
    assert nb_recon_count(_nb_ctx(_SAME_RUN_RECONCILIATION)).score == _PASS


def test_same_run_source_reconciliation_does_not_satisfy_5_3_8():
    """5.2.5 reconciles across a hop in one moment; 5.3.8 reconciles across time.

    Both sides of a same-run comparison can shrink together, so a passing 5.2.5
    says nothing about historical consistency and must not score here.
    """
    verdict = notebook_has_a_run_over_run_volume_control(_nb_ctx(_SAME_RUN_RECONCILIATION))
    assert verdict.score == _FAIL
    assert "no previous-run count" in verdict.evidence


# =============================================================================
# 2.1.4 — PL-ACTIVITY-SELFDOC
# =============================================================================

_WELL_NAMED = {"properties": {"activities": [
    {"name": "Copy Sales To Bronze", "type": "Copy"},
    {"name": "Load Dim Customer", "type": "TridentNotebook"},
    {"name": "Refresh Sales Model", "type": "PBISemanticModelRefresh"},
]}}

_DEFAULT_NAMED_BUT_DESCRIBED = {"properties": {
    "description": "Loads sales into the lakehouse",
    "activities": [
        {"name": "Copy data1", "type": "Copy", "description": "copies the sales extract"},
        {"name": "Notebook1", "type": "TridentNotebook", "description": "runs the transform"},
    ],
}}


def test_activity_selfdoc_passes_when_every_name_says_what_the_step_does():
    verdict = pipeline_activities_are_self_documenting(_pl_ctx(_WELL_NAMED))
    assert verdict.score == _PASS
    assert "3 of 3 activity name(s) are self-documenting" in verdict.evidence


def test_activity_selfdoc_fails_on_fabric_default_activity_names():
    verdict = pipeline_activities_are_self_documenting(_pl_ctx(_DEFAULT_NAMED_BUT_DESCRIBED))
    assert verdict.score == _FAIL
    assert "Copy data1" in verdict.evidence


def test_activity_selfdoc_counts_activities_inside_containers():
    definition = {"properties": {"activities": [
        {"name": "For Each Source Table", "type": "ForEach",
         "typeProperties": {"activities": [
             {"name": "Copy Source Table", "type": "Copy"},
         ]}},
    ]}}
    verdict = pipeline_activities_are_self_documenting(_pl_ctx(definition))
    assert verdict.score == _PASS
    assert "2 of 2" in verdict.evidence


def test_activity_selfdoc_costs_a_band_for_a_long_flat_pipeline():
    """Well-named, but eleven steps in one flat list with no grouping container."""
    definition = {"properties": {"activities": [
        {"name": f"Load Source Table {i}", "type": "Copy"} for i in range(11)
    ]}}
    verdict = pipeline_activities_are_self_documenting(_pl_ctx(definition))
    assert verdict.score == _PARTIAL_HIGH
    assert "flat list with no grouping container" in verdict.evidence


def test_activity_selfdoc_does_not_penalise_a_grouped_long_pipeline():
    definition = {"properties": {"activities": [
        {"name": f"Load Source Table {i}", "type": "Copy"} for i in range(11)
    ] + [{"name": "For Each Domain", "type": "ForEach", "typeProperties": {"activities": []}}]}}
    assert pipeline_activities_are_self_documenting(_pl_ctx(definition)).score == _PASS


def test_activity_selfdoc_is_na_for_an_empty_pipeline():
    verdict = pipeline_activities_are_self_documenting(_pl_ctx({"properties": {"activities": []}}))
    assert verdict.status is Status.NA


def test_activity_selfdoc_is_na_when_pipeline_definitions_were_unreadable():
    ctx = _pl_ctx(_WELL_NAMED, unavailable={Resource.PIPELINE_DEFINITIONS})
    assert pipeline_activities_are_self_documenting(ctx).status is Status.NA


# -- the 2.1.6 dedup pin ------------------------------------------------------

def test_descriptions_pass_where_default_names_fail_the_new_check():
    """Descriptions populated everywhere (2.1.6 = PASS) with default names (2.1.4 = FAIL)."""
    ctx = _pl_ctx(_DEFAULT_NAMED_BUT_DESCRIBED)
    assert descriptions(ctx).score == _PASS
    assert pipeline_activities_are_self_documenting(ctx).score == _FAIL


def test_activity_selfdoc_never_reads_a_description():
    """Self-documenting names score the same with or without descriptions."""
    described = {"properties": {"activities": [
        dict(a, description="documented") for a in _WELL_NAMED["properties"]["activities"]
    ]}}
    assert (pipeline_activities_are_self_documenting(_pl_ctx(described)).score
            == pipeline_activities_are_self_documenting(_pl_ctx(_WELL_NAMED)).score)


# =============================================================================
# 5.4.7 — WS-GOLD-FRESHNESS (workspace-scoped, measures real recency)
# =============================================================================

def _stamp(hours_ago: float) -> str:
    """An ISO-8601 UTC stamp ``hours_ago`` hours before now.

    The check compares against ``datetime.now``, exactly as ``WS-ORPHAN`` (12.3.4)
    does, so the fixture stamps are expressed relative to now rather than pinned
    to a date that would silently age into staleness.
    """
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _gold_ws(*items: Item, **workspace) -> CheckContext:
    return _ws_ctx(items=list(items), **workspace)


def test_gold_freshness_passes_when_every_serving_item_refreshed_inside_the_sla():
    ctx = _gold_ws(
        Item(id="wh-1", type="Warehouse", display_name="WH_Sales", last_run_utc=_stamp(3)),
        Item(id="lh-1", type="Lakehouse", display_name="LH_Sales_Gold",
             last_run_utc=_stamp(10)),
    )
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.score == _PASS
    assert "2 of 2 Gold/serving item(s)" in verdict.evidence
    assert "Delta commit times are not fetched" in verdict.evidence


def test_gold_freshness_fails_when_the_serving_items_are_out_of_sla():
    ctx = _gold_ws(
        Item(id="wh-1", type="Warehouse", display_name="WH_Sales", last_run_utc=_stamp(200)),
        Item(id="lh-1", type="Lakehouse", display_name="LH_Sales_Gold",
             last_run_utc=_stamp(300)),
    )
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.score == _FAIL
    assert "WH_Sales" in verdict.evidence and "LH_Sales_Gold" in verdict.evidence


def test_gold_freshness_window_is_a_project_setting():
    ws = WorkspaceContext(id="w", items=[
        Item(id="wh-1", type="Warehouse", display_name="WH_Sales", last_run_utc=_stamp(30)),
    ])
    inside = CheckContext(workspace=ws, settings={"gold_freshness_sla_hours": 48},
                          obj_name="w", obj=ws)
    outside = CheckContext(workspace=ws, settings={"gold_freshness_sla_hours": 12},
                           obj_name="w", obj=ws)
    assert gold_items_refreshed_within_sla(inside).score == _PASS
    assert gold_items_refreshed_within_sla(outside).score == _FAIL


def test_gold_freshness_never_counts_an_unreadable_timestamp_as_stale():
    """An item with no last-run stamp leaves the denominator — it is not a failure."""
    ctx = _gold_ws(
        Item(id="wh-1", type="Warehouse", display_name="WH_Fresh", last_run_utc=_stamp(2)),
        Item(id="wh-2", type="Warehouse", display_name="WH_Unknown"),
    )
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.score == _PASS
    assert "1 of 1 Gold/serving item(s)" in verdict.evidence
    assert "excluded rather than counted stale" in verdict.evidence


def test_gold_freshness_ignores_a_bronze_lakehouse():
    """Only a Warehouse, or a name-marked serving Lakehouse/model, is Gold."""
    ctx = _gold_ws(
        Item(id="lh-1", type="Lakehouse", display_name="LH_Bronze_Raw",
             last_run_utc=_stamp(500)),
    )
    assert gold_items_refreshed_within_sla(ctx).status is Status.NA


def test_gold_freshness_counts_a_name_marked_semantic_model():
    ctx = _gold_ws(
        Item(id="sm-1", type="SemanticModel", display_name="Sales Mart",
             last_run_utc=_stamp(500)),
    )
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.score == _FAIL
    assert "Sales Mart" in verdict.evidence


def test_gold_freshness_is_na_when_no_serving_item_exists():
    ctx = _gold_ws(Item(id="nb-1", type="Notebook", display_name="NB_Load",
                        last_run_utc=_stamp(1)))
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.status is Status.NA
    assert "No Gold/serving item" in verdict.evidence


def test_gold_freshness_is_na_when_no_serving_item_has_a_timestamp():
    ctx = _gold_ws(Item(id="wh-1", type="Warehouse", display_name="WH_Sales"))
    verdict = gold_items_refreshed_within_sla(ctx)
    assert verdict.status is Status.NA
    assert "readable last run/refresh timestamp" in verdict.evidence


def test_gold_freshness_is_na_when_the_run_history_was_unreadable():
    ctx = _gold_ws(
        Item(id="wh-1", type="Warehouse", display_name="WH_Sales", last_run_utc=_stamp(500)),
        unavailable={Resource.ITEM_RUN_HISTORY},
    )
    assert gold_items_refreshed_within_sla(ctx).status is Status.NA


def test_gold_freshness_is_na_when_items_were_unreadable():
    ctx = _gold_ws(unavailable={Resource.ITEMS})
    assert gold_items_refreshed_within_sla(ctx).status is Status.NA


# =============================================================================
# 4.4.7 — WS-VIEW-ABSTRACTION
# =============================================================================

_TABLES = {"fact_sales": _table(("amount", "decimal(18,2)"), store="WH_Gold", kind="Warehouse")}


def test_view_abstraction_passes_on_a_create_view_in_a_pipeline_script():
    ctx = _ws_ctx(tables=_TABLES, pipelines={
        "PL_Deploy": _script_pipeline("CREATE OR ALTER VIEW dbo.vw_sales AS SELECT 1 AS x")})
    verdict = workspace_defines_a_view_layer_over_its_tables(ctx)
    assert verdict.score == _PASS
    assert "PL_Deploy" in verdict.evidence
    assert "View metadata itself is not fetched" in verdict.evidence


def test_view_abstraction_passes_on_a_create_view_in_notebook_sql():
    ctx = _ws_ctx(tables=_TABLES, notebooks={
        "nb_views": {"cells": [{"cell_type": "code",
                                "source": 'spark.sql("CREATE VIEW gold.vw_sales AS SELECT 1")'}]}})
    assert workspace_defines_a_view_layer_over_its_tables(ctx).score == _PASS


def test_view_abstraction_is_partial_when_only_procedures_are_defined():
    ctx = _ws_ctx(tables=_TABLES, pipelines={
        "PL_Deploy": _script_pipeline("CREATE PROCEDURE dbo.load_sales AS SELECT 1")})
    verdict = workspace_defines_a_view_layer_over_its_tables(ctx)
    assert verdict.score == _PARTIAL_HIGH
    assert "stored procedure or function" in verdict.evidence


def test_view_abstraction_fails_when_nothing_abstracts_the_tables():
    ctx = _ws_ctx(tables=_TABLES, pipelines={
        "PL_Copy": {"properties": {"activities": [{"name": "Copy Sales", "type": "Copy"}]}}})
    verdict = workspace_defines_a_view_layer_over_its_tables(ctx)
    assert verdict.score == _FAIL
    assert "No CREATE VIEW" in verdict.evidence
    assert "View metadata itself is not fetched" in verdict.evidence


def test_view_abstraction_is_na_without_table_metadata():
    ctx = _ws_ctx(pipelines={"PL_Deploy": _script_pipeline("CREATE VIEW v AS SELECT 1")})
    assert workspace_defines_a_view_layer_over_its_tables(ctx).status is Status.NA


def test_view_abstraction_is_na_when_no_definitions_could_be_read():
    ctx = _ws_ctx(tables=_TABLES,
                  unavailable={Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS})
    assert workspace_defines_a_view_layer_over_its_tables(ctx).status is Status.NA


def test_view_abstraction_is_na_when_the_workspace_holds_no_code():
    assert workspace_defines_a_view_layer_over_its_tables(
        _ws_ctx(tables=_TABLES)).status is Status.NA


# =============================================================================
# 14.3.4 - R-REPORT-SHARED-MODEL (scored: the report-binding side)
# =============================================================================

def _report(report_id: str, dataset_id: str = "", workspace_id: str = "w") -> dict:
    return {"id": report_id, "name": f"Report {report_id}",
            "dataset_id": dataset_id, "dataset_workspace_id": workspace_id}


def test_shared_model_passes_when_many_reports_share_one_model():
    ctx = _ws_ctx(reports=[_report("r1", "ds-1"), _report("r2", "ds-1"),
                           _report("r3", "ds-1")])
    verdict = reports_are_built_on_a_shared_model(ctx)
    assert verdict.score == _PASS
    assert "3 of 3 report(s)" in verdict.evidence
    assert "never certification" in verdict.evidence


def test_shared_model_fails_on_one_private_extract_per_report():
    ctx = _ws_ctx(reports=[_report("r1", "ds-1"), _report("r2", "ds-2"),
                           _report("r3", "ds-3")])
    verdict = reports_are_built_on_a_shared_model(ctx)
    assert verdict.score == _FAIL
    assert "0 of 3 report(s)" in verdict.evidence
    assert "no other report uses" in verdict.evidence


def test_shared_model_credits_a_model_published_in_another_workspace():
    """The hub pattern: one local report on a central model is reuse, not an extract."""
    ctx = _ws_ctx(reports=[_report("r1", "ds-hub", workspace_id="other-ws"),
                           _report("r2", "ds-2")])
    verdict = reports_are_built_on_a_shared_model(ctx)
    assert verdict.coverage == 0.5


def test_shared_model_excludes_paginated_reports_instead_of_failing_them():
    ctx = _ws_ctx(reports=[_report("r1", "ds-1"), _report("r2", "ds-1"),
                           _report("rdl", "")])
    verdict = reports_are_built_on_a_shared_model(ctx)
    assert verdict.score == _PASS
    assert "1 paginated/unbound report(s) are excluded" in verdict.evidence


def test_shared_model_is_na_for_a_single_bound_report():
    """One report cannot evidence either sharing or a private extract."""
    verdict = reports_are_built_on_a_shared_model(_ws_ctx(reports=[_report("r1", "ds-1")]))
    assert verdict.status is Status.NA
    assert "cannot be observed from one report" in verdict.evidence


def test_shared_model_is_na_when_no_report_declares_a_binding():
    ctx = _ws_ctx(reports=[_report("r1", ""), _report("r2", "")])
    verdict = reports_are_built_on_a_shared_model(ctx)
    assert verdict.status is Status.NA
    assert "paginated (RDL) reports carry no datasetId" in verdict.evidence


def test_shared_model_is_na_when_the_workspace_holds_no_report():
    assert reports_are_built_on_a_shared_model(_ws_ctx()).status is Status.NA


def test_shared_model_is_na_when_the_report_list_was_unreadable():
    ctx = _ws_ctx(reports=[_report("r1", "ds-1"), _report("r2", "ds-2")],
                  unavailable={Resource.REPORTS})
    assert reports_are_built_on_a_shared_model(ctx).status is Status.NA


def test_only_the_report_side_of_model_reuse_is_scored():
    """14.3.4 scores the report→model binding and nothing else.

    Ref 14.1.5 (shared/*certified* model reuse) was removed because endorsement
    is readable only from the admin/scanner API, so no second check may score
    this same grouping from the model side.
    """
    ctx = _ws_ctx(
        items=[Item(id="ds-1", type="SemanticModel", display_name="Sales")],
        reports=[_report("r1", "ds-1"), _report("r2", "ds-1")],
    )
    assert reports_are_built_on_a_shared_model(ctx).scored is True
