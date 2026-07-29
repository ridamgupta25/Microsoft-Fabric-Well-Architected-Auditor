"""Health and readiness endpoints.

Split deliberately: ``/live`` answers "is this process up" and must never touch a
dependency, while ``/ready`` answers "can it serve traffic". Wiring a
liveness probe to a dependency check causes restart storms when the dependency
is the thing that is down.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from ... import __version__
from ...core.check.registry import REGISTRY
from ...schemas.common import HealthResponse, HealthStatus
from ..deps import SettingsDep

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
)
async def health(settings: SettingsDep) -> HealthResponse:
    """Overall health, including how many checks the rule library loaded."""
    registered = len(REGISTRY)
    return HealthResponse(
        # An empty registry means the check modules failed to import, which
        # would silently produce audits that score nothing.
        status=HealthStatus.OK if registered else HealthStatus.DEGRADED,
        version=__version__,
        environment=settings.environment,
        checks_registered=registered,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/health/live", summary="Liveness probe", status_code=status.HTTP_204_NO_CONTENT)
async def live() -> Response:
    """The process is running. Checks nothing else, by design."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness probe")
async def ready(settings: SettingsDep) -> HealthResponse:
    """Ready to serve: the rule library is loaded and settings resolved."""
    return await health(settings)
