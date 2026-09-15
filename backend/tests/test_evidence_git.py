"""Git-source fetch (mocked HTTP) and manual-score attestation."""
from __future__ import annotations

import base64

import pytest

from auditfast.ai.evidence_intake import git_source
from auditfast.ai.evidence_intake.git_source import GitFetchError, GitRepo, detect_host, fetch_docs
from auditfast.services import evidence_checks_service
from auditfast.services.evidence_checks_service import run_evidence_checks


class _Resp:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = content

    def json(self):
        return self._json


def test_detect_host():
    assert detect_host("https://github.com/org/repo") == "github"
    assert detect_host("https://dev.azure.com/org/proj/_git/repo") == "ado"
    assert detect_host("https://example.com/x") == "unknown"


def test_unsupported_host_raises():
    with pytest.raises(GitFetchError):
        fetch_docs(GitRepo(url="https://gitlab.com/o/r", token="t"))


def test_github_fetches_only_doc_files(monkeypatch):
    tree = {"tree": [
        {"path": "README.md", "type": "blob"},
        {"path": "docs/architecture.md", "type": "blob"},
        {"path": "src/main.py", "type": "blob"},   # code -> ignored
        {"path": "assets", "type": "tree"},          # folder -> ignored
    ]}
    md = base64.b64encode(b"# Architecture\nBronze is append-only.").decode()

    def fake_get(url, *, headers):
        if url.endswith("/org/repo"):
            return _Resp(json_data={"default_branch": "main"})
        if "git/trees" in url:
            return _Resp(json_data=tree)
        return _Resp(json_data={"encoding": "base64", "content": md})

    monkeypatch.setattr(git_source, "_get", fake_get)
    docs = fetch_docs(GitRepo(url="https://github.com/org/repo", token="t"))
    paths = [p for p, _ in docs]
    assert "README.md" in paths and "docs/architecture.md" in paths
    assert "src/main.py" not in paths
    assert all(isinstance(b, bytes) and b for _, b in docs)


def test_github_auth_error_raises(monkeypatch):
    monkeypatch.setattr(git_source, "_get", lambda url, *, headers: (_ for _ in ()).throw(GitFetchError("denied")))
    with pytest.raises(GitFetchError):
        fetch_docs(GitRepo(url="https://github.com/org/repo", token="bad"))


def test_ado_fetches_doc_files(monkeypatch):
    items = {"value": [
        {"path": "/README.md", "isFolder": False},
        {"path": "/docs/runbook.md", "isFolder": False},
        {"path": "/build.yml", "isFolder": False},   # not a doc
        {"path": "/docs", "isFolder": True},
    ]}

    def fake_get(url, *, headers):
        if "scopePath" in url:
            return _Resp(json_data=items)
        return _Resp(content=b"Daily and weekly runbook.")

    monkeypatch.setattr(git_source, "_get", fake_get)
    docs = fetch_docs(GitRepo(url="https://dev.azure.com/org/proj/_git/repo", token="t"))
    paths = [p for p, _ in docs]
    assert "README.md" in paths and "docs/runbook.md" in paths
    assert "build.yml" not in paths


# -------------------------------------------------------- service integration

def _stub_evaluate(drafted_refs):
    from auditfast.ai.evidence_intake.state import EvidenceCheck, EvidenceStatus

    def fake(req, chunks, *, ai=None, generator=None):
        status = EvidenceStatus.DRAFTED if req.ref in drafted_refs else EvidenceStatus.NEEDS_EVIDENCE
        return EvidenceCheck(ref=req.ref, title=req.title, status=status, score=3 if req.ref in drafted_refs else None)
    return fake


def test_run_pulls_from_git_repo(monkeypatch):
    monkeypatch.setattr(
        evidence_checks_service, "fetch_docs",
        lambda repo: [("docs/ops.md", b"Daily, weekly and monthly runbook with owners.")],
    )
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate({"13.2.1"}))
    result = run_evidence_checks(
        [],
        refs=["13.2.1"],
        workspace_ids=["WS1"],
        git=GitRepo(url="https://github.com/org/repo", token="t"),
    )
    assert result["git_files"] == 1
    assert result["git_error"] is None
    assert result["summary"].get("DRAFTED") == 1


def test_run_reports_git_error(monkeypatch):
    def boom(repo):
        raise GitFetchError("Access denied")

    monkeypatch.setattr(evidence_checks_service, "fetch_docs", boom)
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate(set()))
    result = run_evidence_checks(
        [],
        refs=["13.2.1"],
        workspace_ids=["WS1"],
        git=GitRepo(url="https://github.com/org/repo", token="bad"),
    )
    assert result["git_error"] == "Access denied"


def test_run_manual_score_is_attested(monkeypatch):
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate(set()))
    result = run_evidence_checks(
        [],
        refs=["13.2.1"],
        workspace_ids=["WS1"],
        manual_scores={"13.2.1": {"score": 2, "note": "Runbook kept in Confluence"}},
        approved_refs=["13.2.1"],
    )
    row = result["ledger"][0]
    assert row["source"] == "manual"
    assert row["score"] == 2
    assert row["approved"] is True
    assert "manually attested" in result["report_markdown"]
