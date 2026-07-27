"""Remediation guidance for findings.

Today this serves the **deterministic** pre-written remediation already attached
to every failing check — no model, no inference, fully reproducible.

The route exists now so the contract is fixed before AI arrives: when the
optional AI layer is enabled, ``GET /recommendations/{audit_id}`` gains richer
prose for the same findings and the frontend does not change shape. See
:mod:`auditfast.ai` for the intended structure.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...core.enums import SEVERITY_RANK, Severity
from ..deps import OrganizationDep, RunnerDep, SettingsDep

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class Recommendation(BaseModel):
    """One actionable item derived from a finding."""

    check_id: str
    ref: str = Field(description="Audit-checklist reference.")
    title: str
    pillar: str
    severity: str
    workspace: str
    obj: str = ""
    evidence: str = Field(description="What was observed.")
    recommendation: str = Field(description="What to do about it.")
    source: str = Field(
        default="rule",
        description="'rule' for deterministic guidance; 'ai' once generated.",
    )


class RecommendationList(BaseModel):
    audit_id: str
    total: int
    ai_enabled: bool = Field(description="Whether AI-authored guidance is available.")
    items: list[Recommendation]


@router.get(
    "/{audit_id}",
    response_model=RecommendationList,
    summary="Recommendations for an audit",
)
async def recommendations(
    audit_id: str,
    runner: RunnerDep,
    settings: SettingsDep,
    organization_id: OrganizationDep,
) -> RecommendationList:
    """Every finding that has remediation guidance, most severe first."""
    job = await runner.get(audit_id, organization_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit found with id {audit_id!r}.",
        )
    if job.report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Audit {audit_id} is {job.status.value}; no recommendations yet.",
        )

    findings = [
        row for row in job.report.get("results", [])
        if row.get("recommendation") and row.get("status") in {"FAIL", "PARTIAL"}
    ]
    findings.sort(key=lambda r: SEVERITY_RANK.get(Severity(r["severity"]), 9))

    return RecommendationList(
        audit_id=audit_id,
        total=len(findings),
        ai_enabled=settings.ai_enabled,
        items=[
            Recommendation(
                check_id=row["check_id"], ref=row["ref"], title=row["title"],
                pillar=row["pillar"], severity=row["severity"],
                workspace=row["workspace"], obj=row.get("obj", ""),
                evidence=row["evidence"], recommendation=row["recommendation"],
            )
            for row in findings
        ],
    )
