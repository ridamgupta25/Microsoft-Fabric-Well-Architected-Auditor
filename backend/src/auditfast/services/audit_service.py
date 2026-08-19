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
from ..core.models import CheckResult, WorkspaceContext
from ..core.scoring import aggregate
from .project import ProjectConfig, load_project, load_remediation

#: Check id used for workspaces that could not be read at all.
ACCESS_CHECK_ID = "WS-ACCESS"

#: Upper bound on snapshots accepted inline with one knowledge-base run, so an
#: oversized request cannot exhaust memory. One snapshot per audited workspace.
_MAX_INLINE_SNAPSHOTS = 100


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
                   token_refresher=None, powerbi_token: str | None = None,
                   sql_token: str | None = None, storage_token: str | None = None,
                   sql_token_refresher=None, source: str = "live",
                   snapshots: Sequence[dict] | None = None):
    """Create the provider for a run.

    With ``source="live"`` (the default) every run reads the live tenant, but
    through the on-disk **knowledge base**: the returned :class:`CachingProvider`
    serves each workspace from its cached snapshot and calls Fabric only on a
    cache miss or once the snapshot is past its TTL. ``refresh=True`` forces a
    fresh live crawl (rebuilding the KB). Caching can be turned off entirely with
    ``AUDITFAST_CACHE_ENABLED=false``, in which case the raw live provider is
    returned.

    With ``source="kb"`` the run is served entirely from saved snapshots — the
    permanent archive plus any ``snapshots`` uploaded with the request — and no
    token is needed, because not one Fabric call is made. A frozen snapshot makes
    a replay the most reproducible run there is.

    ``powerbi_token`` is an optional Power BI-audience token used only to read
    semantic-model refresh recency; without it that one signal stays unknown.

    ``sql_token`` is an optional SQL-analytics-endpoint token used to read column
    schemas and Warehouse RLS policies, which the Fabric REST API does not expose.
    Without it - or with ``AUDITFAST_SQL_ENDPOINT_ENABLED=false``, or with port
    1433 blocked - those reads are skipped and the column-level checks report N/A,
    exactly as they did before the endpoint was wired in.

    ``storage_token`` is an optional Storage-audience token used only for OneLake
    ADLS Gen2 Files listings. Without it, file-layout checks report N/A.
    """
    settings = get_settings()
    if source == "kb":
        return _build_snapshot_provider(settings, snapshots)
    if not token:
        raise AuditError("A sign-in token is required to run an audit.")
    live = LiveFabricProvider(token, token_refresher=token_refresher,
                              powerbi_token=powerbi_token,
                              sql_token=sql_token if settings.sql_endpoint_enabled else None,
                              storage_token=storage_token,
                              sql_token_refresher=sql_token_refresher)
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


def _build_snapshot_provider(settings, snapshots: Sequence[dict] | None):
    """A replay provider over the saved KB archive plus any uploaded snapshots."""
    from .context_store import KBArchive, SnapshotProvider

    archive = KBArchive(settings.resolve(settings.kb_archive_dir))
    uploaded = _parse_inline_snapshots(snapshots)
    rows = archive.index()
    known = {row["id"] for row in rows}
    # An uploaded workspace the archive has never seen still belongs in the run.
    for ws_id, ctx in uploaded.items():
        if ws_id not in known:
            rows.append(_row_for_context(ctx))
    return SnapshotProvider(uploaded=uploaded, archive=archive, rows=rows)


def _parse_inline_snapshots(snapshots: Sequence[dict] | None) -> dict[str, WorkspaceContext]:
    """Validate and index snapshots supplied inline with a KB run request."""
    items = list(snapshots or [])
    if len(items) > _MAX_INLINE_SNAPSHOTS:
        raise AuditError(
            f"Too many uploaded snapshots ({len(items)}); the limit is "
            f"{_MAX_INLINE_SNAPSHOTS} per run."
        )
    contexts: dict[str, WorkspaceContext] = {}
    for raw in items:
        ctx = _snapshot_to_context(raw)
        contexts[ctx.id] = ctx
    return contexts


