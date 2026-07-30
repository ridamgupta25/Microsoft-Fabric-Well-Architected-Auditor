"""Core data models.

Four types carry the whole domain:

* :class:`WorkspaceContext` — a normalized, read-only snapshot of one Fabric
  workspace. Every provider emits this, which is why checks run unmodified
  against live data or an offline fixture.
* :class:`CheckContext` — what a single check is handed: the workspace, the
  object under inspection, and the project settings.
* :class:`CheckSpec` — a check's *metadata*, known before it ever runs. This is
  what makes the catalog listable and lets a single check be invoked by id.
* :class:`CheckResult` — one verdict.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from .enums import ITEM_TYPE_SCOPE, Automation, Layer, Pillar, Resource, Scope, Severity, Status

#: Highest score any single check can award.
MAX_SCORE = 3


@dataclass(frozen=True, slots=True)
class Item:
    """One Fabric item inside a workspace."""

    id: str = ""
    type: str = ""
    display_name: str = ""
    sensitivity_label: str | None = None
    last_run_utc: str | None = None

    @classmethod
    def from_api(cls, raw: dict) -> Item:
        label = raw.get("sensitivityLabel")
        if isinstance(label, dict):
            label = label.get("labelId")
        return cls(
            id=raw.get("id") or "",
            type=raw.get("type") or "",
            display_name=raw.get("displayName") or "",
            sensitivity_label=label,
            last_run_utc=raw.get("lastRunUtc") or raw.get("lastUpdatedDate"),
        )


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    """One principal granted a role on a workspace."""

    principal_type: str = ""
    display_name: str = ""
    role: str = ""
    principal_id: str = ""

    @classmethod
    def from_api(cls, raw: dict) -> RoleAssignment:
        principal = raw.get("principal")
        if isinstance(principal, dict):  # live Fabric shape
            return cls(
                principal_type=principal.get("type") or "",
                display_name=principal.get("displayName") or "",
                role=raw.get("role") or "",
                principal_id=principal.get("id") or "",
            )
        return cls(  # flat fixture shape
            principal_type=raw.get("principalType") or "",
            display_name=raw.get("displayName") or "",
            role=raw.get("role") or "",
            principal_id=raw.get("principalId") or "",
        )

    @property
    def is_guest(self) -> bool:
        return self.principal_type == "Guest" or "#EXT#" in self.display_name

    @property
    def is_individual(self) -> bool:
        return self.principal_type == "User"


@dataclass(slots=True)
class WorkspaceContext:
    """A normalized snapshot of one workspace — the contract every provider meets.

    Fields a provider did not fetch stay at their defaults, so a check that
    forgets to declare a ``requires`` sees empty data rather than stale data.
    """

    id: str
    display_name: str = ""
    layer: Layer = Layer.MIXED
    capacity_id: str | None = None
    git_connected: bool = False
    deployment_pipeline: bool = False
    role_assignments: list[RoleAssignment] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    pipelines: dict[str, dict] = field(default_factory=dict)
    notebooks: dict[str, dict] = field(default_factory=dict)
    tables: dict[str, dict] = field(default_factory=dict)
    shortcuts: dict[str, list] = field(default_factory=dict)
    semantic_models: dict[str, dict] = field(default_factory=dict)
    #: The Git provider connection details (provider, org, repo, branch, dir) when
    #: the workspace is Git-connected — the authoritative source for item code.
    git_details: dict = field(default_factory=dict)
    #: Resources the provider tried and failed to read. A check whose data lands
    #: here must report N/A rather than failing: "we could not determine this" is
    #: not the same finding as "this is not configured".
    unavailable: set[Resource] = field(default_factory=set)

    def has(self, resource: Resource) -> bool:
        """True when ``resource`` was read successfully and can be judged."""
        return resource not in self.unavailable

    @property
    def name(self) -> str:
        """Display name, falling back to the id so reports never show a blank."""
        return self.display_name or self.id

    def item_types(self) -> set[str]:
        """Every Fabric item type present, including pipelines fetched separately."""
        types = {i.type for i in self.items if i.type}
        if self.pipelines:
            types.add("DataPipeline")
        return types

    def objects(self, scope: Scope) -> Iterator[tuple[str, Any]]:
        """Yield ``(name, object)`` for every object of ``scope`` in this workspace.

        This is the hook the engine dispatches on. Pipelines come from the
        definitions map (their payload is the parsed JSON definition); everything
        else comes from the item inventory.
        """
        if scope is Scope.WORKSPACE:
            yield (self.name, self)
            return
        if scope is Scope.PIPELINE:
            yield from self.pipelines.items()
            return
        if scope is Scope.NOTEBOOK:
            yield from self.notebooks.items()
            return
        for item in self.items:
            if ITEM_TYPE_SCOPE.get(item.type) is scope:
                yield (item.display_name or item.id, item)

    # -- persistence (the knowledge-base cache) -------------------------------
    def to_dict(self) -> dict:
        """A JSON-safe snapshot of this workspace, for the on-disk KB cache."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "layer": self.layer.value,
            "capacity_id": self.capacity_id,
            "git_connected": self.git_connected,
            "deployment_pipeline": self.deployment_pipeline,
            "role_assignments": [asdict(r) for r in self.role_assignments],
            "items": [asdict(i) for i in self.items],
            "pipelines": self.pipelines,
            "notebooks": self.notebooks,
            "tables": self.tables,
            "shortcuts": self.shortcuts,
            "semantic_models": self.semantic_models,
            "git_details": self.git_details,
            "unavailable": sorted(r.value for r in self.unavailable),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkspaceContext:
        """Rebuild a context from :meth:`to_dict` output (the KB cache)."""
        return cls(
            id=data["id"],
            display_name=data.get("display_name", ""),
            layer=Layer(data["layer"]) if data.get("layer") else Layer.MIXED,
            capacity_id=data.get("capacity_id"),
            git_connected=bool(data.get("git_connected", False)),
            deployment_pipeline=bool(data.get("deployment_pipeline", False)),
            role_assignments=[RoleAssignment(**r) for r in data.get("role_assignments", [])],
            items=[Item(**i) for i in data.get("items", [])],
            pipelines=dict(data.get("pipelines", {})),
            notebooks=dict(data.get("notebooks", {})),
            tables=dict(data.get("tables", {})),
            shortcuts=dict(data.get("shortcuts", {})),
            semantic_models=dict(data.get("semantic_models", {})),
            git_details=dict(data.get("git_details", {})),
            unavailable={Resource(v) for v in data.get("unavailable", [])},
        )


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Everything one check invocation is handed.

    A workspace-scoped check reads ``workspace`` and ignores ``obj``. An
    object-scoped check reads ``obj`` (the pipeline definition, the item) and
    ``obj_name``, and still has the workspace for context.
    """

    workspace: WorkspaceContext
    settings: dict
    obj_name: str = ""
    obj: Any = None

    def setting(self, key: str, default: Any = None) -> Any:
        """Read a tunable from the project YAML's ``project:`` block."""
        return self.settings.get(key, default)


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """A check's metadata — everything knowable about it *without running it*.

    This is what makes the catalog listable, lets ``run_check`` invoke one check
    by id, and lets the engine select checks (and therefore decide what data to
    fetch) before any work happens.
    """

    id: str
    ref: str
    title: str
    pillar: Pillar
    scope: Scope
    fn: Callable[[CheckContext], Any]
    severity: Severity = Severity.MEDIUM
    layers: frozenset[Layer] = frozenset({Layer.ANY})
    requires: frozenset[Resource] = frozenset()
    weight: float = 1.0
    description: str = ""
    #: Whether the practice is a must-have (True) or a situational nice-to-have
    #: (False) per industry standard. Surfaced in the catalog for prioritisation.
    required: bool = True
    #: A spec the engine never runs: it describes a practice that cannot be
    #: verified from Fabric data (e.g. "BAA in place") and exists only so the
    #: catalog is a complete, attestable checklist.
    manual: bool = False
    #: How the verdict is (or could be) reached — see :class:`Automation`.
    #: ``AUTOMATED`` checks run; ``ROADMAP``/``MANUAL`` are attestation-only.
    automation: Automation = Automation.AUTOMATED

    def applies_to(self, layer: Layer) -> bool:
        """Does this check run for a workspace tagged ``layer``?

        A workspace tagged ``MIXED`` plays every role, so *every* check runs on
        it regardless of the layers the check itself declares.
        """
        return (
            Layer.ANY in self.layers
            or layer is Layer.MIXED
            or layer in self.layers
        )

    def to_dict(self) -> dict:
        """Serializable form — used by the catalog API and the MCP tools."""
        return {
            "id": self.id,
            "ref": self.ref,
            "title": self.title,
            "pillar": self.pillar.value,
            "scope": self.scope.value,
            "severity": self.severity.value,
            "layers": sorted(layer.value for layer in self.layers),
            "requires": sorted(r.value for r in self.requires),
            "weight": self.weight,
            "required": self.required,
            "manual": self.manual,
            "automation": self.automation.value,
            "description": self.description or (self.fn.__doc__ or "").strip(),
        }


