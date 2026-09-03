"""Cross-workspace (group) checks ported from the local check set.

Sixteen best-practice points are implemented as ``@group_check``s in the
separate ``GROUP_REGISTRY``, so they run only for a project group (>=2 members)
and never touch a normal single-workspace audit. These tests pin that they are
registered, run over the fixture group without error, and obey N/A-not-FAIL.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.data_management_quality.data_operations.group import (
    layer_separation_consistent,
)
from auditfast.core.check.governance_compliance.data_operations.group import (
    tech_metadata_consistent,
)
from auditfast.core.check.data_management_quality.data_storage.group import (
    _aggregate_derivations,
    aggregate_consistency,
    cross_layer_reconciliation,
)
from auditfast.core.check.registry import GROUP_REGISTRY, REGISTRY, CheckRegistry
from auditfast.core.check.security.data_operations.group import (
    secret_scanning_consistent,
)
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext

from .conftest import FIXTURE_SETTINGS

#: The checks ported from the local set, id -> ref. 10.1.2 was ported here too but
#: has been withdrawn: it could never reach a verdict from crawlable data, and the
#: ref is covered by the ``OPS-SPARK-LOGS`` questionnaire check instead.
PORTED = {
    "XW-MEDALLION-CONSIST": "1.1.5",
    "XW-PIPELINE-SLA": "9.4.2",
    "XW-SLA-ALERTS": "9.4.3",
    "XW-SLA-HISTORY": "9.4.4",
    "XW-TIER-SEP": "11.3.1",
    "XW-MEDALLION-DRIFT": "11.4.3a",
    "XW-WH-LOAD-MON": "10.1.5",
    "XW-AUDIT-SCHEMA": "10.2.1",
    "XW-AUDIT-QUERYABLE": "10.2.5",
    "XW-AGG-CONSIST": "5.4.3",
    "XW-LAYER-RECON": "5.4.6",
    "XW-ACCESS-AUDIT": "7.4.3",
    "XW-LINEAGE-E2E": "8.1.2",
    "XW-TECH-METADATA": "8.3.2",
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


def test_all_ported_checks_are_registered():
    specs = {spec.id: spec for spec in GROUP_REGISTRY}
    for check_id, ref in PORTED.items():
        assert check_id in specs, f"{check_id} not registered"
        assert specs[check_id].ref == ref


def test_spark_logs_is_a_questionnaire_check_not_a_group_check():
    """10.1.2 is answered by the reviewer, not by a crawl.

    Fabric exposes no read-only surface for Spark log retention, so a group check
    could only return a hardcoded N/A on every group -- indistinguishable, to a
    reader, from a failed crawl. The ref must be covered exactly once, by the
    questionnaire.
    """
    assert not [spec for spec in GROUP_REGISTRY if spec.ref == "10.1.2"]
    standard = [spec for spec in REGISTRY if spec.ref == "10.1.2"]
    assert [spec.id for spec in standard] == ["OPS-SPARK-LOGS"]
    assert standard[0].manual is True


#: A stored procedure that declares the rollup: it groups the detail table and
#: *writes* the result into the aggregate. Under the derivation model this is what
#: makes 5.4.3 applicable at all -- a table merely *named* like a summary no longer
#: counts, and neither does a plain view, which Fabric recomputes on every read.
_ROLLUP_ROUTINE = {
    "schema": "dbo", "name": "load_daily_sales_aggregate",
    "type": "PROCEDURE", "store": "SalesWarehouse",
    "definition": (
        "INSERT INTO daily_sales_aggregate (sale_day, total_amount) "
        "SELECT sale_day, SUM(amount) FROM fact_sales GROUP BY sale_day"
    ),
}


def _aggregate_group(
    *, measure: dict | None = None, sql: str = "",
    aggregations: list[dict] | None = None,
    rollup: bool = True,
    views: list[dict] | None = None,
    notebooks: dict | None = None,
    members: tuple[tuple[str, int], ...] = (("DEV", 1), ("PROD", 10)),
    unavailable: set[Resource] | None = None,
) -> GroupContext:
    group_members = []
    for name, level in members:
        workspace = WorkspaceContext(
            id=name,
            display_name=name,
            layer=Layer.STORAGE,
            tables={"fact_sales": {}, "daily_sales_aggregate": {}},
            semantic_models={
                "Sales": {
                    "tables": ["fact_sales", "daily_sales_aggregate"],
                    "measures": [measure] if measure else [],
                    "aggregations": list(aggregations or []),
                },
            } if (measure or aggregations) else {},
            notebooks=dict(notebooks or {}),
            sql_views=[dict(view) for view in (views or ())],
            sql_routines=(
                ([dict(_ROLLUP_ROUTINE)] if rollup else [])
                + ([{
                    "schema": "audit", "name": "validate_sales_rollup",
                    "type": "PROCEDURE", "definition": sql, "store": "SalesWarehouse",
                }] if sql else [])
            ),
            unavailable=set(unavailable or ()),
        )
        group_members.append(GroupMemberContext(workspace, level, Layer.STORAGE))
    return GroupContext(name="Sales", members=tuple(group_members), settings={})


def test_a_declared_rollup_without_reconciliation_fails():
    """The rollup is real, nothing verifies it - that is the finding."""
    verdict = aggregate_consistency(_aggregate_group())
    assert verdict.score == 0
    assert "none reconciled" in verdict.evidence


def test_no_declared_rollup_is_na_not_a_failure():
    """Names that look like a summary are no longer enough to make this apply.

    Regression: the old gate opened on any table named ``*aggregate*``, so an
    imported source table could make an environment fail a rollup control it
    never had. It also missed real rollups named ``balances`` or ``consolidated``.
    """
    verdict = aggregate_consistency(_aggregate_group(rollup=False))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "builds no materialised aggregate rollup" in verdict.evidence


def test_a_group_by_view_is_not_a_materialised_rollup():
    """A Fabric Warehouse view is recomputed on every read, so it cannot drift.

    Regression: counting one as a rollup produced seven "unreconciled" findings on
    a real tenant, every one a plain view that recalculates from the detail each
    time it is queried and so has no way to lose a row.
    """
    view = {
        "schema": "dbo", "name": "vw_daily_sales", "store": "SalesWarehouse",
        "definition": (
            "SELECT sale_day, SUM(amount) AS total_amount "
            "FROM fact_sales GROUP BY sale_day"
        ),
    }
    verdict = aggregate_consistency(_aggregate_group(rollup=False, views=[view]))
    assert verdict.status is Status.NA
    assert "builds no materialised aggregate rollup" in verdict.evidence


def test_a_python_import_is_not_read_as_a_detail_table():
    """``from pyspark.sql import functions`` is an import, not a ``FROM`` clause.

    Regression: it was captured as a detail table named ``sql``, inventing a
    rollup of ``sql`` into the target in every PySpark notebook that grouped.
    """
    code = (
        "from pyspark.sql import functions as F\n"
        "df = spark.table('fact_sales')\n"
        "df.groupBy('sale_day').agg(F.sum('amount'))"
        ".write.saveAsTable('daily_sales_aggregate')\n"
    )
    group = _aggregate_group(
        rollup=False,
        notebooks={"nb_rollup": {"cells": [{"cell_type": "code", "source": code}]}},
    )
    derivations = _aggregate_derivations(group.members[0].workspace)
    assert derivations, "the real groupBy -> saveAsTable rollup should still be found"
    assert all(detail != "sql" for _, detail, _ in derivations)
    assert any(detail == "fact_sales" for _, detail, _ in derivations)


def test_a_lone_environment_with_an_unreconciled_rollup_is_still_scored():
    """One environment is enough to report a real gap.

    Regression: a two-environment floor discarded genuine findings -- on a real
    tenant only one workspace per group built a rollup at all, so every group
    returned N/A while unreconciled rollups sat unreported in the estate.
    """
    verdict = aggregate_consistency(_aggregate_group(members=(("PROD", 10),)))
    assert verdict.scored is True
    assert verdict.score == 0
    assert "the only environment" in verdict.evidence


def test_a_lone_environment_that_reconciles_its_rollup_passes():
    sql = """
