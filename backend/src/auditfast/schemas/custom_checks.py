"""Schemas for the custom-checks endpoint.

Mirrors :func:`auditfast.services.custom_checks_service.run_custom_checks`. The
request is a batch of plain-English checks; the response is the lifecycle ledger,
the ids still awaiting human review, and a rendered Markdown report. Offline and
token-free by construction.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CustomChecksRequest(BaseModel):
    """A batch of plain-English custom checks to run over the offline KB."""

    prompts: list[str] = Field(
        min_length=1,
        max_length=100,
        description="Plain-English checks, one per item.",
        examples=[["Ensure all semantic models have incremental refresh policies"]],
    )
    workspace_ids: list[str] | None = Field(
        default=None,
        description="Workspaces to evaluate against. Defaults to every crawled workspace.",
    )
    approved_check_ids: list[str] | None = Field(
        default=None,
        description="Check ids to mark approved before rendering the report (the HITL step).",
    )


class CustomChecksResult(BaseModel):
    """The ledger, pending-review ids, and rendered report for a batch."""

    prompts: int = Field(description="Distinct checks received (duplicates collapse).")
    workspaces: int = Field(description="Crawled workspaces evaluated against.")
    summary: dict[str, int] = Field(description="Count of checks per lifecycle status.")
    ledger: list[dict] = Field(description="One ledger row per check, full detail.")
    pending_review_ids: list[str] = Field(description="Checks still awaiting a decision.")
    report_markdown: str = Field(description="The rendered custom-checks report.")
