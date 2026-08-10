"""The six checks promoted from interactive (self-assessed) to automated.

Refs 10.1.5, 10.4.2, 8.1.1, 8.1.5, 7.4.6 and 7.2.3 were originally recorded as
unanswerable from a read-only crawl. They are not: four are derivable from data
the knowledge base already holds (run history, item definitions, shortcuts), and
two come from one ordinary delegated Fabric REST read of a Warehouse's
``settings/sqlAudit`` configuration.

Every check here is exercised three ways — a case that must pass, a case that
must fail, and the N/A path — because the failure mode these promotions risk is
a check that quietly returns N/A everywhere and looks like a clean sheet.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.governance_compliance.data_logs.automated import (
    warehouse_auditing_enabled,
)
from auditfast.core.check.governance_compliance.data_operations.automated import (
    cross_domain_dependencies_identifiable,
    financial_data_modifications_audited,
    lineage_view_is_accurate,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    monitoring_refresh_cadence,
    warehouse_loads_monitored,
)
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Automation, Layer, Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

#: ref -> the check id that now answers it automatically.
PROMOTED: dict[str, str] = {
    "10.1.5": "OPS-WH-LOAD-MONITORED",
    "10.4.2": "OPS-MONITOR-REFRESH",
    "8.1.1": "GOV-LINEAGE-VIEW",
    "8.1.5": "GOV-LINEAGE-CROSSDOMAIN",
    "7.4.6": "GOV-WH-AUDIT",
    "7.2.3": "GOV-FIN-CHANGE-AUDIT",
}


def _ctx(**fields) -> CheckContext:
    workspace = WorkspaceContext(id="w", display_name="Ops-Prod-Logs", **fields)
    return CheckContext(workspace=workspace, settings={}, obj_name=workspace.name,
                        obj=workspace)


def _nb(code: str, *, lakehouse: str = "") -> dict:
    metadata: dict = {}
    if lakehouse:
        metadata["trident"] = {"lakehouse": {"known_lakehouses": [{"id": lakehouse}]}}
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": metadata}


def _audit(state: str, groups: list[str], retention: int | None = 90) -> dict:
    return {
        "state": state,
        "enabled": state.lower() == "enabled",
        "action_groups": groups,
        "retention_days": retention,
    }


def _warehouse(name: str, item_id: str = "wh-1") -> Item:
    return Item(id=item_id, type="Warehouse", display_name=name)


# -- registration: promoted once, automated, and remediable --------------------

@pytest.mark.parametrize("ref,check_id", sorted(PROMOTED.items()))
def test_promoted_ref_is_registered_exactly_once_and_automated(ref, check_id):
    """A promotion that leaves the interactive spec behind registers the ref twice."""
    specs = [s for s in REGISTRY if s.ref == ref]
    assert [s.id for s in specs] == [check_id], f"ref {ref} is not registered exactly once"
    spec = specs[0]
    assert spec.automation is Automation.AUTOMATED
    assert spec.manual is False  # the engine must actually run it
    assert spec.options == ()    # and no reviewer is asked to answer it


@pytest.mark.parametrize("ref", sorted(PROMOTED))
def test_promoted_ref_has_remediation_text(ref):
    """Interactive checks are exempt from remediation.yaml; automated ones are not."""
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    assert load_remediation(load_project(PROJECT_FILE)).get(ref)


# -- 7.4.6 — Warehouse auditing enabled ---------------------------------------

def test_warehouse_audit_passes_when_every_warehouse_audits():
    verdict = warehouse_auditing_enabled(_ctx(
        items=[_warehouse("Finance", "wh-1"), _warehouse("Sales", "wh-2")],
        warehouse_audit={
            "Finance": _audit("Enabled", ["BATCH_COMPLETED_GROUP"]),
            "Sales": _audit("Enabled", ["BATCH_COMPLETED_GROUP"], retention=30),
        },
    ))
    assert verdict.score == 3
    assert "2 of 2" in verdict.evidence


def test_warehouse_audit_fails_when_auditing_is_off():
    verdict = warehouse_auditing_enabled(_ctx(
        items=[_warehouse("Finance")],
        warehouse_audit={"Finance": _audit("Disabled", [])},
    ))
    assert verdict.score == 0
    assert "Finance" in verdict.evidence


def test_warehouse_audit_is_na_when_the_setting_is_unreadable():
    """A forbidden setting is 'we could not ask', never 'auditing is off'."""
    verdict = warehouse_auditing_enabled(_ctx(
        items=[_warehouse("Finance")],
        unavailable={Resource.WAREHOUSE_AUDIT},
    ))
    assert verdict.status is Status.NA
    assert verdict.score is None


def test_warehouse_audit_is_na_without_a_warehouse():
    verdict = warehouse_auditing_enabled(_ctx(
        items=[Item(id="lh-1", type="Lakehouse", display_name="Bronze")],
    ))
    assert verdict.status is Status.NA


# -- 7.2.3 — the audit captures data modifications ----------------------------

def test_financial_audit_passes_on_a_statement_action_group():
    verdict = financial_data_modifications_audited(_ctx(
        items=[_warehouse("Finance")],
        warehouse_audit={"Finance": _audit(
            "Enabled", ["BATCH_COMPLETED_GROUP", "SCHEMA_OBJECT_CHANGE_GROUP"])},
    ))
    assert verdict.score == 3


def test_financial_audit_is_partial_when_only_logins_are_captured():
    """Auditing on, but configured to record connections — it cannot say what changed."""
    verdict = financial_data_modifications_audited(_ctx(
        items=[_warehouse("Finance")],
        warehouse_audit={"Finance": _audit(
            "Enabled", ["SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP"])},
    ))
    assert verdict.score == 1
    assert "connections only" in verdict.evidence


def test_financial_audit_fails_when_auditing_is_disabled():
    verdict = financial_data_modifications_audited(_ctx(
        items=[_warehouse("Finance")],
        warehouse_audit={"Finance": _audit("Disabled", [])},
    ))
    assert verdict.score == 0


def test_financial_audit_is_na_when_the_setting_is_unreadable():
    verdict = financial_data_modifications_audited(_ctx(
        items=[_warehouse("Finance")],
        unavailable={Resource.WAREHOUSE_AUDIT},
    ))
    assert verdict.status is Status.NA


# -- 8.1.1 — the items are wired so lineage can connect them -------------------

def test_lineage_passes_for_an_attached_lakehouse_notebook():
    verdict = lineage_view_is_accurate(_ctx(
        notebooks={"NB_Gold": _nb('df = spark.table("sales")\ndf.write.saveAsTable("gold")',
                                  lakehouse="lh-1")},
    ))
    assert verdict.score == 3
    assert "1 of 1" in verdict.evidence


def test_lineage_fails_for_a_hardcoded_path_notebook():
    """A flow expressed only as a storage URL is real, and invisible in the lineage view."""
    verdict = lineage_view_is_accurate(_ctx(
        notebooks={"NB_Raw": _nb(
            'df = spark.read.parquet("abfss://raw@storage.dfs.core.windows.net/in/")\n'
            'df.write.parquet("abfss://raw@storage.dfs.core.windows.net/out/")'
        )},
    ))
    assert verdict.score == 0
    assert "NB_Raw" in verdict.evidence


def test_lineage_does_not_count_a_python_import_as_wiring():
    """``from pyspark.sql import ...`` is an import, not a table read."""
    verdict = lineage_view_is_accurate(_ctx(
        notebooks={"NB_Util": _nb(
            "from pyspark.sql import functions as F\n"
            'F.lit(1)\ndf.write.parquet("abfss://raw@storage.dfs.core.windows.net/out/")'
        )},
    ))
    assert verdict.score == 0


def test_lineage_ignores_a_notebook_with_no_data_reference():
    """No data flow is not a broken data flow — it leaves the population, not fails."""
    verdict = lineage_view_is_accurate(_ctx(
        notebooks={"NB_Doc": _nb("print('hello')")},
    ))
    assert verdict.status is Status.NA


def test_lineage_is_na_when_definitions_are_unreadable():
    verdict = lineage_view_is_accurate(_ctx(
        unavailable={Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS},
    ))
    assert verdict.status is Status.NA


# -- 8.1.5 — cross-domain dependencies are identifiable ------------------------

def test_cross_domain_passes_for_a_named_shortcut():
    verdict = cross_domain_dependencies_identifiable(_ctx(
        shortcuts={"Bronze": [{"name": "finance_gl", "path": "/Tables",
                               "target_type": "OneLake"}]},
    ))
    assert verdict.score == 3
    assert "finance_gl" in verdict.evidence


def test_cross_domain_fails_for_a_bare_guid_path():
    """A GUID names nothing: the dependency exists but no owning team can be found."""
    guid_ws = "11111111-2222-3333-4444-555555555555"
    guid_item = "66666666-7777-8888-9999-000000000000"
    verdict = cross_domain_dependencies_identifiable(_ctx(
        notebooks={"NB_Join": _nb(
            f'spark.read.load("abfss://{guid_ws}@onelake.dfs.fabric.microsoft.com/'
            f'{guid_item}/Tables/gl")'
        )},
    ))
    assert verdict.score == 0
    assert "opaque" in verdict.evidence


def test_cross_domain_named_workspace_path_is_identifiable():
    verdict = cross_domain_dependencies_identifiable(_ctx(
        notebooks={"NB_Join": _nb(
            'spark.read.load("abfss://Finance-Prod@onelake.dfs.fabric.microsoft.com/'
            'GoldLake.Lakehouse/Tables/gl")'
        )},
    ))
    assert verdict.score == 3


def test_cross_domain_ignores_a_path_back_into_this_workspace():
    """A path into the workspace being audited is not a cross-domain dependency."""
    verdict = cross_domain_dependencies_identifiable(_ctx(
        notebooks={"NB_Self": _nb(
            'spark.read.load("abfss://Ops-Prod-Logs@onelake.dfs.fabric.microsoft.com/'
            'Bronze.Lakehouse/Tables/t")'
        )},
    ))
    assert verdict.status is Status.NA


def test_cross_domain_is_na_when_nothing_is_readable():
    verdict = cross_domain_dependencies_identifiable(_ctx(
        unavailable={Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS,
                     Resource.SHORTCUTS},
    ))
    assert verdict.status is Status.NA


# -- 10.1.5 — Warehouse loads are observable -----------------------------------

_WH_COPY = {
    "properties": {"activities": [{
        "name": "Load fact_sales", "type": "Copy",
        "typeProperties": {
            "sink": {
                "type": "DataWarehouseSink",
                "datasetSettings": {
                    "type": "DataWarehouseTable",
                    "typeProperties": {"schema": "dbo", "table": "fact_sales"},
                    "linkedService": {"properties": {"type": "DataWarehouse"}},
                },
            },
        },
    }]}
}


def test_warehouse_load_monitored_passes_with_run_history_and_row_counts():
    verdict = warehouse_loads_monitored(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Load_WH",
                    last_run_utc="2026-03-02T09:30:00Z")],
        pipelines={"PL_Load_WH": _WH_COPY},
        tables={"etl_audit_log": {"type": "Managed", "format": "Delta", "columns": [
            {"name": "row_count", "type": "int"},
            {"name": "run_ts", "type": "timestamp"},
        ]}},
    ))
    assert verdict.score == 3
    assert "row volume per load is recorded" in verdict.evidence


def test_warehouse_load_monitored_fails_without_runs_or_row_counts():
    verdict = warehouse_loads_monitored(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Load_WH")],
        pipelines={"PL_Load_WH": _WH_COPY},
    ))
    assert verdict.score == 0
    assert "nothing records the rows a load moved" in verdict.evidence


def test_warehouse_load_monitored_is_na_without_a_warehouse_load():
    verdict = warehouse_loads_monitored(_ctx(
        layer=Layer.LOGS,
        pipelines={"PL_Lakehouse_Only": {"properties": {"activities": [
            {"name": "Copy", "type": "Copy",
             "typeProperties": {"sink": {"type": "LakehouseTableSink"}}},
        ]}}},
    ))
    assert verdict.status is Status.NA


def test_warehouse_load_monitored_is_na_when_run_history_is_unreadable():
    verdict = warehouse_loads_monitored(_ctx(
        layer=Layer.LOGS,
        pipelines={"PL_Load_WH": _WH_COPY},
        unavailable={Resource.ITEM_RUN_HISTORY},
    ))
    assert verdict.status is Status.NA


# -- 10.4.2 — the observed monitoring cadence ----------------------------------

def _hourly(count: int = 4) -> list[str]:
    return [f"2026-03-02T{9 - i:02d}:00:00Z" for i in range(count)]


def _weekly(count: int = 3) -> list[str]:
    return [f"2026-03-{22 - 7 * i:02d}T09:00:00Z" for i in range(count)]


def test_monitor_refresh_passes_on_an_hourly_cadence():
    verdict = monitoring_refresh_cadence(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Monitoring_Refresh")],
        run_history={"p1": _hourly()},
    ))
    assert verdict.score == 3
    assert "near-real-time or hourly" in verdict.evidence


def test_monitor_refresh_fails_on_a_weekly_cadence():
    verdict = monitoring_refresh_cadence(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Monitoring_Refresh")],
        run_history={"p1": _weekly()},
    ))
    assert verdict.score == 0
    assert "too slow to act on" in verdict.evidence


def test_monitor_refresh_is_na_without_two_runs():
    """One run is a reading, not a cadence."""
    verdict = monitoring_refresh_cadence(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Monitoring_Refresh")],
        run_history={"p1": ["2026-03-02T09:00:00Z"]},
    ))
    assert verdict.status is Status.NA


def test_monitor_refresh_is_na_when_run_history_is_unreadable():
    verdict = monitoring_refresh_cadence(_ctx(
        layer=Layer.LOGS, unavailable={Resource.ITEM_RUN_HISTORY},
    ))
    assert verdict.status is Status.NA


def test_monitor_refresh_uses_every_item_in_a_data_logs_workspace():
    """In a Data Logs workspace every runnable item is part of the monitoring estate."""
    verdict = monitoring_refresh_cadence(_ctx(
        layer=Layer.LOGS,
        items=[Item(id="p1", type="DataPipeline", display_name="PL_Nightly")],
        run_history={"p1": _hourly()},
    ))
    assert verdict.score == 3
