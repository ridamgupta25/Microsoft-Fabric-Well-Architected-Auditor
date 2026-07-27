"""Schemas describing the check catalog.

These endpoints answer purely from registered metadata — no tenant, no sign-in,
no audit run — so they are the cheapest way to explore what the tool covers.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
