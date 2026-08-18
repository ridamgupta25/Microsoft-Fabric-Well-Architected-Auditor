"""Schemas for requesting audits and reading their results."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .catalog import CheckOptionOut


class JobStatus(str, Enum):
    """Lifecycle of a submitted audit."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkspaceSelection(BaseModel):
    """One workspace to audit, and the layer role it plays."""

    id: str = Field(description="Fabric workspace GUID.")
    role: str | None = Field(
        default=None,
        description="Layer role: Data Prep | Data Storage | Data Logs | "
                    "Data Operations | Reporting / Semantic | Mixed.",
    )
    name: str | None = None
    #: Cross-workspace grouping. Both are optional and purely additive: an
    #: isolated workspace sends neither, so its audit is byte-for-byte unchanged.
    group: str | None = Field(
        default=None,
        description="Project group name this workspace belongs to (cross-workspace). "
                    "Null for an isolated workspace.",
    )
    environment_level: int | None = Field(
        default=None, ge=1, le=10,
        description="Environment position within its group: 1 = dev / least "
                    "critical, 10 = prod / most critical. Null when ungrouped.",
    )


class AuditRequest(BaseModel):
    """A request to run an audit.

    ``source`` chooses where the workspace data comes from: ``"live"`` crawls the
    tenant (requires a completed sign-in), while ``"kb"`` replays saved snapshots
    — the on-disk archive plus any uploaded ``snapshots`` — with no token.
    """

    project: str | None = Field(
        default=None, description="Project YAML path. Defaults to the server's project."
    )
    pillars: list[str] = Field(
        default_factory=list,
        description="Restrict scoring to these pillars. Empty means all of them.",
    )
    workspaces: list[WorkspaceSelection] = Field(
        default_factory=list,
        description="Workspaces to audit. Empty means whatever the project declares.",
    )
    auth_session: str | None = Field(
        default=None,
        description="Completed sign-in session id. Required for source='live'; "
                    "ignored for source='kb'.",
    )
    source: Literal["live", "kb"] = Field(
        default="live",
        description="'live' crawls the tenant; 'kb' replays saved/uploaded snapshots.",
    )
    snapshots: list[dict[str, Any]] = Field(
        default_factory=list,
        description="For source='kb': uploaded workspace snapshots to audit "
                    "alongside (or instead of) the saved archive.",
    )
    weight_by_environment: bool = Field(
        default=False,
        description="Opt-in cross-workspace scoring: weight each workspace's checks "
                    "by its environment level (1..10) in the overall/pillar/layer "
                    "roll-ups. Off = today's unweighted mean. Per-workspace scores "
                    "are unchanged either way.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "pillars": ["Security", "Reliability"],
                "workspaces": [{"id": "ws-prep-01", "role": "Data Prep"}],
                "auth_session": "3f2a9c14",
                "source": "live",
            }
        }
    }


class AuditAccepted(BaseModel):
    """Returned immediately when an audit is submitted."""

    audit_id: str
    status: JobStatus
    submitted_at: datetime

    model_config = {
        "json_schema_extra": {
            "example": {
                "audit_id": "a3f19c2e8b7d4a10",
                "status": "running",
                "submitted_at": "2026-07-27T10:15:00Z",
            }
        }
    }


class CheckResultOut(BaseModel):
    """One verdict about one implemented object."""

    check_id: str
    ref: str
    title: str
    pillar: str
    status: str
    score: int | None = Field(description="0-3, or null when informational.")
    coverage: float | None = Field(description="0..1 for proportional checks.")
    evidence: str
    recommendation: str
    severity: str
    workspace: str
    workspace_role: str
    layer: str
    obj: str = Field(description="Object name; empty for workspace-level checks.")
    scope: str
    weight: float
    scored: bool
    common: bool = Field(description="True for checks that apply to every project.")
    validated: bool = Field(
        default=False,
        description="True once the check has completed Phase 1 validation; False "
        "while it is still pending validation for the next phase.",
    )


class WorkspaceError(BaseModel):
    """A workspace that could not be read at all."""

    workspace: str
    role: str
    message: str
    recommendation: str


class PillarScore(BaseModel):
    pct: float | None = Field(description="Null means not assessed, which is not zero.")
    count: int


class WorkspaceScore(BaseModel):
    role: str
    layer: str
    pct: float | None
    count: int
    by_pillar: dict[str, float | None]


class GroupMember(BaseModel):
    """One workspace inside a project group, with its environment position."""

    id: str
    name: str | None = None
    role: str | None = None
    environment_level: int | None = None


