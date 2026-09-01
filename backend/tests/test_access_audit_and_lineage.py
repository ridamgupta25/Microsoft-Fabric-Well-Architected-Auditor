"""Access-audit depth (7.4.3) and lineage connectivity (8.1.2).

Both group checks previously scored a proxy rather than the point:

* ``XW-ACCESS-AUDIT`` scored only the ``enabled`` flag, so a login-only audit with
  zero retention read as a complete data-access trail -- and a workspace with no
  Warehouse at all was reported as *failing* to enable one.
* ``XW-LINEAGE-E2E`` scored whether a source item, a store and a reporting item
  coexisted, which fails a correctly layered estate and passes a workspace whose
  three stages are entirely unconnected.
"""
from __future__ import annotations

from auditfast.core.check._lineage import attached_lakehouses, attached_stores
from auditfast.core.check.governance_compliance.data_operations.group import (
    access_audit_consistent,
    lineage_e2e_consistent,
)
from auditfast.core.enums import Layer, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext

#: The three action groups Fabric enables by default: logins and batch outcomes.
DEFAULT_GROUPS = [
    "BATCH_COMPLETED_GROUP",
    "FAILED_DATABASE_AUTHENTICATION_GROUP",
    "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
]
DATA_ACCESS_GROUPS = [*DEFAULT_GROUPS, "SCHEMA_OBJECT_ACCESS_GROUP"]


def _ws(name: str, **kwargs) -> WorkspaceContext:
    ctx = WorkspaceContext(id=name, display_name=name, layer=Layer.MIXED)
    ctx.items = list(kwargs.pop("items", []))
    ctx.warehouse_audit = dict(kwargs.pop("warehouse_audit", {}))
    ctx.pipelines = dict(kwargs.pop("pipelines", {}))
    ctx.notebooks = dict(kwargs.pop("notebooks", {}))
    ctx.reports = list(kwargs.pop("reports", []))
    for resource in kwargs.pop("unavailable", ()):
        ctx.unavailable.add(resource)
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="G",
        members=tuple(GroupMemberContext(ws, lvl, Layer.MIXED) for ws, lvl in members),
        settings={},
    )


def _warehouse(name: str) -> Item:
    return Item(id=f"{name}-id", type="Warehouse", display_name=name)


def _audit(enabled: bool, groups: list[str], retention: int) -> dict:
    return {"state": "Enabled" if enabled else "Disabled", "enabled": enabled,
            "action_groups": list(groups), "retention_days": retention}


# -- 7.4.3 ---------------------------------------------------------------------

def test_a_warehouseless_environment_is_excluded_not_failed():
    """The real Leadership Reporting case: reporting workspaces, no Warehouse.

    Telling the owner of a Warehouse-less workspace to "enable Warehouse SQL
    audit" is not a finding -- there is nothing to enable.
    """
    reporting_a = _ws("rep-a", items=[Item(id="r1", type="Report", display_name="R")])
    reporting_b = _ws("rep-b", items=[Item(id="r2", type="Report", display_name="R")])
    verdict = access_audit_consistent(_group((reporting_a, 9), (reporting_b, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "no Warehouse" in verdict.evidence


def test_login_only_audit_does_not_count_as_a_data_access_trail():
    """Enabled + default action groups + zero retention must not read as a pass."""
    envs = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(True, DEFAULT_GROUPS, 0)})
        for name in ("dev", "prod")
    ]
    verdict = access_audit_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "no action group records data access" in verdict.evidence


def test_data_access_group_without_retention_is_not_a_pass():
    envs = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(True, DATA_ACCESS_GROUPS, 0)})
        for name in ("dev", "prod")
    ]
    verdict = access_audit_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "retains it for 0 days" in verdict.evidence


def test_enabled_with_data_access_and_retention_passes():
    envs = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(True, DATA_ACCESS_GROUPS, 90)})
        for name in ("dev", "prod")
    ]
    verdict = access_audit_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3
    assert "audit Warehouse data access with retention" in verdict.evidence


def test_disabled_audit_is_still_a_real_failure():
    envs = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(False, DEFAULT_GROUPS, 0)})
        for name in ("dev", "prod")
    ]
    verdict = access_audit_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "SQL audit is disabled" in verdict.evidence


def test_every_verdict_scopes_itself_away_from_tenant_audit_logging():
    envs = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(True, DATA_ACCESS_GROUPS, 30)})
        for name in ("dev", "prod")
    ]
    verdict = access_audit_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert "tenant audit logging is not covered" in verdict.evidence


def test_a_warehouseless_member_does_not_drag_down_the_others():
    """The reporting workspace is excluded; the two Warehouse envs are judged."""
    good = [
        _ws(name, items=[_warehouse("WH")],
            warehouse_audit={"WH": _audit(True, DATA_ACCESS_GROUPS, 30)})
        for name in ("dev", "prod")
    ]
    reporting = _ws("rep", items=[Item(id="r", type="Report", display_name="R")])
    verdict = access_audit_consistent(
        _group((good[0], 1), (reporting, 5), (good[1], 10)))
    assert verdict.score == 3
    assert "1 environment(s) excluded" in verdict.evidence


