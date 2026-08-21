"""Node 6 (pipeline + HITL + report) tests.

Drives the full node sequence with a no-op router (to force the unique path
deterministically), a fake read-only provider, and a fake generator/reviewer.
"""
from __future__ import annotations

import pytest

from auditfast.ai.agents.kb_updater_agent import FetchResponse
from auditfast.ai.custom_runtime.base_check import clear_custom_registry
from auditfast.ai.orchestrator import pipeline
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    LifecycleStatus,
    make_check_id,
)
from auditfast.core.check.registry import REGISTRY

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


_GEN = _generate
_NOOP_ROUTER = _noop_router


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


# -- pipeline paths ------------------------------------------------------------

def test_guardrail_drop_stops_pipeline():
    session = pipeline.run_batch(["Delete all stale lakehouses"], router=_NOOP_ROUTER)
    assert session.checks[0].lifecycle_status is LifecycleStatus.DROPPED_GUARDRAIL


def test_known_check_routes_to_default():
    title = next(iter(REGISTRY)).title
    session = pipeline.run_batch([title])  # real router, Stage 1 deterministic
    assert session.checks[0].lifecycle_status is LifecycleStatus.ROUTED_DEFAULT


def test_unique_with_present_data_generates():
    session = CustomCheckSession()
    session.shared_kb = {"git_connected": True}
    pipeline.run_batch(
        ["verify git integration is present"],
        session=session, router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    check = session.checks[0]
    assert check.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM
    assert check.code_gen.status == "GENERATED"


def test_unique_missing_data_with_provider_augments_then_generates():
    session = CustomCheckSession()
    provider = FakeProvider(FetchResponse(200, body={"Model A": {"enabled": True}}))
    pipeline.run_batch(
        ["ensure semantic models have incremental refresh"],
        session=session, provider=provider, router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    check = session.checks[0]
    assert check.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert check.code_gen.status == "GENERATED"
    assert "refresh_schedules" in session.shared_kb


def test_unique_missing_data_without_provider_stays_pending():
    session = pipeline.run_batch(
        ["ensure semantic models have incremental refresh"],
        router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    check = session.checks[0]
    assert check.lifecycle_status is LifecycleStatus.PENDING
    assert check.fetch_plan is not None


# -- HITL ----------------------------------------------------------------------

def test_hitl_approve_reject_and_pending_review():
    session = CustomCheckSession()
    a = session.add("check one")
    b = session.add("check two")
    session.add("check three")
    pipeline.approve(a)
    pipeline.reject(b)
    remaining = pipeline.pending_review(session)
    assert a not in remaining and b not in remaining
    assert len(remaining) == 1


# -- report --------------------------------------------------------------------

def _generated_check(prompt: str) -> CustomCheck:
    check = CustomCheck(check_id=make_check_id(prompt), raw_prompt=prompt)
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
    check.generated_code = _GOOD
    check.approved = True
    return check


def test_render_report_has_all_sections():
    session = CustomCheckSession()
    dropped = session.add("Delete everything")
    pipeline.run_check(dropped, session, router=_NOOP_ROUTER)  # -> DROPPED_GUARDRAIL
    session.checks.append(_generated_check("ensure tables are compacted"))

    report = pipeline.render_report(session)
    assert "# Custom Checks Report" in report
    assert "## Summary" in report
    assert "## Ledger" in report
    assert "## Custom Checks" in report      # approved generated check
    assert "## Not evaluated" in report      # the dropped check
    assert "100 / 100" in report             # rendered custom score


def test_render_report_runs_approved_generated_checks():
    session = CustomCheckSession()
    session.checks.append(_generated_check("some check"))
    report = pipeline.render_report(session)
    assert "PASS" in report
