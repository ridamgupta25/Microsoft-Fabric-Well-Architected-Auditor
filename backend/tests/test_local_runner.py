"""Node 5 (hardened Local Runner) + custom-runtime contract tests.

The AST allow-list, restricted-namespace exec, timeout, and result-shape
validation are exercised directly. No LLM and no optional extras.
"""
from __future__ import annotations

import time

import pytest

from auditfast.ai.custom_runtime.base_check import (
    CUSTOM_REGISTRY,
    BaseAuditCheck,
    clear_custom_registry,
)
from auditfast.ai.custom_runtime.local_runner import (
    UnsafeCodeError,
    load_and_run,
    load_check,
    run_check,
    validate_result,
    validate_source,
)

_GOOD_SOURCE = '''
class RefreshCheck(BaseAuditCheck):
    check_id = "chk_refresh"

    def evaluate(self, kb):
        models = kb.get("refresh_schedules", {})
        total = len(models)
        enabled = sum(1 for m in models.values() if m.get("enabled"))
        score = 100.0 if total and enabled == total else 0.0
        findings = [] if score == 100.0 else ["some models lack a refresh schedule"]
        return {
            "status": "PASS" if score == 100.0 else "FAIL",
            "score": score,
            "findings": findings,
            "recommendations": [],
        }
'''


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_custom_registry()
    yield
    clear_custom_registry()


# -- AST allow-list ------------------------------------------------------------

def test_valid_source_passes():
    ok, reason = validate_source(_GOOD_SOURCE)
    assert ok is True
    assert reason == ""


@pytest.mark.parametrize(
    "snippet",
    [
        "import os",
        "from os import system",
        "import subprocess",
        "import socket",
        "x = eval('1+1')",
        "x = exec('y=1')",
        "f = open('/etc/passwd')",
        "m = __import__('os')",
        "g = (lambda: 0).__globals__",
        "b = ().__class__.__bases__",
        "d = getattr(object, 'x')",
    ],
)
def test_unsafe_snippets_are_rejected(snippet):
    ok, reason = validate_source(snippet)
    assert ok is False
    assert reason


def test_allowed_import_passes():
    ok, _r = validate_source("import math\nimport re\nx = math.sqrt(4)")
    assert ok is True


def test_syntax_error_is_reported_not_raised():
    ok, reason = validate_source("def (:")
    assert ok is False
    assert "syntax" in reason.lower()


# -- load_check ----------------------------------------------------------------

def test_load_check_returns_subclass_and_registers():
    cls = load_check(_GOOD_SOURCE)
    assert issubclass(cls, BaseAuditCheck)
    assert "chk_refresh" in CUSTOM_REGISTRY


def test_load_check_rejects_unsafe_source():
    with pytest.raises(UnsafeCodeError):
        load_check("import os\nclass X(BaseAuditCheck):\n    def evaluate(self, kb):\n        return {}")


def test_load_check_requires_a_subclass():
    with pytest.raises(UnsafeCodeError):
        load_check("x = 1")


# -- run_check -----------------------------------------------------------------

def test_run_check_scores_a_valid_kb():
    cls = load_check(_GOOD_SOURCE)
    kb = {"refresh_schedules": {"A": {"enabled": True}, "B": {"enabled": True}}}
    result = run_check(cls, kb)
    assert result["status"] == "PASS"
    assert result["score"] == 100.0


def test_run_check_reports_a_failing_kb():
    cls = load_check(_GOOD_SOURCE)
    kb = {"refresh_schedules": {"A": {"enabled": False}}}
    result = run_check(cls, kb)
    assert result["status"] == "FAIL"
    assert result["score"] == 0.0
    assert result["findings"]


def test_run_check_traps_evaluate_errors():
    class Boom(BaseAuditCheck):
        check_id = "chk_boom"

        def evaluate(self, kb):
            raise RuntimeError("kaboom")

    result = run_check(Boom, {})
    assert result["status"] == "ERROR"
    assert result["error"] == "RuntimeError"
    assert validate_result(result)[0] is True  # error result is still a valid shape


def test_run_check_rejects_a_malformed_result():
    class Bad(BaseAuditCheck):
        check_id = "chk_bad"

        def evaluate(self, kb):
            return {"status": "PASS"}  # missing score/findings/recommendations

    result = run_check(Bad, {})
    assert result["error"] == "InvalidResult"


def test_run_check_rejects_out_of_range_score():
    class Over(BaseAuditCheck):
        check_id = "chk_over"

        def evaluate(self, kb):
            return {"status": "PASS", "score": 250.0, "findings": [], "recommendations": []}

    result = run_check(Over, {})
    assert result["error"] == "InvalidResult"


def test_run_check_times_out_a_slow_check():
    class Slow(BaseAuditCheck):
        check_id = "chk_slow"

        def evaluate(self, kb):
            time.sleep(1.0)
            return {"status": "PASS", "score": 100.0, "findings": [], "recommendations": []}

    result = run_check(Slow, {}, timeout=0.1)
    assert result["status"] == "ERROR"
    assert result["error"] == "TimeoutError"


# -- result validation ---------------------------------------------------------

def test_validate_result_accepts_bool_score_as_invalid():
    ok, _r = validate_result(
        {"status": "PASS", "score": True, "findings": [], "recommendations": []}
    )
    assert ok is False


# -- load_and_run --------------------------------------------------------------

def test_load_and_run_end_to_end():
    kb = {"refresh_schedules": {"A": {"enabled": True}}}
    result = load_and_run(_GOOD_SOURCE, kb)
    assert result["status"] == "PASS"


def test_load_and_run_returns_error_for_unsafe_code():
    result = load_and_run("import os\nclass X(BaseAuditCheck):\n    def evaluate(self, kb):\n        return {}", {})
    assert result["status"] == "ERROR"
    assert result["error"] == "UnsafeCodeError"


def test_sandbox_blocks_runtime_import_attempt():
    # Even if the AST somehow passed, the restricted builtins have no __import__.
    src = (
        "class X(BaseAuditCheck):\n"
        "    check_id = 'chk_x'\n"
        "    def evaluate(self, kb):\n"
        "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
    )
    result = load_and_run(src, {})
    assert result["score"] == 100.0
