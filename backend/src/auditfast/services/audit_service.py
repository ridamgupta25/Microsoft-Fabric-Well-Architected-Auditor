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
from ..core.advisory import is_advisory
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
    #: Non-deterministic (advisory) checks, kept out of the deterministic score
    #: and routed to a separate same-format Advisory report.
    advisory_results: list[CheckResult] = field(default_factory=list)
    advisory_aggregate: dict = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    #: Knowledge-base provenance: whether this report was served from the disk
    #: cache and whether a fresher live crawl is being (or should be) fetched.
    kb: dict = field(default_factory=dict)
    #: Cross-workspace project groups, purely for display. Built from the
    #: request's workspace selections; never influences targets or scoring, so an
    #: isolated-only run leaves this empty and behaves exactly as before.
    groups: list[dict] = field(default_factory=list)
    #: True when the roll-up was environment-weighted (opt-in). Display only —
    #: the per-check and per-workspace numbers are unchanged either way.
    weighted_by_environment: bool = False


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
    """Map pillar names from an API/CLI caller onto enum members."""
    if not names:
        return None
    wanted = {str(n).strip().lower() for n in names if str(n).strip()}
    if not wanted:
        return None
    resolved = [p for p in Pillar if p.value.lower() in wanted]
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


def _resolve_groups(
    workspaces: Sequence[dict] | Sequence[str] | None,
) -> list[dict]:
    """Extract cross-workspace project groups from the request selections.

    Display metadata only — it never changes which workspaces are audited or how
    they score. A selection without a ``group`` is an isolated workspace and is
    omitted, so an isolated-only run yields an empty list.
    """
    if not workspaces or not isinstance(workspaces[0], dict):
        return []
    grouped: dict[str, list[dict]] = {}
    for entry in workspaces:  # type: ignore[union-attr]
        name = str(entry.get("group") or "").strip()
        if not name or not entry.get("id"):
            continue
        grouped.setdefault(name, []).append({
            "id": entry["id"],
            "name": entry.get("name") or entry["id"],
            "role": entry.get("role") or entry.get("layer") or "Mixed",
            "environment_level": entry.get("environment_level"),
        })
    return [
        {
            "name": name,
            "workspaces": sorted(
                members,
                key=lambda member: (member.get("environment_level") or 0),
            ),
        }
        for name, members in grouped.items()
    ]


def _resolve_weights(
    workspaces: Sequence[dict] | Sequence[str] | None,
    enabled: bool,
) -> dict[str, float] | None:
    """Map each workspace id to its environment weight (level 1..10 == weight).

    Returns ``None`` when weighting is off or no levels were given, so the engine
    runs its plain unweighted mean — identical to today. A workspace without a
    level defaults to weight 1.0, so an isolated workspace is never up- or
    down-weighted.
    """
    if not enabled or not workspaces or not isinstance(workspaces[0], dict):
        return None
    weights: dict[str, float] = {}
    for entry in workspaces:  # type: ignore[union-attr]
        ws_id = entry.get("id")
        level = entry.get("environment_level")
        if ws_id and isinstance(level, (int, float)) and level > 0:
            weights[ws_id] = float(level)
    return weights or None


