"""Centralised exception handling.

Every failure leaves the API as the same JSON shape
(:class:`~auditfast.schemas.common.ErrorResponse`) carrying a correlation id, so
the frontend needs exactly one error path and support can find the matching log
lines.

Domain exceptions are mapped here rather than caught in each route — a router
that has to remember to translate errors will eventually forget.
"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..clients.errors import ProviderError, WorkspaceAccessError
from ..config.logging import correlation_id, get_logger
from ..services.audit_service import AuditError
from ..services.auth_service import AuthError

logger = get_logger(__name__)


def _error(status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id.get(),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers for every failure the API can produce."""

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        """422 — the request body or query did not match the schema."""
        first = exc.errors()[0] if exc.errors() else {}
        location = " -> ".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = first.get("msg", "Invalid request.")
        detail = f"{location}: {message}" if location else message
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, detail, "validation_error")

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError):
        """Sign-in problems carry the status the auth service chose."""
        return _error(exc.status, exc.message, "authentication_error")

    @app.exception_handler(WorkspaceAccessError)
    async def workspace_access_error(request: Request, exc: WorkspaceAccessError):
        """A workspace the caller cannot read — 403, not a server fault."""
        return _error(status.HTTP_403_FORBIDDEN, str(exc), "workspace_access_denied")

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError):
        """Upstream Fabric problem — the caller can retry."""
        return _error(status.HTTP_502_BAD_GATEWAY, str(exc), "provider_error")

    @app.exception_handler(AuditError)
    async def audit_error(request: Request, exc: AuditError):
        """The run could not be started — bad mode, missing token, unknown check."""
        return _error(status.HTTP_400_BAD_REQUEST, str(exc), "audit_error")

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request: Request, exc: FileNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, str(exc), "not_found")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, str(exc.detail), "http_error")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        """Anything unforeseen: log the detail, return none of it.

        Internal messages can carry workspace ids, file paths, and token
        fragments, so the client gets a correlation id and nothing else.
        """
        logger.exception("unhandled error", extra={"path": request.url.path})
        return _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "An unexpected error occurred. Quote the correlation id when reporting it.",
            "internal_error",
        )