class WorkspaceGroup(BaseModel):
    """A project group spanning several workspaces (cross-workspace)."""

    name: str
    workspaces: list[GroupMember] = Field(default_factory=list)


class KBProvenance(BaseModel):
    """Where a run's data came from — live crawl, cache, or saved KB replay."""

    source: Literal["live", "kb"] = "live"
    served_from_cache: bool = False
    refreshing: bool = Field(
        default=False,
        description="A cached live run is being refreshed by a background crawl.",
    )


class AuditReport(BaseModel):
    """The full result of a completed audit."""

    audit_id: str | None = None
    partial: bool = Field(
        default=False,
        description="True while the audit is still running — results so far only.",
    )
    project_name: str
    overall: float | None
    by_pillar: dict[str, PillarScore]
    by_workspace: dict[str, WorkspaceScore]
    by_layer: dict[str, PillarScore] = Field(
        default_factory=dict, description="Score per architecture layer."
    )
    matrix: dict[str, dict[str, float | None]] = Field(
        default_factory=dict,
        description="Pillar x layer scores — how each layer fares on each pillar.",
    )
    layers: list[str] = Field(default_factory=list)
    counts: dict[str, int]
    total_scored: int
    results: list[CheckResultOut]
    groups: list[WorkspaceGroup] = Field(
        default_factory=list,
        description="Project workspace groups (cross-workspace). Empty for an "
                    "isolated-only run; display metadata only, never affects scoring.",
    )
    weighted_by_environment: bool = Field(
        default=False,
        description="True when the overall/pillar/layer roll-ups were weighted by "
                    "environment level. Per-workspace scores are unaffected.",
    )
    errors: list[WorkspaceError] = Field(default_factory=list)
    files: dict[str, str] = Field(
        default_factory=dict, description="Generated report file names."
    )
    kb: KBProvenance = Field(
        default_factory=KBProvenance,
        description="Provenance of the run's data (live, cache, or saved KB).",
    )
class QuestionnaireItem(BaseModel):
    """One interactive, self-assessed checklist point for a run."""

    id: str
    ref: str
    title: str
    pillar: str
    scope: str
    severity: str
    layers: list[str]
    question: str = Field(description="The question shown to the reviewer.")
    options: list[CheckOptionOut] = Field(description="The scored answers to choose from.")
    required: bool = True
    automation: str = "interactive"
    description: str = ""


class AuditAnswersRequest(BaseModel):
    """The reviewer's answers to a run's interactive questionnaire.

    Maps each interactive check id to the chosen option ``value``. Use
    ``"__skip__"`` (or simply omit a check) to skip it — skipped points are
    recorded as N/A and never scored.
    """

    answers: dict[str, str] = Field(
        default_factory=dict,
        description="Interactive check id -> chosen option value (or '__skip__').",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"answers": {"Q-SEC-LABELS": "enforced", "Q-OPS-DR": "__skip__"}}
        }
    }


class AuditJobOut(BaseModel):
    """Status of a submitted audit, with the report once it has finished."""

    audit_id: str
    status: JobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    error: str | None = None
    report: AuditReport | None = None
    questionnaire: list[QuestionnaireItem] = Field(
        default_factory=list,
        description="Interactive, self-assessed checklist points to answer while the "
        "automated audit runs. Grouped in the UI by pillar and layer.",
    )
    answers_submitted: bool = Field(
        default=False,
        description="True once the reviewer's questionnaire answers have been recorded.",
    )


class AuditJobSummary(BaseModel):
    """One row in the audit history — no report body."""

    audit_id: str
    status: JobStatus
    submitted_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    project_name: str | None = None
    overall: float | None = None
    workspaces: int = 0


class SingleCheckRequest(BaseModel):
    """Run exactly one check against one workspace — the fast feedback loop."""

    check_id: str
    workspace_id: str
    project: str | None = None
    layer: str | None = None
    auth_session: str | None = Field(
        default=None,
        description="Completed sign-in session id. Required — omitting it, or "
                    "supplying an expired one, fails with 401.",
    )


class WorkspaceOut(BaseModel):
    """A workspace available for selection."""

    id: str
    name: str
    role: str = ""
    layer: str = ""
    items: int | None = None
    pipelines: int | None = None
    #: Set for saved-KB workspaces: whether the archived crawl was complete, and
    #: when it was captured. ``None`` for live/declared workspaces.
    complete: bool | None = None
    captured_at: str | None = None


class KBUploadResponse(BaseModel):
    """The result of validating one uploaded knowledge-base file."""

    workspace: WorkspaceOut = Field(description="Display metadata for the picker.")
    snapshot: dict[str, Any] = Field(
        description="The normalized snapshot to resubmit with a source='kb' audit."
    )
