"""The four checks added for refs 14.2.3, 14.5.3, 2.6.4 and 9.3.2.

Every check here is exercised at least three ways — a case that must pass, a case
that must fail, and the N/A path — because the failure mode these additions risk
is a check that quietly returns N/A everywhere and looks like a clean sheet.

Two of them carry extra burden:

* **14.2.3** must never be read as a cardinality *measurement*. Its evidence is
  asserted to say so out loud, and the parser change it depends on is tested
  separately (``test_tmsl.py``) so a regression there is not mistaken for a
  check bug.
* **9.3.2** overlaps ``NB-IDEMPOTENT`` (ref 9.3.1) and ``PL-IDEMPOTENT``
  (ref 2.4.6). ``test_9_3_2_is_strictly_narrower_than_9_3_1`` runs the *same*
  notebook through both and pins the gap: evidence that satisfies 9.3.1 must not
  automatically satisfy 9.3.2, or the new check is a duplicate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from auditfast.core.check.operations_reliability.data_prep.automated import (
    notebook_idempotent,
    notebook_merge_keyed,
    pipeline_idempotent,
    pipeline_merge_keyed,
)
from auditfast.core.check.operations_reliability.reporting_semantic.automated import (
    sm_refresh_failure_alerts,
)
from auditfast.core.check.performance_capacity.data_prep.automated import schedule_stagger
from auditfast.core.check.performance_capacity.reporting_semantic.automated import (
    sm_column_shape,
    sm_query_transform,
)
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Automation, Layer, Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

#: ref -> the check id(s) that answer it. 9.3.2 is answered by two checks (a
#: notebook one and a pipeline one) because the evidence genuinely differs.
ADDED: dict[str, list[str]] = {
    "14.2.3": ["SM-COLUMN-SHAPE"],
    "14.5.3": ["SM-REFRESH-ALERT"],
    "2.6.4": ["WS-SCHEDULE-STAGGER"],
    "9.3.2": ["NB-MERGE-KEYED", "PL-MERGE-KEYED"],
}


# -- helpers -------------------------------------------------------------------

def _ctx(*, obj=None, obj_name: str = "", layer: Layer = Layer.MIXED, **fields) -> CheckContext:
    workspace = WorkspaceContext(id="w", display_name="Prep-Prod-Core", layer=layer, **fields)
    return CheckContext(
        workspace=workspace,
        settings={},
        obj_name=obj_name or workspace.name,
        obj=workspace if obj is None else obj,
    )


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}]}


def _pipeline(activities: list[dict]) -> dict:
    return {"properties": {"activities": activities}}


def _script(sql: str, name: str = "Load") -> dict:
    return {"name": name, "type": "Script",
            "typeProperties": {"scripts": [{"text": sql}]}}


def _copy(name: str, sink: dict) -> dict:
    return {"name": name, "type": "Copy", "typeProperties": {"sink": sink}}


def _column(name: str, table: str = "Sales", **fields) -> dict:
    return {
        "table": table, "name": name,
        "data_type": fields.get("data_type", "string"),
        "source_provider_type": fields.get("source_provider_type", ""),
        "source_column": "",
        "is_hidden": fields.get("is_hidden", False),
        "is_key": fields.get("is_key", False),
    }


def _schedule(*, enabled: bool = True, notify: str = "MailOnFailure") -> dict:
    return {
        "enabled": enabled,
        "notify_option": notify,
        "notifies_on_failure": bool(notify) and notify.lower() != "nonotification",
        "days": ["Monday"], "times": ["06:00"], "local_time_zone_id": "UTC",
    }


def _model_item(name: str, item_id: str = "sm-1") -> Item:
    return Item(id=item_id, type="SemanticModel", display_name=name)


_BASE = datetime(2026, 3, 2, 6, 0, tzinfo=timezone.utc)


def _stamps(*offsets_minutes: float) -> list[str]:
    """ISO-8601 UTC stamps at the given minute offsets from a fixed base.

    A fixed base keeps the check deterministic: nothing here reads the clock.
    """
    return [
        (_BASE + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
        for offset in offsets_minutes
    ]


def _runnable(item_id: str, name: str, item_type: str = "DataPipeline") -> Item:
    return Item(id=item_id, type=item_type, display_name=name)


# -- registration + remediation ------------------------------------------------

@pytest.mark.parametrize("ref,ids", sorted(ADDED.items()))
def test_new_ref_is_registered_and_automated(ref, ids):
    specs = [s for s in REGISTRY if s.ref == ref]
    assert sorted(s.id for s in specs) == sorted(ids), f"ref {ref} registration drifted"
    for spec in specs:
        assert spec.automation is Automation.AUTOMATED
        assert spec.manual is False
        assert spec.options == ()


@pytest.mark.parametrize("ref", sorted(ADDED))
def test_new_ref_has_remediation_text(ref):
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    assert load_remediation(load_project(PROJECT_FILE)).get(ref)


# -- 14.2.3 — model column shape ----------------------------------------------

def test_column_shape_passes_when_columns_are_modelling_shaped():
    model = {
        "tables": ["Sales", "Customer"],
        "relationships": [{"from_table": "Sales", "from_column": "CustomerKey",
                           "to_table": "Customer", "to_column": "CustomerKey"}],
        "columns": [
            _column("CustomerKey", "Sales", data_type="int64"),
            _column("CustomerKey", "Customer", data_type="int64", is_key=True),
            _column("Amount", "Sales", data_type="decimal"),
            _column("OrderDate", "Sales", data_type="dateTime",
                    source_provider_type="date"),
            _column("Country", "Customer", data_type="string"),
        ],
    }
    verdict = sm_column_shape(_ctx(obj_name="Sales", semantic_models={"Sales": model}))
    assert verdict.score == 3
    assert "flagged" not in verdict.evidence
    # The proxy caveat is not optional — it must reach the report.
    assert "shape" in verdict.evidence and "distinct-value count" in verdict.evidence


def test_column_shape_fails_on_guid_timestamp_freetext_and_unused_identity():
    model = {
        "tables": ["Sales"],
        "relationships": [],
        "columns": [
            _column("RowGuid", data_type="string",
                    source_provider_type="uniqueidentifier"),
            _column("CreatedTimestamp", data_type="dateTime",
                    source_provider_type="datetime2"),
            _column("Comment", data_type="string",
                    source_provider_type="nvarchar(max)"),
            _column("TransactionID", data_type="int64", is_key=True),
            _column("Amount", data_type="decimal"),
        ],
    }
    verdict = sm_column_shape(_ctx(obj_name="Sales", semantic_models={"Sales": model}))
    assert verdict.score == 0
    for reason in ("GUID", "full-precision datetime", "free text", "unused row identifier"):
        assert reason in verdict.evidence


def test_column_shape_exempts_columns_a_relationship_binds():
    """A GUID key a relationship uses is load-bearing, not an accident."""
    model = {
        "tables": ["Sales", "Customer"],
        "relationships": [{"from_table": "Sales", "from_column": "CustomerGuid",
                           "to_table": "Customer", "to_column": "CustomerGuid"}],
        "columns": [
            _column("CustomerGuid", "Sales", source_provider_type="uniqueidentifier"),
            _column("CustomerGuid", "Customer", source_provider_type="uniqueidentifier"),
            _column("Amount", "Sales", data_type="decimal"),
        ],
    }
    verdict = sm_column_shape(_ctx(obj_name="Sales", semantic_models={"Sales": model}))
    assert verdict.score == 3
    assert "relationship key(s) exempt" in verdict.evidence


def test_column_shape_does_not_flag_a_plain_date_or_an_ordinary_attribute():
    """`order_date` and `customer_name` must never read as high-cardinality shapes."""
    model = {
        "tables": ["Sales"], "relationships": [],
        "columns": [
            _column("order_date", data_type="dateTime"),
            _column("customer_name", data_type="string"),
            _column("status", data_type="string"),
        ],
    }
    verdict = sm_column_shape(_ctx(obj_name="Sales", semantic_models={"Sales": model}))
    assert verdict.score == 3


def test_column_shape_does_not_flag_a_description_named_column_without_an_unbounded_type():
    """A `Description` column is judged free text only by its source type, not its name."""
    model = {
        "tables": ["Product"], "relationships": [],
        "columns": [
            _column("Description", "Product", data_type="string"),
            _column("Notes", "Product", data_type="string"),
            _column("Amount", "Product", data_type="decimal"),
        ],
    }
    verdict = sm_column_shape(_ctx(obj_name="Product", semantic_models={"Product": model}))
    assert verdict.score == 3


def test_column_shape_is_na_when_definitions_unreadable():
    verdict = sm_column_shape(_ctx(
        obj_name="Sales", unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None


def test_column_shape_is_na_when_the_snapshot_predates_column_parsing():
    """An old KB snapshot has no `columns` key — that is unknown, not a failure."""
    model = {"tables": ["Sales"], "relationships": [], "measures": []}
    verdict = sm_column_shape(_ctx(obj_name="Sales", semantic_models={"Sales": model}))
    assert verdict.status is Status.NA
    assert "re-crawl" in verdict.evidence


# -- 14.2.6 — warehouse serves the model, no inline transformation ------------

def _storage(**tables) -> dict:
    """A `storage` facts dict; each value is (modes, native_query_expressions)."""
    return {
        name: {
            "modes": modes,
            "source_types": ["m"] if exprs else ["entity"],
            "native_query_partitions": len(exprs),
            "native_query_expressions": list(exprs),
        }
        for name, (modes, exprs) in tables.items()
    }


def test_query_transform_passes_a_plain_source_read():
    """A source navigation or a plain SELECT is warehouse-served, not a transform."""
    model = {"storage": _storage(
        DimCustomer=(["import"], ['let Source = Sql.Database("s","db"){[Item="DimCustomer"]}[Data] in Source']),
        FactSales=(["import"], ['Value.NativeQuery(Source, "SELECT Id, Amount FROM dbo.FactSales")']),
    )}
    verdict = sm_query_transform(_ctx(obj_name="M", semantic_models={"M": model}))
    assert verdict.score == 3
    assert "2 of 2" in verdict.evidence
    assert "inline transformation" not in verdict.evidence


def test_query_transform_flags_a_genuine_merge_or_group_by():
    model = {"storage": _storage(
        Appended=(["import"], ['let s = Table.Combine({A, B}) in s']),
        Grouped=(["import"], ['Value.NativeQuery(Source, "SELECT k, SUM(v) FROM t GROUP BY k")']),
        Clean=(["import"], ['let Source = Lakehouse.Contents(){[Item="dim"]}[Data] in Source']),
    )}
    verdict = sm_query_transform(_ctx(obj_name="M", semantic_models={"M": model}))
    assert verdict.score == 0
    assert "Appended" in verdict.evidence and "Grouped" in verdict.evidence
    assert "Clean" not in verdict.evidence


def test_query_transform_falls_back_when_the_snapshot_lacks_query_text():
    """An old snapshot has only the count; it must still produce a verdict, not error."""
    model = {"storage": {
        "T": {"modes": ["import"], "source_types": ["m"], "native_query_partitions": 1},
    }}
    verdict = sm_query_transform(_ctx(obj_name="M", semantic_models={"M": model}))
    assert verdict.score == 0
    assert "inline transformation" in verdict.evidence


def test_query_transform_is_na_when_definitions_unreadable():
    verdict = sm_query_transform(_ctx(
        obj_name="M", unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS},
    ))
    assert verdict.status is Status.NA


# -- 14.5.3 — refresh failures alert the owning team ---------------------------

def test_refresh_alert_passes_when_every_schedule_notifies_on_failure():
    verdict = sm_refresh_failure_alerts(_ctx(
        layer=Layer.REPORTING,
        items=[_model_item("Sales", "sm-1"), _model_item("Finance", "sm-2")],
        refresh_schedules={"Sales": _schedule(), "Finance": _schedule()},
    ))
    assert verdict.score == 3
    assert "2 of 2" in verdict.evidence


def test_refresh_alert_fails_when_schedules_are_silent_and_nothing_else_alerts():
    verdict = sm_refresh_failure_alerts(_ctx(
        layer=Layer.REPORTING,
        items=[_model_item("Sales", "sm-1"), _model_item("Finance", "sm-2")],
        refresh_schedules={
            "Sales": _schedule(notify="NoNotification"),
            "Finance": _schedule(notify="NoNotification"),
        },
    ))
    assert verdict.score == 0
    assert "silent" in verdict.evidence


def test_refresh_alert_is_lifted_but_not_cleared_by_a_reflex_item():
    """A Data Activator alerts, but cannot be tied to the silent model."""
    verdict = sm_refresh_failure_alerts(_ctx(
        layer=Layer.REPORTING,
        items=[_model_item("Sales", "sm-1"),
               Item(id="rx-1", type="Reflex", display_name="Refresh Watchdog")],
        refresh_schedules={"Sales": _schedule(notify="NoNotification")},
    ))
    assert verdict.score == 2
    assert "Reflex" in verdict.evidence
    assert "corroborates rather than clears" in verdict.evidence


def test_refresh_alert_credits_a_pipeline_refresh_with_an_on_failure_path():
    pipeline = _pipeline([
        {"name": "Refresh", "type": "PBISemanticModelRefresh", "typeProperties": {}},
        {"name": "Notify", "type": "Teams",
         "dependsOn": [{"activity": "Refresh", "dependencyConditions": ["Failed"]}]},
    ])
    verdict = sm_refresh_failure_alerts(_ctx(
        layer=Layer.REPORTING,
        items=[_model_item("Sales", "sm-1")],
        refresh_schedules={"Sales": _schedule(enabled=False, notify="NoNotification")},
        pipelines={"PL_Gold": pipeline},
    ))
    assert verdict.score == 3
    assert "on-failure path" in verdict.evidence


def test_refresh_alert_is_na_without_a_power_bi_token():
    """No Power BI-audience token means unknown — never a FAIL."""
    verdict = sm_refresh_failure_alerts(_ctx(
        layer=Layer.REPORTING,
        items=[_model_item("Sales", "sm-1")],
        unavailable={Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None
    assert "Power BI-audience token" in verdict.evidence


def test_refresh_alert_is_na_without_semantic_models():
    verdict = sm_refresh_failure_alerts(_ctx(layer=Layer.REPORTING, items=[]))
    assert verdict.status is Status.NA


# -- 2.6.4 — scheduling avoids capacity contention -----------------------------

def test_schedule_stagger_passes_when_runs_are_spread_over_the_clock():
    verdict = schedule_stagger(_ctx(
        layer=Layer.PREP,
        items=[_runnable("a", "PL_Sales"), _runnable("b", "PL_Finance"),
               _runnable("c", "PL_HR"), _runnable("d", "PL_Ops")],
        run_history={
            "a": _stamps(7, 1447),
            "b": _stamps(103, 1543),
            "c": _stamps(214, 1654),
            "d": _stamps(322, 1762),
        },
    ))
    assert verdict.score == 3
    assert "well staggered" in verdict.evidence
    # The honesty caveat must survive into the report.
    assert "not from the configured schedule" in verdict.evidence


def test_schedule_stagger_fails_when_everything_runs_in_one_window():
    verdict = schedule_stagger(_ctx(
        layer=Layer.PREP,
        items=[_runnable("a", "PL_Sales"), _runnable("b", "PL_Finance"),
               _runnable("c", "PL_HR"), _runnable("d", "PL_Ops")],
        run_history={
            "a": _stamps(0, 1440),
            "b": _stamps(1, 1441),
            "c": _stamps(2, 1442),
            "d": _stamps(1, 1443),
        },
    ))
    assert verdict.score == 0
    assert "everything runs at once" in verdict.evidence


def test_schedule_stagger_counts_items_not_runs_so_one_chatty_item_cannot_fail_it():
    """A single item retrying ten times in a window is not estate-wide contention."""
    verdict = schedule_stagger(_ctx(
        layer=Layer.PREP,
        items=[_runnable("a", "PL_Retry"), _runnable("b", "PL_Finance"),
               _runnable("c", "PL_HR")],
        run_history={
            "a": _stamps(0, 1, 2, 3, 4, 0.5, 1.5, 2.5),
            "b": _stamps(400, 1840),
            "c": _stamps(800, 2240),
        },
    ))
    assert verdict.score == 3


def test_schedule_stagger_is_na_with_too_few_items_to_judge():
    verdict = schedule_stagger(_ctx(
        layer=Layer.PREP,
        items=[_runnable("a", "PL_Only")],
        run_history={"a": _stamps(0, 60, 120)},
    ))
    assert verdict.status is Status.NA
    assert "coincidence" in verdict.evidence


def test_schedule_stagger_is_na_when_run_history_unreadable():
    verdict = schedule_stagger(_ctx(
        layer=Layer.PREP, unavailable={Resource.ITEM_RUN_HISTORY},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None


# -- 9.3.2 — keyed merge/upsert (notebook) -------------------------------------

_KEYED_MERGE_NB = """
target = DeltaTable.forName(spark, "gold.customer")
(target.alias("t")
   .merge(updates.alias("s"), "t.customer_id = s.customer_id")
   .whenMatchedUpdateAll()
   .whenNotMatchedInsertAll()
   .execute())
