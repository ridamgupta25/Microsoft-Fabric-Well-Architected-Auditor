"""LangGraph wrapper (ai/orchestrator/graph.py) parity + HITL tests.

Skipped unless the ``graph`` extra (langgraph) is installed. Mirrors the key
pipeline paths to prove the graph wires the same nodes to the same terminal
statuses, plus the ``interrupt_before`` HITL pause.
"""
from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from auditfast.ai.agents.kb_updater_agent import FetchResponse
from auditfast.ai.custom_runtime.base_check import clear_custom_registry
from auditfast.ai.orchestrator import graph as graph_mod
from auditfast.ai.orchestrator.state import (
    CustomCheckSession,
    LifecycleStatus,
)

_GOOD = (
    "class Chk(BaseAuditCheck):\n"
    "    check_id = 'chk_gen'\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
)


def _generate(*_args):
    return _GOOD


def _noop_router(check):
    return check  # leaves the check PENDING -> unique path


class FakeProvider:
    def __init__(self, response):
        self._response = response

    def fetch(self, plan, strategy):
        return self._response


@pytest.fixture(autouse=True)
def _clean():
    clear_custom_registry()
    yield
    clear_custom_registry()


# -- parity with the plain pipeline --------------------------------------------

def test_graph_guardrail_drop_stops():
    session = CustomCheckSession()
    check = session.add("Delete all stale lakehouses")
    result = graph_mod.run_check_graph(check, session, router=_noop_router)
    assert result.lifecycle_status is LifecycleStatus.DROPPED_GUARDRAIL


def test_graph_present_data_generates():
    session = CustomCheckSession()
    session.shared_kb = {"git_connected": True}
    check = session.add("verify git integration is present")
    result = graph_mod.run_check_graph(
        check, session, router=_noop_router, generator=_generate, reviewer=None
    )
    assert result.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM
    assert result.code_gen.status == "GENERATED"


def test_graph_missing_data_with_provider_augments_then_generates():
    session = CustomCheckSession()
    provider = FakeProvider(FetchResponse(200, body={"Model A": {"enabled": True}}))
    check = session.add("ensure semantic models have incremental refresh")
    result = graph_mod.run_check_graph(
        check, session, provider=provider, router=_noop_router,
        generator=_generate, reviewer=None,
    )
    assert result.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert result.code_gen.status == "GENERATED"


def test_graph_missing_data_without_provider_stays_pending():
    session = CustomCheckSession()
    check = session.add("ensure semantic models have incremental refresh")
    result = graph_mod.run_check_graph(
        check, session, router=_noop_router, generator=_generate, reviewer=None
    )
    assert result.lifecycle_status is LifecycleStatus.PENDING
    assert result.fetch_plan is not None


# -- HITL interrupt ------------------------------------------------------------

def test_graph_hitl_pauses_before_approval():
    session = CustomCheckSession()
    session.shared_kb = {"git_connected": True}
    check = session.add("verify git integration is present")
    app = graph_mod.compile_with_hitl(
        session, router=_noop_router, generator=_generate, reviewer=None
    )
    config = {"configurable": {"thread_id": "t-1"}}
    app.invoke({"check": check}, config)
    state = app.get_state(config)
    # The run paused *before* the approval node (code-gen already ran).
    assert graph_mod.HITL_INTERRUPT_NODE in state.next
    assert check.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM
    # Resuming runs to completion.
    app.invoke(None, config)
    assert not app.get_state(config).next