@dataclass(slots=True)
class CheckResult:
    """One deterministic verdict about one implemented object."""

    check_id: str
    ref: str
    title: str
    pillar: Pillar
    status: Status
    score: int | None = None
    coverage: float | None = None
    evidence: str = ""
    recommendation: str = ""
    severity: Severity = Severity.MEDIUM
    workspace: str = ""
    layer: Layer = Layer.MIXED
    obj: str = ""
    scope: Scope = Scope.WORKSPACE
    weight: float = 1.0
    scored: bool = True

    #: Kept as a class attribute so callers can reference ``CheckResult.MAX_SCORE``.
    MAX_SCORE = MAX_SCORE

    @property
    def workspace_role(self) -> str:
        """Human-readable layer name, as used by the reports and the JSON API."""
        return self.layer.value if self.layer is not Layer.ANY else ""

    @property
    def counts_toward_score(self) -> bool:
        return self.scored and self.score is not None

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "ref": self.ref,
            "title": self.title,
            "pillar": self.pillar.value,
            "status": self.status.value,
            "score": self.score,
            "coverage": self.coverage,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "severity": self.severity.value,
            "workspace": self.workspace,
            "workspace_role": self.workspace_role,
            "layer": self.workspace_role,
            "obj": self.obj,
            "scope": self.scope.value,
            "weight": self.weight,
            "scored": self.scored,
            # Workspace-level checks are common to every project regardless of
            # source system; the UI flags them as such.
            "common": self.scope is Scope.WORKSPACE,
        }
