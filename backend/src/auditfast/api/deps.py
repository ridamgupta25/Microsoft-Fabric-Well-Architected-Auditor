"""Dependency injection providers.

Routers declare what they need; this module decides how it is built. Nothing is
constructed at import time, so a test can override any of these with
``app.dependency_overrides`` and get a genuinely isolated instance.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request, status

from ..config.settings import Settings, get_settings
from ..database.repositories.base import AuditJobRepository
from ..services import auth_service
from ..services.audit_runner import AuditRunner


def settings_dep() -> Settings:
    """Cached application settings."""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dep)]


def audit_runner(request: Request) -> AuditRunner:
    """The shared runner, created once per process in the app lifespan."""
    return request.app.state.audit_runner


RunnerDep = Annotated[AuditRunner, Depends(audit_runner)]


def audit_repository(request: Request) -> AuditJobRepository:
    return request.app.state.audit_repository


RepositoryDep = Annotated[AuditJobRepository, Depends(audit_repository)]


def project_path(
    settings: SettingsDep,
    project: Annotated[
        str | None,
        Query(description="Project YAML path. Defaults to the server's configured project."),
    ] = None,
) -> str:
    """Resolve which project definition a request refers to."""
    return str(settings.resolve(project) if project else settings.project_path)


ProjectDep = Annotated[str, Depends(project_path)]


def organization_id(
    x_organization_id: Annotated[
        str | None,
        Header(description="Tenant scope. Reserved for multi-organization deployments."),
    ] = None,
) -> str | None:
    """Multi-tenant scoping hook.

    Today an opt-in header; once Entra auth is enforced this becomes a claim on
    the validated token. Every repository query already filters on it, so
    turning multi-tenancy on is a change here and nowhere else.
    """
    return x_organization_id


OrganizationDep = Annotated[str | None, Depends(organization_id)]


def resolve_token(auth_session: str | None) -> str:
    """Exchange a sign-in session for a Fabric token.

    Every audit reads the live tenant, so every audit needs a token. Resolving
    it here — before any work is scheduled — means an unauthenticated request
    gets an immediate 401 rather than a background job that dies asynchronously.

    Raises the domain :class:`AuthError` rather than an ``HTTPException`` so the
    registered handler classifies it as ``authentication_error``; this module
    should not have to know HTTP status codes when the domain already says what
    went wrong.
    """
    token = auth_service.token_for(auth_session)
    if not token:
        raise auth_service.AuthError(
            "Not signed in — complete the Microsoft sign-in first.",
            status.HTTP_401_UNAUTHORIZED,
        )
    return token


def pagination(
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 25,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> tuple[int, int]:
    """Bounded paging — an unbounded list endpoint is a future outage."""
    return limit, offset


PaginationDep = Annotated[tuple[int, int], Depends(pagination)]