DECLARE @detail_total decimal(18,2) = (SELECT SUM(amount) FROM fact_sales);
DECLARE @aggregate_total decimal(18,2) = (SELECT SUM(total_amount) FROM daily_sales_aggregate);
IF @detail_total <> @aggregate_total THROW 51000, 'Rollup mismatch', 1;
"""
    verdict = aggregate_consistency(
        _aggregate_group(sql=sql, members=(("PROD", 10),))
    )
    assert verdict.score == 3


def test_a_rollup_is_not_failed_when_the_sql_endpoint_could_not_be_read():
    """Views and stored procedures are where a SQL reconciliation lives.

    The real Sales case: a notebook rollup was found, no reconciliation was
    visible, and the check scored 0 -- but ``tableColumns`` was unreadable in
    every workspace, so the SQL endpoint had never been crawled and a
    reconciling stored procedure could not have been seen. Unreadable is not
    absent.
    """
    code = (
        "df = spark.table('fact_sales')\n"
        "df.groupBy('sale_day').agg({'amount': 'sum'})"
        ".write.saveAsTable('daily_sales_aggregate')\n"
    )
    verdict = aggregate_consistency(_aggregate_group(
        rollup=False,
        notebooks={"nb_rollup": {"cells": [{"cell_type": "code", "source": code}]}},
        unavailable={Resource.TABLE_COLUMNS},
    ))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "could not be judged" in verdict.evidence
    assert "SQL endpoint could not be read" in verdict.evidence


# -- 11.1.8 secret scanning ----------------------------------------------------


def _git_group(*members: tuple[str, bool, bool]) -> GroupContext:
    """``(name, git_readable, git_connected)`` per environment."""
    built = []
    for index, (name, readable, connected) in enumerate(members):
        built.append(GroupMemberContext(
            WorkspaceContext(
                id=name, display_name=name, layer=Layer.OPERATIONS,
                git_connected=connected,
                unavailable=set() if readable else {Resource.GIT},
            ),
            (index + 1) * 5,
            Layer.OPERATIONS,
        ))
    return GroupContext(name="Proj", members=tuple(built), settings={})


def test_secret_scanning_is_na_when_no_git_connection_could_be_read():
    """The real MDM case: git unreadable in all 3, yet it scored 1 of 3.

    A blocked read is a permission gap, not a security finding. Scoring it turns
    "we could not determine this" into a Security deduction.
    """
    verdict = secret_scanning_consistent(
        _git_group(("dev", False, False), ("uat", False, False), ("prod", False, False))
    )
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "permission gap" in verdict.evidence


def test_secret_scanning_still_scores_when_one_environment_is_readable():
    """Partial readability is a coverage question, so it stays scored."""
    verdict = secret_scanning_consistent(
        _git_group(("dev", True, False), ("prod", False, False))
    )
    assert verdict.scored is True
    assert verdict.score is not None


# -- 8.3.2 technical metadata --------------------------------------------------


def _metadata_group(*models: dict) -> GroupContext:
    built = []
    for index, model in enumerate(models):
        built.append(GroupMemberContext(
            WorkspaceContext(
                id=f"ws{index}", display_name=f"ws{index}", layer=Layer.STORAGE,
                tables={"sales": {"columns": [{"name": "id", "type": "int"}]}},
                semantic_models={"Model": model},
            ),
            (index + 1) * 5,
            Layer.STORAGE,
        ))
    return GroupContext(name="Proj", members=tuple(built), settings={})


def test_a_bare_semantic_model_is_not_captured_technical_metadata():
    """Every reporting workspace has a model; crediting one passed every estate.

    Regression: ``bool(ws.semantic_models)`` made this check score 3 in all five
    lines of business, including a workspace holding five items and one model.
    """
    plain = {"tables": ["sales"], "measures": [{"name": "Total", "description": ""}]}
    verdict = tech_metadata_consistent(_metadata_group(plain, plain))
    assert verdict.score == 0
    assert "missing in" in verdict.evidence


def test_a_documented_measure_is_captured_technical_metadata():
    documented = {
        "tables": ["sales"],
        "measures": [{"name": "Total", "description": "Net sales excluding tax"}],
    }
    verdict = tech_metadata_consistent(_metadata_group(documented, documented))
    assert verdict.score == 3


def test_a_declared_data_category_is_captured_technical_metadata():
    categorised = {
        "tables": ["dim_date"], "measures": [],
        "data_categories": {"dim_date": "Time"},
    }
    verdict = tech_metadata_consistent(_metadata_group(categorised, categorised))
    assert verdict.score == 3


def test_a_semantic_model_alternate_of_declares_the_rollup():
    """TMSL states the base table outright - the strongest signal available."""
    verdict = aggregate_consistency(_aggregate_group(
        rollup=False,
        aggregations=[{
            "table": "daily_sales_aggregate", "column": "total_amount",
            "summarization": "sum",
            "base_table": "fact_sales", "base_column": "amount",
        }],
        measure={
            "name": "Rollup variance",
            "expression": "SUM(fact_sales[amount]) - SUM(daily_sales_aggregate[total_amount])",
        },
    ))
    assert verdict.score == 3
    assert "declares" in verdict.evidence


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
    """The model can declare the rollup *and* verify it, with no SQL read at all."""
    measure = {
        "name": "Detail vs Aggregate Variance",
        "expression": "SUM(fact_sales[amount]) - SUM(daily_sales_aggregate[total_amount])",
    }
    verdict = aggregate_consistency(_aggregate_group(
        measure=measure, rollup=False,
        aggregations=[{
            "table": "daily_sales_aggregate", "column": "total_amount",
            "summarization": "sum",
            "base_table": "fact_sales", "base_column": "amount",
        }],
        unavailable={Resource.TABLE_COLUMNS},
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


# -- Analysis enhancements folded onto dev's versions: name the specific item ---

def test_secret_scan_names_the_connected_repository():
    """11.1.8: a connected environment names its repository in the evidence."""
    from auditfast.core.check.security.data_operations.group import (
        secret_scanning_consistent,
    )

    def _git_ws(name, *, connected, provider="", org="", repo="", branch="", scanning=None):
        details = {"connected": connected, "provider": provider,
                   "organization": org, "repository": repo, "branch": branch}
        if scanning is not None:
            details["secret_scanning"] = {"enabled": scanning}
        return WorkspaceContext(id=name, display_name=name, layer=Layer.MIXED,
                                git_connected=connected, git_details=details)

    verdict = secret_scanning_consistent(_layer_group(
        _git_ws("DEV", connected=True, provider="GitHub", org="contoso",
                repo="fabric", branch="main", scanning=True),
        _git_ws("PROD", connected=False),
    ))
    assert "GitHub repo `contoso/fabric` (branch `main`)" in verdict.evidence
    assert "not connected to source control" in verdict.evidence
    assert "**Prod**" in verdict.evidence


def test_lineage_e2e_names_the_untraceable_item():
    """8.1.2: an item Fabric cannot trace is named, with its environment.

    Rewritten when the check moved from "do a source, a store and a reporting
    item coexist here?" to "can Fabric actually draw the lineage edge?". Item
    *types* in the inventory say nothing about wiring, so the fixture now carries
    real definitions: one pipeline naming a Fabric item, one reaching a raw
    storage path that no lineage edge can hang off.
    """
    from auditfast.core.check.governance_compliance.data_operations.group import (
        lineage_e2e_consistent,
    )

    dev = _workspace("DEV", Layer.MIXED)
    dev.pipelines = {"PL_Load": {"properties": {"activities": [
        {"typeProperties": {"notebookId": "nb-1"}}]}}}
    prod = _workspace("PROD", Layer.MIXED)
    prod.pipelines = {"PL_Raw": {"properties": {"activities": [
        {"typeProperties": {"path": "abfss://d@x.dfs.core.windows.net/raw"}}]}}}

    verdict = lineage_e2e_consistent(_layer_group(dev, prod))
    assert verdict.score != 3
    assert "PROD (L2)" in verdict.evidence
    assert "pipeline 'PL_Raw' names no Fabric item" in verdict.evidence


def test_pipeline_sla_names_pipelines_without_run_history():
    """9.4.2: a gap environment names the pipelines lacking run history."""
    from auditfast.core.check.operations_reliability.data_operations.group import (
        pipeline_sla_monitored,
    )

    def _pl_ws(name, jobs):
        items = [Item(id=f"{name}-{j}", type="DataPipeline", display_name=j,
                      last_run_utc="2026-01-01T00:00:00Z" if ran else None)
                 for j, ran in jobs.items()]
        return WorkspaceContext(
            id=name, display_name=name, layer=Layer.MIXED, items=items,
            run_history={f"{name}-{j}": ["2026-01-01T00:00:00Z"]
                         for j, ran in jobs.items() if ran},
        )

    verdict = pipeline_sla_monitored(_layer_group(
        _pl_ws("DEV", {"LoadSales": True, "LoadFinance": True}),
        _pl_ws("PROD", {"LoadFinance": False, "LoadHR": False}),
    ))
    assert "no run history: LoadFinance, LoadHR" in verdict.evidence


def test_wh_load_names_the_rowcount_audit_tables():
    """10.1.5: the row-count dimension names the audit table(s) it found."""
    from auditfast.core.check.operations_reliability.data_logs.group import (
        warehouse_load_monitored,
    )

    def _wh_pipeline():
        return {"properties": {"activities": [
            {"name": "Load", "type": "Copy",
             "typeProperties": {"sink": {"type": "DataWarehouseSink"}},
             "warehouse": {"type": "DataWarehouse"}}]}}

    def _wh_ws(name, tables, jobs=None):
        items = [Item(id=f"{name}-wh", type="Warehouse", display_name="WH")]
        pipelines = {}
        for job, ran in (jobs or {}).items():
            items.append(Item(id=f"{name}-{job}", type="DataPipeline", display_name=job,
                              last_run_utc="2026-01-01T00:00:00Z" if ran else None))
            pipelines[job] = _wh_pipeline()
        return WorkspaceContext(
            id=name, display_name=name, layer=Layer.MIXED, items=items,
            pipelines=pipelines,
            tables={t: {"columns": [{"name": "source_count"}, {"name": "target_count"}]}
                    for t in tables},
        )

    verdict = warehouse_load_monitored(_layer_group(
        _wh_ws("DEV", ["audit_detail", "delta_load_audit"],
               {"LoadSales": True, "LoadFinance": False}),
        _wh_ws("PROD", ["audit_detail"], {"LoadHR": False}),
    ))
    assert "audit_detail" in verdict.evidence
    assert "delta_load_audit" in verdict.evidence
    # Every load job is named, split by run-history presence.
    assert "no history for 'LoadFinance'" in verdict.evidence
    assert "only 'LoadSales'" in verdict.evidence
    assert "'LoadHR' has no run history" in verdict.evidence
