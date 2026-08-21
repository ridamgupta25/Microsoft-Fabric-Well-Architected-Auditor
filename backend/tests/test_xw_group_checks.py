"""Cross-workspace (group) checks ported from the local check set.

Eighteen best-practice points are implemented as ``@group_check``s in the
separate ``GROUP_REGISTRY``, so they run only for a project group (>=2 members)
and never touch a normal single-workspace audit. These tests pin that they are
registered, run over the fixture group without error, and obey N/A-not-FAIL.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.data_management_quality.data_operations.group import (
    layer_separation_consistent,
)
from auditfast.core.check.data_management_quality.data_storage.group import (
    aggregate_consistency,
    cross_layer_reconciliation,
)
from auditfast.core.check.registry import GROUP_REGISTRY, CheckRegistry
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext

from .conftest import FIXTURE_SETTINGS

#: The 18 checks ported from the local set, id -> ref.
PORTED = {
    "XW-MEDALLION-CONSIST": "1.1.5",
    "XW-PIPELINE-SLA": "9.4.2",
    "XW-SLA-ALERTS": "9.4.3",
    "XW-SLA-HISTORY": "9.4.4",
    "XW-TIER-SEP": "11.3.1",
    "XW-MEDALLION-DRIFT": "11.4.3",
    "XW-SPARK-LOGS": "10.1.2",
    "XW-WH-LOAD-MON": "10.1.5",
    "XW-AUDIT-SCHEMA": "10.2.1",
    "XW-AUDIT-QUERYABLE": "10.2.5",
    "XW-CONFORMED-DIM": "4.4.9",
    "XW-AGG-CONSIST": "5.4.3",
    "XW-LAYER-RECON": "5.4.6",
    "XW-ACCESS-AUDIT": "7.4.3",
    "XW-LINEAGE-E2E": "8.1.2",
    "XW-TECH-METADATA": "8.3.2",
    "XW-CU-ALERTS": "12.2.7",
    "XW-SECRET-SCAN": "11.1.8",
}

_THREE_MEMBER_GROUP = [(
    "Proj",
    (
        ("ws-prep-01", Layer.PREP, 1),
        ("ws-store-01", Layer.STORAGE, 5),
        ("ws-ops-01", Layer.OPERATIONS, 10),
    ),
)]


def test_all_eighteen_ported_checks_are_registered():
    specs = {spec.id: spec for spec in GROUP_REGISTRY}
    for check_id, ref in PORTED.items():
        assert check_id in specs, f"{check_id} not registered"
        assert specs[check_id].ref == ref


def _aggregate_group(
    *, measure: dict | None = None, sql: str = "",
    unavailable: set[Resource] | None = None,
) -> GroupContext:
    members = []
    for name, level in (("DEV", 1), ("PROD", 10)):
        workspace = WorkspaceContext(
            id=name,
            display_name=name,
            layer=Layer.STORAGE,
            tables={"fact_sales": {}, "daily_sales_aggregate": {}},
            semantic_models={
                "Sales": {
                    "tables": ["fact_sales", "daily_sales_aggregate"],
                    "measures": [measure],
                },
            } if measure else {},
            sql_routines=[{
                "schema": "audit", "name": "validate_sales_rollup",
                "type": "PROCEDURE", "definition": sql, "store": "SalesWarehouse",
            }] if sql else [],
            unavailable=set(unavailable or ()),
        )
        members.append(GroupMemberContext(workspace, level, Layer.STORAGE))
    return GroupContext(name="Sales", members=tuple(members), settings={})


def test_aggregate_table_names_alone_do_not_prove_reconciliation():
    verdict = aggregate_consistency(_aggregate_group())
    assert verdict.score != 3


def test_semantic_model_detail_to_aggregate_variance_measure_passes():
    measure = {
        "name": "Detail vs Aggregate Variance",
        "expression": "SUM(fact_sales[amount]) - SUM(daily_sales_aggregate[total_amount])",
    }
    verdict = aggregate_consistency(_aggregate_group(measure=measure))
    assert verdict.score == 3


def test_warehouse_enforced_detail_to_aggregate_reconciliation_passes():
    sql = """
DECLARE @detail_total decimal(18,2) = (SELECT SUM(amount) FROM fact_sales);
DECLARE @aggregate_total decimal(18,2) = (SELECT SUM(total_amount) FROM daily_sales_aggregate);
IF @detail_total <> @aggregate_total THROW 51000, 'Rollup mismatch', 1;
"""
    verdict = aggregate_consistency(_aggregate_group(sql=sql))
    assert verdict.score == 3


def test_warehouse_reconciliation_does_not_require_semantic_model_readability():
    sql = """
