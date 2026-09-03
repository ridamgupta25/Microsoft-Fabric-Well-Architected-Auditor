"""Reading finished reports and downloading their file renderings."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from ...schemas.audit import AuditReport, JobStatus
from ..deps import OrganizationDep, RunnerDep, SettingsDep

router = APIRouter(prefix="/reports", tags=["reports"])

#: Download kind -> (path relative to the run directory, media type). A
#: whitelist, so a path fragment in the URL can never be used to read an
#: arbitrary file off the server.
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOWNLOADS: dict[str, tuple[str, str]] = {
    "markdown": ("audit-report.md", "text/markdown"),
    "excel": ("audit-report.xlsx", _XLSX),
    "advisory-markdown": ("advisory-report.md", "text/markdown"),
    "advisory-excel": ("advisory-report.xlsx", _XLSX),
    # Written by advisory judging, which runs after the audit on request.
    "advisory-judged-markdown": ("advisory-judged/advisory-report.md", "text/markdown"),
    "advisory-judged-excel": ("advisory-judged/advisory-report.xlsx", _XLSX),
}


@router.get(
    "/{audit_id}",
    response_model=AuditReport,
    summary="Get a finished report",
    responses={
        404: {"description": "No audit with that id."},
        409: {"description": "The audit has not finished yet."},
    },
)
async def get_report(
    audit_id: str,
    runner: RunnerDep,
    organization_id: OrganizationDep,
) -> AuditReport:
    """The scorecard for a completed audit.

    Returns 409 while the audit is still running — distinct from 404, so a
    polling client can tell "not ready yet" from "never existed".
    """
    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    # Return whatever results exist — including a partial report while the audit
    # is still running, or the results gathered before a failure — so a slow or
    # interrupted run still shows the workspaces it managed to evaluate.
    if job.report is not None:
        return AuditReport(**job.report)
    if job.status is JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Audit {audit_id} failed: {job.error}",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Audit {audit_id} is {job.status.value}; no report yet.",
    )


@router.get(
    "/{audit_id}/download/{kind}",
    summary="Download a report file",
    response_class=FileResponse,
    responses={404: {"description": "No such report file."}},
)
async def download_report(
    audit_id: str,
    kind: Annotated[str, PathParam(description="markdown | excel")],
    runner: RunnerDep,
    organization_id: OrganizationDep,
    settings: SettingsDep,
) -> FileResponse:
    """Download the Markdown or Excel rendering of a report.

    Resolved against **this audit's** own output directory. Each run writes its
    own timestamped folder, so a fixed path would hand back whichever audit
    finished most recently - which was already wrong when two ran, and is a
    plain 404 now.
    """
    entry = DOWNLOADS.get(kind)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown report kind {kind!r}. Expected one of: {', '.join(DOWNLOADS)}.",
        )
    relative, media_type = entry

    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    # Fall back to the output root only for a job recorded before per-run
    # directories existed.
    base = Path(job.out_dir) if job.out_dir else Path(settings.output_path)
    path = base / relative
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No {kind} file for audit {audit_id!r}. "
                "Advisory files appear only after advisory judging has run."
            ),
        )
    return FileResponse(path, media_type=media_type, filename=path.name)
