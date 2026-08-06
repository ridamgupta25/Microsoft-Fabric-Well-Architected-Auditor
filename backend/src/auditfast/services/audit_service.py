"""The one audit path, shared by every adapter.

The CLI, the HTTP API, and the MCP server all enter here. That is what
guarantees they cannot disagree: there is one implementation with several front
doors, not several implementations.

Nothing in this module imports a web framework — it takes plain arguments and
returns plain objects, which is exactly what makes a second (or third) adapter
cheap to add.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..clients import LiveFabricProvider
from ..config.settings import get_settings
from ..core.check.helpers import RemediationBook
from ..core.check.registry import REGISTRY
from ..core.engine import READ_INCOMPLETE_CHECK_ID
from ..core.engine import run_audit as run_engine
from ..core.enums import Layer, Pillar
from ..core.models import CheckResult
from ..core.scoring import aggregate
from .project import ProjectConfig, load_project, load_remediation

#: Check id used for workspaces that could not be read at all.
ACCESS_CHECK_ID = "WS-ACCESS"


class AuditError(RuntimeError):
    """A run could not be started — missing token, unknown check, bad project."""


@dataclass(slots=True)
class AuditRun:
    """Everything one audit produced."""

    project_name: str
    results: list[CheckResult] = field(default_factory=list)
    errors: list[CheckResult] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    #: Knowledge-base provenance: whether this report was served from the disk
    #: cache and whether a fresher live crawl is being (or should be) fetched.
    kb: dict = field(default_factory=dict)


# -- provider construction ----------------------------------------------------

def build_provider(config: ProjectConfig, token: str | None = None, *, refresh: bool = False,
                   token_refresher=None, powerbi_token: str | None = None):
    """Create the provider for a run.

    Every run reads the live tenant, but through the on-disk **knowledge base**:
    the returned :class:`CachingProvider` serves each workspace from its cached
    snapshot and calls Fabric only on a cache miss or once the snapshot is past
    its TTL. ``refresh=True`` forces a fresh live crawl (rebuilding the KB).
    Caching can be turned off entirely with ``AUDITFAST_CACHE_ENABLED=false``, in
    which case the raw live provider is returned.

    ``powerbi_token`` is an optional Power BI-audience token used only to read
    semantic-model refresh recency; without it that one signal stays unknown.
    """
    if not token:
        raise AuditError("A sign-in token is required to run an audit.")
    settings = get_settings()
    live = LiveFabricProvider(token, token_refresher=token_refresher,
                              powerbi_token=powerbi_token)
    provider = live
    if settings.cache_enabled:
        from .context_store import CachingProvider, ContextStore

        store = ContextStore(settings.resolve(settings.cache_dir))
        provider = CachingProvider(
            live,
            store,
            ttl_seconds=settings.cache_ttl_seconds,
            soft_seconds=settings.cache_soft_seconds,
            background_refresh=settings.cache_background_refresh,
            force_refresh=refresh,
        )
    # A permanent, timestamped snapshot of every crawl, written on top of
    # whatever provider serves it (cache or live) so each audit run is archived.
    if settings.kb_archive_enabled:
        from .context_store import ArchivingProvider, KBArchive

        archive = KBArchive(settings.resolve(settings.kb_archive_dir))
        provider = ArchivingProvider(provider, archive)
    return provider


def _resolve_pillars(names: Iterable[str] | None) -> list[Pillar] | None:
    """Map pillar names from an API/CLI caller onto enum members.

    Foundation is cross-cutting, informational context (item inventory, access
    errors, crawl-completeness) — never scored, but always reported. It is kept
    in every run even when the caller selects a subset of the scored pillars, so
    the report's Workspace Inventory section is never silently empty.
    """
    if not names:
        return None
    wanted = {str(n).strip().lower() for n in names if str(n).strip()}
    if not wanted:
        return None
    resolved = [p for p in Pillar if p.value.lower() in wanted]
    if Pillar.FOUNDATION not in resolved:
        resolved.append(Pillar.FOUNDATION)
    return resolved


def _resolve_targets(
    config: ProjectConfig,
    workspaces: Sequence[dict] | Sequence[str] | None,
) -> list[tuple[str, Layer]]:
    """Work out which workspaces to audit.

    Accepts the UI's ``[{id, role}]`` objects, a bare list of ids, or nothing at
    all (in which case the project file decides).
    """
    if not workspaces:
        return list(config.targets)

    if isinstance(workspaces[0], dict):
        return [
            (entry["id"], Layer.parse(entry.get("role") or entry.get("layer")))
            for entry in workspaces  # type: ignore[union-attr]
            if entry.get("id")
        ]

    wanted = {str(w) for w in workspaces}
    declared = {ws_id: layer for ws_id, layer in config.targets}
    # Preserve caller order, and default a workspace the project never declared.
    return [(ws_id, declared.get(ws_id, Layer.MIXED)) for ws_id in wanted]


# -- listing ------------------------------------------------------------------

def list_workspaces(project_path: str | Path) -> list[dict]:
    """Workspaces declared by the project file, before any sign-in.

    Contents cannot be enumerated without a token, so this only echoes what the
    project declares. Use :func:`list_live_workspaces` once signed in to see the
    real tenant.
    """
    config = load_project(project_path)
    return [
        {"id": ws_id, "name": ws_id, "role": layer.value, "layer": layer.value,
         "items": None, "pipelines": None}
        for ws_id, layer in config.targets
    ]


def list_live_workspaces(token: str) -> list[dict]:
    """Every workspace the signed-in user can access."""
    rows = LiveFabricProvider(token).list_workspaces()
    for row in rows:
        row.setdefault("role", "")
    return rows


def diagnose(token: str) -> dict:
    """Probe what the token can actually read, per sub-resource.

    Also surfaces the token's granted scopes and audience, which is what makes a
    missing-permission problem (for example: no Item.ReadWrite for getDefinition)
    diagnosable without guessing.
    """
    from .auth_service import _decode_jwt_claims

    result = LiveFabricProvider(token).probe()
    claims = _decode_jwt_claims(token)
    result["granted_scopes"] = claims.get("scp", "")
    result["token_audience"] = claims.get("aud", "")
    return result


# -- running ------------------------------------------------------------------

def _build_run(project_name: str, raw_results: list[CheckResult]) -> AuditRun:
    """Split access-errors from scored results and aggregate.

    Workspaces that could not be read are warnings, not failing checks. Keeping
    them out of the scored set means every consumer — console, Markdown, Excel,
    and the browser — reports the same pass/partial/fail counts.
    """
    error_ids = {ACCESS_CHECK_ID, READ_INCOMPLETE_CHECK_ID}
    errors = [r for r in raw_results if r.check_id in error_ids]
    results = [r for r in raw_results if r.check_id not in error_ids]
    return AuditRun(
        project_name=project_name,
        results=results,
        errors=errors,
        aggregate=aggregate(results),
    )


def run_audit(
    project_path: str | Path,
    pillars: Iterable[str] | None = None,
    workspaces: Sequence[dict] | Sequence[str] | None = None,
    out_dir: str | Path | None = None,
    token: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    refresh: bool = False,
    token_refresher=None,
    powerbi_token: str | None = None,
) -> AuditRun:
    """Run an audit and, when ``out_dir`` is given, write the report files.

    ``on_progress``, if given, receives a partial report dict after each
    workspace — so a caller can surface results before the whole run finishes.

    The run is served from the on-disk knowledge base; pass ``refresh=True`` to
    force a fresh live crawl and rebuild the KB.
    """
    config = load_project(project_path)
    provider = build_provider(config, token, refresh=refresh, token_refresher=token_refresher,
                              powerbi_token=powerbi_token)
    remediation: RemediationBook = load_remediation(config)

    def _progress(partial: list[CheckResult]) -> None:
        report = to_json(_build_run(config.name, partial))
        report["partial"] = True
        on_progress(report)  # type: ignore[misc]

    raw_results = run_engine(
        provider,
        _resolve_targets(config, workspaces),
        config.settings,
        pillars=_resolve_pillars(pillars),
        remediation=remediation,
        on_progress=_progress if on_progress else None,
    )

    run = _build_run(config.name, raw_results)
    served = bool(getattr(provider, "served_from_cache", False))
    run.kb = {"served_from_cache": served, "refreshing": served and not refresh}
    if out_dir:
        run.files = write_reports(run, out_dir)
    return run


def run_check(
    check_id: str,
    workspace_id: str,
    project_path: str | Path,
    layer: str | None = None,
    token: str | None = None,
) -> list[dict]:
    """Run exactly one check against one workspace.

    The fast feedback loop: no report files, and the provider is asked only for
    the resources this single check declares. Impossible before checks carried
    metadata — there was no way to address one by id.

    Raises:
        AuditError: the check id is not registered.
    """
    spec = REGISTRY.get(check_id)
    if spec is None:
        raise AuditError(f"Unknown check id: {check_id!r}")

    config = load_project(project_path)
    provider = build_provider(config, token)

    declared = {ws: lyr for ws, lyr in config.targets}
    resolved_layer = Layer.parse(layer) if layer else declared.get(workspace_id, Layer.MIXED)

    results = run_engine(
        provider,
        [(workspace_id, resolved_layer)],
        config.settings,
        registry=_single_check_registry(check_id),
        remediation=load_remediation(config),
    )
    return [r.to_dict() for r in results]


def _single_check_registry(check_id: str):
    """A throwaway registry holding one check, so the engine runs only that one."""
    from ..core.check.registry import CheckRegistry

    narrow = CheckRegistry()
    spec = REGISTRY.get(check_id)
    if spec is not None:
        narrow.register(spec)
    return narrow


# -- output -------------------------------------------------------------------

def write_reports(run: AuditRun, out_dir: str | Path) -> dict[str, str]:
    """Write the Markdown and Excel reports; return their paths."""
    from ..reporting.excel import build_excel
    from ..reporting.markdown import build_markdown

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    markdown_path = directory / "audit-report.md"
    excel_path = directory / "audit-report.xlsx"

    markdown_path.write_text(
        build_markdown(run.project_name, run.aggregate, run.results, run.errors),
        encoding="utf-8",
    )
    build_excel(str(excel_path), run.project_name, run.aggregate, run.results)
    return {"markdown": str(markdown_path), "excel": str(excel_path)}


def to_json(run: AuditRun) -> dict:
    """Serialize a run for an API response."""
    agg = run.aggregate
    return {
        "project_name": run.project_name,
        "overall": agg.get("overall"),
        "by_pillar": agg.get("by_pillar", {}),
        "by_workspace": agg.get("by_workspace", {}),
        "by_layer": agg.get("by_layer", {}),
        "matrix": agg.get("matrix", {}),
        "layers": agg.get("layers", []),
        "counts": agg.get("counts", {}),
        "total_scored": agg.get("total_scored", 0),
        "results": [r.to_dict() for r in run.results],
        "kb": run.kb,
        "errors": [
            {
                "workspace": r.workspace,
                "role": r.workspace_role,
                "message": r.evidence,
                "recommendation": r.recommendation,
            }
            for r in run.errors
        ],
        "files": {kind: Path(path).name for kind, path in run.files.items()},
    }
