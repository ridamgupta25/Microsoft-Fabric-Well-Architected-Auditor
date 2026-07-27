"""HTTP middleware.

Correlation ids and access logging. Both are prerequisites for operating a
multi-tenant service: without them, one organization's failing audit is
indistinguishable from another's in the log stream.
"""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..config.logging import correlation_id, get_logger

logger = get_logger("auditfast.access")

CORRELATION_HEADER = "X-Correlation-Id"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign every request an id, echo it back, and bind it to the log context.

    An inbound ``X-Correlation-Id`` is honoured so a trace started in the
    frontend (or an upstream gateway) continues through the backend.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(CORRELATION_HEADER)
        request_id = incoming or uuid.uuid4().hex[:8]
        token = correlation_id.set(request_id)
        request.state.correlation_id = request_id
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers[CORRELATION_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log one structured line per request, with its duration."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers turn this into a response; log the timing
            # here so failed requests still appear in the access log.
            logger.warning(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
