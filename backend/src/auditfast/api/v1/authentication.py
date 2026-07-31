"""Read-only Microsoft Entra sign-in.

All flows request read-only Fabric scopes and run MSAL on a background thread,
returning an opaque session id the client polls. The Fabric access token stays
server-side and is never sent to the browser.
"""
from __future__ import annotations

from fastapi import APIRouter, status

from ...schemas.auth import (
    AuthorizeRequest,
    AuthorizeResponse,
    CallbackRequest,
    DeviceFlowRequest,
    LoginConfig,
    LoginRequest,
    SessionResponse,
    SessionStatus,
    SignInStatus,
    UserProfile,
)
from ...schemas.common import Message
from ...services import auth_service
from ...services import project as project_config
from ..deps import ProjectDep, SettingsDep

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
    "/login/config",
    response_model=LoginConfig,
    summary="Which sign-in methods the server offers",
)
async def login_config(settings: SettingsDep) -> LoginConfig:
    """Lets the UI show redirect sign-in only when the server is configured for it."""
    return LoginConfig(redirect_enabled=settings.redirect_sign_in_enabled)


@router.post(
    "/login/authorize",
    response_model=AuthorizeResponse,
    summary="Begin redirect (Authorization Code) sign-in",
)
async def authorize(request: AuthorizeRequest, settings: SettingsDep) -> AuthorizeResponse:
    """Return the Microsoft URL to send the user's browser to.

    The standard hosted-web sign-in: the user authenticates on Microsoft's page
    in their own browser and is redirected to ``redirect_uri`` with a code, which
    :func:`callback` exchanges server-side. Needs an Entra app registration whose
    redirect URI matches ``redirect_uri``.
    """
    result = auth_service.start_auth_code_flow(
        request.redirect_uri,
        settings.auth_client_id,
        settings.auth_tenant_id,
        settings.auth_client_secret,
    )
    return AuthorizeResponse(**result)


@router.post(
    "/login/callback",
    response_model=SessionResponse,
    summary="Complete redirect sign-in",
)
async def callback(request: CallbackRequest, settings: SettingsDep) -> SessionResponse:
    """Exchange the redirect's authorization code for a token, server-side.

    Returns an opaque session id; the Fabric token never reaches the browser.
    """
    result = auth_service.complete_auth_code_flow(
        request.auth_response,
        settings.auth_client_id,
        settings.auth_tenant_id,
        settings.auth_client_secret,
    )
    return SessionResponse(
        session=result["session"], message=result["message"], status=SignInStatus.DONE
    )


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


@router.get(
    "/me",
    response_model=UserProfile,
    summary="The signed-in user's profile",
)
async def me(session: str) -> UserProfile:
    """Display name / username for a completed session. Never returns a token."""
    return UserProfile(**auth_service.me(session))


@router.post("/logout", response_model=Message, summary="End a sign-in session")
async def logout(session: str) -> Message:
    """Discard the stored token for a session."""
    auth_service.logout(session)
    return Message(message="Signed out.")
