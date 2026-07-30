"""Schemas for the checklist-intake endpoints.

Mirrors :func:`auditfast.services.intake_service.assess_point` (single point) and
:func:`auditfast.services.checklist_batch.run_checklist` (a whole uploaded
checklist). The single-point assessment is token-free; the batch runner is
offline by default and only uses a token to read a workspace with no cached
snapshot, so neither contract depends on a live Fabric read.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ChecklistIntakeRequest(BaseModel):
    """A single best-practice checklist point to assess."""

    point: str = Field(
        min_length=1,
        max_length=500,
        description="The best-practice statement to check for existing coverage.",
        examples=["Delta tables are OPTIMIZE-compacted after large writes"],
    )


class CheckMatchOut(BaseModel):
    """An existing check that resembles the submitted point."""

    check_id: str
    ref: str
    title: str
    pillar: str
    scope: str
    severity: str
    automation: str
    confidence: float = Field(description="0-1 similarity; higher is closer.")
    reason: str


class CheckProposalOut(BaseModel):
    """A draft check for an uncovered point — scaffolding, never auto-registered."""

    point: str
    suggested_id: str
    suggested_ref: str
    pillar: str
    scope: str
    severity: str
    requires: list[str]
    title: str
    rationale: str
    code_skeleton: str = Field(description="A ready-to-edit @check skeleton.")
    remediation_stub: str


class ChecklistAssessment(BaseModel):
    """The result of assessing one checklist point."""

    point: str
    status: str = Field(description="'covered', 'not_covered', or 'invalid'.")
    covered: bool
    ai_enabled: bool = Field(description="Whether AI-authored advisory was available.")
    matches: list[CheckMatchOut]
    proposal: CheckProposalOut | None = None
    advisory: str = Field(description="Assessment text — never a score.")
    next_steps: list[str]


# =============================================================================
# Batch — assess a whole user-supplied checklist and run the matched checks
# =============================================================================


class ChecklistBatchRequest(BaseModel):
    """A custom checklist to assess in bulk (and optionally evaluate over the KB).

    Supply **either** ``content`` (the raw text of an uploaded CSV / JSON /
    Markdown file, with ``filename`` to hint the format) **or** ``points`` (a
    ready list of statements). The batch is deterministic and offline by default;
    ``auth_session`` is used only to read a workspace that has no cached snapshot
    yet (the live fallback).
    """

    content: str | None = Field(
        default=None,
        description="Raw checklist file text (CSV, JSON, or Markdown/plain text).",
    )
    filename: str | None = Field(
        default=None,
        description="Original filename — used to detect the format (.csv/.json/.md).",
    )
    points: list[str] | None = Field(
        default=None,
        description="Checklist points as plain strings, instead of an uploaded file.",
    )
    workspace_ids: list[str] | None = Field(
        default=None,
        description="Workspaces to evaluate covered checks against; defaults to every cached workspace.",
    )
    run_checks: bool = Field(
        default=True,
        description="Evaluate covered automated checks over the KB; false = assess/dedup only.",
    )
    auth_session: str | None = Field(
        default=None,
        description="Sign-in session; used only for the live fallback when a workspace has no snapshot.",
    )

    @model_validator(mode="after")
    def _require_content_or_points(self) -> ChecklistBatchRequest:
        if not (self.content and self.content.strip()) and not self.points:
            raise ValueError("Provide either 'content' (a checklist file) or 'points'.")
        return self


class ChecklistEvaluationOut(BaseModel):
    """One workspace's verdict for a covered check, KB or live."""

    workspace: str
    source: str = Field(description="'kb' (offline snapshot), 'live', or 'none' (no data).")
    status: str = Field(description="Headline PASS/PARTIAL/FAIL/N/A/INFO for this workspace.")
    objects: int = Field(description="How many objects of the check's scope were evaluated.")
    counts: dict[str, int] = Field(description="Per-status counts across those objects.")
    evidence: str
    recommendation: str


class ChecklistBatchItemOut(BaseModel):
    """One checklist point: its coverage, closest checks, and any evaluations."""

    point: str
    hint_pillar: str | None = None
    hint_scope: str | None = None
    notes: str | None = None
    status: str = Field(description="'covered', 'not_covered', or 'invalid'.")
    covered: bool
    matches: list[CheckMatchOut]
    proposal: CheckProposalOut | None = None
    advisory: str
    next_steps: list[str]
    evaluated_check: str | None = Field(
        default=None, description="The check id that was run over the KB, when covered."
    )
    evaluations: list[ChecklistEvaluationOut] = Field(default_factory=list)


class ChecklistBatchSummary(BaseModel):
    """Roll-up of a batch run."""

    total_points: int
    covered: int
    not_covered: int
    invalid: int
    evaluated_points: int
    workspaces: int
    run_checks: bool
    verdicts: dict[str, int] = Field(description="Per-status counts across all evaluations.")


class ChecklistWorkspaceOut(BaseModel):
    """A workspace the batch evaluated against."""

    id: str
    name: str
    layer: str | None = None
    items: int | None = None
    pipelines: int | None = None


class ChecklistBatchResult(BaseModel):
    """The result of assessing and evaluating a whole checklist."""

    summary: ChecklistBatchSummary
    workspaces: list[ChecklistWorkspaceOut]
    items: list[ChecklistBatchItemOut]
