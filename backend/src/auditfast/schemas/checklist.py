"""Schemas for the checklist-intake endpoint.

Mirrors :func:`auditfast.services.intake_service.assess_point`. The assessment is
token-free (no tenant, no sign-in): it answers from the registered catalog plus
an optional model, so this contract never depends on a live Fabric read.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
