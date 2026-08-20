"""3.6.7 and 4.4.6 - Fabric maintains statistics, so absence of manual work is not a gap.

The reviewer's comment on both: *"statistics are autocaptured for Fabric, check
should pass."* They are right, and Microsoft's documentation is unambiguous:

    "Whenever you issue a query and query optimizer requires statistics for plan
    exploration, Microsoft Fabric automatically creates those statistics if they
    don't already exist."

    "if the query engine determines that existing statistics relevant to query no
    longer accurately reflect the data, those statistics are automatically
    refreshed."

    "The proactive statistics refresh feature is enabled by default."

    -- learn.microsoft.com/fabric/data-warehouse/statistics

Both checks previously scored an estate down for having no pipeline or stored
procedure running UPDATE STATISTICS. On Fabric that is the normal, correct
configuration, so the checks were reporting a gap that cannot exist and asking
customers to build maintenance the platform already performs.

One residual use is documented, and it is an optimisation rather than a
requirement: pre-warming statistics "if there's a large enough window between
your table transformations and your query workload", which removes first-query
latency after a batch load.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_storage.automated import (
    stats_strategy_defined,
    wh_stats_updated_after_loads,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import Item

from .fixtures.builders import workspace_ctx

_WAREHOUSE = Item(id="wh-1", display_name="WH_Gold", type="Warehouse")


def _script(sql: str) -> dict:
    return {"properties": {"activities": [
        {"name": "load", "type": "Script",
         "typeProperties": {"scripts": [{"text": sql}]}},
    ]}}


def _ctx(*, pipelines=None, routines=None, tables=None, options=None):
    return workspace_ctx(
        items=[_WAREHOUSE],
        pipelines=pipelines or {},
        sql_routines=routines or [],
        tables=tables or {},
        warehouse_options=options or {},
    )


def _table(stats: int = 0) -> dict:
    table = {"type": "Managed", "format": "Delta", "store": "WH_Gold",
             "columns": [{"name": "id", "type": "bigint"}]}
    if stats:
        table["statistics"] = stats
    return table


def _options(create: bool | None = True, update: bool | None = True) -> dict:
    return {"WH_Gold": {"auto_create_stats": create, "auto_update_stats": update,
                        "auto_update_stats_async": True}}


# ===========================================================================
# The auditable setting: AUTO_CREATE / AUTO_UPDATE_STATISTICS switched off
# ===========================================================================

def test_auto_statistics_switched_off_is_reported():
    """The one statistics misconfiguration a Fabric user can actually reach.

    NORECOMPUTE looked like the equivalent signal, but Fabric rejects the option
    outright, so no_recompute is always 0 and proves nothing. These options are
    in Fabric's own ALTER DATABASE syntax and are readable from sys.databases -
    and Microsoft documents the OFF state as causing "suboptimal query plans and
    degraded query performance".
    """
    verdicts = stats_strategy_defined(_ctx(
        tables={"dbo.fact_sales": _table(stats=2)},
        options=_options(update=False),
    ))
    summary = verdicts[0]
    assert summary.score is not None and summary.score < 3
    assert "AUTO_UPDATE_STATISTICS switched OFF" in summary.evidence
    assert "WH_Gold" in summary.evidence


def test_auto_create_off_is_reported_too():
    verdicts = stats_strategy_defined(_ctx(
        tables={"dbo.fact_sales": _table()}, options=_options(create=False)))
    assert verdicts[0].score is not None and verdicts[0].score < 3


def test_each_store_with_statistics_off_gets_a_named_row():
    verdicts = stats_strategy_defined(_ctx(
        tables={"dbo.fact_sales": _table()},
        options={"WH_A": {"auto_create_stats": False, "auto_update_stats": True},
                 "WH_B": {"auto_create_stats": True, "auto_update_stats": False},
                 "WH_C": {"auto_create_stats": True, "auto_update_stats": True}},
    ))
    rows = {v.obj: v.evidence for v in verdicts if v.obj}
    assert set(rows) == {"WH_A", "WH_B"}
    assert all(not v.scored for v in verdicts if v.obj)


def test_a_confirmed_on_estate_says_so():
    """The pass must be visibly verified, not assumed."""
    verdicts = stats_strategy_defined(_ctx(
        tables={"dbo.fact_sales": _table(stats=2)}, options=_options()))
    assert verdicts[0].score == 3
    assert "confirmed ON for all 1 readable store(s)" in verdicts[0].evidence


def test_unreadable_options_are_not_treated_as_off():
    """None means we could not read it - never the same as switched off."""
    verdicts = stats_strategy_defined(_ctx(
        tables={"dbo.fact_sales": _table()},
        options={"WH_Gold": {"auto_create_stats": None, "auto_update_stats": None}},
    ))
    assert verdicts[0].score == 3
    assert "could not be read" in verdicts[0].evidence


def test_statistics_off_beats_prewarming():
    """Pre-warming does not compensate for the engine being switched off."""
    verdicts = stats_strategy_defined(_ctx(
        pipelines={"pl_nightly": _script("UPDATE STATISTICS dbo.fact_sales;")},
        tables={"dbo.fact_sales": _table(stats=2)},
        options=_options(update=False),
    ))
    assert verdicts[0].score is not None and verdicts[0].score < 3


def test_load_side_reports_statistics_switched_off():
    verdict = wh_stats_updated_after_loads(_ctx(
        pipelines={"pl_load": _script("INSERT INTO dbo.fact_sales SELECT * FROM stg;")},
        tables={"dbo.fact_sales": _table(stats=2)},
        options=_options(update=False),
    ))
    assert verdict.score is not None and verdict.score < 3
    assert "switched OFF" in verdict.evidence


# ===========================================================================
# 3.6.7 - a load with no UPDATE STATISTICS is correctly configured
# ===========================================================================

def test_a_load_without_manual_statistics_is_not_a_finding():
    """The reviewer's case: the engine refreshes statistics itself."""
    ctx = _ctx(pipelines={"pl_load": _script(
        "INSERT INTO dbo.fact_sales SELECT * FROM staging.sales;")})
    verdict = wh_stats_updated_after_loads(ctx)
    assert verdict.score == 3
    assert "automatically" in verdict.evidence


