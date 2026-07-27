"""Read-only Microsoft Entra sign-in.

All flows request read-only Fabric scopes and run MSAL on a background thread,
returning an opaque session id the client polls. The Fabric access token stays
server-side and is never sent to the browser.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from ...schemas.auth import (
    DeviceFlowRequest,
    LoginRequest,
    SessionResponse,
    SessionStatus,
    SignInStatus,
)
from ...schemas.common import Message
from ...services import auth_service
from ...services import project as project_config
from ..deps import ProjectDep

router = APIRouter(tags=["authentication"])


def _project_auth(project_path: str) -> dict:
    """Auth defaults (tenant/client id, scopes) declared by the project file."""
    try:
        return project_config.load_project(project_path).auth
    except Exception:
        return {}


@router.post(
    "/login",
    response_model=SessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start interactive sign-in",
)
async def login(request: LoginRequest, project: ProjectDep) -> SessionResponse:
    """Open the Microsoft sign-in in a browser and return a session to poll.

    Returns immediately: MSAL runs on a background thread while the user
    completes sign-in.
    """
    result = auth_service.login_interactive(
        (request.email or "").strip(),
        request.tenant_id,
        request.client_id,
        _project_auth(project),
    )
    return SessionResponse(**result)


@router.post(
    "/login/azure-cli",
    response_model=SessionResponse,
    summary="Sign in using an existing 'az login'",
)
async def login_azure_cli() -> SessionResponse:
    """Reuse a local Azure CLI session — no app registration required."""
    result = auth_service.login_azcli()
    return SessionResponse(
        session=result["session"],
        message=result["message"],
        status=SignInStatus.DONE,
    )


@router.post(
    "/login/device-code",
    response_model=SessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start device-code sign-in",
)
async def login_device_code(request: DeviceFlowRequest) -> SessionResponse:
    """Begin a device-code flow, for headless environments."""
    result = auth_service.start_device_flow(
        request.tenant_id, request.client_id, request.scopes or None
    )
    return SessionResponse(**result)


@router.get(
    "/login/{session}",
    response_model=SessionStatus,
    summary="Poll a sign-in session",
)
async def poll(session: str) -> SessionStatus:
    """Whether a sign-in has completed.

    Always HTTP 200 — a failed *sign-in* is a valid answer about the session's
    state, not a failed request. Read ``status``.
    """
    return SessionStatus(**auth_service.poll(session))


@router.post("/logout", response_model=Message, summary="End a sign-in session")
async def logout(session: str) -> Message:
    """Discard the stored token for a session."""
    auth_service.logout(session)
    return Message(message="Signed out.")
