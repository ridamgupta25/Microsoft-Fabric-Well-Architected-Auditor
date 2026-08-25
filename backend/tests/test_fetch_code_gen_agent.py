"""Tests for Node 3b's read-only fetch-code generator (artifact generation)."""
from __future__ import annotations

from auditfast.ai.agents import fetch_code_gen_agent as agent
from auditfast.ai.orchestrator.state import CustomCheck, FetchPlan

_SAFE = "def fetch(client, workspace_id):\n    return client.get(f'/workspaces/{workspace_id}/notebooks')\n"
_IMPORT = "import os\ndef fetch(client, workspace_id):\n    return os.listdir('.')\n"
_MUTATION = "def fetch(client, workspace_id):\n    return client.delete(f'/workspaces/{workspace_id}/notebooks/1')\n"
_SYNTAX = "def fetch(client, workspace_id)\n    return 1\n"


def _check_with_plan() -> CustomCheck:
    check = CustomCheck(check_id="CHK-abc12345", raw_prompt="Verify notebooks have descriptions")
    check.fetch_plan = FetchPlan(field="notebooks", resource="notebook", endpoint="/notebooks")
    return check


# -- validate_fetch_source ----------------------------------------------------

def test_safe_read_only_code_passes():
    ok, reason = agent.validate_fetch_source(_SAFE)
    assert ok, reason


def test_disallowed_import_is_rejected():
    ok, reason = agent.validate_fetch_source(_IMPORT)
    assert not ok
    assert "import" in reason.lower() or "os" in reason.lower()


def test_write_verb_is_rejected():
    ok, reason = agent.validate_fetch_source(_MUTATION)
    assert not ok
    assert "read-only" in reason.lower() or "mutation" in reason.lower()


def test_syntax_error_is_rejected():
    ok, reason = agent.validate_fetch_source(_SYNTAX)
    assert not ok


# -- generate_fetch_code ------------------------------------------------------

def test_no_plan_is_a_noop():
    check = CustomCheck(check_id="CHK-00000000", raw_prompt="anything")
    agent.generate_fetch_code(check, generator=lambda _p, _plan: _SAFE)
    assert check.fetch_code is None


def test_safe_generation_is_stored():
    check = _check_with_plan()
    agent.generate_fetch_code(check, generator=lambda _p, _plan: _SAFE)
    assert check.fetch_code is not None
    assert "def fetch(client, workspace_id)" in check.fetch_code


def test_unsafe_generation_is_not_stored():
    check = _check_with_plan()
    agent.generate_fetch_code(check, generator=lambda _p, _plan: _MUTATION)
    assert check.fetch_code is None


def test_ai_off_generation_is_noop():
    check = _check_with_plan()
    agent.generate_fetch_code(check, generator=lambda _p, _plan: None)
    assert check.fetch_code is None
