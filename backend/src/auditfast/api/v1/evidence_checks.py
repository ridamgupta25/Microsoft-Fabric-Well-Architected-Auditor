"""Evidence checks — score manual checklist points from uploaded documents.

``GET /evidence-checks/catalog`` lists what to upload for each manual check (the
intake form). ``POST /evidence-checks`` accepts the uploaded documents plus the
refs to assess and returns the drafted ledger + a rendered report. Review flow:
call once to get drafts, then call again with ``approved_refs`` to finalise — the
human-in-the-loop gate. Additive and token-free; never writes to Fabric.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ...ai.evidence_intake import catalog
from ...ai.evidence_intake.git_source import GitRepo
from ...ai.orchestrator.ai_config import AiConfig
from ...schemas.custom_checks import AiConfigIn
from ...schemas.evidence_checks import EvidenceChecksResult, EvidenceRequirementOut
from ...services import evidence_checks_service
from ...services.evidence_checks_service import UploadedFile

router = APIRouter(prefix="/evidence-checks", tags=["evidence-checks"])


def _to_ai_config(ai: AiConfigIn | None) -> AiConfig | None:
    if ai is None:
        return None
    return AiConfig(
        provider=ai.provider,
        api_key=ai.api_key.get_secret_value(),
        model=ai.model,
        base_url=ai.base_url,
        endpoint=ai.endpoint,
        deployment=ai.deployment,
    )


@router.get(
    "/catalog",
    response_model=list[EvidenceRequirementOut],
    summary="List the manual checks and what to upload for each",
)
async def get_catalog() -> list[EvidenceRequirementOut]:
    """The intake form: one entry per check with its ask-for and 0-3 rubric."""
    return [
        EvidenceRequirementOut(
            ref=req.ref,
            title=req.title,
            tier=req.tier,
            pillar=req.pillar,
            ask_for=req.ask_for,
            accepted_types=list(req.accepted_types),
            rubric=req.rubric,
        )
        for req in catalog.load().values()
    ]


@router.post(
    "",
    response_model=EvidenceChecksResult,
    summary="Draft evidence-check results from uploaded documents",
)
async def run(
    files: list[UploadFile] = File(default_factory=list, description="Supporting documents."),
    refs: str | None = Form(default=None, description="JSON array of check refs. Omit for all."),
    workspace_ids: str | None = Form(default=None, description="JSON array of workspace ids the evidence is for."),
    git_url: str | None = Form(default=None, description="GitHub or Azure DevOps repo URL to pull docs from."),
    git_pat: str | None = Form(default=None, description="Personal access token for the repo. Never stored."),
    git_branch: str | None = Form(default=None, description="Branch to read (defaults to the repo default)."),
    manual_scores: str | None = Form(default=None, description='JSON {ref: {"score": 0-3, "note": "..."}} human attestations.'),
    approved_refs: str | None = Form(default=None, description="JSON array of refs to approve."),
    ai: str | None = Form(default=None, description="JSON AiConfig for a per-request key."),
) -> EvidenceChecksResult:
    """Parse the uploads and/or Git repo, draft grounded results, and return the ledger."""
    ref_list = _parse_json_list(refs, "refs")
    workspace_list = _parse_json_list(workspace_ids, "workspace_ids")
    approved_list = _parse_json_list(approved_refs, "approved_refs")
    manual = _parse_json_object(manual_scores, "manual_scores")
    ai_config = _parse_ai(ai)
    git = GitRepo(url=git_url, token=git_pat or "", branch=git_branch or "") if (git_url and git_url.strip()) else None

    uploads: list[UploadedFile] = []
    for f in files:
        uploads.append(UploadedFile(name=f.filename or "upload", data=await f.read()))

    if not uploads and git is None and not manual and not approved_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide a document, a Git repo, or a manual score to assess.",
        )

    result = evidence_checks_service.run_evidence_checks(
        uploads,
        refs=ref_list,
        workspace_ids=workspace_list,
        git=git,
        manual_scores=manual,
        ai=ai_config,
        approved_refs=approved_list,
    )
    return EvidenceChecksResult(**result)


def _parse_json_list(raw: str | None, field: str) -> list[str] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a JSON array of strings.",
        ) from exc
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a JSON array of strings.",
        )
    return value


@router.post(
    "/save",
    summary="Remember approved manual checks so an audit can fold them in",
)
async def save(payload: dict) -> dict:
    """Persist approved manual-check rows, scoped to their workspaces.

    Body: ``{"rows": [...ledger rows...], "workspace_ids": [...]}``. The next audit
    for those workspaces shows a "Manual checks" section built from these rows.
    """
    rows = payload.get("rows")
    workspace_ids = payload.get("workspace_ids")
    if not isinstance(rows, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="rows must be a list of approved check rows.",
        )
    ws = workspace_ids if isinstance(workspace_ids, list) else None
    saved = evidence_checks_service.save_approved(rows, ws)
    return {"saved": saved}


def _parse_json_object(raw: str | None, field: str) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a JSON object.",
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a JSON object.",
        )
    return value


def _parse_ai(raw: str | None) -> AiConfig | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return _to_ai_config(AiConfigIn(**payload))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ai must be a JSON object matching AiConfig.",
        ) from exc
