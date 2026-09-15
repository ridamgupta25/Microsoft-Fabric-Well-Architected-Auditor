"""Edge-case coverage for the evidence judge and the run wiring.

Two layers:
  A. The judge (``evidence_evaluator.evaluate``) with an injected fake model, so
     every scoring edge is deterministic without a real key.
  B. The run (``evidence_checks_service.run_evidence_checks``) with the evaluator
     stubbed, to prove workspace scope, approval, skipped files and the report.
"""
from __future__ import annotations

import json

import pytest

from auditfast.ai.agents.evidence_evaluator import evaluate
from auditfast.ai.evidence_intake.catalog import EvidenceRequirement
from auditfast.ai.evidence_intake.ingestion import Chunk
from auditfast.ai.evidence_intake.state import EvidenceCheck, EvidenceStatus
from auditfast.services import evidence_checks_service
from auditfast.services.evidence_checks_service import UploadedFile, run_evidence_checks

_REQ = EvidenceRequirement(
    ref="13.2.1",
    title="Runbook exists for daily/weekly/monthly operations",
    tier="A",
    ask_for="operations runbook",
    accepted_types=("pdf", "docx", "md"),
    rubric={"0": "none", "1": "ad-hoc", "2": "daily+weekly", "3": "daily+weekly+monthly"},
)
_DOC = Chunk(
    text="The runbook covers daily reconciliation, weekly validation, and monthly capacity review with owners.",
    source="runbook.md",
    page=2,
)


def _gen(payload: dict):
    return lambda system, user: json.dumps(payload)


# ---------------------------------------------------------------- A. the judge

def test_edge_full_evidence_scores_top_rung_with_recommendations():
    payload = {
        "score": 3, "rung_matched": "3",
        "citations": [{"quote": "daily reconciliation, weekly validation, and monthly capacity review", "source": "runbook.md", "page": 2}],
        "gaps": [], "recommendations": ["Add named owners for the monthly review"], "confidence": 0.9,
    }
    r = evaluate(_REQ, [_DOC], generator=_gen(payload))
    assert r.status == EvidenceStatus.DRAFTED
    assert r.score == 3
    assert r.recommendations == ["Add named owners for the monthly review"]
    assert r.citations[0].page == 2


def test_edge_partial_evidence_scores_middle_rung():
    payload = {
        "score": 2, "rung_matched": "2",
        "citations": [{"quote": "daily reconciliation, weekly validation", "source": "runbook.md", "page": 2}],
        "gaps": ["No monthly routine"], "recommendations": ["Document a monthly routine"], "confidence": 0.6,
    }
    r = evaluate(_REQ, [_DOC], generator=_gen(payload))
    assert r.status == EvidenceStatus.DRAFTED
    assert r.score == 2
    assert r.gaps == ["No monthly routine"]


def test_edge_no_document_needs_evidence():
    r = evaluate(_REQ, [], generator=_gen({"score": 3}))
    assert r.status == EvidenceStatus.NEEDS_EVIDENCE
    assert r.score is None


def test_edge_irrelevant_document_model_returns_null_needs_evidence():
    # Chunks exist but the model finds nothing supporting -> score null.
    irrelevant = Chunk(text="Lunch is at noon and parking code is 4821.", source="offsite.md", page=1)
    payload = {"score": None, "rung_matched": None, "citations": [], "gaps": ["No runbook content"], "recommendations": [], "confidence": 0.1}
    r = evaluate(_REQ, [irrelevant], generator=_gen(payload))
    assert r.status == EvidenceStatus.NEEDS_EVIDENCE


def test_edge_fabricated_citation_is_rejected():
    payload = {
        "score": 3, "rung_matched": "3",
        "citations": [{"quote": "quarterly disaster recovery rehearsal that is not in the doc", "source": "runbook.md", "page": 2}],
        "gaps": [], "recommendations": [], "confidence": 0.95,
    }
    r = evaluate(_REQ, [_DOC], generator=_gen(payload))
    assert r.status == EvidenceStatus.NEEDS_EVIDENCE
    assert r.score is None


