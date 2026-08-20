"""The cross-workspace environment-isolation check (ref 1.1.3).

Unit-tests ``XW-ENV-ISOLATION`` directly: it searches each group member's
pipeline / notebook / shortcut definitions for the workspace GUID of another
member — the cross-environment dependency the per-workspace ``WS-ENV-ISOLATION``
check documents it cannot resolve. Fewer than two inspectable members ⇒ N/A,
never a low score.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_operations.group import (
    environment_isolation_consistent,
)
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext

_ALL = (
    Resource.PIPELINE_DEFINITIONS,
    Resource.NOTEBOOK_DEFINITIONS,
    Resource.SHORTCUTS,
)


def _ws(
    ws_id: str,
    *,
    pipelines: dict | None = None,
    notebooks: dict | None = None,
    shortcuts: dict | None = None,
    inspectable: bool = True,
) -> WorkspaceContext:
    ctx = WorkspaceContext(id=ws_id, display_name=ws_id, layer=Layer.OPERATIONS)
    ctx.pipelines = pipelines or {}
    ctx.notebooks = notebooks or {}
    ctx.shortcuts = shortcuts or {}
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
