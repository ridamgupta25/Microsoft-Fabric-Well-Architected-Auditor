"""The metadata-driven cross-workspace environment-isolation check (ref 1.1.3)."""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_operations.group import (
    environment_isolation_consistent,
)
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext

_ALL = (
    Resource.PIPELINE_DEFINITIONS,
    Resource.NOTEBOOK_DEFINITIONS,
    Resource.SHORTCUTS,
    Resource.REPORTS,
)


def _ws(
    ws_id: str,
    *,
    pipelines: dict | None = None,
    notebooks: dict | None = None,
    shortcuts: dict | None = None,
    reports: list[dict] | None = None,
    items: list[Item] | None = None,
    connections: list[dict] | None = None,
    inspectable: bool = True,
) -> WorkspaceContext:
    ctx = WorkspaceContext(id=ws_id, display_name=ws_id, layer=Layer.OPERATIONS)
    ctx.pipelines = pipelines or {}
    ctx.notebooks = notebooks or {}
    ctx.shortcuts = shortcuts or {}
    ctx.reports = reports or []
    ctx.items = items or []
    ctx.connections = connections or []
    if not inspectable:
        ctx.unavailable.update(_ALL)
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="Sales",
        members=tuple(
            GroupMemberContext(ws, level, Layer.OPERATIONS) for ws, level in members
        ),
        settings={},
    )


def test_isolated_environments_pass():
    dev = _ws("ws-dev", pipelines={"PL_Load": {"activities": [{"name": "copy"}]}})
    prod = _ws("ws-prod", pipelines={"PL_Load": {"activities": [{"name": "copy"}]}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3
    assert verdict.scored is True


def test_cross_environment_pipeline_reference_is_flagged():
    # Prod pipeline reaches into the Dev workspace by GUID.
    prod = _ws("ws-prod", pipelines={
        "PL_Load": {"source": {"workspaceId": "ws-dev"}},
    })
    dev = _ws("ws-dev", pipelines={"PL_Load": {"activities": []}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "ws-prod" in verdict.evidence  # the offender is named
    assert "ws-dev" in verdict.evidence   # the environment it depends on


def test_cross_environment_shortcut_reference_is_flagged():
    prod = _ws("ws-prod", shortcuts={
        "Gold": [{"target": {"oneLake": {"workspaceId": "ws-dev"}}}],
    })
    dev = _ws("ws-dev", shortcuts={"Bronze": []})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "ws-prod" in verdict.evidence


def test_cross_environment_artifact_reference_is_flagged_without_workspace_id():
    dev = _ws("ws-dev", items=[Item(
        id="lakehouse-dev-id", type="Lakehouse", display_name="LH_Sales_Dev")])
    prod = _ws("ws-prod", notebooks={
        "NB_Load": {"defaultLakehouse": {"itemId": "lakehouse-dev-id"}},
    })
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "LH_Sales_Dev" in verdict.evidence


def test_cross_environment_report_binding_is_flagged():
    dev = _ws("ws-dev", items=[Item(
        id="model-dev-id", type="SemanticModel", display_name="SM_Sales_Dev")])
    prod = _ws("ws-prod", reports=[{
        "name": "Sales", "dataset_id": "model-dev-id",
        "dataset_workspace_id": "ws-dev",
    }])
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "SM_Sales_Dev" in verdict.evidence


def test_connection_referenced_by_multiple_environments_is_flagged():
    connection = [{"id": "connection-shared", "display_name": "Sales DB"}]
    dev = _ws("ws-dev", pipelines={"PL": {"connectionId": "connection-shared"}},
              connections=connection)
    prod = _ws("ws-prod", pipelines={"PL": {"connectionId": "connection-shared"}},
               connections=connection)
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 0
    assert "shared connection 'Sales DB'" in verdict.evidence


def test_tenant_connection_catalog_alone_is_not_evidence_of_sharing():
    connection = [{"id": "connection-shared", "display_name": "Sales DB"}]
    dev = _ws("ws-dev", pipelines={"PL": {}}, connections=connection)
    prod = _ws("ws-prod", pipelines={"PL": {}}, connections=connection)
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_same_external_storage_hardcoded_in_two_environments_is_flagged():
    path = "abfss://data@saleslake.dfs.core.windows.net/gold/orders"
    dev = _ws("ws-dev", notebooks={
        "NB": {"cells": [{"cell_type": "code", "source": f"df.write.save('{path}')"}]}})
    prod = _ws("ws-prod", notebooks={
        "NB": {"cells": [{"cell_type": "code", "source": f"df = spark.read.load('{path}')"}]}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 0
    assert "shared external storage" in verdict.evidence
    assert "saleslake.dfs.core.windows.net" in verdict.evidence


def test_external_storage_in_only_one_environment_is_not_shared():
    dev = _ws("ws-dev", notebooks={"NB": {"cells": [{"cell_type": "code",
        "source": "df.write.save('abfss://data@devlake.dfs.core.windows.net/gold')"}]}})
    prod = _ws("ws-prod", notebooks={"NB": {"cells": [{"cell_type": "code",
        "source": "df.write.save('abfss://data@prodlake.dfs.core.windows.net/gold')"}]}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_commented_out_external_storage_is_not_counted():
    path = "abfss://data@saleslake.dfs.core.windows.net/gold/orders"
    dev = _ws("ws-dev", notebooks={"NB": {"cells": [{"cell_type": "code",
        "source": f"# old path: {path}"}]}})
    prod = _ws("ws-prod", notebooks={"NB": {"cells": [{"cell_type": "code",
        "source": f"df = spark.read.load('{path}')"}]}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_case_insensitive_guid_match():
    prod = _ws("WS-PROD", pipelines={"PL": {"ref": "WS-DEV"}})
    dev = _ws("ws-dev", pipelines={"PL": {}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3


def test_self_reference_only_is_isolated():
    # A workspace naming its own id is not a cross-environment dependency.
    dev = _ws("ws-dev", pipelines={"PL": {"self": "ws-dev"}})
    prod = _ws("ws-prod", pipelines={"PL": {"self": "ws-prod"}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_fewer_than_two_inspectable_members_is_na():
    dev = _ws("ws-dev", pipelines={"PL": {}})
    prod = _ws("ws-prod", inspectable=False)
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
