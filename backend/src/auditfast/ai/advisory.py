"""AI-assisted re-evaluation of the advisory (non-deterministic) checks.

Grounded in the knowledge base: for each advisory check the deterministic engine
already produced, an LLM re-judges the point using the relevant workspace data
(notebook code, table/schema inventory, semantic-model list, SQL views/routines)
and returns a fresh score + evidence + recommendation. The AI *understands intent*
where the deterministic regex could only pattern-match, which is exactly why these
checks were flagged low-confidence.

Strictly optional and best-effort. When AI is disabled (the default) or anything
fails — model outage, bad JSON, missing data — the deterministic verdict is kept
unchanged. The AI never touches the deterministic scorecard: it only rewrites the
verdicts that land in the separate Advisory report.
"""
from __future__ import annotations

import json
from dataclasses import replace

from ..core.check.registry import REGISTRY
from ..core.enums import Scope
from ..core.models import CheckResult, WorkspaceContext
from ..core.scoring import status_from_score
from . import orchestrator

#: Reply contract the model must follow — a single JSON object, nothing else.
_SYSTEM = (
    "You are a Microsoft Fabric Well-Architected reviewer judging ONE best-practice "
    "check against real workspace evidence. Reply with ONLY a JSON object: "
    '{"score": <0-3 integer>, "evidence": "<one or two sentences of what you found>", '
    '"recommendation": "<what to do if not fully met>", '
    '"confidence": "high"|"medium"|"low"}. '
    "Score 3 = fully meets the practice, 2 = mostly, 1 = partially, 0 = does not meet it. "
    "Judge strictly from the evidence provided. If the evidence is insufficient to be "
    "sure, keep confidence 'low' and do not invent facts."
)

#: Keep prompts bounded so a huge notebook or table list cannot blow the token budget.
_MAX_EVIDENCE_CHARS = 6000


def evaluate(
    results: list[CheckResult],
    workspaces: dict[str, WorkspaceContext],
) -> list[CheckResult]:
    """Return advisory results re-judged by AI, or unchanged when AI is off."""
    if not orchestrator.is_enabled() or not results:
        return results
    cache: dict[tuple, CheckResult] = {}
    return [_judged(result, workspaces.get(result.workspace), cache) for result in results]


def _judged(
    result: CheckResult,
    workspace: WorkspaceContext | None,
    cache: dict[tuple, CheckResult],
) -> CheckResult:
    spec = REGISTRY.get(result.check_id)
    if spec is None:
        return result

    context = _kb_context(result, workspace)
    # Cache by (check, workspace, object, evidence) so identical inputs cost one
    # call — and stay stable across a run.
    key = (result.check_id, result.workspace, result.obj, hash(context))
    if key in cache:
        cached = cache[key]
        return replace(
            result,
            score=cached.score,
            status=cached.status,
            evidence=cached.evidence,
            recommendation=cached.recommendation,
            source="advisory-ai",
        )

    point = f"{spec.title}\n{(spec.description or (spec.fn.__doc__ or '')).strip()}"
    user = (
        f"CHECK:\n{point}\n\n"
        f"OBJECT: {result.obj or '(workspace-level)'} in workspace "
        f"'{result.workspace}' (scope: {result.scope.value})\n\n"
        f"DETERMINISTIC HEURISTIC FINDING (may be wrong): {result.status.value} - "
        f"{result.evidence}\n\n"
        f"WORKSPACE EVIDENCE (from the knowledge base):\n"
        f"{context or '(no additional data available)'}\n\n"
        "Re-judge the check from the evidence and reply with the JSON object only."
    )
    # Budget covers reasoning-model "thinking" tokens plus the JSON answer.
    verdict = _parse(orchestrator.complete(_SYSTEM, user, max_tokens=1500))
    if verdict is None:
        return result

    score, evidence, recommendation = verdict
    judged = replace(
        result,
        score=score,
        status=status_from_score(score),
        evidence=evidence,
        recommendation=recommendation or result.recommendation,
        source="advisory-ai",
    )
    cache[key] = judged
    return judged


def _parse(raw: str | None) -> tuple[int, str, str] | None:
    """Extract ``(score, evidence, recommendation)`` from the model's JSON reply."""
    if not raw:
        return None
    try:
        text = raw.strip().lstrip("`")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return None
        data = json.loads(text[start : end + 1])
        score = int(data["score"])
        if not 0 <= score <= 3:
            return None
        confidence = str(data.get("confidence", "medium")).strip().lower()
        evidence = str(data.get("evidence", "")).strip()
        recommendation = str(data.get("recommendation", "")).strip()
        label = f"[AI - {confidence} confidence]"
        return score, (f"{label} {evidence}".strip()), recommendation
    except (ValueError, KeyError, TypeError):
        return None


def _kb_context(result: CheckResult, workspace: WorkspaceContext | None) -> str:
    """The slice of the knowledge base relevant to this check, bounded in size."""
    if workspace is None:
        return ""
    if result.scope is Scope.NOTEBOOK:
        notebook = workspace.notebooks.get(result.obj)
        return _clip(_notebook_code(notebook)) if notebook else ""
    if result.scope is Scope.PIPELINE:
        pipeline = workspace.pipelines.get(result.obj)
        return _clip(json.dumps(pipeline)) if pipeline else ""
    return _clip(_workspace_summary(workspace))


def _notebook_code(notebook: dict) -> str:
    from ..core.check._notebook import notebook_code

    try:
        return notebook_code(notebook)
    except Exception:  # noqa: BLE001 - best-effort context extraction
        return ""


def _workspace_summary(workspace: WorkspaceContext) -> str:
    """A compact structural view for the workspace-scope modeling/DQ checks."""
    lines: list[str] = []
    if workspace.tables:
        lines.append("TABLES: " + ", ".join(sorted(workspace.tables)[:100]))
    if workspace.semantic_models:
        lines.append("SEMANTIC MODELS: " + ", ".join(sorted(workspace.semantic_models)[:50]))
    if workspace.sql_views:
        lines.append(
            "SQL VIEWS: " + ", ".join(v.get("name", "") for v in workspace.sql_views[:50])
        )
    if workspace.sql_routines:
        lines.append(
            "SQL ROUTINES: "
            + ", ".join(v.get("name", "") for v in workspace.sql_routines[:50])
        )
    types = workspace.item_types()
    if types:
        lines.append("ITEM TYPES: " + ", ".join(sorted(types)))
    return "\n".join(lines)


def _clip(text: str) -> str:
    return (text or "")[:_MAX_EVIDENCE_CHARS]
