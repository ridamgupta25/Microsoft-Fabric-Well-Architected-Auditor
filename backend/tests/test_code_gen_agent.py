"""Node 4 (Code Generator) tests.

AI is off, so the real generator/reviewer are exercised only for their AI-off
behaviour; the generate/validate/review loop is driven with injected fakes.
"""
from __future__ import annotations

import pytest

from auditfast.ai.agents.code_gen_agent import ReviewVerdict, generate
from auditfast.ai.custom_runtime.base_check import clear_custom_registry
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    FeasibilityClass,
    LifecycleStatus,
    make_check_id,
)

_GOOD = (
    "class Chk(BaseAuditCheck):\n"
    "    check_id = 'chk_gen'\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
)
_BAD_STATIC = "import os\n" + _GOOD
_MALFORMED = (
    "class Bad(BaseAuditCheck):\n"
    "    check_id = 'chk_bad'\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS'}\n"  # missing keys -> functional failure
)


@pytest.fixture(autouse=True)
def _clean():
    clear_custom_registry()
    yield
    clear_custom_registry()


def _eligible_check() -> CustomCheck:
    check = CustomCheck(check_id=make_check_id("p"), raw_prompt="ensure something")
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
    return check


def _seq_generator(*sources):
    calls = {"n": 0}

    def gen(_prompt, _feedback):
        i = min(calls["n"], len(sources) - 1)
        calls["n"] += 1
        return sources[i]

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


def _approve(_prompt, _source):
    return ReviewVerdict(True)


def _reject(_prompt, _source):
    return ReviewVerdict(False, "not audit-only")


_APPROVE = _approve
_REJECT = _reject


def test_ai_off_marks_ai_required():
    check = generate(_eligible_check(), CustomCheckSession())  # default LLM generator, AI off
    assert check.lifecycle_status is LifecycleStatus.AI_REQUIRED
    assert check.code_gen.status == "AI_REQUIRED"


def test_generates_on_first_attempt():
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(_GOOD), reviewer=_APPROVE,
    )
    assert check.code_gen.status == "GENERATED"
    assert check.code_gen.attempts == 1
    assert check.generated_code == _GOOD
    assert check.feasibility is FeasibilityClass.FULLY_FEASIBLE


def test_retries_after_static_failure_then_succeeds():
    gen = _seq_generator(_BAD_STATIC, _GOOD)
    check = generate(_eligible_check(), CustomCheckSession(), generator=gen, reviewer=None)
    assert check.code_gen.status == "GENERATED"
    assert check.code_gen.attempts == 2


def test_fails_after_max_static_failures():
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(_BAD_STATIC), reviewer=None, max_attempts=3,
    )
    assert check.code_gen.status == "FAILED"
    assert check.code_gen.stage_failed == "static"
    assert check.feasibility is FeasibilityClass.NOT_FEASIBLE


def test_functional_failure_then_succeeds():
    gen = _seq_generator(_MALFORMED, _GOOD)
    check = generate(_eligible_check(), CustomCheckSession(), generator=gen, reviewer=None)
    assert check.code_gen.status == "GENERATED"
    assert check.code_gen.attempts == 2


def test_reviewer_rejection_fails_after_retries():
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(_GOOD), reviewer=_REJECT, max_attempts=2,
    )
    assert check.code_gen.status == "FAILED"
    assert check.code_gen.stage_failed == "review"


def test_reviewer_none_skips_review():
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(_GOOD), reviewer=None,
    )
    assert check.code_gen.status == "GENERATED"


def test_ignores_non_eligible_check():
    check = CustomCheck(check_id="CHK-x", raw_prompt="p")  # PENDING, not eligible
    out = generate(check, CustomCheckSession(), generator=_seq_generator(_GOOD), reviewer=None)
    assert out.code_gen is None
    assert out.lifecycle_status is LifecycleStatus.PENDING
