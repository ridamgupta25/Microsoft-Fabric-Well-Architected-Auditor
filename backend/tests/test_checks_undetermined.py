"""Regression tests for two "undetermined scored as a failure" defects.

Both violate the library's central rule - *we could not determine this* is not
*this is misconfigured* - in ways that read as real findings in a report:

* **2.2.2 (PL-FULLLOAD)** scored 1 of 3 while its own evidence said "cannot
  confirm it is a small reference/dimension table". A pipeline whose Copy sink
  is a dataset reference was marked down for this tool's blind spot. It also
  read the sink one level too shallow, so the target table Fabric embeds
  inline was never found.
* **3.6.5 (WS-WH-TRYCATCH)** counted Copy activities as unreadable SQL loads.
  A Copy runs no SQL of its own - Fabric generates the load internally - so
  there is nothing to read, ever. On a real workspace 109 of 114 "loads" were
  Copy activities and the check reported FAIL from the remaining 5.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import pl_full_load
from auditfast.core.check.data_management_quality.data_storage.automated import (
    wh_try_catch_transactions,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

_TRANSACTIONAL_LOAD = (
    "BEGIN TRY BEGIN TRANSACTION; INSERT INTO dbo.fact_sales SELECT * FROM stg.sales; "
    "COMMIT; END TRY BEGIN CATCH ROLLBACK; END CATCH"
)
#: A genuinely multi-statement load with no error handling — the case that IS
#: judged (a single statement is atomic and exempt).
_MULTI_UNGUARDED = (
    "TRUNCATE TABLE dbo.fact_sales; INSERT INTO dbo.fact_sales SELECT * FROM stg.sales"
)


def _pipe(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


def _script(name: str, sql: str) -> dict:
    return {"name": name, "type": "Script",
            "typeProperties": {"scripts": [{"text": sql}]}}


def _copy(name: str) -> dict:
    return {"name": name, "type": "Copy",
            "typeProperties": {"sink": {"type": "DataWarehouseSink"}}}


def _ws(**kwargs) -> WorkspaceContext:
    kwargs.setdefault("items", [Item(id="wh", type="Warehouse", display_name="WH_Gold")])
    return WorkspaceContext(id="w", **kwargs)


def _ctx(obj, workspace=None, obj_name="pipeline") -> CheckContext:
    return CheckContext(workspace=workspace or _ws(), settings={},
                        obj_name=obj_name, obj=obj)


def _lakehouse_sink(table, action: str = "OverwriteSchema") -> dict:
    """A Copy sink in the exact shape Fabric emits for a Lakehouse table."""
    return {
        "name": "Copy to lakehouse", "type": "Copy", "dependsOn": [],
        "typeProperties": {
            "source": {"type": "SqlServerSource"},
            "sink": {
                "type": "LakehouseTableSink",
                "tableActionOption": action,
                "partitionOption": "None",
                "datasetSettings": {
                    "type": "LakehouseTable",
                    "linkedService": {"name": "Bronze",
                                      "properties": {"type": "Lakehouse"}},
                    "typeProperties": {"table": table},
                },
            },
        },
    }


def _warehouse_sink(table: str, schema: str = "dbo", **sink_props) -> dict:
    """A Copy sink in the exact shape Fabric emits for a Warehouse table."""
    sink = {
        "type": "DataWarehouseSink",
        "allowCopyCommand": True,
        "datasetSettings": {
            "type": "DataWarehouseTable",
            "typeProperties": {"schema": schema, "table": table},
        },
    }
    sink.update(sink_props)
    return {"name": "Copy to warehouse", "type": "Copy", "dependsOn": [],
            "typeProperties": {"source": {"type": "ParquetSource"}, "sink": sink}}


# ---------------------------------------------------------------------------
# 2.2.2 - the Copy sink names its target, and says whether it overwrites
# ---------------------------------------------------------------------------

def test_full_load_reads_lakehouse_overwrite_target():
    """The fix: OverwriteSchema + an inline table name is a readable full reload."""
    verdict = pl_full_load(_ctx(_pipe(_lakehouse_sink("fact_sales"))))
    assert verdict.score == 0
    assert "fact_sales" in verdict.evidence


def test_full_load_reads_warehouse_overwrite_target():
    verdict = pl_full_load(_ctx(_pipe(
        _warehouse_sink("fact_orders", preCopyScript="TRUNCATE TABLE dbo.fact_orders"),
    )))
    assert verdict.score == 0
    assert "fact_orders" in verdict.evidence


def test_full_load_passes_a_dimension_overwrite():
    verdict = pl_full_load(_ctx(_pipe(_lakehouse_sink("dim_currency"))))
    assert verdict.score == 3
    assert "dim_currency" in verdict.evidence


def test_append_is_not_a_full_reload():
    """Append/Upsert add to a table - only Overwrite replaces it."""
    verdict = pl_full_load(_ctx(_pipe(_lakehouse_sink("fact_sales", action="Append"))))
    assert verdict.status is Status.NA
    assert "no full-reload statement" in verdict.evidence


def test_expression_named_target_is_na_not_scored():
    """A metadata-driven ForEach has no single target until run time."""
    expression = {"value": "@{item().TABLE_NAME}", "type": "Expression"}
    verdict = pl_full_load(_ctx(_pipe(_lakehouse_sink(expression))))
    assert verdict.status is Status.NA
    assert verdict.score is None
    assert "run time" in verdict.evidence


def test_a_named_target_is_judged_even_when_another_is_dynamic():
    expression = {"value": "@{item().TABLE_NAME}", "type": "Expression"}
    verdict = pl_full_load(_ctx(_pipe(
        _lakehouse_sink("fact_sales"),
        _lakehouse_sink(expression),
    )))
    assert verdict.score == 0
    assert "fact_sales" in verdict.evidence
    assert "run-time expression" in verdict.evidence


def test_nested_copy_sink_is_found():
    """A Copy inside a ForEach is the commonest metadata-driven shape."""
    pipeline = _pipe({
        "name": "per table", "type": "ForEach",
        "typeProperties": {"activities": [_lakehouse_sink("fact_sales")]},
    })
    verdict = pl_full_load(_ctx(pipeline))
    assert verdict.score == 0
    assert "fact_sales" in verdict.evidence


def test_initial_load_may_overwrite_a_fact():
    verdict = pl_full_load(_ctx(_pipe(_lakehouse_sink("fact_sales")),
                                obj_name="PL_Initial_Load_Sales"))
    assert verdict.score == 3
    assert "initial/one-time load" in verdict.evidence


# ---------------------------------------------------------------------------
# 2.2.2 - an unnamed overwrite target is undetermined, not a partial failure
# ---------------------------------------------------------------------------

def test_full_load_with_unnamed_target_is_na_not_a_partial_score():
    """The bug: evidence said "cannot confirm" and the check scored 1 anyway."""
    pipeline = _pipe({
        "name": "Copy into warehouse", "type": "Copy",
        "typeProperties": {"sink": {"type": "DataWarehouseSink",
                                    "writeBehavior": "overwrite"}},
    })
    verdict = pl_full_load(_ctx(pipeline))
    assert verdict.status is Status.NA
    assert verdict.score is None
    assert "cannot be determined" in verdict.evidence


def test_full_load_still_fails_a_named_fact_reload():
    """The case that must keep working: a named fact table is a real finding."""
    pipeline = _pipe(_script("Reload", "TRUNCATE TABLE dbo.fact_sales"))
    verdict = pl_full_load(_ctx(pipeline))
    assert verdict.score == 0
    assert "fact_sales" in verdict.evidence


def test_full_load_still_passes_a_named_dimension_reload():
    pipeline = _pipe(_script("Reload", "TRUNCATE TABLE dbo.dim_currency"))
    assert pl_full_load(_ctx(pipeline)).score == 3


def test_full_load_is_na_when_nothing_reloads():
    pipeline = _pipe(_script("Merge", "MERGE INTO dbo.fact_sales USING stg.sales ON 1=1"))
    assert pl_full_load(_ctx(pipeline)).status is Status.NA


# ---------------------------------------------------------------------------
# 3.6.5 - a Copy activity has no SQL to judge, so it is out of scope
# ---------------------------------------------------------------------------

def test_try_catch_is_na_when_only_copy_activities_load():
    """The bug: 1 readable load among 10 Copy activities produced a FAIL.

    A Copy activity runs no SQL of its own, so it has no error handling to
    find - counting it as an unreadable load made the check look blind on an
    estate that simply does not load through SQL.
    """
    pipelines = {"P": _pipe(*[_copy(f"Copy {i}") for i in range(10)])}
    workspace = _ws(pipelines=pipelines)
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.status is Status.NA
    assert verdict.score is None
    assert "10 Copy activity" in verdict.evidence
    assert "not the same as having none" in verdict.evidence


def test_try_catch_judges_readable_sql_regardless_of_copy_count():
    """One multi-statement load is a verdict about that load - Copy count is irrelevant."""
    pipelines = {"P": _pipe(
        _script("Readable", _MULTI_UNGUARDED),
        *[_copy(f"Copy {i}") for i in range(10)],
    )}
    workspace = _ws(pipelines=pipelines)
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.score == 0
    assert "0 of 1 multi-statement SQL load" in verdict.evidence
    assert "10 Copy activity/activities load without SQL and are out of scope" in verdict.evidence


def test_try_catch_scores_mixed_readable_loads():
    pipelines = {"P": _pipe(
        _script("Good", _TRANSACTIONAL_LOAD),
        _script("Bad", _MULTI_UNGUARDED),
    )}
    workspace = _ws(pipelines=pipelines)
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.score is not None
    assert "1 of 2 multi-statement SQL load" in verdict.evidence
    assert "out of scope" not in verdict.evidence


def test_try_catch_is_na_when_only_single_statement_loads():
    """A lone single-statement load (TRUNCATE or MERGE) is atomic - nothing to wrap."""
    merge = ("MERGE INTO dbo.fact t USING stg s ON t.id = s.id "
             "WHEN MATCHED THEN UPDATE SET t.a = s.a")
    pipelines = {"P": _pipe(
        _script("Wipe", "TRUNCATE TABLE dbo.stg_a"),
        _script("Merge", merge),
    )}
    verdict = wh_try_catch_transactions(_ctx(None, _ws(pipelines=pipelines)))[0]
    assert verdict.status is Status.NA
    assert verdict.score is None
    assert "single-statement (atomic) load" in verdict.evidence
    assert "no multi-statement load procedure to assess" in verdict.evidence


def test_try_catch_excludes_single_statement_loads_from_the_denominator():
    """A single-statement load does not inflate the denominator past the multi-step ones."""
    pipelines = {"P": _pipe(
        _script("Atomic", "TRUNCATE TABLE dbo.stg_a"),
        _script("Load", _MULTI_UNGUARDED),
    )}
    verdict = wh_try_catch_transactions(_ctx(None, _ws(pipelines=pipelines)))[0]
    assert verdict.score == 0
    assert "0 of 1 multi-statement SQL load" in verdict.evidence
    assert "1 single-statement (atomic) load(s)" in verdict.evidence


def test_try_catch_exempts_a_lone_atomic_merge():
    """A single unguarded MERGE is atomic - exempt, not a finding (the reviewer's fix)."""
    merge = ("MERGE INTO dbo.fact_sales t USING stg.sales s ON t.id = s.id "
             "WHEN MATCHED THEN UPDATE SET t.amt = s.amt")
    verdict = wh_try_catch_transactions(
        _ctx(None, _ws(pipelines={"P": _pipe(_script("Merge", merge))})))[0]
    assert verdict.status is Status.NA
    assert "single-statement (atomic) load" in verdict.evidence


def test_try_catch_flags_a_multi_statement_load_without_handling():
    """A genuinely multi-step load with no TRY...CATCH is still a real finding."""
    verdict = wh_try_catch_transactions(
        _ctx(None, _ws(pipelines={"P": _pipe(_script("Load", _MULTI_UNGUARDED))})))[0]
    assert verdict.score == 0
    assert "0 of 1 multi-statement SQL load" in verdict.evidence


def test_try_catch_unreadable_definitions_are_na():
    workspace = _ws(unavailable={Resource.PIPELINE_DEFINITIONS})
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.status is Status.NA


def test_try_catch_reads_stored_procedure_bodies():
    """Verified against a live Fabric Warehouse: routine bodies do arrive."""
    workspace = _ws(
        pipelines={"P": _pipe(_copy("Opaque"))},
        sql_routines=[{"store": "WH_Gold", "name": "usp_load",
                       "definition": _TRANSACTIONAL_LOAD}],
    )
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.score == 3
    assert "1 of 1 multi-statement SQL load" in verdict.evidence


def test_try_catch_reports_a_called_procedure_whose_body_is_missing():
    """A stored procedure IS readable in principle, so a missing body is a gap.

    This is the case the permission hint is actually for - unlike a Copy
    activity, which has nothing to read by design.
    """
    workspace = _ws(pipelines={"P": _pipe({
        "name": "Run load", "type": "SqlServerStoredProcedure",
        "typeProperties": {"storedProcedureName": "dbo.usp_missing"},
    })})
    verdict = wh_try_catch_transactions(_ctx(None, workspace))[0]
    assert verdict.status is Status.NA
    assert "body is not in this snapshot" in verdict.evidence
