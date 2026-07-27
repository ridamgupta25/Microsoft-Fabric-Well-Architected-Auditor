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

#: Download kind -> (filename, media type). A whitelist, so a path fragment in
#: the URL can never be used to read an arbitrary file off the server.
DOWNLOADS: dict[str, tuple[str, str]] = {
    "markdown": ("audit-report.md", "text/markdown"),
    "excel": (
        "audit-report.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
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
    if job.status is JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Audit {audit_id} failed: {job.error}",
        )
    if job.report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Audit {audit_id} is {job.status.value}; no report yet.",
        )
    return AuditReport(**job.report)


@router.get(
    "/{audit_id}/download/{kind}",
    summary="Download a report file",
    response_class=FileResponse,
    responses={404: {"description": "No such report file."}},
)
async def download_report(
    audit_id: str,
    kind: Annotated[str, PathParam(description="markdown | excel")],
    settings: SettingsDep,
) -> FileResponse:
    """Download the Markdown or Excel rendering of a report."""
    entry = DOWNLOADS.get(kind)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown report kind {kind!r}. Expected one of: {', '.join(DOWNLOADS)}.",
        )
    filename, media_type = entry
    path = Path(settings.output_path) / filename
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report file has been generated yet. Run an audit first.",
        )
    return FileResponse(path, media_type=media_type, filename=filename)
