"""Batch checklist runner — assess a whole user checklist and run the matches.

This is the "the client handed us *their* checklist" front door. It is separate
from a normal audit (which runs the full registered library) and only fires when
a user supplies a **custom** checklist file. It does two things per point:

1. **Assess** it against the registered catalog (dedup), reusing the same
   deterministic matcher the single-point ``/checklist/assess`` endpoint uses.
2. When the point is already **covered** by an automated check, **run that check**
   over the **offline knowledge base** (the on-disk ``kb-cache`` snapshots,
   token-free) and, for any workspace with no snapshot, fall back to a **live**
   crawl when a token is supplied.

Why this is safe (the determinism boundary is untouched):

* It **never registers a check** and **never changes a score** — a covered point
  is evaluated by the *existing* engine through a single-check registry, so a
  batch verdict is identical to the verdict the same check produces in a full
  audit.
* Uncovered points get the same draft **proposal** the intake service produces —
  design-time scaffolding for the ``.github`` authoring agents, never auto-run.
* Reading the offline KB contacts nothing; the live fallback is the only path
  that needs a token, and it uses the one shared audit path.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..ai import matching
from ..clients.base import ALL_RESOURCES
from ..clients.errors import WorkspaceAccessError
from ..config.settings import get_settings
from ..core.check.registry import REGISTRY, CheckRegistry
from ..core.engine import run_audit as run_engine
from ..core.enums import Automation, Layer, Resource
from ..core.models import CheckResult, CheckSpec, WorkspaceContext
from . import audit_service, intake_service
from .context_store import ContextStore
from .project import load_project, load_remediation

#: Worst-first ordering used to pick a headline verdict for a workspace and to
#: sort the batch summary. FAIL is the most actionable, INFO the least.
_STATUS_RANK: dict[str, int] = {
    "FAIL": 0, "PARTIAL": 1, "N/A": 2, "PASS": 3, "INFO": 4,
}


class ChecklistParseError(ValueError):
    """The uploaded checklist could not be parsed into any points."""


@dataclass(slots=True)
class ChecklistPoint:
    """One row of a user-supplied checklist.

    Only ``point`` is required; ``pillar``/``scope`` are optional author hints
    (never trusted over the deterministic matcher) and ``notes`` is free text
    carried through to the report.
    """

    point: str
    pillar: str | None = None
    scope: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict:
        return {
            "point": self.point,
            "pillar": self.pillar,
            "scope": self.scope,
            "notes": self.notes,
        }


# =============================================================================
# Parsing — CSV / JSON / plain-text-or-markdown
# =============================================================================

_BULLET = re.compile(r"^\s*(?:[-*+\u2022\u25CF]|\d+[.)])\s+")
_CHECKBOX = re.compile(r"^\s*\[[ xX]?\]\s*")
_HEADER_WORDS = frozenset({"point", "points", "checklist", "item", "items", "best practice"})


def parse_checklist(
    content: str | bytes,
    *,
    filename: str | None = None,
) -> list[ChecklistPoint]:
    """Parse a checklist file into points, dispatching on extension then content.

    Accepts three formats: CSV (a ``point`` column, optionally ``pillar``,
    ``scope``, ``notes``), JSON (an array of strings or ``{point, ...}`` objects,
    or an object with a ``points`` key), and plain text / Markdown (one point per
    line, bullets / numbering / task-list checkboxes stripped, headings skipped).

    Raises:
        ChecklistParseError: the content is empty or yields no usable points.
    """
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    if not text or not text.strip():
        raise ChecklistParseError("The checklist is empty.")

    fmt = _detect_format(text, filename)
    if fmt == "json":
        return _parse_json(text)
    if fmt == "csv":
        return _parse_csv(text)
    return _parse_text(text)


def _detect_format(text: str, filename: str | None) -> str:
    ext = Path(filename).suffix.lower() if filename else ""
    if ext == ".json":
        return "json"
    if ext in {".csv", ".tsv"}:
        return "csv"
    if ext in {".md", ".markdown", ".txt", ".text"}:
        return "text"
    stripped = text.lstrip()
    if stripped[:1] in {"[", "{"}:
        return "json"
    first_line = stripped.splitlines()[0].lower() if stripped.splitlines() else ""
    if "," in first_line and "point" in first_line:
        return "csv"
    return "text"


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _parse_csv(text: str) -> list[ChecklistPoint]:
    reader = csv.DictReader(io.StringIO(text))
    field_map = {(name or "").lower().strip(): name for name in (reader.fieldnames or [])}
    if "point" in field_map:
        points = [
            ChecklistPoint(
                point=raw,
                pillar=_clean(row.get(field_map.get("pillar", ""))),
                scope=_clean(row.get(field_map.get("scope", ""))),
                notes=_clean(row.get(field_map.get("notes", ""))),
            )
            for row in reader
            if (raw := (row.get(field_map["point"]) or "").strip())
        ]
        if points:
            return points

    # No ``point`` header — treat the first column of each row as the point.
    points = []
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        cell = (row[0].strip() if row else "")
        if not cell or (index == 0 and cell.lower() in _HEADER_WORDS):
            continue
        points.append(ChecklistPoint(point=cell))
    if not points:
        raise ChecklistParseError("No checklist points found in the CSV.")
    return points


def _parse_json(text: str) -> list[ChecklistPoint]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ChecklistParseError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("points") or data.get("checklist") or []
    if not isinstance(data, list):
        raise ChecklistParseError("Expected a JSON array of points, or an object with a 'points' array.")

    points: list[ChecklistPoint] = []
    for entry in data:
        if isinstance(entry, str):
            if raw := entry.strip():
                points.append(ChecklistPoint(point=raw))
        elif isinstance(entry, dict):
            raw = (entry.get("point") or entry.get("text") or entry.get("title") or "").strip()
            if raw:
                points.append(ChecklistPoint(
                    point=raw,
                    pillar=_clean(entry.get("pillar")),
                    scope=_clean(entry.get("scope")),
                    notes=_clean(entry.get("notes")),
                ))
    if not points:
        raise ChecklistParseError("No checklist points found in the JSON.")
    return points


def _parse_text(text: str) -> list[ChecklistPoint]:
    points: list[ChecklistPoint] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # blank line or Markdown heading = structure, not a point
        if set(stripped) <= {"|", "-", ":", " ", "="}:
            continue  # Markdown table rule / horizontal rule
        cleaned = _CHECKBOX.sub("", _BULLET.sub("", stripped)).strip()
        cleaned = cleaned.strip("|").strip()  # tolerate a leading table pipe
        if cleaned:
            points.append(ChecklistPoint(point=cleaned))
    if not points:
        raise ChecklistParseError("No checklist points found in the text.")
    return points


# =============================================================================
# Running — offline KB first, live-workspace fallback
# =============================================================================

class _KBProvider:
    """A read-only provider that serves workspace snapshots from the disk KB.

    Token-free: it never contacts Fabric. A workspace with no snapshot raises
    :class:`WorkspaceAccessError`, which the runner catches to decide whether a
    live fallback is possible.
    """

    def __init__(self, store: ContextStore):
        self._store = store

    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        ctx = self._store.load(workspace_id)
        if ctx is None:
            raise WorkspaceAccessError(
                f"No offline knowledge-base snapshot for workspace {workspace_id!r}. "
                f"Crawl it once (run an audit) or supply a token for a live read."
            )
        return ctx

    def list_workspaces(self) -> list[dict]:
        rows = []
        for ws_id in self._store.workspaces():
            ctx = self._store.load(ws_id)
            rows.append({
                "id": ws_id,
                "name": ctx.name if ctx else ws_id,
                "layer": (ctx.layer.value if ctx else Layer.MIXED.value),
                "items": len(ctx.items) if ctx else None,
                "pipelines": len(ctx.pipelines) if ctx else None,
            })
        return rows


def _kb_store() -> ContextStore:
    settings = get_settings()
    return ContextStore(settings.resolve(settings.cache_dir))


def _single_registry(spec: CheckSpec) -> CheckRegistry:
    """A throwaway registry holding one check, so the engine runs only that one."""
    narrow = CheckRegistry()
    narrow.register(spec)
    return narrow


def _remediation(project_path: str | Path | None):
    from ..core.check.helpers import RemediationBook

    try:
        return load_remediation(load_project(project_path or get_settings().project_path))
    except Exception:  # noqa: BLE001 - a missing project must not stop a batch
        return RemediationBook()


def _run_spec_offline(spec: CheckSpec, workspace_ids: Sequence[str], store: ContextStore,
                      remediation) -> list[dict]:
    """Run one check over the KB snapshots and return its own results as dicts.

    Uses ``Layer.MIXED`` so the check always applies (a MIXED workspace plays
    every layer role), and filters to this check's own results so the engine's
    WS-ACCESS / WS-READ-INCOMPLETE bookkeeping rows do not leak into the report.
    """
    provider = _KBProvider(store)
    targets = [(ws_id, Layer.MIXED) for ws_id in workspace_ids]
    results: list[CheckResult] = run_engine(
        provider, targets, {}, registry=_single_registry(spec), remediation=remediation,
    )
    return [r.to_dict() for r in results if r.check_id == spec.id]


def _run_spec_live(spec: CheckSpec, workspace_id: str, token: str,
                   project_path: str | Path) -> list[dict]:
    rows = audit_service.run_check(spec.id, workspace_id, project_path, token=token)
    return [r for r in rows if r.get("check_id") == spec.id]


def _evaluate_point(
    spec: CheckSpec,
    workspace_ids: Sequence[str],
    *,
    store: ContextStore,
    remediation,
    token: str | None,
    project_path: str | Path,
) -> list[dict]:
    """Evaluate one matched check across the target workspaces, KB then live."""
    offline_ids = [w for w in workspace_ids if store.load(w) is not None]
    missing_ids = [w for w in workspace_ids if w not in offline_ids]

    evaluations: list[dict] = []
    if offline_ids:
        rows = _run_spec_offline(spec, offline_ids, store, remediation)
        evaluations.extend(_rollup_by_workspace(rows, source="kb"))

    for ws_id in missing_ids:
        if token:
            try:
                rows = _run_spec_live(spec, ws_id, token, project_path)
            except Exception as exc:  # noqa: BLE001 - one bad workspace must not stop the batch
                evaluations.append(_error_evaluation(ws_id, str(exc)))
            else:
                evaluations.extend(_rollup_by_workspace(rows, source="live"))
        else:
            evaluations.append({
                "workspace": ws_id,
                "source": "none",
                "status": "N/A",
                "objects": 0,
                "counts": {},
                "evidence": (
                    "No offline snapshot for this workspace and no token supplied — "
                    "run a normal audit once to build its knowledge base, or provide "
                    "a sign-in token to read it live."
                ),
                "recommendation": "",
            })
    return evaluations


def _rollup_by_workspace(rows: list[dict], *, source: str) -> list[dict]:
    """Collapse a check's per-object results into one row per workspace.

    A workspace check yields one row already; a notebook/pipeline check yields
    one per object, so this reports the worst status, the per-status counts, and
    the most actionable evidence for that workspace.
    """
    by_ws: dict[str, list[dict]] = {}
    for row in rows:
        by_ws.setdefault(row.get("workspace") or "", []).append(row)

    out: list[dict] = []
    for workspace, group in by_ws.items():
        counts: dict[str, int] = {}
        for row in group:
            status = row.get("status") or "N/A"
            counts[status] = counts.get(status, 0) + 1
        headline = min(group, key=lambda r: _STATUS_RANK.get(r.get("status") or "N/A", 9))
        out.append({
            "workspace": workspace,
            "source": source,
            "status": headline.get("status") or "N/A",
            "objects": len(group),
            "counts": counts,
            "evidence": headline.get("evidence") or "",
            "recommendation": headline.get("recommendation") or "",
        })
    out.sort(key=lambda r: _STATUS_RANK.get(r["status"], 9))
    return out


def _error_evaluation(workspace_id: str, message: str) -> dict:
    return {
        "workspace": workspace_id,
        "source": "live",
        "status": "N/A",
        "objects": 0,
        "counts": {},
        "evidence": f"Live read failed: {message}",
        "recommendation": "",
    }


# =============================================================================
# The batch entry point
# =============================================================================

def run_checklist(
    points: Sequence[ChecklistPoint],
    *,
    workspace_ids: Sequence[str] | None = None,
    token: str | None = None,
    run_checks: bool = True,
    project_path: str | Path | None = None,
    threshold: float = matching.DEFAULT_MATCH_THRESHOLD,
) -> dict:
    """Assess every point and, for covered automated checks, run them over the KB.

    Args:
        points: the parsed checklist.
        workspace_ids: workspaces to evaluate covered checks against. Defaults to
            every workspace that has an offline KB snapshot.
        token: optional Fabric token, used only to read a workspace that has no
            snapshot yet (live fallback). Omit for a fully offline run.
        run_checks: when ``False``, only assess/dedup — do not evaluate anything.
        project_path: project YAML for remediation text and the live fallback.
        threshold: match confidence at/above which a point is "covered".

    Returns a JSON-safe report: a summary, the target workspaces, and one entry
    per point (its coverage, closest checks, evaluations, or draft proposal).
    """
    settings = get_settings()
    resolved_project = project_path or settings.project_path
    store = _kb_store()
    remediation = _remediation(resolved_project)
    targets = list(workspace_ids) if workspace_ids else store.workspaces()

    items: list[dict] = []
    for cp in points:
        assessment = intake_service.assess_point(cp.point, threshold=threshold)
        item: dict = {
            "point": cp.point,
            "hint_pillar": cp.pillar,
            "hint_scope": cp.scope,
            "notes": cp.notes,
            "status": assessment["status"],
            "covered": assessment["covered"],
            "matches": assessment["matches"],
            "proposal": assessment["proposal"],
            "advisory": assessment["advisory"],
            "next_steps": assessment["next_steps"],
            "evaluated_check": None,
            "evaluations": [],
        }

        if assessment["covered"] and run_checks and targets:
            top_id = assessment["matches"][0]["check_id"]
            spec = REGISTRY.get(top_id)
            if spec is not None and spec.automation is Automation.AUTOMATED and not spec.manual:
                item["evaluated_check"] = top_id
                item["evaluations"] = _evaluate_point(
                    spec, targets, store=store, remediation=remediation,
                    token=token, project_path=resolved_project,
                )
            elif spec is not None:
                # Covered, but by an attestation-only check the engine never runs.
                item["advisory"] = (
                    f"Covered by {spec.id} ({spec.automation.value}) — an "
                    f"attestation-only check the engine does not evaluate. "
                    f"{assessment['advisory']}"
                )
        items.append(item)

    return {
        "summary": _summary(items, targets, run_checks=run_checks),
        "workspaces": _KBProvider(store).list_workspaces() if not workspace_ids else
                      [{"id": w, "name": w} for w in targets],
        "items": items,
    }


def _summary(items: list[dict], targets: Sequence[str], *, run_checks: bool) -> dict:
    covered = sum(1 for i in items if i["status"] == "covered")
    not_covered = sum(1 for i in items if i["status"] == "not_covered")
    invalid = sum(1 for i in items if i["status"] == "invalid")
    evaluated = sum(1 for i in items if i["evaluations"])

    verdicts: dict[str, int] = {}
    for item in items:
        for evaluation in item["evaluations"]:
            status = evaluation["status"]
            verdicts[status] = verdicts.get(status, 0) + 1

    return {
        "total_points": len(items),
        "covered": covered,
        "not_covered": not_covered,
        "invalid": invalid,
        "evaluated_points": evaluated,
        "workspaces": len(targets),
        "run_checks": run_checks,
        "verdicts": verdicts,
    }


# =============================================================================
# Reporting — a readable Markdown rendering of a batch result (CLI / export)
# =============================================================================

def render_markdown(result: dict, *, title: str = "Custom checklist assessment") -> str:
    """Render a :func:`run_checklist` result as a Markdown report."""
    summary = result["summary"]
    lines: list[str] = [f"# {title}", ""]
    lines.append(
        f"- **Points:** {summary['total_points']} "
        f"({summary['covered']} covered, {summary['not_covered']} not covered, "
        f"{summary['invalid']} invalid)"
    )
    lines.append(f"- **Evaluated over the knowledge base:** {summary['evaluated_points']} "
                 f"point(s) across {summary['workspaces']} workspace(s)")
    if summary["verdicts"]:
        verdicts = ", ".join(f"{status}: {count}" for status, count in sorted(summary["verdicts"].items()))
        lines.append(f"- **Workspace verdicts:** {verdicts}")
    lines.append("")

    for index, item in enumerate(result["items"], start=1):
        head = item["point"]
        lines.append(f"## {index}. {head}")
        if item["status"] == "covered":
            match = item["matches"][0] if item["matches"] else {}
            lines.append(
                f"**Covered** by `{match.get('check_id', '?')}` "
                f"({match.get('ref', '-')}, {match.get('pillar', '')}) — "
                f"{round(match.get('confidence', 0) * 100)}% match."
            )
            if item["evaluations"]:
                lines.append("")
                lines.append("| Workspace | Source | Verdict | Objects | Evidence |")
                lines.append("|-----------|--------|---------|--------:|----------|")
                for ev in item["evaluations"]:
                    evidence = (ev["evidence"] or "").replace("|", "\\|").replace("\n", " ")
                    lines.append(
                        f"| {ev['workspace']} | {ev['source']} | {ev['status']} "
                        f"| {ev['objects']} | {evidence[:160]} |"
                    )
            else:
                lines.append("_Not evaluated (attestation-only check, or no workspaces to run against)._")
        elif item["status"] == "not_covered":
            proposal = item.get("proposal") or {}
            lines.append(
                f"**Not yet covered.** Draft proposal: `{proposal.get('suggested_id', '?')}` "
                f"({proposal.get('pillar', '')} / {proposal.get('scope', '')}). "
                f"Author it with the `.github` checklist-author agent."
            )
        else:
            lines.append("_Invalid / empty point._")
        lines.append("")

    return "\n".join(lines)

