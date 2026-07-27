"""Audit history — what has been run, when, and how it scored."""
from __future__ import annotations

from fastapi import APIRouter

from ...schemas.audit import AuditJobSummary
from ...schemas.common import Page
from ..deps import OrganizationDep, PaginationDep, RunnerDep

router = APIRouter(prefix="/history", tags=["history"])


@router.get(
    "",
    response_model=Page[AuditJobSummary],
    summary="List past audits",
)
async def list_history(
    runner: RunnerDep,
    pagination: PaginationDep,
    organization_id: OrganizationDep,
) -> Page[AuditJobSummary]:
    """Past audits, newest first.

    Summaries only — report bodies are large and this endpoint is polled by
    dashboards. Fetch the full report from ``/reports/{audit_id}``.
    """
    limit, offset = pagination
    jobs, total = await runner.history(limit, offset, organization_id)
    return Page[AuditJobSummary](
        items=[
            AuditJobSummary(
                audit_id=job.id,
                status=job.status,
                submitted_at=job.submitted_at,
                finished_at=job.finished_at,
                duration_seconds=job.duration_seconds,
                mode=job.mode,
                project_name=job.project_name,
                overall=job.overall,
                workspaces=job.workspace_count,
            )
            for job in jobs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