def test_a_load_that_prewarms_statistics_is_credited():
    """Pre-warming is the one documented reason to run it manually."""
    ctx = _ctx(pipelines={"pl_load": _script(
        "INSERT INTO dbo.fact_sales SELECT * FROM staging.sales;\n"
        "UPDATE STATISTICS dbo.fact_sales;")})
    verdict = wh_stats_updated_after_loads(ctx)
    assert verdict.score == 3
    assert "pre-warm" in verdict.evidence
    assert "pl_load" in verdict.evidence


def test_a_stored_procedure_that_prewarms_is_credited():
    ctx = _ctx(routines=[{"name": "sp_load_sales",
                          "definition": "INSERT INTO dbo.fact_sales SELECT * FROM stg;"
                                        "UPDATE STATISTICS dbo.fact_sales;"}])
    verdict = wh_stats_updated_after_loads(ctx)
    assert verdict.score == 3
    assert "sp_load_sales" in verdict.evidence


def test_no_readable_load_is_na():
    assert wh_stats_updated_after_loads(_ctx()).status is Status.NA


def test_no_storage_item_is_na():
    ctx = workspace_ctx(items=[], pipelines={})
    assert wh_stats_updated_after_loads(ctx).status is Status.NA


def test_unreadable_pipelines_is_na_not_fail():
    ctx = workspace_ctx(items=[_WAREHOUSE],
                        unavailable={Resource.PIPELINE_DEFINITIONS.value})
    assert wh_stats_updated_after_loads(ctx).status is Status.NA


# ===========================================================================
# 4.4.6 - the store's side of the same question
# ===========================================================================

def test_a_store_with_no_manual_strategy_is_not_a_finding():
    verdicts = stats_strategy_defined(_ctx(tables={"dbo.fact_sales": _table()}))
    assert verdicts[0].score == 3
    assert "no manual maintenance schedule is required" in verdicts[0].evidence


def test_tables_carrying_statistics_are_reported():
    verdicts = stats_strategy_defined(_ctx(tables={
        "dbo.fact_sales": _table(stats=4),
        "dbo.dim_customer": _table(),
    }))
    assert verdicts[0].score == 3
    assert "1 of 2 readable table(s) already carry statistics" in verdicts[0].evidence


def test_prewarming_is_credited_on_the_store_side_too():
    verdicts = stats_strategy_defined(_ctx(
        pipelines={"pl_nightly": _script("UPDATE STATISTICS dbo.fact_sales;")},
        tables={"dbo.fact_sales": _table(stats=2)},
    ))
    assert verdicts[0].score == 3
    assert "pre-warm" in verdicts[0].evidence
    assert "pl_nightly" in verdicts[0].evidence


def test_unreadable_tables_is_na_not_a_pass():
    """Nothing readable at all means nothing was assessed - do not claim health."""
    assert stats_strategy_defined(_ctx())[0].status is Status.NA


def test_no_storage_item_is_na_on_the_store_side():
    assert stats_strategy_defined(workspace_ctx(items=[]))[0].status is Status.NA
