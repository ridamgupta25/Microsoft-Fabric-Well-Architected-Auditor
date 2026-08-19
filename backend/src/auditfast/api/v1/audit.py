"""Submitting and tracking audits.

The contract is **fire-and-poll**: ``POST /audit`` accepts the work and returns
an id immediately; the client polls ``GET /audit/{id}`` until it reaches a
terminal state. A tenant-wide audit can take minutes, so a synchronous endpoint
would tie up a worker and time out at any gateway in front of it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ...schemas.audit import (
    AuditAccepted,
    AuditAnswersRequest,
    AuditJobOut,
    AuditReport,
    AuditRequest,
    CheckResultOut,
    QuestionnaireItem,
    SingleCheckRequest,
)
from ...services import audit_service
from ..deps import OrganizationDep, RunnerDep, SettingsDep, resolve_token

router = APIRouter(prefix="/audit", tags=["audit"])


@router.post(
    "",
    response_model=AuditAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an audit",
    response_description="The audit was accepted and is running in the background.",
)
async def submit_audit(
    request: AuditRequest,
    runner: RunnerDep,
    settings: SettingsDep,
    organization_id: OrganizationDep,
) -> AuditAccepted:
    """Start an audit and return its id.

    A ``source="live"`` run requires a completed sign-in; the token is resolved
    here so an unauthenticated request fails immediately rather than as a dead
    background job. A ``source="kb"`` run replays saved snapshots and needs no
    token, so sign-in is skipped entirely.
    """
    token = None if request.source == "kb" else resolve_token(request.auth_session)
    project = str(settings.resolve(request.project) if request.project else settings.project_path)

    job = await runner.submit(
        project_path=project,
        pillars=request.pillars,
        workspaces=[w.model_dump(exclude_none=True) for w in request.workspaces],
        out_dir=str(settings.output_path),
        token=token,
        organization_id=organization_id,
        auth_session=request.auth_session,
        source=request.source,
        snapshots=request.snapshots,
    )
    return AuditAccepted(
        audit_id=job.id, status=job.status, submitted_at=job.submitted_at
    )


@router.get(
    "/{audit_id}",
    response_model=AuditJobOut,
    summary="Get audit status",
    responses={404: {"description": "No audit with that id."}},
)
async def get_audit(
    audit_id: str,
    runner: RunnerDep,
    organization_id: OrganizationDep,
) -> AuditJobOut:
    """Status of a submitted audit, including the report once it has finished."""
    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    return AuditJobOut(
        audit_id=job.id,
        status=job.status,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
        error=job.error,
        report=AuditReport(**job.report) if job.report else None,
        questionnaire=[QuestionnaireItem(**item) for item in job.questionnaire],
        answers_submitted=job.answers_submitted,
    )


@router.post(
    "/{audit_id}/answers",
    response_model=AuditJobOut,
    summary="Submit interactive questionnaire answers",
    responses={404: {"description": "No audit with that id."}},
)
async def submit_audit_answers(
    audit_id: str,
    request: AuditAnswersRequest,
    runner: RunnerDep,
    organization_id: OrganizationDep,
) -> AuditJobOut:
    """Record the reviewer's answers to a run's self-assessed checklist points.

    Each answer maps an interactive check id to a chosen option ``value`` (or
    ``"__skip__"`` to skip). Scoring folds the answers into the report as soon as
    the automated crawl finishes, so they can be submitted while the audit is
    still running.
    """
    job = await runner.submit_answers(audit_id, request.answers, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    return AuditJobOut(
        audit_id=job.id,
        status=job.status,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
        error=job.error,
        report=AuditReport(**job.report) if job.report else None,
        questionnaire=[QuestionnaireItem(**item) for item in job.questionnaire],
        answers_submitted=job.answers_submitted,
    )


@router.post(
    "/check",
    response_model=list[CheckResultOut],
    summary="Run a single check",
)
async def run_single_check(
    request: SingleCheckRequest,
    settings: SettingsDep,
) -> list[CheckResultOut]:
    """Run one check against one workspace, synchronously.

    Deliberately not a background job: a single check reads only the resources
    it declares, so it returns in well under a second and is the fastest way to
    iterate on a rule. Only addressable because checks carry metadata — there
    was previously no way to invoke one by id.
    """
    token = resolve_token(request.auth_session)
    project = str(settings.resolve(request.project) if request.project else settings.project_path)

    results = audit_service.run_check(
        check_id=request.check_id,
        workspace_id=request.workspace_id,
        project_path=project,
        layer=request.layer,
        token=token,
    )
    return [CheckResultOut(**row) for row in results]
