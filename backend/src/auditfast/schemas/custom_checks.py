"""Schemas for the custom-checks endpoint.

Mirrors :func:`auditfast.services.custom_checks_service.run_custom_checks`. The
request is a batch of plain-English checks; the response is the lifecycle ledger,
the ids still awaiting human review, and a rendered Markdown report. Offline and
token-free by construction.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


class AiConfigIn(BaseModel):
    """A user-supplied AI key + model, used for one request only.

    ``api_key`` is a :class:`SecretStr` so it is masked in logs and reprs, and it
    is never included in any response.
    """

    provider: Literal["openai", "azure"]
    api_key: SecretStr = Field(description="The caller's own API key. Never stored or returned.")
    model: str = Field(default="", description="Model / deployment name.")
    base_url: str | None = Field(default=None, description="OpenAI-compatible gateway base URL.")
    endpoint: str | None = Field(default=None, description="Azure OpenAI endpoint.")
    deployment: str | None = Field(default=None, description="Azure deployment name.")


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
    ai: AiConfigIn | None = Field(
        default=None,
        description="Optional per-request AI key/model. When omitted, AI stays off (deterministic).",
    )
    auth_session: str | None = Field(
        default=None,
        description="Optional sign-in session. Only used when live fetch is enabled; ignored otherwise.",
    )


class VerifyAiResult(BaseModel):
    """Whether a supplied AI config can reach a model. Never echoes the key."""

    ok: bool
    message: str


class VerifyAiRequest(BaseModel):
    ai: AiConfigIn


class CustomChecksResult(BaseModel):
    """The ledger, pending-review ids, and rendered report for a batch."""

    prompts: int = Field(description="Distinct checks received (duplicates collapse).")
    workspaces: int = Field(description="Crawled workspaces evaluated against.")
    summary: dict[str, int] = Field(description="Count of checks per lifecycle status.")
    ledger: list[dict] = Field(description="One ledger row per check, full detail.")
    pending_review_ids: list[str] = Field(description="Checks still awaiting a decision.")
    report_markdown: str = Field(description="The rendered custom-checks report.")
