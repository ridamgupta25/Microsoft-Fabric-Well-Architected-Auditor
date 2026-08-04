"""Regression tests for detector-precision fixes to seven checks.

Each check is a pure function of a notebook / pipeline / table definition, so
these build synthetic definitions and assert the verdict directly. Every test
pairs the previously-misjudged case (the bug) with the case that must keep
working, so a future rewrite cannot silently reintroduce the false PASS/FAIL.
"""
from __future__ import annotations

from datetime import datetime, timezone

from auditfast.core.check.cost_resource_optimization.data_operations.automated import (
    no_orphaned_items,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    nb_no_display,
    nb_no_udf,
    nb_timeout,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    table_date_dimension,
)
from auditfast.core.check.operations_reliability.data_prep.automated import (
    failure_notification,
)
from auditfast.core.check.performance_capacity.data_prep.automated import (
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
