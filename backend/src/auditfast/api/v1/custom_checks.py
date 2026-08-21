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

from ...schemas.custom_checks import CustomChecksRequest, CustomChecksResult
from ...services import custom_checks_service

router = APIRouter(prefix="/custom-checks", tags=["custom-checks"])


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
    )
    return CustomChecksResult(**result)
