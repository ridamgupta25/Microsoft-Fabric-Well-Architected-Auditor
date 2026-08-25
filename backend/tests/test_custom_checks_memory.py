"""Tests for cross-run custom-checks memory and generated-code reuse."""
from __future__ import annotations

from auditfast.ai.agents import code_gen_agent
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    LifecycleStatus,
)
from auditfast.services.custom_checks_memory import CustomChecksMemory

_VALID = (
    "class Reused(BaseAuditCheck):\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
)


# -- CustomChecksMemory -------------------------------------------------------

def test_record_then_code_cache_roundtrip(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([{"check_id": "CHK-1", "raw_prompt": "p", "generated_code": _VALID, "approved": True}])
    assert mem.code_cache() == {"CHK-1": _VALID}
    assert mem.prior_decisions() == {"CHK-1": True}


def test_record_merges_and_keeps_prior_code(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([{"check_id": "CHK-1", "generated_code": _VALID}])
    # A later run without code must not erase the remembered code.
    mem.record([{"check_id": "CHK-1", "generated_code": None, "approved": False}])
    cache = mem.code_cache()
    assert cache == {"CHK-1": _VALID}
    assert mem.prior_decisions() == {"CHK-1": False}


def test_missing_file_is_empty(tmp_path):
    mem = CustomChecksMemory(tmp_path / "nope.json")
    assert mem.code_cache() == {}
    assert mem.prior_decisions() == {}


# -- generated-code reuse in Node 4 ------------------------------------------

def test_generate_reuses_cached_code_without_generator():
    session = CustomCheckSession()
    check = CustomCheck(check_id="CHK-reuse01", raw_prompt="reuse me")
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM

    def _boom(_prompt, _feedback):  # generator must NOT be called on a cache hit
        raise AssertionError("generator should not run when code is reused")

    code_gen_agent.generate(
        check, session, generator=_boom, reviewer=None,
        code_cache={"CHK-reuse01": _VALID},
    )
    assert check.generated_code == _VALID
    assert check.code_gen is not None
    assert check.code_gen.status == "GENERATED"
    assert "reused" in check.code_gen.reason


def test_generate_ignores_unsafe_cached_code():
    session = CustomCheckSession()
    check = CustomCheck(check_id="CHK-reuse02", raw_prompt="reuse unsafe")
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
    unsafe = "import os\nclass X(BaseAuditCheck):\n    def evaluate(self, kb):\n        return os.getcwd()\n"

    # Unsafe cache is rejected; with no generator available the check falls through.
    code_gen_agent.generate(
        check, session, generator=lambda _p, _f: None, reviewer=None,
        code_cache={"CHK-reuse02": unsafe},
    )
    assert check.generated_code is None
