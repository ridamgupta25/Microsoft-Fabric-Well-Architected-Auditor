"""Checklist intake — assess a user-supplied best-practice point.

The "does the tool already cover this, and if not what would it take?" endpoint.
It is deliberately **token-free**: it answers from the registered catalog plus an
optional model, never contacting Fabric, so it always returns a result and can
never emit a "could not fetch" error. It also never mutates the registry, so the
deterministic score and check count are untouched.
"""
from __future__ import annotations

from fastapi import APIRouter

from ...schemas.checklist import ChecklistAssessment, ChecklistIntakeRequest
from ...services import intake_service

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
