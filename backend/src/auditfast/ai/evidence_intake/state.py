"""The ledger for one evidence-check run: rows, statuses, and citations.

Mirrors the custom-checks session shape. An :class:`EvidenceCheck` is one drafted
result awaiting human review; a score is only ever a *draft* here (``approved`` is
``None`` until a human decides). The session is idempotent by ref.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EvidenceStatus(str, Enum):
    """Lifecycle of one evidence check."""

    DRAFTED = "DRAFTED"  # AI produced a grounded score, awaiting human review
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"  # no document/among-chunks support -> no score
    AI_REQUIRED = "AI_REQUIRED"  # no AI key supplied -> cannot draft
    ADVISORY_ONLY = "ADVISORY_ONLY"  # Tier D: AI advises, human must score


@dataclass(frozen=True, slots=True)
class Citation:
    """A quote pulled from an uploaded document, with where it came from."""

    quote: str
    source: str
    page: int


@dataclass(slots=True)
class EvidenceCheck:
    """One drafted result for a checklist point."""

    ref: str
    title: str
    status: EvidenceStatus
    score: int | None = None  # withheld unless DRAFTED
    rung_matched: str | None = None
    citations: list[Citation] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "ai"  # "ai" (document-evidenced) or "manual" (human-attested)
    requires_human: bool = True
    approved: bool | None = None  # None=pending, True/False after review


@dataclass(slots=True)
class EvidenceSession:
    """All rows for one run, keyed by ref (idempotent add)."""

    rows: dict[str, EvidenceCheck] = field(default_factory=dict)

    def add(self, row: EvidenceCheck) -> None:
        self.rows[row.ref] = row

    def pending_review_ids(self) -> list[str]:
        return [ref for ref, r in self.rows.items() if r.approved is None and r.status == EvidenceStatus.DRAFTED]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows.values():
            out[r.status.value] = out.get(r.status.value, 0) + 1
        return out


__all__ = ["EvidenceStatus", "Citation", "EvidenceCheck", "EvidenceSession"]
