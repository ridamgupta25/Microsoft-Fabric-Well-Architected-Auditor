"""Milestone 3 — the grounded evaluator: scores only what the document supports."""
from __future__ import annotations

import json

from auditfast.ai.agents import evidence_evaluator
from auditfast.ai.agents.evidence_evaluator import evaluate
from auditfast.ai.evidence_intake.catalog import EvidenceRequirement
from auditfast.ai.evidence_intake.ingestion import Chunk
from auditfast.ai.evidence_intake.state import EvidenceStatus

_REQ = EvidenceRequirement(
    ref="13.2.1",
    title="Runbook exists for daily/weekly/monthly operations",
    tier="A",
    ask_for="operations runbook",
    accepted_types=("pdf", "docx", "md"),
    rubric={"0": "none", "1": "ad-hoc", "2": "daily+weekly", "3": "daily+weekly+monthly"},
)

_CHUNK = Chunk(
    text="The operations runbook covers daily reconciliation and weekly load validation.",
    source="runbook.md",
    page=4,
)


def _gen(payload: dict):
    return lambda system, user: json.dumps(payload)


def test_empty_upload_needs_evidence():
    result = evaluate(_REQ, [], generator=_gen({}))
    assert result.status == EvidenceStatus.NEEDS_EVIDENCE
    assert result.score is None


def test_ai_off_returns_ai_required():
    # No generator injected and AI disabled in tests -> AI_REQUIRED, never a guess.
    result = evaluate(_REQ, [_CHUNK])
    assert result.status == EvidenceStatus.AI_REQUIRED
    assert result.score is None


def test_grounded_score_is_drafted():
    payload = {
        "score": 2,
        "rung_matched": "2",
        "citations": [{"quote": "daily reconciliation and weekly load validation", "source": "runbook.md", "page": 4}],
        "gaps": ["No monthly routine found"],
        "confidence": 0.7,
    }
    result = evaluate(_REQ, [_CHUNK], generator=_gen(payload))
    assert result.status == EvidenceStatus.DRAFTED
    assert result.score == 2
    assert result.citations and result.citations[0].page == 4
    assert result.requires_human is True


def test_fabricated_citation_is_rejected():
    # The quote does NOT appear in the chunk -> dropped -> score withheld.
    payload = {
        "score": 3,
        "rung_matched": "3",
        "citations": [{"quote": "monthly disaster-recovery rehearsal every quarter", "source": "runbook.md", "page": 4}],
        "gaps": [],
        "confidence": 0.9,
    }
    result = evaluate(_REQ, [_CHUNK], generator=_gen(payload))
    assert result.status == EvidenceStatus.NEEDS_EVIDENCE
    assert result.score is None


def test_score_zero_with_no_citations_needs_evidence():
    payload = {"score": 0, "rung_matched": "0", "citations": [], "gaps": ["nothing found"], "confidence": 0.1}
    result = evaluate(_REQ, [_CHUNK], generator=_gen(payload))
    assert result.status == EvidenceStatus.NEEDS_EVIDENCE


def test_unparseable_reply_needs_evidence():
    result = evaluate(_REQ, [_CHUNK], generator=lambda s, u: "not json at all")
    assert result.status == EvidenceStatus.NEEDS_EVIDENCE


def test_score_is_clamped_to_0_3():
    payload = {
        "score": 9,
        "rung_matched": "3",
        "citations": [{"quote": "daily reconciliation and weekly load validation", "source": "runbook.md", "page": 4}],
        "gaps": [],
        "confidence": 2.0,
    }
    result = evaluate(_REQ, [_CHUNK], generator=_gen(payload))
    assert result.score == 3
    assert 0.0 <= result.confidence <= 1.0
