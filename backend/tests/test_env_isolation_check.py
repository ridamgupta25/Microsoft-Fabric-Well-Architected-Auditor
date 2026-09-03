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
    partial_notebooks: tuple[int, int] | None = None,
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
    if partial_notebooks:
        # A *partial* getDefinition read: some definitions landed, the rest were
        # blocked. The resource stays "available" (read > 0), which is exactly the
        # case that used to look identical to a complete crawl.
        failed, attempted = partial_notebooks
        ctx.read_failures[Resource.NOTEBOOK_DEFINITIONS.value] = {
            "attempted": attempted,
            "read": attempted - failed,
            "failed": failed,
            "forbidden": failed,
            "transient": 0,
            "empty": 0,
        }
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


# -- incomplete notebook capture (ref 1.1.3, UAT case) -------------------------

def test_partial_capture_with_no_finding_is_not_credited_as_isolated():
    """A search over 5 of 50 notebooks that finds nothing proves nothing.

    Regression: ``has(NOTEBOOK_DEFINITIONS)`` stays True for a *partial* read, so
    this environment used to be indistinguishable from a fully-crawled one and
    was scored as cleanly isolated -- a false pass.
    """
    dev = _ws("ws-dev", pipelines={"PL": {}})
    uat = _ws("ws-uat", notebooks={"NB": {"cells": []}},
              partial_notebooks=(45, 50))
    prod = _ws("ws-prod", pipelines={"PL": {}})
    verdict = environment_isolation_consistent(
        _group((dev, 1), (uat, 5), (prod, 10)))
    # Only Dev and Prod are judged; UAT is excluded, not counted as isolated.
    assert "2 judged environment(s)" in verdict.evidence
    assert "indeterminate" in verdict.evidence
    assert "ws-uat" in verdict.evidence
    assert "45 of 50 notebookDefinitions" in verdict.evidence


def test_partial_capture_with_a_finding_is_still_flagged():
    """Evidence found in a partial crawl is real -- the environment still fails.

    It must also disclose that the reference list may be short: the reviewer is
    told to remove the references, so "we only searched 5 of 50 notebooks" is
    material. This is the MDM/UAT case that was reported as fully conclusive.
    """
    dev = _ws("ws-dev", items=[Item(
        id="lakehouse-dev-id", type="Lakehouse", display_name="LH_Sales_Dev")])
    uat = _ws("ws-uat", notebooks={
        "NB_Load": {"defaultLakehouse": {"itemId": "lakehouse-dev-id"}}},
        partial_notebooks=(45, 50))
    verdict = environment_isolation_consistent(_group((dev, 1), (uat, 5)))
    assert verdict.score is not None and verdict.score < 3
    assert "ws-uat" in verdict.evidence
    assert "LH_Sales_Dev" in verdict.evidence
    # Flagged on found evidence, so it is judged -- not excluded as indeterminate.
    assert "indeterminate" not in verdict.evidence
    # ...but the incomplete capture is disclosed rather than passed off as whole.
    assert "may hold further" in verdict.evidence
    assert "45 of 50 notebookDefinitions" in verdict.evidence


def test_fully_captured_offender_claims_no_missing_references():
    """The disclosure must not fire when the crawl really did read everything."""
    dev = _ws("ws-dev", items=[Item(
        id="lakehouse-dev-id", type="Lakehouse", display_name="LH_Sales_Dev")])
    prod = _ws("ws-prod", notebooks={
        "NB_Load": {"defaultLakehouse": {"itemId": "lakehouse-dev-id"}}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "may hold further" not in verdict.evidence


def test_group_is_na_when_too_few_environments_remain_judgeable():
    """Excluding the indeterminate members can leave nothing to compare."""
    dev = _ws("ws-dev", notebooks={"NB": {"cells": []}},
              partial_notebooks=(45, 50))
    uat = _ws("ws-uat", notebooks={"NB": {"cells": []}},
              partial_notebooks=(30, 40))
    verdict = environment_isolation_consistent(_group((dev, 1), (uat, 5)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "indeterminate" in verdict.evidence


def test_complete_capture_with_no_finding_still_passes():
    """The exclusion must not fire on a clean, fully-captured crawl."""
    dev = _ws("ws-dev", notebooks={"NB": {"cells": []}})
    prod = _ws("ws-prod", notebooks={"NB": {"cells": []}})
    verdict = environment_isolation_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3
    assert "indeterminate" not in verdict.evidence


def test_near_complete_capture_is_judged_not_set_aside():
    """98 of 99 notebooks read is a sound basis for a verdict, not a blind spot.

    The real MDM/UAT crawl lost a single notebook to a throttle. Treating that
    the same as a 5-of-50 read would discard an entire environment's evidence
    over one HTTP 429.
    """
    dev = _ws("ws-dev", pipelines={"PL": {}})
    uat = _ws("ws-uat", notebooks={"NB": {"cells": []}},
              partial_notebooks=(1, 99))
    verdict = environment_isolation_consistent(_group((dev, 1), (uat, 5)))
    # Both environments judged, so this is a real pass -- not an exclusion.
    assert verdict.score == 3
    assert "2 judged environment(s)" in verdict.evidence
    assert "indeterminate" not in verdict.evidence
    # The remainder is still disclosed rather than quietly ignored.
    assert "near-complete crawl" in verdict.evidence
    assert "1 of 99 notebookDefinitions" in verdict.evidence


def test_capture_below_the_materiality_bar_is_still_set_aside():
    """Just under the threshold must behave like the badly-read case."""
    dev = _ws("ws-dev", pipelines={"PL": {}})
    prod = _ws("ws-prod", pipelines={"PL": {}})
    uat = _ws("ws-uat", notebooks={"NB": {"cells": []}},
              partial_notebooks=(15, 100))  # 85% read, under the 90% bar
    verdict = environment_isolation_consistent(
        _group((dev, 1), (uat, 5), (prod, 10)))
    assert "indeterminate" in verdict.evidence
    assert "2 judged environment(s)" in verdict.evidence


def test_materiality_bar_is_inclusive_at_exactly_ninety_percent():
    """A member read to exactly the bar is judged, not excluded."""
    dev = _ws("ws-dev", pipelines={"PL": {}})
    uat = _ws("ws-uat", notebooks={"NB": {"cells": []}},
              partial_notebooks=(10, 100))  # exactly 90% read
    verdict = environment_isolation_consistent(_group((dev, 1), (uat, 5)))
    assert verdict.score == 3
    assert "indeterminate" not in verdict.evidence
    assert "near-complete crawl" in verdict.evidence


def test_wholly_unreadable_member_is_named_not_silently_dropped():
    """'2 of 3' must never masquerade as '2 of 2' (cf. XW-SECRET-SCAN 11.1.8)."""
    dev = _ws("ws-dev", pipelines={"PL": {}})
    prod = _ws("ws-prod", pipelines={"PL": {}})
    uat = _ws("ws-uat", inspectable=False)
    verdict = environment_isolation_consistent(
        _group((dev, 1), (uat, 5), (prod, 10)))
    assert verdict.score == 3
    assert "ws-uat" in verdict.evidence
    assert "no readable pipeline" in verdict.evidence
