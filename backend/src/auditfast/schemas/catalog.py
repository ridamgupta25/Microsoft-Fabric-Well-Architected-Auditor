"""Schemas describing the check catalog.

These endpoints answer purely from registered metadata — no tenant, no sign-in,
no audit run — so they are the cheapest way to explore what the tool covers.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CheckOptionOut(BaseModel):
    """One selectable answer for an interactive (self-assessed) check."""

    value: str = Field(description="Stable answer key sent back when chosen.")
    label: str = Field(description="Human-readable answer shown to the reviewer.")
    score: int | None = Field(
        default=None,
        description="0-3 the answer contributes; null for a not-applicable choice.",
    )
    guidance: str = Field(
        default="", description="Recommendation shown when the choice does not fully pass."
    )


class CheckSpecOut(BaseModel):
    """One check's definition, as registered."""

    id: str = Field(description="Stable check code, e.g. 'PL-RETRY'.")
    ref: str = Field(description="Audit-checklist reference; also the remediation key.")
    title: str
    pillar: str
    scope: str = Field(description="The kind of object this inspects.")
    severity: str = Field(description="Severity reported when the check does not pass.")
    layers: list[str] = Field(description="Layer roles it applies to; '*' means all.")
    requires: list[str] = Field(description="Data the provider must fetch for it.")
    weight: float = Field(description="Relative influence on the roll-up.")
    required: bool = Field(
        default=True,
        description="Whether the point is expected in every project (True) or is "
        "situational / optional (False).",
    )
    manual: bool = Field(
        default=False,
        description="True for attestation-only checks the engine never runs.",
    )
    automation: str = Field(
        default="automated",
        description="How the verdict is reached: 'automated' (runs now), "
        "'roadmap' (automatable once the provider integrates the needed Fabric "
        "API), 'interactive' (self-assessed via a scored question during the "
        "audit), or 'manual' (only a human can attest).",
    )
    interactive: bool = Field(
        default=False,
        description="True when the reviewer answers this via a scored question.",
    )
    question: str = Field(
        default="", description="The question shown for an interactive check."
    )
    options: list[CheckOptionOut] = Field(
        default_factory=list,
        description="The scored answers for an interactive check; empty otherwise.",
    )
    description: str = ""


class PillarOut(BaseModel):
    name: str
    checks: int = Field(description="How many checks currently roll up into it.")


class LayerOut(BaseModel):
    name: str
    checks: int


class CatalogSummary(BaseModel):
    """Coverage at a glance: how many checks, and where they sit."""

    total: int
    by_pillar: dict[str, int]
    by_scope: dict[str, int]
