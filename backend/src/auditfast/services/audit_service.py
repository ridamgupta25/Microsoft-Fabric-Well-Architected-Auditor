"""The one audit path, shared by every adapter.

The CLI, the HTTP API, and the MCP server all enter here. That is what
guarantees they cannot disagree: there is one implementation with several front
doors, not several implementations.

Nothing in this module imports a web framework — it takes plain arguments and
returns plain objects, which is exactly what makes a second (or third) adapter
cheap to add.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..clients import LiveFabricProvider, MockProvider
from ..core.checks.helpers import RemediationBook
from ..core.checks.registry import REGISTRY
from ..core.engine import run_audit as run_engine
from ..core.enums import Layer, Pillar
from ..core.models import CheckResult
from ..core.scoring import aggregate
from .project import ProjectConfig, load_project, load_remediation

#: Check id used for workspaces that could not be read at all.
ACCESS_CHECK_ID = "WS-ACCESS"

MOCK = "mock"
LIVE = "live"


class AuditError(RuntimeError):
    """A run could not be started — bad mode, missing token, unknown check."""


@dataclass(slots=True)
class AuditRun:
    """Everything one audit produced."""

    project_name: str
    mode: str
    results: list[CheckResult] = field(default_factory=list)
    errors: list[CheckResult] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)


# -- provider construction ----------------------------------------------------

def build_provider(config: ProjectConfig, mode: str = MOCK, token: str | None = None):
    """Create the provider for a run. Mode is the only branch in the system."""
    if mode == LIVE:
        if not token:
            raise AuditError("Live mode requires a sign-in token.")
        return LiveFabricProvider(token)
    if not config.tenant_file or not config.tenant_file.exists():
        raise AuditError(f"Mock tenant fixture not found: {config.tenant_file}")
    return MockProvider(config.tenant_file)


def _resolve_pillars(names: Iterable[str] | None) -> list[Pillar] | None:
    """Map pillar names from an API/CLI caller onto enum members."""
    if not names:
        return None
    wanted = {str(n).strip().lower() for n in names if str(n).strip()}
    if not wanted:
        return None
    return [p for p in Pillar if p.value.lower() in wanted]


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

def list_workspaces(project_path: str | Path, mode: str = MOCK) -> list[dict]:
    """Workspaces available for selection, before any sign-in."""
    config = load_project(project_path)
    declared = {ws_id: layer for ws_id, layer in config.targets}

    if mode == MOCK:
        provider = build_provider(config, MOCK)
        rows = provider.list_workspaces()
        # A project file narrows the fixture down to the workspaces it declares.
        if declared:
            rows = [r for r in rows if r["id"] in declared]
        for row in rows:
            row["role"] = declared.get(row["id"], Layer.parse(row.get("layer"))).value
        return rows

    # Live: contents cannot be enumerated without a token, so report what the
    # project declares and let the UI load the real list after sign-in.
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
    """Probe what the token can actually read, per sub-resource."""
    return LiveFabricProvider(token).probe()


# -- running ------------------------------------------------------------------

def run_audit(
    project_path: str | Path,
    mode: str = MOCK,
    pillars: Iterable[str] | None = None,
    workspaces: Sequence[dict] | Sequence[str] | None = None,
    out_dir: str | Path | None = None,
    token: str | None = None,
) -> AuditRun:
    """Run an audit and, when ``out_dir`` is given, write the report files."""
    config = load_project(project_path)
    provider = build_provider(config, mode, token)
    remediation: RemediationBook = load_remediation(config)

    raw_results = run_engine(
        provider,
        _resolve_targets(config, workspaces),
        config.settings,
        pillars=_resolve_pillars(pillars),
        remediation=remediation,
    )

    # Workspaces that could not be read are warnings, not failing checks. Keeping
    # them out of the scored set means every consumer — console, Markdown, Excel,
    # and the browser — reports the same pass/partial/fail counts.
    errors = [r for r in raw_results if r.check_id == ACCESS_CHECK_ID]
    results = [r for r in raw_results if r.check_id != ACCESS_CHECK_ID]

    run = AuditRun(
        project_name=config.name,
        mode=mode,
        results=results,
        errors=errors,
        aggregate=aggregate(results),
    )

    if out_dir:
        run.files = write_reports(run, out_dir)
    return run


def run_check(
    check_id: str,
    workspace_id: str,
    project_path: str | Path,
    mode: str = MOCK,
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
    provider = build_provider(config, mode, token)

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
    from ..core.checks.registry import CheckRegistry

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
        build_markdown(run.project_name, run.aggregate, run.results, run.mode),
        encoding="utf-8",
    )
    build_excel(str(excel_path), run.project_name, run.aggregate, run.results, run.mode)
    return {"markdown": str(markdown_path), "excel": str(excel_path)}


def to_json(run: AuditRun) -> dict:
    """Serialize a run for an API response."""
    agg = run.aggregate
    return {
        "project_name": run.project_name,
        "mode": run.mode,
        "overall": agg.get("overall"),
        "by_pillar": agg.get("by_pillar", {}),
        "by_workspace": agg.get("by_workspace", {}),
        "by_layer": agg.get("by_layer", {}),
        "matrix": agg.get("matrix", {}),
        "layers": agg.get("layers", []),
        "counts": agg.get("counts", {}),
        "total_scored": agg.get("total_scored", 0),
        "results": [r.to_dict() for r in run.results],
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