def _snapshot_to_context(raw: dict) -> WorkspaceContext:
    """Turn one uploaded/saved snapshot dict into a :class:`WorkspaceContext`.

    Untrusted input: this only ever *reads* the payload into the normalized model
    (the engine is a pure function of it, so no code can execute), but it still
    validates the shape and rejects anything that is not a workspace so a bad
    upload fails fast with a clear message instead of a confusing later error.
    """
    if not isinstance(raw, dict):
        raise AuditError("A KB snapshot must be a JSON object.")
    # The permanent archive stores the context at the top level; the TTL cache
    # wraps it under "context". Accept either so a file copied from either store
    # (or a report the tool itself produced) loads without hand-editing.
    inner = raw.get("context")
    data = inner if isinstance(inner, dict) else raw
    try:
        ctx = WorkspaceContext.from_dict(data)
    except Exception as exc:  # noqa: BLE001 - any shape error is a bad upload
        raise AuditError(f"That file is not a valid workspace snapshot: {exc}") from exc
    if not ctx.id:
        raise AuditError("That snapshot is missing a workspace id.")
    return ctx


def _row_for_context(ctx: WorkspaceContext) -> dict:
    """Display metadata for a snapshot the archive has not indexed."""
    return {
        "id": ctx.id,
        "name": ctx.display_name or ctx.id,
        "role": ctx.layer.value,
        "layer": ctx.layer.value,
        "items": len(ctx.items),
        "pipelines": len(ctx.pipelines),
        "complete": ctx.is_complete,
        "captured_at": "",
    }


def list_kb_workspaces() -> list[dict]:
    """Workspaces available to replay from the saved knowledge-base archive.

    No token, no Fabric call — just what has already been crawled to disk. This
    is the picker behind the "Saved KB" audit source.
    """
    from .context_store import KBArchive

    settings = get_settings()
    return KBArchive(settings.resolve(settings.kb_archive_dir)).index()


def validate_snapshot(payload: dict) -> dict:
    """Validate one uploaded KB file and echo it back normalized.

    Returns ``{"workspace": <display row>, "snapshot": <normalized dict>}``. The
    caller holds the normalized snapshot and submits it with a ``source="kb"``
    audit; re-normalizing here means the run reads exactly what was validated.
    """
    ctx = _snapshot_to_context(payload)
    return {"workspace": _row_for_context(ctx), "snapshot": ctx.to_dict()}


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
    sql_token: str | None = None,
    storage_token: str | None = None,
    sql_token_refresher=None,
    source: str = "live",
    snapshots: Sequence[dict] | None = None,
) -> AuditRun:
    """Run an audit and, when ``out_dir`` is given, write the report files.

    ``on_progress``, if given, receives a partial report dict after each
    workspace — so a caller can surface results before the whole run finishes.

    With ``source="live"`` the run is served from the on-disk knowledge base;
    pass ``refresh=True`` to force a fresh live crawl and rebuild the KB. With
    ``source="kb"`` the run reads only saved snapshots — the archive plus any
    ``snapshots`` uploaded with the request — and needs no token.
    """
    config = load_project(project_path)
    provider = build_provider(config, token, refresh=refresh, token_refresher=token_refresher,
                              powerbi_token=powerbi_token, sql_token=sql_token,
                              storage_token=storage_token,
                              sql_token_refresher=sql_token_refresher,
                              source=source, snapshots=snapshots)
    targets = _resolve_targets(config, workspaces)
    remediation: RemediationBook = load_remediation(config)

    def _progress(partial: list[CheckResult]) -> None:
        report = to_json(_build_run(config.name, partial))
        report["partial"] = True
        on_progress(report)  # type: ignore[misc]

    raw_results = run_engine(
        provider,
        targets,
        config.settings,
        pillars=_resolve_pillars(pillars),
        remediation=remediation,
        on_progress=_progress if on_progress else None,
    )

    run = _build_run(config.name, raw_results)
    served = bool(getattr(provider, "served_from_cache", False))
    run.kb = {
        "source": source,
        "served_from_cache": served,
        "refreshing": served and not refresh and source == "live",
    }
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
    build_excel(
        str(excel_path),
        run.project_name,
        run.aggregate,
        run.results,
        run.errors,
    )
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
