"""Discovering the workspaces available to audit."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from ...schemas.audit import KBUploadResponse, WorkspaceOut
from ...schemas.auth import DiagnosticsResponse
from ...services import audit_service
from ..deps import ProjectDep, resolve_token

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut], summary="List declared workspaces")
async def list_workspaces(project: ProjectDep) -> list[WorkspaceOut]:
    """Workspaces declared by the project file, before any sign-in.

    Contents cannot be enumerated without a token, so this only echoes what the
    project declares. Use ``/workspaces/live`` once signed in to see the real
    tenant.
    """
    rows = audit_service.list_workspaces(project)
    return [WorkspaceOut(**row) for row in rows]


@router.get(
    "/live",
    response_model=list[WorkspaceOut],
    summary="List every workspace the signed-in user can see",
)
async def list_live_workspaces(
    session: Annotated[str, Query(description="Completed sign-in session id.")],
) -> list[WorkspaceOut]:
    """Enumerate the tenant, regardless of what the project file declares."""
    token = resolve_token(session)
    return [WorkspaceOut(**row) for row in audit_service.list_live_workspaces(token)]


@router.get(
    "/kb",
    response_model=list[WorkspaceOut],
    summary="List workspaces saved in the knowledge-base archive",
)
async def list_kb_workspaces() -> list[WorkspaceOut]:
    """Workspaces available to replay from the saved KB — no sign-in needed.

    Each was crawled to disk on an earlier run. Audit one with ``source="kb"`` to
    re-score it against the current check library without touching the tenant.
    """
    return [WorkspaceOut(**row) for row in audit_service.list_kb_workspaces()]


@router.post(
    "/kb/upload",
    response_model=KBUploadResponse,
    summary="Validate an uploaded knowledge-base file",
    responses={400: {"description": "The file is not a valid workspace snapshot."}},
)
async def upload_kb_snapshot(payload: dict[str, Any]) -> KBUploadResponse:
    """Validate one uploaded ``workspace.json`` and return it normalized.

    Accepts a snapshot from the KB archive (context at the top level) or the TTL
    cache (wrapped under ``context``). The client keeps the returned snapshot and
    submits it with a ``source="kb"`` audit, so the run reads exactly what was
    validated here — no snapshot is written to the server.
    """
    try:
        result = audit_service.validate_snapshot(payload)
    except audit_service.AuditError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return KBUploadResponse(**result)


@router.get(
    "/diagnostics",
    response_model=DiagnosticsResponse,
    summary="Probe what the token can read",
)
async def diagnostics(
    session: Annotated[str, Query(description="Completed sign-in session id.")],
) -> DiagnosticsResponse:
    """Per-resource HTTP status codes for the first few workspaces.

    Use when a live audit returns less than expected: it distinguishes a bad
    token from missing permissions on a specific sub-resource.
    """
    token = resolve_token(session)
    return DiagnosticsResponse(**audit_service.diagnose(token))
