"""Custom checks — turn plain-English checks into read-only audit results.

``POST /custom-checks`` runs a batch of user-written checks through the
custom-checks pipeline (guardrails -> router -> KB identify/update -> code-gen ->
runner) over the **offline** knowledge base, and returns the lifecycle ledger plus
a rendered report. It is additive and token-free: it never registers a check,
never changes the deterministic score, and never writes to Fabric.

Review flow: call once to get the ledger, then call again with
``approved_check_ids`` to finalise the report (the human-in-the-loop gate).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ...ai.orchestrator.ai_config import AiConfig
from ...schemas.custom_checks import (
    AiConfigIn,
    CustomChecksRequest,
    CustomChecksResult,
    VerifyAiRequest,
    VerifyAiResult,
)
from ...services import custom_checks_service

router = APIRouter(prefix="/custom-checks", tags=["custom-checks"])


def _to_ai_config(ai: AiConfigIn | None) -> AiConfig | None:
    """Convert the request schema to the internal config, unwrapping the secret."""
    if ai is None:
        return None
    return AiConfig(
        provider=ai.provider,
        api_key=ai.api_key.get_secret_value(),
        model=ai.model,
        base_url=ai.base_url,
        endpoint=ai.endpoint,
        deployment=ai.deployment,
    )


@router.post(
    "",
    response_model=CustomChecksResult,
    summary="Run plain-English custom checks over the knowledge base",
)
async def run(request: CustomChecksRequest) -> CustomChecksResult:
    """Run the custom-checks pipeline and return the ledger + report."""
    prompts = [p for p in request.prompts if p.strip()]
    if not prompts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No non-empty custom-check prompts were provided.",
        )
    result = custom_checks_service.run_custom_checks(
        prompts,
        workspace_ids=request.workspace_ids,
        approved_check_ids=request.approved_check_ids,
        ai=_to_ai_config(request.ai),
    )
    return CustomChecksResult(**result)


@router.post(
    "/verify-ai",
    response_model=VerifyAiResult,
    summary="Check a supplied AI key can reach a model (never echoes the key)",
)
async def verify_ai(request: VerifyAiRequest) -> VerifyAiResult:
    """Validate a user's AI key with a tiny completion; the key is never returned."""
    result = custom_checks_service.verify_ai(_to_ai_config(request.ai))
    return VerifyAiResult(**result)

