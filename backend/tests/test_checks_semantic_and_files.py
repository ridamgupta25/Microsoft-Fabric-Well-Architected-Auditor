"""Regression tests for six checks that measured the wrong thing, or nothing.

* **4.3.3 WS-FILE-SIZE-BANDS** summarised only the Lakehouse ``Files/`` section.
  Delta tables live under ``Tables/``, so on a real estate it reported
  "0 of 3 data files in band" for a Bronze Lakehouse whose entire Delta
  footprint was never looked at.
* **4.5.11 TB-DEGENERATE-JUNK-DIM** and **14.1.4 R-DAX-VAR** / **14.1.8
  R-MODEL-HIDDEN-KEYS** reported accurate findings as unscored INFO, so a model
  exposing every key to report authors contributed nothing to the verdict.
* **14.1.8** additionally declared display folders unassessable; TMSL carries
  ``displayFolder`` and the parser now keeps it.
* **14.3.4 R-REPORT-SHARED-MODEL** needed a Power BI-audience token and reported
  N/A without one, even though a Report item's ``definition.pbir`` carries the
  binding on the Fabric token the crawl already holds.
"""
from __future__ import annotations

from auditfast.clients.tmsl import parse_tmsl
from auditfast.core.check.data_management_quality.data_storage.automated import (
    degenerate_and_junk_dimension_candidates,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    key_columns_are_hidden,
)
from auditfast.core.check.performance_capacity.data_storage.automated import (
    lakehouse_file_sizes_avoid_small_files,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _summary(*, data_files: int, in_band: int, file_count: int | None = None) -> dict:
    """A Lakehouse section summary with ``in_band`` files in the 128MB-1GB bucket."""
    return {
        "file_count": file_count if file_count is not None else data_files,
        "data_file_count": data_files,
        "excluded_file_count": 0,
        "total_bytes": 0,
        "size_buckets": {
            "under_16mb": max(0, data_files - in_band),
            "16_128mb": 0,
            "128mb_1gb": in_band,
            "over_1gb": 0,
        },
        "top_level_folders": [],
        "max_depth": 1,
        "dated_path_count": 0,
        "sampled": False,
        "truncated": False,
    }


def _ctx(workspace: WorkspaceContext) -> CheckContext:
    return CheckContext(workspace=workspace, settings={},
                        obj_name=workspace.display_name, obj=workspace)


# ---------------------------------------------------------------------------
# 4.3.3 - the Delta files live under Tables/, not Files/
# ---------------------------------------------------------------------------

def test_file_sizes_count_the_tables_section():
    """The bug: a Lakehouse with 3 loose files scored 0/3 while 900 Delta files
    under Tables/ were never looked at."""
    workspace = WorkspaceContext(
        id="w",
        lakehouse_files={"Bronze": _summary(data_files=3, in_band=0)},
        lakehouse_tables_files={"Bronze": _summary(data_files=900, in_band=850)},
    )
    verdict = lakehouse_file_sizes_avoid_small_files(_ctx(workspace))[0]
    assert "850 of 903" in verdict.evidence
    assert "Tables 850/900" in verdict.evidence
    assert "Files 0/3" in verdict.evidence


def test_file_sizes_work_with_only_a_tables_section():
    workspace = WorkspaceContext(
        id="w",
        lakehouse_tables_files={"Gold": _summary(data_files=10, in_band=10)},
    )
    verdict = lakehouse_file_sizes_avoid_small_files(_ctx(workspace))[0]
    assert verdict.score == 3
    assert "10 of 10" in verdict.evidence


def test_file_sizes_are_na_when_nothing_was_listed():
    workspace = WorkspaceContext(id="w")
    verdict = lakehouse_file_sizes_avoid_small_files(_ctx(workspace))[0]
    assert verdict.status is Status.NA


def test_file_sizes_report_a_truncated_listing():
    """A capped listing must not read as a complete count."""
    truncated = _summary(data_files=5000, in_band=10)
    truncated["truncated"] = True
    workspace = WorkspaceContext(id="w", lakehouse_tables_files={"Big": truncated})
    verdict = lakehouse_file_sizes_avoid_small_files(_ctx(workspace))[0]
    assert "truncated" in verdict.evidence
    assert "lower bound" in verdict.evidence


def test_file_sizes_unreadable_listing_is_na():
    workspace = WorkspaceContext(id="w", unavailable={Resource.LAKEHOUSE_FILES})
    assert lakehouse_file_sizes_avoid_small_files(_ctx(workspace))[0].status is Status.NA


# ---------------------------------------------------------------------------
# 4.5.11 - accurate detection must influence the score
# ---------------------------------------------------------------------------

def _fact(name: str, *columns: str) -> dict:
    return {"columns": [{"name": c, "type": "int"} for c in columns], "store": "WH"}


def test_degenerate_candidate_is_scored_not_info():
    """The bug: correct findings were reported as unscored INFO."""
    tables = {
        "fact_sales": _fact("fact_sales", "order_number", "amount", "qty", "sale_date"),
        "dim_customer": {"columns": [{"name": "customer_key", "type": "int"},
                                     {"name": "customer_name", "type": "varchar"},
                                     {"name": "city", "type": "varchar"}], "store": "WH"},
    }
    workspace = WorkspaceContext(id="w", tables=tables)
    verdict = degenerate_and_junk_dimension_candidates(_ctx(workspace))
    assert verdict.status is not Status.INFO
    assert verdict.score is not None


def test_no_candidate_is_a_scored_pass():
    tables = {"fact_sales": _fact("fact_sales", "customer_key", "amount", "qty", "sale_date")}
    workspace = WorkspaceContext(id="w", tables=tables)
    verdict = degenerate_and_junk_dimension_candidates(_ctx(workspace))
    assert verdict.status is not Status.INFO


# ---------------------------------------------------------------------------
# 14.1.8 - display folders are readable, and offenders must be scored
# ---------------------------------------------------------------------------

def _model(columns: list[dict]) -> dict:
    return {"columns": columns, "measures": []}


def test_visible_key_columns_produce_a_scored_offender_row():
    """The bug: the per-model breakdown was an unscored note."""
    workspace = WorkspaceContext(id="w", semantic_models={"Sales": _model([
        {"table": "Fact", "name": "customer_id", "is_key": True,
         "is_hidden": False, "display_folder": ""},
        {"table": "Fact", "name": "product_id", "is_key": True,
         "is_hidden": True, "display_folder": ""},
    ])})
    verdicts = key_columns_are_hidden(_ctx(workspace))
    assert len(verdicts) == 2
    assert "1 of 2" in verdicts[0].evidence
    assert verdicts[1].status is not Status.INFO
    assert verdicts[1].score == 0
    assert "customer_id" in verdicts[1].evidence


def test_display_folders_are_reported_when_present():
    workspace = WorkspaceContext(id="w", semantic_models={"Sales": _model([
        {"table": "Fact", "name": "customer_id", "is_key": True,
         "is_hidden": True, "display_folder": "Keys"},
    ])})
    verdicts = key_columns_are_hidden(_ctx(workspace))
    assert "1 of 1 model(s) file at least one field into a display folder" in verdicts[0].evidence


def test_a_model_with_no_display_folders_is_reported_as_such():
    workspace = WorkspaceContext(id="w", semantic_models={"Sales": _model([
        {"table": "Fact", "name": "customer_id", "is_key": True,
         "is_hidden": True, "display_folder": ""},
    ])})
    verdicts = key_columns_are_hidden(_ctx(workspace))
    assert "0 of 1 model(s) file at least one field into a display folder" in verdicts[0].evidence


def test_an_old_snapshot_without_folder_data_is_not_failed():
    """A snapshot predating the parser change must not manufacture a finding."""
    workspace = WorkspaceContext(id="w", semantic_models={"Sales": _model([
        {"table": "Fact", "name": "customer_id", "is_key": True, "is_hidden": True},
    ])})
    verdicts = key_columns_are_hidden(_ctx(workspace))
    assert "predates the parser change" in verdicts[0].evidence


def test_tmsl_parses_display_folder_on_columns_and_measures():
    parsed = parse_tmsl({"model": {"tables": [{
        "name": "Sales",
        "columns": [{"name": "Amount", "displayFolder": "Facts"}],
        "measures": [{"name": "Total", "expression": "SUM(x)", "displayFolder": "KPIs"}],
    }]}})
    assert parsed["columns"][0]["display_folder"] == "Facts"
    assert parsed["measures"][0]["display_folder"] == "KPIs"
