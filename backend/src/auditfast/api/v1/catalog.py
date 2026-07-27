"""The check catalog — what this tool can assess.

Every endpoint answers from registered metadata alone: no tenant, no sign-in, no
audit run. Useful for populating frontend filters, and the quickest way to
review rule coverage.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from ...schemas.catalog import CatalogSummary, CheckSpecOut, LayerOut, PillarOut
from ...services import catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/pillars", response_model=list[PillarOut], summary="List pillars")
async def pillars() -> list[PillarOut]:
    """The scored Well-Architected pillars and their check counts."""
    return [PillarOut(**row) for row in catalog_service.list_pillars()]


@router.get("/layers", response_model=list[LayerOut], summary="List layer roles")
async def layers() -> list[LayerOut]:
    """The architecture layers a workspace can be tagged with."""
    return [LayerOut(**row) for row in catalog_service.list_layers()]


@router.get("/checks", response_model=list[CheckSpecOut], summary="List checks")
async def checks(
    pillar: Annotated[str | None, Query(description="Filter by pillar name.")] = None,
    layer: Annotated[str | None, Query(description="Filter by layer role.")] = None,
    scope: Annotated[str | None, Query(description="Filter by object kind.")] = None,
) -> list[CheckSpecOut]:
    """The rule library, optionally filtered."""
    return [CheckSpecOut(**row) for row in catalog_service.list_checks(pillar, layer, scope)]


@router.get("/checks/{check_id}", response_model=CheckSpecOut, summary="Describe one check")
async def check_detail(check_id: str) -> CheckSpecOut:
    """Full metadata for a single check."""
    spec = catalog_service.describe_check(check_id)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No check registered with id {check_id!r}.",
        )
    return CheckSpecOut(**spec)


@router.get("/summary", response_model=CatalogSummary, summary="Coverage summary")
async def summary() -> CatalogSummary:
    """Counts by pillar and object kind — the coverage-at-a-glance view."""
    return CatalogSummary(**catalog_service.catalog_summary())