def _resolve_group_targets(
    workspaces: Sequence[dict] | Sequence[str] | None,
) -> list[tuple[str, tuple[tuple[str, Layer, int], ...]]]:
    """Build the engine's group targets from the request's grouped selections.

    Each is ``(group_name, ((workspace_id, layer, level), ...))``. Only groups
    with two or more members are worth a cross-workspace comparison; a lone
    grouped workspace yields nothing here (it is still audited individually).
    """
    if not workspaces or not isinstance(workspaces[0], dict):
        return []
    grouped: dict[str, list[tuple[str, Layer, int]]] = {}
    for entry in workspaces:  # type: ignore[union-attr]
        name = str(entry.get("group") or "").strip()
        ws_id = entry.get("id")
        if not name or not ws_id:
            continue
        layer = Layer.parse(entry.get("role") or entry.get("layer"))
        level = entry.get("environment_level")
        level = int(level) if isinstance(level, (int, float)) and level > 0 else 1
        grouped.setdefault(name, []).append((ws_id, layer, level))
    return [
        (name, tuple(members))
        for name, members in grouped.items()
        if len(members) >= 2
    ]


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
    scored = [r for r in raw_results if r.check_id not in error_ids]
    # Advisory (non-deterministic) checks are kept out of the deterministic
    # scorecard and the main report; they go to a separate Advisory report.
    results = [r for r in scored if not is_advisory(r.ref)]
    advisory_results = [r for r in scored if is_advisory(r.ref)]
    return AuditRun(
        project_name=project_name,
        results=results,
        errors=errors,
        aggregate=aggregate(results),
        advisory_results=advisory_results,
        advisory_aggregate=aggregate(advisory_results),
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
    weight_by_environment: bool = False,
    external_checks_csv: str | Path | None = None,
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

    ``external_checks_csv``, if given, loads additional check results from a CSV
    file (e.g., AdminChecks.csv) and merges them with the automated results.
    Raises AuditError if the CSV is invalid.
    """
    config = load_project(project_path)
    provider = build_provider(config, token, refresh=refresh, token_refresher=token_refresher,
                              powerbi_token=powerbi_token, sql_token=sql_token,
                              storage_token=storage_token,
                              sql_token_refresher=sql_token_refresher,
                              source=source, snapshots=snapshots)
    targets = _resolve_targets(config, workspaces)
    groups = _resolve_groups(workspaces)
    weights = _resolve_weights(workspaces, weight_by_environment)
    group_targets = _resolve_group_targets(workspaces)
    remediation: RemediationBook = load_remediation(config)

    def _progress(partial: list[CheckResult]) -> None:
        run = _build_run(config.name, partial)
        run.groups = groups
        run.weighted_by_environment = bool(weights)
        report = to_json(run)
        report["partial"] = True
        on_progress(report)  # type: ignore[misc]

    deterministic_registry, advisory_registry = _split_registries()

    # Stage 1 -- the deterministic audit. This crawl is what builds the KB.
    raw_results = run_engine(
        provider,
        targets,
        config.settings,
        registry=deterministic_registry,
        pillars=_resolve_pillars(pillars),
        remediation=remediation,
        on_progress=_progress if on_progress else None,
        weights=weights,
        groups=group_targets,
    )

    # Merge external checks if provided
    if external_checks_csv:
        from .external_checks_service import ExternalCheckError, load_external_checks

        try:
            # Strip surrounding whitespace and stray quotes (Windows "Copy as path"
            # wraps the path in double quotes, which would otherwise be taken literally).
            raw_csv = str(external_checks_csv).strip().strip('"').strip("'").strip()

            # Resolve CSV path: if relative, resolve it relative to project root (parent of config dir)
            csv_path = Path(raw_csv)
            if not csv_path.is_absolute():
                # Try relative to project root first (parent of config.yaml directory)
                project_root = Path(config.path).parent
                candidate = project_root / csv_path
                if candidate.exists():
                    csv_path = candidate
                # Otherwise fall back to cwd (current working directory)

            target_ws_ids = {ws_id for ws_id, _ in targets}
            external_results, warnings = load_external_checks(
                csv_path,
                target_workspaces=target_ws_ids,
            )

            if warnings:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(
                    f"Loaded external checks with {len(warnings)} warning(s):\n" +
                    "\n".join(f"  - {w}" for w in warnings)
                )

            # Merge: workspace-agnostic (external checks added regardless of workspace)
            # Simply append external checks to automated results without requiring workspace
            # match. This allows external checks from different workspaces to contribute to
            # the overall audit score. External checks keep their original workspace from CSV.
            merged_results = raw_results + external_results

        except ExternalCheckError as e:
            raise AuditError(f"Cannot load external checks: {e}") from e
    else:
        merged_results = raw_results

    run = _build_run(config.name, merged_results)
    run.groups = groups
    run.weighted_by_environment = bool(weights)
    served = bool(getattr(provider, "served_from_cache", False))
    run.kb = {
        "source": source,
        "served_from_cache": served,
        "refreshing": served and not refresh and source == "live",
    }
    if out_dir:
        run.files.update(write_reports(run, out_dir))

    # Stage 2 -- advisory (non-deterministic) checks, evaluated *after* the audit
    # against the knowledge base the deterministic crawl just built. The same
    # provider serves each workspace from cache, so there is no second crawl; the
    # results stay out of the deterministic score and go to their own report.
    advisory_raw = run_engine(
        provider,
        targets,
        config.settings,
        registry=advisory_registry,
        pillars=_resolve_pillars(pillars),
        remediation=remediation,
        weights=weights,
    )
    error_ids = {ACCESS_CHECK_ID, READ_INCOMPLETE_CHECK_ID}
    advisory_results = [r for r in advisory_raw if r.check_id not in error_ids]
    # Gathered once and shared by both judging routes: each call re-fetches every
    # workspace and rebuilds its WorkspaceContext, and the two routes must ground
    # a verdict in exactly the same evidence anyway.
    contexts = (
        _advisory_contexts(provider, targets, advisory_registry)
        if advisory_results else {}
    )
    # AI re-judges the advisory checks against the knowledge base for a more
    # accurate verdict. When AI is off (the default) this is a no-op and the
    # deterministic verdicts are kept unchanged.
    advisory_results = _ai_judge_advisory(advisory_results, contexts)
    run.advisory_results = advisory_results
    run.advisory_aggregate = aggregate(advisory_results)
    if out_dir:
        run.files.update(write_advisory_reports(run, out_dir))
        # The offline judging bundle is written whether or not a model is
        # configured: it is the route for reviewers who have no server-side key,
        # and it costs one small file. It never affects this run's numbers.
        run.files.update(_write_advisory_bundle(advisory_results, contexts, out_dir))
    return run


def _write_advisory_bundle(results, contexts, out_dir) -> dict:
    """Write the offline judging bundles; never fatal to an audit.

    Writes the flat bundle, a themed split with a manifest, and a pre-filled
    verdict template per theme. The split is what makes the route usable at
    scale: one workspace produced 1,940 advisory findings, which no single
    review session can judge well, but grouped by question they become a handful
    of focused jobs.
    """
    if not results:
        return {}
    try:
        from ..ai.advisory_bundle import build_bundle, write_bundle, write_themed_bundles

        # Built once and shared: each record embeds up to 16 KB of evidence, so
        # letting both writers derive their own costs a second full pass over
        # every finding and a second copy of the same payload.
        records = build_bundle(results, contexts)
        files = {
            "advisory_bundle": str(
                write_bundle(results, contexts, Path(out_dir), records=records)
            )
        }
        files.update(
            write_themed_bundles(results, contexts, Path(out_dir), records=records)
        )
        return files
    except Exception:  # noqa: BLE001 - an export must never break the audit
        # Logged rather than swallowed: silence makes a total failure of this
        # feature indistinguishable from "there were no advisory findings".
        import logging

        logging.getLogger(__name__).warning(
            "advisory: could not write the judging bundle", exc_info=True
        )
        return {}


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


def _split_registries():
    """Split the global registry into (deterministic, advisory) catalogs.

    The deterministic catalog drives Stage 1 (the scored audit + KB build); the
    advisory catalog drives Stage 2, evaluated afterwards against that KB. Keyed
    by ref via ``is_advisory``, so moving a check between tiers is a one-line
    edit in ``auditfast.core.advisory``.
    """
    from ..core.check.registry import CheckRegistry

    deterministic = CheckRegistry()
    advisory = CheckRegistry()
    for spec in REGISTRY.all():
        (advisory if is_advisory(spec.ref) else deterministic).register(spec)
    return deterministic, advisory


def _advisory_contexts(provider, targets, advisory_registry) -> dict:
    """Each workspace's cached KB context, keyed by workspace name.

    Served from the cache the deterministic stage already filled, so this costs
    no second crawl. Shared by both judging routes - the API path and the offline
    bundle - so they ground a verdict in exactly the same evidence.
    """
    contexts: dict[str, WorkspaceContext] = {}
    for workspace_id, layer in targets:
        specs = [s for s in advisory_registry.select(layer=layer) if not s.manual]
        if not specs:
            continue
        try:
            ctx = provider.fetch(workspace_id, layer, advisory_registry.required_resources(specs))
        except Exception:  # noqa: BLE001 - context is best-effort, never fatal
            continue
        contexts[ctx.name] = ctx
    return contexts


def _ai_judge_advisory(results, contexts):
    """Re-judge advisory results with AI grounded in the KB; no-op when AI is off.

    ``contexts`` is the already-gathered per-workspace KB slice, so this costs no
    fetch of its own. Any failure or a disabled model leaves the deterministic
    verdicts untouched.
    """
    if not results:
        return results
    from ..ai import advisory as ai_advisory
    from ..ai import orchestrator as ai_orchestrator

    if not ai_orchestrator.is_enabled():
        return results
    return ai_advisory.evaluate(results, contexts)


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


def write_advisory_reports(run: AuditRun, out_dir: str | Path) -> dict[str, str]:
    """Write the advisory (non-deterministic) reports -- identical format.

    Built in Stage 2, after the deterministic audit, from the advisory checks
    evaluated against the knowledge base. Returns empty when a run produced no
    advisory results (e.g. a pillar filter excluded them all).
    """
    if not run.advisory_results:
        return {}
    from ..reporting.excel import build_excel
    from ..reporting.markdown import build_markdown

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    advisory_name = f"{run.project_name} - Advisory (non-deterministic)"
    advisory_md = directory / "advisory-report.md"
    advisory_xlsx = directory / "advisory-report.xlsx"
    advisory_md.write_text(
        build_markdown(
            advisory_name,
            run.advisory_aggregate,
            run.advisory_results,
            run.errors,
        ),
        encoding="utf-8",
    )
    build_excel(
        str(advisory_xlsx),
        advisory_name,
        run.advisory_aggregate,
        run.advisory_results,
        run.errors,
    )
    return {
        "advisory_markdown": str(advisory_md),
        "advisory_excel": str(advisory_xlsx),
    }


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
        "groups": run.groups,
        "weighted_by_environment": run.weighted_by_environment,
        "kb": run.kb,
        "advisory": {
            "overall": run.advisory_aggregate.get("overall"),
            "by_pillar": run.advisory_aggregate.get("by_pillar", {}),
            "counts": run.advisory_aggregate.get("counts", {}),
            "total_scored": run.advisory_aggregate.get("total_scored", 0),
            "results": [r.to_dict() for r in run.advisory_results],
        },
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
