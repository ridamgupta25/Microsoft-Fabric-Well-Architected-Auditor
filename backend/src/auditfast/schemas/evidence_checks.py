"""Schemas for the evidence-checks endpoint.

Mirrors :func:`auditfast.services.evidence_checks_service.run_evidence_checks`.
The request carries uploaded documents (multipart) plus the refs to assess and an
optional per-request AI key; the response is the ledger, the ids awaiting review,
and a rendered Markdown report.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .custom_checks import AiConfigIn


class EvidenceRequirementOut(BaseModel):
    """One catalog entry: what to upload for a check and how it is graded."""

    ref: str
    title: str
    tier: str
    pillar: str
    ask_for: str
    accepted_types: list[str]
    rubric: dict[str, str]


class EvidenceCitationOut(BaseModel):
    quote: str
    source: str
    page: int


class EvidenceCheckRow(BaseModel):
    ref: str
    title: str
    status: str
    score: int | None
    rung_matched: str | None
    citations: list[EvidenceCitationOut]
    gaps: list[str]
    recommendations: list[str]
    confidence: float
    source: str
    requires_human: bool
    approved: bool | None
    previously_approved: bool = False


class EvidenceChecksResult(BaseModel):
    """The ledger, pending-review ids, and rendered report for one upload run."""

    checks: int = Field(description="Checks assessed.")
    files: int = Field(description="Documents uploaded.")
    git_files: int = Field(default=0, description="Documentation files pulled from the Git repo.")
    git_error: str | None = Field(default=None, description="Why the Git fetch failed, if it did.")
    workspaces: list[str] = Field(default_factory=list, description="Workspaces the evidence is attested for.")
    skipped_files: list[str] = Field(default_factory=list, description="Uploads with no parser.")
    summary: dict[str, int] = Field(description="Count of rows per status.")
    ledger: list[EvidenceCheckRow]
    pending_review_ids: list[str]
    report_markdown: str
