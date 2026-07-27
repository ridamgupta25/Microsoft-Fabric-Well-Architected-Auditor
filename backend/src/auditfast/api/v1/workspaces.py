"""Discovering the workspaces available to audit."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ...schemas.audit import AuditMode, WorkspaceOut
from ...schemas.auth import DiagnosticsResponse
from ...services import audit_service
from ..deps import ProjectDep, resolve_live_token

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut], summary="List selectable workspaces")
async def list_workspaces(
    project: ProjectDep,
    mode: Annotated[AuditMode, Query(description="mock reads the fixture; live reads the project file.")] = AuditMode.MOCK,
) -> list[WorkspaceOut]:
    """Workspaces available for selection, before any sign-in.

    In live mode this can only echo what the project declares — enumerating a
    tenant needs a token. Use ``/workspaces/live`` once signed in.
    """
    rows = audit_service.list_workspaces(project, mode.value)
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
    token = resolve_live_token("live", session)
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
    token = resolve_live_token("live", session)
    return DiagnosticsResponse(**audit_service.diagnose(token))
