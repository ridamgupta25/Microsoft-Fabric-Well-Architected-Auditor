"""Discovering the workspaces available to audit."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...schemas.audit import WorkspaceOut
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