"""

_BLIND_APPEND_NB = """
batch_id = run_id
watermark = last_load_date
df.write.mode("append").saveAsTable("gold.customer")
"""


def test_nb_merge_keyed_passes_on_a_keyed_delta_merge():
    verdict = notebook_merge_keyed(_ctx(obj=_nb(_KEYED_MERGE_NB), obj_name="NB_Load"))
    assert verdict.score == 3
    assert "keyed MERGE/upsert" in verdict.evidence


def test_nb_merge_keyed_fails_on_a_blind_append():
    verdict = notebook_merge_keyed(_ctx(obj=_nb(_BLIND_APPEND_NB), obj_name="NB_Load"))
    assert verdict.score == 0
    assert "no key handling" in verdict.evidence


def test_nb_merge_keyed_grades_a_full_overwrite_in_the_middle():
    code = 'df.write.mode("overwrite").saveAsTable("gold.customer")'
    verdict = notebook_merge_keyed(_ctx(obj=_nb(code), obj_name="NB_Load"))
    assert verdict.score == 2
    assert "not a keyed upsert" in verdict.evidence


def test_nb_merge_keyed_credits_a_keyed_replace():
    code = 'df.write.option("replaceWhere", "load_date = \'2026-03-02\'").save(path)'
    verdict = notebook_merge_keyed(_ctx(obj=_nb(code), obj_name="NB_Load"))
    assert verdict.score == 3
    assert "keyed replace" in verdict.evidence


def test_nb_merge_keyed_grades_append_then_dedup_low_but_not_zero():
    code = (
        'df.write.mode("append").saveAsTable("gold.customer")\n'
        'spark.table("gold.customer").dropDuplicates(["customer_id"])'
    )
    verdict = notebook_merge_keyed(_ctx(obj=_nb(code), obj_name="NB_Load"))
    assert verdict.score == 1


def test_nb_merge_keyed_ignores_a_merge_that_is_only_a_comment():
    """executable_code, not notebook_code: a comment describing MERGE is not a MERGE."""
    code = (
        "# we should MERGE INTO gold.customer USING staging ON t.id = s.id\n"
        'df.write.mode("append").saveAsTable("gold.customer")'
    )
    verdict = notebook_merge_keyed(_ctx(obj=_nb(code), obj_name="NB_Load"))
    assert verdict.score == 0


def test_nb_merge_keyed_is_na_without_a_write():
    code = 'df = spark.table("silver.customer")\ndisplay(df)'
    verdict = notebook_merge_keyed(_ctx(obj=_nb(code), obj_name="NB_Read"))
    assert verdict.status is Status.NA


def test_nb_merge_keyed_is_na_when_definitions_unreadable():
    verdict = notebook_merge_keyed(_ctx(
        obj=_nb(_BLIND_APPEND_NB), obj_name="NB_Load",
        unavailable={Resource.NOTEBOOK_DEFINITIONS},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None


# -- 9.3.2 — the dedup claim, pinned ------------------------------------------

def test_9_3_2_is_strictly_narrower_than_9_3_1():
    """The same notebook must pass 9.3.1 and fail 9.3.2, or 9.3.2 is a duplicate.

    ``_BLIND_APPEND_NB`` appends with no key handling, but mentions ``batch_id``
    and ``watermark`` — both in ``NB-IDEMPOTENT``'s ``_IDEMPOTENT_PATTERN``. That
    is exactly the gap: "some rerun-safety mechanism is present" is a weaker
    claim than "the write is a keyed upsert".
    """
    ctx = _ctx(obj=_nb(_BLIND_APPEND_NB), obj_name="NB_Load")
    assert notebook_idempotent(ctx).score == 3, "9.3.1 no longer passes this notebook"
    assert notebook_merge_keyed(ctx).score == 0, "9.3.2 has widened into a duplicate of 9.3.1"


def test_9_3_2_is_strictly_narrower_than_2_4_6_for_pipelines():
    """A pipeline whose Copy inserts, with a batch_id parameter, passes 2.4.6 only."""
    pipeline = {
        "properties": {
            "parameters": {"batch_id": {"type": "String"}},
            "activities": [_copy("Load", {"type": "DataWarehouseSink"})],
        }
    }
    ctx = _ctx(obj=pipeline, obj_name="PL_Load")
    assert pipeline_idempotent(ctx).score == 3, "2.4.6 no longer passes this pipeline"
    assert pipeline_merge_keyed(ctx).score == 0, "9.3.2 has widened into a duplicate of 2.4.6"


# -- 9.3.2 — keyed merge/upsert (pipeline) ------------------------------------

def test_pl_merge_keyed_passes_on_an_upserting_copy_sink():
    pipeline = _pipeline([
        _copy("Load", {"type": "DataWarehouseSink", "writeBehavior": "upsert",
                       "upsertSettings": {"keys": ["customer_id"]}}),
    ])
    verdict = pipeline_merge_keyed(_ctx(obj=pipeline, obj_name="PL_Load"))
    assert verdict.score == 3
    assert "upsertSettings.keys" in verdict.evidence


def test_pl_merge_keyed_fails_on_an_inserting_copy_sink():
    pipeline = _pipeline([_copy("Load", {"type": "DataWarehouseSink"})])
    verdict = pipeline_merge_keyed(_ctx(obj=pipeline, obj_name="PL_Load"))
    assert verdict.score == 0
    assert "0 of 1" in verdict.evidence


def test_pl_merge_keyed_passes_on_keyed_merge_sql_in_a_script_activity():
    sql = ("MERGE INTO gold.customer AS t USING staging.customer AS s "
           "ON t.customer_id = s.customer_id "
           "WHEN MATCHED THEN UPDATE SET t.name = s.name "
           "WHEN NOT MATCHED THEN INSERT (customer_id, name) VALUES (s.customer_id, s.name);")
    verdict = pipeline_merge_keyed(_ctx(obj=_pipeline([_script(sql)]), obj_name="PL_Load"))
    assert verdict.score == 3


def test_pl_merge_keyed_fails_on_insert_only_script_sql():
    sql = "INSERT INTO gold.customer SELECT * FROM staging.customer;"
    verdict = pipeline_merge_keyed(_ctx(obj=_pipeline([_script(sql)]), obj_name="PL_Load"))
    assert verdict.score == 0


def test_pl_merge_keyed_is_na_without_a_write():
    pipeline = _pipeline([{"name": "Wait", "type": "Wait", "typeProperties": {}}])
    verdict = pipeline_merge_keyed(_ctx(obj=pipeline, obj_name="PL_Idle"))
    assert verdict.status is Status.NA


def test_pl_merge_keyed_is_na_when_definitions_unreadable():
    pipeline = _pipeline([_copy("Load", {"type": "DataWarehouseSink"})])
    verdict = pipeline_merge_keyed(_ctx(
        obj=pipeline, obj_name="PL_Load",
        unavailable={Resource.PIPELINE_DEFINITIONS},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None