DECLARE @detail_total decimal(18,2) = (SELECT SUM(amount) FROM fact_sales);
DECLARE @aggregate_total decimal(18,2) = (SELECT SUM(total_amount) FROM daily_sales_aggregate);
IF @detail_total <> @aggregate_total THROW 51000, 'Rollup mismatch', 1;
"""
    verdict = aggregate_consistency(_aggregate_group(
        sql=sql, unavailable={Resource.SEMANTIC_MODEL_DEFINITIONS},
    ))
    assert verdict.score == 3


def test_semantic_reconciliation_does_not_require_warehouse_readability():
    measure = {
        "name": "Detail vs Aggregate Variance",
        "expression": "SUM(fact_sales[amount]) - SUM(daily_sales_aggregate[total_amount])",
    }
    verdict = aggregate_consistency(_aggregate_group(
        measure=measure, unavailable={Resource.TABLE_COLUMNS},
    ))
    assert verdict.score == 3


def _layer_recon_group(
    codes: tuple[str, ...], *, unavailable: set[int] | None = None,
) -> GroupContext:
    unavailable = unavailable or set()
    members = []
    for index, code in enumerate(codes):
        workspace = WorkspaceContext(
            id=f"WS-{index}",
            display_name=f"WS-{index}",
            layer=Layer.PREP,
            notebooks={f"promote-{index}": {
                "cells": [{"cell_type": "code", "source": code}],
            }},
            unavailable={Resource.NOTEBOOK_DEFINITIONS} if index in unavailable else set(),
        )
        members.append(GroupMemberContext(workspace, index + 1, Layer.PREP))
    return GroupContext(name="Sales", members=tuple(members), settings={})


_RECONCILED_FLOW = """
silver = spark.read.table("silver.fact_sales")
gold = silver.groupBy("sale_date").agg({"amount": "sum"})
source_count = silver.count()
target_count = gold.agg({"source_rows": "sum"}).first()[0]
assert source_count == target_count
gold.write.mode("overwrite").saveAsTable("gold.daily_sales")
"""

_UNCONTROLLED_FLOW = """
silver = spark.read.table("silver.fact_sales")
gold = silver.groupBy("sale_date").agg({"amount": "sum"})
gold.write.mode("overwrite").saveAsTable("gold.daily_sales")
"""


def test_cross_layer_reconciliation_passes_without_reading_table_data():
    verdict = cross_layer_reconciliation(
        _layer_recon_group((_RECONCILED_FLOW, _RECONCILED_FLOW))
    )
    assert verdict.score == 3
    assert "Gold record counts reconcile with Silver" in verdict.evidence


def test_cross_layer_reconciliation_fails_when_one_flow_has_no_control():
    verdict = cross_layer_reconciliation(
        _layer_recon_group((_RECONCILED_FLOW, _UNCONTROLLED_FLOW))
    )
    assert verdict.score != 3
    assert "promote-1" in verdict.evidence


def test_cross_layer_reconciliation_ignores_layer_names_in_comments():
    code = """# Read Silver and write Gold\ndf.write.saveAsTable("curated.sales")"""
    verdict = cross_layer_reconciliation(_layer_recon_group((code, code)))
    assert verdict.status is Status.NA


def test_cross_layer_reconciliation_is_na_with_one_readable_workspace():
    verdict = cross_layer_reconciliation(
        _layer_recon_group((_RECONCILED_FLOW, _RECONCILED_FLOW), unavailable={1})
    )
    assert verdict.status is Status.NA


def test_cross_layer_reconciliation_ignores_the_bare_word_reconcile():
    """A variable merely named ``reconcile_notes`` is not a reconciliation control."""
    code = (
        'reconcile_notes = "todo"\n'
        'silver = spark.read.table("silver.fact_sales")\n'
        'gold = silver.groupBy("sale_date").agg({"amount": "sum"})\n'
        'gold.write.mode("overwrite").saveAsTable("gold.daily_sales")\n'
    )
    verdict = cross_layer_reconciliation(_layer_recon_group((code, code)))
    assert verdict.score != 3
    assert "promote-0" in verdict.evidence


@pytest.mark.parametrize("check_id,ref", sorted(PORTED.items()))
def test_ported_ref_has_remediation_text(check_id, ref):
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    assert load_remediation(load_project(PROJECT_FILE)).get(ref), (
        f"{check_id} (ref {ref}) has no remediation text"
    )


def test_group_checks_run_over_the_fixture_group_without_error(provider):
    """Every group check produces exactly one scored-or-N/A result, no exception."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=_THREE_MEMBER_GROUP,
        group_registry=GROUP_REGISTRY,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    # One result per registered group check (17 ported + XW-SCHEMA-DRIFT +
    # XW-LAYER-SEP + XW-ENV-ISOLATION + XW-LINEAGE-CROSSDOMAIN).
    assert len(group_results) == len(GROUP_REGISTRY)
    valid = {Status.PASS, Status.PARTIAL, Status.FAIL, Status.NA}
    for result in group_results:
        assert result.status in valid, f"{result.check_id}: {result.status}"
        assert result.workspace == "Proj"
        assert result.evidence


def test_ported_checks_are_na_with_a_single_readable_member(provider):
    """Fewer than two readable members => N/A for every group check (never FAIL)."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=[("Solo", (("ws-prep-01", Layer.PREP, 1), ("missing-ws", Layer.MIXED, 10)))],
        group_registry=GROUP_REGISTRY,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    ported_ids = set(PORTED)
    for result in group_results:
        if result.check_id in ported_ids:
            assert result.status is Status.NA
            assert result.scored is False


