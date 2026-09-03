"""Submitting and tracking audits.

The contract is **fire-and-poll**: ``POST /audit`` accepts the work and returns
an id immediately; the client polls ``GET /audit/{id}`` until it reaches a
terminal state. A tenant-wide audit can take minutes, so a synchronous endpoint
would tie up a worker and time out at any gateway in front of it.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ...schemas.audit import (
    AdvisoryRunOut,
    AdvisoryRunRequest,
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
        weight_by_environment=request.weight_by_environment,
        external_checks_csv=request.external_checks_csv,
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
        advisory_status=job.advisory_status,
    )


@router.post(
    "/{audit_id}/advisory",
    response_model=AdvisoryRunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run advisory judging for a finished audit",
    responses={
        404: {"description": "No audit with that id."},
        409: {"description": "The audit has not finished yet."},
    },
)
async def run_advisory(
    audit_id: str,
    request: AdvisoryRunRequest,
    runner: RunnerDep,
    settings: SettingsDep,
    organization_id: OrganizationDep,
) -> AdvisoryRunOut:
    """Judge this audit's advisory checks with a model, in the background.

    Deliberately a separate call rather than part of the audit: judging costs
    tokens against a key the reviewer supplies, so it is something they choose
    to do once they have seen the deterministic report - not a side effect of
    running one.

    The key is used for this run and discarded. It is never persisted and never
    returned.
    """
    from ...ai.orchestrator import Credentials, is_enabled
    from ...schemas.audit import JobStatus

    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    if job.status is not JobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Audit {audit_id!r} is {job.status.value}. Advisory judging reads the "
                "jobs an audit writes when it finishes, so it can only run after one."
            ),
        )

    credentials = None
    if request.api_key:
        credentials = Credentials(
            provider=request.provider,
            api_key=request.api_key,
            endpoint=request.endpoint,
            deployment=request.deployment,
            base_url=request.base_url,
            model=request.model,
        )
        if not credentials.is_usable():
            needs = (
                "base_url and model" if request.provider == "openai"
                else "endpoint and deployment"
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"provider '{request.provider}' also needs {needs}.",
            )
    elif not is_enabled():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No model is configured on the server, so supply an api_key (and the "
                "matching endpoint/deployment or base_url/model) with this request."
            ),
        )

    started = await runner.submit_advisory(
        audit_id,
        organization_id,
        out_dir=str(settings.output_path),
        credentials=credentials,
    )
    if started is None:  # pragma: no cover - the 404 above already covered this
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    return AdvisoryRunOut(
        audit_id=started.id,
        advisory_status=started.advisory_status,
        advisory_error=started.advisory_error,
        summary=started.advisory_summary,
    )


@router.get(
    "/{audit_id}/advisory",
    response_model=AdvisoryRunOut,
    summary="Get advisory judging status",
    responses={404: {"description": "No audit with that id."}},
)
async def get_advisory(
    audit_id: str,
    runner: RunnerDep,
    organization_id: OrganizationDep,
) -> AdvisoryRunOut:
    """Poll advisory judging; the summary appears once it has finished."""
    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    return AdvisoryRunOut(
        audit_id=job.id,
        advisory_status=job.advisory_status,
        advisory_error=job.advisory_error,
        summary=job.advisory_summary,
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
