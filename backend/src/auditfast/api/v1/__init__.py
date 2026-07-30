"""Version 1 of the REST API.

Every router is mounted under ``/api/v1``. Versioning the prefix means a future
breaking change ships as ``/api/v2`` alongside this one, so existing clients
keep working while they migrate.
"""
from fastapi import APIRouter

from . import (
    audit,
    authentication,
    catalog,
    checklist,
    health,
    history,
    recommendations,
    reports,
    workspaces,
)

router = APIRouter()

# Order here is the order they appear in the OpenAPI docs.
router.include_router(health.router)
router.include_router(authentication.router)
router.include_router(workspaces.router)
router.include_router(catalog.router)
router.include_router(audit.router)
router.include_router(reports.router)
router.include_router(recommendations.router)
router.include_router(checklist.router)
router.include_router(history.router)

__all__ = ["router"]