# -- XW-LAYER-SEP: the cross-workspace angle of ref 1.1.1 ----------------------

def test_layer_separation_group_check_is_registered_with_remediation():
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    spec = GROUP_REGISTRY.get("XW-LAYER-SEP")
    assert spec is not None, "XW-LAYER-SEP not registered"
    assert spec.ref == "1.1.1"
    assert load_remediation(load_project(PROJECT_FILE)).get("1.1.1"), (
        "XW-LAYER-SEP (ref 1.1.1) has no remediation text"
    )


def test_layer_separation_group_check_scores_the_group(provider):
    """Over a readable multi-environment group it scores (never errors or N/As)."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=_THREE_MEMBER_GROUP,
        group_registry=GROUP_REGISTRY,
    )
    row = next(
        r for r in results
        if r.scope is Scope.GROUP and r.check_id == "XW-LAYER-SEP"
    )
    assert row.status in {Status.PASS, Status.PARTIAL, Status.FAIL}
    assert row.scored is True
    assert row.evidence


def test_layer_separation_group_check_is_na_with_a_single_readable_member(provider):
    """Fewer than two readable members ⇒ N/A, never FAIL."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=[("Solo", (("ws-prep-01", Layer.PREP, 1), ("missing-ws", Layer.MIXED, 10)))],
        group_registry=GROUP_REGISTRY,
    )
    row = next(
        r for r in results
        if r.scope is Scope.GROUP and r.check_id == "XW-LAYER-SEP"
    )
    assert row.status is Status.NA
    assert row.scored is False


def _layer_group(*workspaces: WorkspaceContext) -> GroupContext:
    members = tuple(
        GroupMemberContext(workspace, index + 1, workspace.layer)
        for index, workspace in enumerate(workspaces)
    )
    return GroupContext(name="Sales", members=members, settings={})


def _workspace(name: str, layer: Layer, *item_types: str) -> WorkspaceContext:
    return WorkspaceContext(
        id=name,
        display_name=name,
        layer=layer,
        items=[
            Item(id=f"{name}-{index}", type=item_type, display_name=item_type)
            for index, item_type in enumerate(item_types)
        ],
    )


def test_layer_separation_group_requires_expected_content():
    verdict = layer_separation_consistent(_layer_group(
        _workspace("Prep-Dev", Layer.PREP, "Notebook"),
        _workspace("Prep-Prod", Layer.PREP),
    ))
    assert verdict.coverage == 0.5
    assert verdict.score == 1


def test_layer_separation_group_rejects_foreign_content():
    verdict = layer_separation_consistent(_layer_group(
        _workspace("Prep-Dev", Layer.PREP, "Notebook"),
        _workspace("Prep-Prod", Layer.PREP, "Notebook", "Lakehouse"),
    ))
    assert verdict.coverage == 0.5
    assert verdict.score == 1


def test_layer_separation_group_infers_an_untagged_workspace_layer():
    verdict = layer_separation_consistent(_layer_group(
        _workspace("Sales_DataPrep_Dev", Layer.MIXED, "Notebook"),
        _workspace("Sales_DataPrep_Prod", Layer.MIXED, "DataPipeline"),
    ))
    assert verdict.coverage == 1.0
    assert verdict.score == 3


def test_layer_separation_group_excludes_an_unresolved_mixed_workspace():
    verdict = layer_separation_consistent(_layer_group(
        _workspace("Sales-Dev", Layer.MIXED, "Notebook", "Lakehouse"),
        _workspace("Sales-Prod", Layer.PREP, "Notebook"),
    ))
    assert verdict.status is Status.NA
    assert verdict.scored is False


# -- XW-ENV-ISOLATION: the cross-workspace angle of ref 1.1.3 ------------------

def test_env_isolation_group_check_is_registered_with_remediation():
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    spec = GROUP_REGISTRY.get("XW-ENV-ISOLATION")
    assert spec is not None, "XW-ENV-ISOLATION not registered"
    assert spec.ref == "1.1.3"
    assert spec.title == (
        "Environment isolation enforced (Dev / QA / Prod workspaces have no "
        "shared mutable artifacts or cross-env dependencies)"
    )
    assert spec.pillar is Pillar.ARCHITECTURE
    assert spec.severity is Severity.MEDIUM
    assert load_remediation(load_project(PROJECT_FILE)).get("1.1.3"), (
        "XW-ENV-ISOLATION (ref 1.1.3) has no remediation text"
    )


# -- XW-LINEAGE-CROSSDOMAIN: the cross-workspace angle of ref 8.1.5 ------------

def test_lineage_crossdomain_group_check_is_registered_with_remediation():
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    spec = GROUP_REGISTRY.get("XW-LINEAGE-CROSSDOMAIN")
    assert spec is not None, "XW-LINEAGE-CROSSDOMAIN not registered"
    assert spec.ref == "8.1.5"
    assert load_remediation(load_project(PROJECT_FILE)).get("8.1.5"), (
        "XW-LINEAGE-CROSSDOMAIN (ref 8.1.5) has no remediation text"
    )