def test_edge_score_zero_needs_evidence():
    payload = {"score": 0, "citations": [], "gaps": ["nothing"], "recommendations": [], "confidence": 0.0}
    r = evaluate(_REQ, [_DOC], generator=_gen(payload))
    assert r.status == EvidenceStatus.NEEDS_EVIDENCE


def test_edge_out_of_range_score_is_clamped():
    payload = {
        "score": 7, "rung_matched": "3",
        "citations": [{"quote": "daily reconciliation, weekly validation", "source": "runbook.md", "page": 2}],
        "gaps": [], "recommendations": [], "confidence": 3.0,
    }
    r = evaluate(_REQ, [_DOC], generator=_gen(payload))
    assert r.score == 3
    assert 0.0 <= r.confidence <= 1.0


def test_edge_unparseable_model_reply_needs_evidence():
    r = evaluate(_REQ, [_DOC], generator=lambda s, u: "sorry, I cannot help")
    assert r.status == EvidenceStatus.NEEDS_EVIDENCE


def test_edge_ai_off_reports_ai_required():
    # No generator + AI off in tests -> never a guess.
    r = evaluate(_REQ, [_DOC])
    assert r.status == EvidenceStatus.AI_REQUIRED


# --------------------------------------------------------------- B. the run

def _md(text: str, name: str = "doc.md") -> UploadedFile:
    return UploadedFile(name=name, data=text.encode("utf-8"))


def _stub_evaluate(drafted_refs: set[str]):
    def fake(req, chunks, *, ai=None, generator=None):
        if req.ref in drafted_refs:
            return EvidenceCheck(
                ref=req.ref, title=req.title, status=EvidenceStatus.DRAFTED,
                score=3, rung_matched="3", gaps=[],
                recommendations=["Keep it current"], confidence=0.9,
            )
        return EvidenceCheck(ref=req.ref, title=req.title, status=EvidenceStatus.NEEDS_EVIDENCE)
    return fake


def test_run_tags_report_with_workspaces_and_approves(monkeypatch):
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate({"13.2.1"}))
    result = run_evidence_checks(
        [_md("daily weekly monthly runbook")],
        refs=["13.2.1"],
        workspace_ids=["Finance-WS", "Sales-WS"],
        approved_refs=["13.2.1"],
    )
    assert result["workspaces"] == ["Finance-WS", "Sales-WS"]
    assert "Attested for workspaces" in result["report_markdown"]
    assert "Finance-WS, Sales-WS" in result["report_markdown"]
    assert "13.2.1" in result["report_markdown"]
    assert result["summary"]["DRAFTED"] == 1


def test_run_combined_doc_answers_many_checks(monkeypatch):
    refs = ["13.2.1", "5.1.1", "8.2.2"]
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate(set(refs)))
    result = run_evidence_checks(
        [_md("one combined document with runbook, DQ framework and stewards")],
        refs=refs,
        workspace_ids=["WS1"],
    )
    assert result["checks"] == 3
    assert result["files"] == 1
    assert result["summary"].get("DRAFTED") == 3


def test_run_unsupported_file_is_skipped(monkeypatch):
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate(set()))
    result = run_evidence_checks(
        [UploadedFile(name="screenshot.png", data=b"\x89PNG")],
        refs=["13.2.1"],
        workspace_ids=["WS1"],
    )
    assert "screenshot.png" in result["skipped_files"]
    # No readable text -> the check cannot be drafted.
    assert result["ledger"][0]["status"] == "NEEDS_EVIDENCE"


def test_run_unknown_ref_is_ignored(monkeypatch):
    monkeypatch.setattr(evidence_checks_service, "evaluate", _stub_evaluate(set()))
    result = run_evidence_checks([_md("x")], refs=["0.0.0"], workspace_ids=["WS1"])
    assert result["checks"] == 0
