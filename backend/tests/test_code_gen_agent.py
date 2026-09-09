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


# -- _coerce_to_check: rescue weak-model "forgot to subclass" mistakes ---------

from auditfast.ai.agents.code_gen_agent import _coerce_to_check


def test_coerce_injects_base_when_class_has_no_base():
    src = "class GitCheck:\n    def evaluate(self, kb):\n        return {}\n"
    out = _coerce_to_check(src)
    assert "class GitCheck(BaseAuditCheck):" in out


def test_coerce_appends_base_to_other_base():
    src = "class GitCheck(object):\n    def evaluate(self, kb):\n        return {}\n"
    out = _coerce_to_check(src)
    assert "BaseAuditCheck" in out and "object" in out


def test_coerce_leaves_proper_subclass_untouched():
    assert _coerce_to_check(_GOOD) == _GOOD


def test_coerce_noop_without_evaluate():
    src = "x = 1\n"
    assert _coerce_to_check(src) == src


def test_coerce_wraps_bare_evaluate_function():
    src = (
        "def evaluate(kb):\n"
        "    return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
    )
    out = _coerce_to_check(src)
    assert "class GeneratedCheck(BaseAuditCheck):" in out
    assert "def evaluate(self, kb):" in out


def test_coerce_strips_model_defined_stub_base():
    # Weak model redefines BaseAuditCheck (shadowing the real one). Strip the stub so
    # the generated subclass binds to the real injected base and loads.
    src = (
        "class BaseAuditCheck:\n"
        "    def evaluate(self, kb):\n"
        "        return {}\n"
        "class GitCheck(BaseAuditCheck):\n"
        "    check_id = 'chk_git'\n"
        "    def evaluate(self, kb):\n"
        "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
    )
    out = _coerce_to_check(src)
    assert "class BaseAuditCheck:" not in out  # stub removed
    assert "class GitCheck(BaseAuditCheck):" in out  # real subclass kept
    # And it now actually loads + runs against the real base.
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(out), reviewer=None,
    )
    assert check.code_gen.status == "GENERATED"


def test_generate_rescues_missing_base_class_in_the_loop():
    # A generator that "forgets" (BaseAuditCheck) should still succeed thanks to the rescue.
    no_base = (
        "class GitChk:\n"
        "    check_id = 'chk_git'\n"
        "    def evaluate(self, kb):\n"
        "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
    )
    rescued = _coerce_to_check(no_base)  # what default_generator would now return
    check = generate(
        _eligible_check(), CustomCheckSession(),
        generator=_seq_generator(rescued), reviewer=None,
    )
    assert check.code_gen.status == "GENERATED"
    assert check.code_gen.attempts == 1
