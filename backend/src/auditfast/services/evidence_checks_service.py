"""Evidence checks — score manual checklist points from uploaded documents.

Parse every uploaded file into one shared, scrubbed chunk pool, retrieve the
chunks relevant to each requested check, and draft a grounded 0-3 result the user
then approves. Additive and isolated: it never changes the deterministic score and
never writes to Fabric. Mirrors :mod:`custom_checks_service`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..ai.agents.evidence_evaluator import evaluate
from ..ai.evidence_intake import catalog, ingestion
from ..ai.evidence_intake.git_source import GitFetchError, GitRepo, fetch_docs
from ..ai.evidence_intake.report import render_report
from ..ai.evidence_intake.state import Citation, EvidenceCheck, EvidenceSession, EvidenceStatus
from ..ai.orchestrator.ai_config import AiConfig
from ..config.settings import get_settings
from .evidence_memory import EvidenceMemory


def _memory() -> EvidenceMemory | None:
    """The durable manual-checks memory, or ``None`` when disabled."""
    settings = get_settings()
    if not settings.evidence_checks_memory_enabled:
        return None
    return EvidenceMemory(settings.resolve(settings.evidence_checks_memory_file))


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """One uploaded document: its name and raw bytes."""

    name: str
    data: bytes


def _citation_dict(c: Citation) -> dict:
    return {"quote": c.quote, "source": c.source, "page": c.page}


def _row_dict(row: EvidenceCheck) -> dict:
    return {
        "ref": row.ref,
        "title": row.title,
        "status": row.status.value,
        "score": row.score,
        "rung_matched": row.rung_matched,
        "citations": [_citation_dict(c) for c in row.citations],
        "gaps": list(row.gaps),
        "recommendations": list(row.recommendations),
        "confidence": row.confidence,
        "source": row.source,
        "requires_human": row.requires_human,
        "approved": row.approved,
    }


def _known_refs(refs: list[str] | None) -> list[str]:
    """Requested refs that exist in the catalog, preserving order; default = all."""
    available = catalog.load()
    if not refs:
        return list(available.keys())
    return [r for r in refs if r in available]


def run_evidence_checks(
    files: list[UploadedFile],
    *,
    refs: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    git: GitRepo | None = None,
    manual_scores: dict[str, dict] | None = None,
    ai: AiConfig | None = None,
    approved_refs: list[str] | None = None,
) -> dict:
    """Run the evidence pipeline over uploaded docs and/or a Git repo.

    Evidence can come from uploaded files, a Git repo (its documentation is pulled
    and treated the same way), or a human-supplied manual score per check. Manual
    scores are recorded as attestations and never overwritten by the AI.
    """
    targets = _known_refs(refs)
    workspaces = [w for w in (workspace_ids or []) if w]
    manual = manual_scores or {}
    session = EvidenceSession()

    sources: list[tuple[str, bytes]] = [(f.name, f.data) for f in files]
    git_error: str | None = None
    git_files = 0
    if git is not None and git.url.strip():
        try:
            fetched = fetch_docs(git)
            git_files = len(fetched)
            sources.extend(fetched)
        except GitFetchError as exc:
            git_error = str(exc)

    chunks: list[ingestion.Chunk] = []
    skipped: list[str] = []
    for name, data in sources:
        try:
            chunks.extend(ingestion.parse_and_chunk(data, name))
        except ingestion.UnsupportedTypeError:
            skipped.append(name)

    retriever = ingestion.Retriever(chunks, ai=ai) if chunks else None
    for ref in targets:
        req = catalog.get(ref)
        if ref in manual:  # human-attested score bypasses the AI
            session.add(_manual_row(req.ref, req.title, manual[ref]))
            continue
        top = retriever.nearest(f"{req.ask_for} {req.title}", k=6) if retriever else []
        session.add(evaluate(req, top, ai=ai))

    approved = set(approved_refs or [])
    for ref, row in session.rows.items():
        if ref in approved and row.status == EvidenceStatus.DRAFTED:
            row.approved = True

    # Recall approvals made for these workspaces in an earlier run.
    mem = _memory()
    prev = mem.previously_approved(workspaces) if mem else set()

    ledger = [_row_dict(session.rows[r]) for r in targets]
    for row in ledger:
        row["previously_approved"] = row["ref"] in prev

    return {
        "checks": len(targets),
        "files": len(files),
        "git_files": git_files,
        "git_error": git_error,
        "workspaces": workspaces,
        "skipped_files": skipped,
        "summary": session.summary(),
        "ledger": ledger,
        "pending_review_ids": session.pending_review_ids(),
        "report_markdown": render_report(session, workspaces=workspaces),
    }


def _manual_row(ref: str, title: str, entry: dict) -> EvidenceCheck:
    """Build a human-attested result from a supplied score (0-3) + optional note."""
    try:
        score = max(0, min(3, int(entry.get("score"))))
    except (TypeError, ValueError):
        score = 0
    note = str(entry.get("note", "")).strip()
    return EvidenceCheck(
        ref=ref, title=title, status=EvidenceStatus.DRAFTED,
        score=score, rung_matched=str(score), source="manual",
        recommendations=[note] if note else [], confidence=1.0, requires_human=True,
    )


def save_approved(rows: list[dict], workspace_ids: list[str] | None) -> int:
    """Remember the approved manual-check rows so an audit can fold them in.

    Best-effort: returns the number of rows remembered (0 when memory is off).
    """
    memory = _memory()
    if memory is None:
        return 0
    keep = [r for r in rows if r.get("ref") and r.get("score") is not None]
    memory.record(keep, workspace_ids)
    return len(keep)


def approved_evidence_report(workspace_ids: Sequence[str] | None = None) -> dict | None:
    """Approved manual checks for the audited workspaces, as a report section.

    Additive and read-only: reads only the remembered results (no AI, no Fabric),
    so it is safe to call while finalising an audit report. Returns ``None`` when
    memory is off or nothing was approved for these workspaces.
    """
    memory = _memory()
    if memory is None:
        return None
    ids = [str(w) for w in (workspace_ids or []) if w]
    approved = memory.approved_for(ids or None)
    if not approved:
        return None
    checks = [
        {
            "ref": e.get("ref"),
            "title": e.get("title"),
            "score": e.get("score"),
            "rung_matched": e.get("rung_matched"),
            "citations": e.get("citations") or [],
            "recommendations": e.get("recommendations") or [],
            "source": e.get("source", "ai"),
        }
        for e in approved
    ]
    return {"checks": checks, "workspaces": len(ids) or 0}


__all__ = ["UploadedFile", "run_evidence_checks", "save_approved", "approved_evidence_report"]