# -- 8.1.2 ---------------------------------------------------------------------

_WIRED_PIPELINE = {"properties": {"activities": [
    {"typeProperties": {"notebookId": "abc"}}]}}
_UNWIRED_PIPELINE = {"properties": {"activities": [
    {"typeProperties": {"path": "abfss://data@x.dfs.core.windows.net/raw"}}]}}


def _notebook(source: str, *, lakehouse_id: str | None = None) -> dict:
    definition: dict = {"cells": [{"cell_type": "code", "source": source}]}
    if lakehouse_id:
        definition["metadata"] = {
            "dependencies": {"lakehouse": {"known_lakehouses": [{"id": lakehouse_id}]}}
        }
    return definition


def test_a_reporting_only_workspace_is_no_longer_incomplete():
    """The real MLC Executive case: reports bound to models, no pipelines.

    A reporting workspace holding no pipeline is correct architecture, not
    missing lineage. The old check scored it INCOMPLETE.
    """
    envs = [
        _ws(name, reports=[{"name": "R1", "dataset_id": "m1"},
                           {"name": "R2", "dataset_id": "m2"}])
        for name in ("rep-a", "rep-b")
    ]
    verdict = lineage_e2e_consistent(_group((envs[0], 9), (envs[1], 10)))
    assert verdict.score == 3


def test_a_data_only_workspace_is_no_longer_incomplete():
    """The real MLC_Fabric_PROD case: wired pipelines, no reports."""
    envs = [_ws(name, pipelines={"PL": _WIRED_PIPELINE}) for name in ("dev", "prod")]
    verdict = lineage_e2e_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3


def test_an_unbound_report_is_untraceable():
    env_a = _ws("a", reports=[{"name": "R1", "dataset_id": ""}])
    env_b = _ws("b", reports=[{"name": "R2", "dataset_id": "m"}])
    verdict = lineage_e2e_consistent(_group((env_a, 1), (env_b, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "bound to no semantic model" in verdict.evidence


def test_a_pipeline_naming_no_fabric_item_is_untraceable():
    env_a = _ws("a", pipelines={"PL_Raw": _UNWIRED_PIPELINE})
    env_b = _ws("b", pipelines={"PL_Ok": _WIRED_PIPELINE})
    verdict = lineage_e2e_consistent(_group((env_a, 1), (env_b, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "names no Fabric item" in verdict.evidence


def test_a_notebook_using_only_a_hardcoded_path_is_untraceable():
    code = "df = spark.read.load('abfss://d@x.dfs.core.windows.net/raw')"
    env_a = _ws("a", notebooks={"NB_Raw": _notebook(code)})
    env_b = _ws("b", notebooks={"NB_Ok": _notebook("df = spark.table('sales')")})
    verdict = lineage_e2e_consistent(_group((env_a, 1), (env_b, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "hard-coded path" in verdict.evidence


def test_a_notebook_touching_no_data_is_not_counted_either_way():
    """A helper notebook is not part of any chain, so it neither passes nor fails."""
    envs = [
        _ws(name, notebooks={"NB_Util": _notebook("def add(a, b):\n    return a + b")},
            reports=[{"name": "R", "dataset_id": "m"}])
        for name in ("a", "b")
    ]
    verdict = lineage_e2e_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3
    assert "2 lineage-bearing item(s)" in verdict.evidence


def test_no_lineage_bearing_item_anywhere_is_na():
    envs = [_ws(name) for name in ("a", "b")]
    verdict = lineage_e2e_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False


# -- the attached-store metadata path -------------------------------------------

def test_attached_lakehouses_reads_the_modern_dependencies_block():
    """Regression: reading only ``trident`` saw 0 of 171 notebooks on a real estate."""
    modern = _notebook("x = 1", lakehouse_id="lh-1")
    assert attached_lakehouses(modern) == ["lh-1"]


def test_attached_lakehouses_still_reads_the_legacy_trident_block():
    legacy = {"metadata": {"trident": {"lakehouse": {
        "known_lakehouses": [{"id": "lh-2"}], "default_lakehouse_name": "LH"}}}}
    assert set(attached_lakehouses(legacy)) == {"lh-2", "LH"}


def test_attached_stores_also_sees_a_warehouse_attachment():
    definition = {"metadata": {"dependencies": {
        "warehouse": {"default_warehouse": "wh-1"}}}}
    assert attached_lakehouses(definition) == []
    assert attached_stores(definition) == ["wh-1"]


def test_an_attached_notebook_is_traceable_without_catalog_code():
    """The attachment alone is the lineage edge - no code inspection needed."""
    code = "df = spark.read.load('abfss://d@x.dfs.core.windows.net/raw')"
    envs = [
        _ws(name, notebooks={"NB": _notebook(code, lakehouse_id="lh-1")})
        for name in ("a", "b")
    ]
    verdict = lineage_e2e_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3
