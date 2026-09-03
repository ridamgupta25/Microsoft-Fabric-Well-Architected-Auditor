"""Tests for cross-run custom-checks memory and generated-code reuse."""
from __future__ import annotations

from auditfast.ai.agents import code_gen_agent
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    LifecycleStatus,
)
from auditfast.services.custom_checks_memory import CustomChecksMemory
from auditfast.services import custom_checks_service

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


def test_record_keeps_prior_approval_when_new_run_has_no_decision(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([{"check_id": "CHK-1", "generated_code": _VALID, "approved": True}])
    # A plain review run reports approved=None; the earlier YES must survive.
    mem.record([{"check_id": "CHK-1", "generated_code": _VALID, "approved": None}])
    assert mem.prior_decisions() == {"CHK-1": True}
    # An explicit reject still overrides.
    mem.record([{"check_id": "CHK-1", "generated_code": _VALID, "approved": False}])
    assert mem.prior_decisions() == {"CHK-1": False}


def test_missing_file_is_empty(tmp_path):
    mem = CustomChecksMemory(tmp_path / "nope.json")
    assert mem.code_cache() == {}
    assert mem.prior_decisions() == {}


def test_approved_checks_lists_only_approved_with_code(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([
        {"check_id": "CHK-yes", "raw_prompt": "keep me", "generated_code": _VALID, "approved": True},
        {"check_id": "CHK-no", "raw_prompt": "reject", "generated_code": _VALID, "approved": False},
        {"check_id": "CHK-pending", "raw_prompt": "pending", "generated_code": _VALID, "approved": None},
        {"check_id": "CHK-nocode", "raw_prompt": "no code", "generated_code": None, "approved": True},
    ])
    approved = mem.approved_checks()
    assert [c["check_id"] for c in approved] == ["CHK-yes"]
    assert approved[0]["raw_prompt"] == "keep me"
    assert approved[0]["generated_code"] == _VALID


def test_previously_approved_is_scoped_to_its_workspaces(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    # Approved while checking only workspace A.
    mem.record(
        [{"check_id": "CHK-a", "raw_prompt": "p", "generated_code": _VALID, "approved": True}],
        workspace_ids=["ws-A"],
    )
    # Recalled on workspace A, but NOT on a different workspace B.
    assert mem.previously_approved(["ws-A"]) == {"CHK-a"}
    assert mem.previously_approved(["ws-B"]) == set()
    # A run covering A plus an unapproved B is not fully covered, so not recalled.
    assert mem.previously_approved(["ws-A", "ws-B"]) == set()


def test_previously_approved_without_scope_is_unrestricted(tmp_path):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([{"check_id": "CHK-a", "generated_code": _VALID, "approved": True}])
    # Older data has no recorded scope -> treated as approved anywhere.
    assert mem.previously_approved(["ws-anything"]) == {"CHK-a"}


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


# -- approved_checks_report (folded into the audit report) --------------------

class _FakeStore:
    """Minimal ContextStore stand-in: one or more workspace snapshots by id."""

    def __init__(self, *snapshots: dict) -> None:
        self._snaps = {s["id"]: s for s in snapshots}

    def workspaces(self) -> list[str]:
        return list(self._snaps)

    def load(self, wid: str):
        return self._snaps.get(wid)


def test_approved_checks_report_runs_only_approved(tmp_path, monkeypatch):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([
        {"check_id": "CHK-yes", "raw_prompt": "count notebooks", "generated_code": _VALID, "approved": True},
        {"check_id": "CHK-pending", "raw_prompt": "pending", "generated_code": _VALID, "approved": None},
    ])
    snapshot = {"id": "ws-1", "display_name": "WS One", "notebooks": {}}
    monkeypatch.setattr(custom_checks_service, "_memory", lambda: mem)
    monkeypatch.setattr(custom_checks_service, "_kb_store", lambda: _FakeStore(snapshot))

    section = custom_checks_service.approved_checks_report(["ws-1"])
    assert section is not None
    assert section["workspaces"] == 1
    assert [c["check_id"] for c in section["checks"]] == ["CHK-yes"]
    row = section["checks"][0]
    assert row["prompt"] == "count notebooks"
    assert row["score"] == 100.0


def test_approved_checks_report_none_when_nothing_approved(tmp_path, monkeypatch):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record([{"check_id": "CHK-pending", "generated_code": _VALID, "approved": None}])
    monkeypatch.setattr(custom_checks_service, "_memory", lambda: mem)
    monkeypatch.setattr(
        custom_checks_service, "_kb_store", lambda: _FakeStore({"id": "ws-1"})
    )
    assert custom_checks_service.approved_checks_report(["ws-1"]) is None


def test_approved_checks_report_excludes_checks_from_other_workspaces(tmp_path, monkeypatch):
    mem = CustomChecksMemory(tmp_path / "memory.json")
    mem.record(
        [{"check_id": "CHK-b", "raw_prompt": "for B", "generated_code": _VALID, "approved": True}],
        workspace_ids=["ws-B"],
    )
    monkeypatch.setattr(custom_checks_service, "_memory", lambda: mem)
    monkeypatch.setattr(custom_checks_service, "_kb_store", lambda: _FakeStore({"id": "ws-A"}))
    # The only approved check belongs to ws-B, so auditing ws-A folds in nothing.
    assert custom_checks_service.approved_checks_report(["ws-A"]) is None
