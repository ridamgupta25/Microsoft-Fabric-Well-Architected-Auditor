"""Checklist intake — assess a user-supplied best-practice point (or a whole file).

The "does the tool already cover this, and if not what would it take?" endpoints.
``/assess`` takes one point; ``/batch`` takes a whole uploaded checklist, dedups
every point, and — for points already covered by an automated check — runs that
check over the offline knowledge base (falling back to a live read only for a
workspace with no snapshot).

Both are deliberately **additive**: they never mutate the registry, so the
deterministic score and check count are untouched. ``/assess`` is fully
token-free; ``/batch`` is offline by default and only uses a token for the live
fallback.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ...schemas.checklist import (
    ChecklistAssessment,
    ChecklistBatchRequest,
    ChecklistBatchResult,
    ChecklistIntakeRequest,
)
from ...services import auth_service, checklist_batch, intake_service

router = APIRouter(prefix="/checklist", tags=["checklist"])


@router.post(
    "/assess",
    response_model=ChecklistAssessment,
    summary="Assess a checklist point",
)
async def assess(request: ChecklistIntakeRequest) -> ChecklistAssessment:
    """Check whether a point is already covered; if not, draft a proposal.

    Returns the closest existing checks (with a confidence score), and for an
    uncovered point a deterministic draft check plus the steps to promote it.
    """
    return ChecklistAssessment(**intake_service.assess_point(request.point))


@router.post(
    "/batch",
    response_model=ChecklistBatchResult,
    summary="Assess a whole checklist and run the matches over the knowledge base",
)
async def batch(request: ChecklistBatchRequest) -> ChecklistBatchResult:
    """Assess an uploaded checklist and evaluate the covered checks offline.

    For each point: dedup against the registry; when a point is already covered
    by an automated check, run that check over the on-disk knowledge base
    (token-free), falling back to a live read only for a workspace that has no
    cached snapshot and only when signed in. Uncovered points get a draft
    proposal. Never registers a check and never changes a score.
    """
    if request.points:
        points = [checklist_batch.ChecklistPoint(point=p) for p in request.points if p.strip()]
    else:
        try:
            points = checklist_batch.parse_checklist(request.content or "", filename=request.filename)
        except checklist_batch.ChecklistParseError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

    if not points:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No checklist points were found to assess.",
        )

    token = auth_service.token_for(request.auth_session) if request.auth_session else None
    result = checklist_batch.run_checklist(
        points,
        workspace_ids=request.workspace_ids,
        token=token,
        run_checks=request.run_checks,
    )
    return ChecklistBatchResult(**result)
