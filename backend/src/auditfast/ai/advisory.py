"""AI-assisted re-evaluation of the advisory (non-deterministic) checks.

Grounded in the knowledge base and, wherever a judging guide exists, routed
through **the same machinery the offline agent uses**: the model is shown the
guide and the evidence and asked to LABEL each object, and
:mod:`.classify` turns those labels into a score. The model never returns a
score, so it cannot invent a number, and the keyed path and the agent path
cannot disagree about arithmetic - only about labels.

Checks that have no guide yet fall back to the older per-finding path, where the
model is asked for a score directly. That path is less constrained; it exists so
a check without a guide still reaches the report rather than being stranded.

Strictly optional and best-effort. When AI is disabled (the default) or anything
fails - model outage, bad JSON, missing data - the deterministic verdict is kept
unchanged. The AI never touches the deterministic scorecard: it only rewrites the
verdicts that land in the separate Advisory report.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

from ..core.check.registry import REGISTRY
from ..core.enums import Scope
from ..core.judging import guide_for
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

#: Workspace summaries carry the item list, role assignments and per-table
#: columns, and are the whole evidence for the table-modelling, audit and
#: monitoring checks, so they get more room than a single notebook.
_MAX_WORKSPACE_CHARS = 24000


#: Reply contract for the guide-driven path: the model returns LABELS, never a
#: score. Scoring stays in :mod:`.classify`, which is what keeps the keyed and
#: the agent paths arithmetically identical.
_LABEL_SYSTEM = (
    "You are a Microsoft Fabric Well-Architected reviewer. You will be given one "
    "check, the labels it allows, and a list of objects with their evidence. "
    "Label EVERY object from its own evidence. "
    'Reply with ONLY a JSON array: [{"object": "<id exactly as given>", '
    '"label": "<one of the allowed labels>", "reason": "<short clause citing the '
    'evidence>", "confidence": "high"|"medium"|"low"}]. '
    "Do not return a score - scoring is not your job. "
    "Never use a label outside the allowed list. "
    "Use the undetermined label only when the evidence could not be read; "
    '"I am unsure" is not undetermined. '
    "A label that contradicts the evidence shown for that object is the worst "
    "error you can make."
)

#: One reply line per object is short, but a chunk can hold many objects.
_LABEL_MAX_TOKENS = 4000


def evaluate(
    results: list[CheckResult],
    workspaces: dict[str, WorkspaceContext],
) -> list[CheckResult]:
    """Return advisory results re-judged by AI, or unchanged when AI is off."""
    if not orchestrator.is_enabled() or not results:
        return results

    guided = [r for r in results if guide_for(r.check_id) is not None]
    plain = [r for r in results if guide_for(r.check_id) is None]

    judged, unbuilt = _judge_with_guides(guided, workspaces) if guided else ([], [])

    # Anything without a guide, and anything whose job could not be built (no
    # workspace slice, or the evidence builder found nothing), still gets a look
    # via the older per-finding path rather than being left unjudged.
    cache: dict[tuple, CheckResult] = {}
    fallback = [_judged(r, workspaces.get(r.workspace), cache) for r in plain + unbuilt]
    return judged + fallback


def _judge_with_guides(
    results: list[CheckResult],
    workspaces: dict[str, WorkspaceContext],
) -> tuple[list[CheckResult], list[CheckResult]]:
    """``(judged, unbuilt)`` - label each object, then let ``classify`` score it.

    ``unbuilt`` is whatever could not be turned into a job, for the caller to
    put through the fallback path. A model that returns nothing usable is NOT
    unbuilt: ``apply_labels`` keeps the deterministic verdict for those
    findings, which is the honest outcome rather than a second guess.
    """
    from .jobs import build_job
    from .labels import apply_labels

    by_check: dict[str, list[CheckResult]] = {}
    for result in results:
        by_check.setdefault(result.check_id, []).append(result)

    jobs: dict[str, dict] = {}
    labels: dict[str, dict[str, dict]] = {}
    unbuilt: list[CheckResult] = []

    for check_id, rows in sorted(by_check.items()):
        workspace = workspaces.get(rows[0].workspace)
        job = build_job(check_id, rows, workspace) if workspace else None
        if job is None:
            unbuilt.extend(rows)
            continue
        jobs[check_id] = job
        given = label_job(job)
        if given:
            labels[check_id] = given

    if not jobs:
        return [], results

    try:
        judged, _summary = apply_labels(jobs, labels, judged_by="ai")
    except Exception:  # noqa: BLE001 - advisory path must never raise
        return [], results
    return judged, unbuilt


def label_job(job: dict, *, credentials=None) -> dict[str, dict]:
    """``{object_id: {label, reason, confidence}}`` for one job, chunk by chunk.

    An unparseable or missing reply for a chunk simply leaves those objects
    unlabelled, and ``apply_labels`` keeps their deterministic verdict.

    ``credentials`` routes the call to a caller-supplied key instead of the
    configured provider, which is how a signed-in user judges with their own
    model without that key being stored anywhere.
    """
    allowed = set(job.get("labels") or ()) | {job.get("undetermined_label", "undetermined")}
    out: dict[str, dict] = {}

    for chunk in job.get("chunks", []):
        objects = chunk.get("objects") or []
        if not objects:
            continue
        lines = [
            f"--- OBJECT: {obj.get('id', '')}\n"
            f"the rule concluded: {obj.get('rule_says') or '(no per-object verdict)'}\n"
            f"{obj.get('facts', '')}"
            for obj in objects
        ]
        user = (
            f"CHECK: {job.get('title', '')}\n"
            f"QUESTION: {job.get('question', '')}\n\n"
            f"HOW TO LABEL:\n{job.get('instruction', '')}\n\n"
            f"ALLOWED LABELS: {', '.join(sorted(allowed))}\n\n"
            f"Label all {len(objects)} object(s) below.\n\n"
            + "\n\n".join(lines)
        )
        for obj_id, entry in _parse_labels(
            orchestrator.complete(
                _LABEL_SYSTEM, user,
                max_tokens=_LABEL_MAX_TOKENS,
                credentials=credentials,
            )
        ).items():
            if entry["label"] in allowed:
                out[obj_id] = entry

    return out


def _parse_labels(raw: str | None) -> dict[str, dict]:
    """Parse the JSON array of labels, tolerating fenced or chatty replies."""
    if not raw:
        return {}
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            return {}
        text = text[start : end + 1]
    try:
        rows = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(rows, list):
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # NOT stripped: a Fabric item may genuinely be named with a trailing
        # space, and the job carries the real name.
        obj = row.get("object")
        label = row.get("label")
        if not isinstance(obj, str) or not isinstance(label, str) or not label.strip():
            continue
        out[obj] = {
            "label": label.strip(),
            "reason": str(row.get("reason") or "").strip(),
            "confidence": str(row.get("confidence") or "").strip(),
        }
    return out


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
    if result.scope is Scope.SEMANTIC_MODEL:
        model = (workspace.semantic_models or {}).get(result.obj)
        # Falls back to the workspace view when the model could not be read, so
        # the check still sees the estate rather than nothing.
        if model:
            return _clip(_model_summary(result.obj, model), _MAX_WORKSPACE_CHARS)
    # A workspace summary now carries per-table columns, which is what the
    # table-modelling checks are actually about, so it gets a wider budget than
    # a single notebook or pipeline. Truncating it to 6k cut the column lists
    # off after a handful of tables - the very evidence the check needs.
    return _clip(_workspace_summary(workspace), _MAX_WORKSPACE_CHARS)


def _model_summary(name: str, model: dict) -> str:
    """One semantic model's own structure: relationships, measures, storage.

    Semantic-model checks used to fall through to the workspace summary, so a
    check asking "do relationships join on surrogate keys?" (5.4.1, the largest
    single ref on a real estate at 411 findings) was handed a list of *tables*
    and never saw a relationship at all. The model's declared structure is what
    those checks are about, and the crawl already parses it.
    """
    lines = [f"SEMANTIC MODEL: {name}"]

    modes = sorted({
        str(m).lower()
        for facts in (model.get("storage") or {}).values()
        for m in (facts.get("modes") or [])
    })
    if modes:
        lines.append(f"STORAGE MODES: {', '.join(modes)}")
    if model.get("direct_lake_behavior"):
        lines.append(f"DIRECT LAKE BEHAVIOUR: {model['direct_lake_behavior']}")

    tables = model.get("tables") or []
    if tables:
        lines.append(f"TABLES ({len(tables)}): " + ", ".join(str(t) for t in tables[:60]))

    relationships = model.get("relationships") or []
    lines.append(f"RELATIONSHIPS ({len(relationships)}):")
    for rel in relationships[:_MAX_MODEL_RELATIONSHIPS]:
        cardinality = "->".join(
            c or "default" for c in (rel.get("from_cardinality"), rel.get("to_cardinality"))
        )
        lines.append(
            f"  {rel.get('from_table', '')}[{rel.get('from_column', '')}]"
            f" -> {rel.get('to_table', '')}[{rel.get('to_column', '')}]"
            f"  cardinality={cardinality}"
            f"  cross_filter={rel.get('cross_filter') or 'default'}"
        )
    if len(relationships) > _MAX_MODEL_RELATIONSHIPS:
        lines.append(f"  ... and {len(relationships) - _MAX_MODEL_RELATIONSHIPS} more")

    aggregations = model.get("aggregations") or []
    if aggregations:
        lines.append(
            f"AGGREGATION COLUMNS ({len(aggregations)}): "
            + ", ".join(f"{a.get('table', '')}.{a.get('column', '')}"
                        for a in aggregations[:20])
        )

    measures = model.get("measures") or []
    lines.append(f"MEASURES ({len(measures)}):")
    for measure in measures[:_MAX_MODEL_MEASURES]:
        expression = " ".join(str(measure.get("expression") or "").split())
        lines.append(
            f"  {measure.get('table', '')}[{measure.get('name', '')}] = "
            f"{expression[:_MAX_MEASURE_CHARS]}"
        )
    if len(measures) > _MAX_MODEL_MEASURES:
        lines.append(f"  ... and {len(measures) - _MAX_MODEL_MEASURES} more")

    return "\n".join(lines)


def _notebook_code(notebook: dict) -> str:
    from ..core.check._notebook import notebook_code

    try:
        return notebook_code(notebook)
    except Exception:  # noqa: BLE001 - best-effort context extraction
        return ""


#: How many tables to describe with their columns, and how many columns each.
#: The budget is finite (``_MAX_WORKSPACE_CHARS``), so it is spent on a readable
#: sample of real tables rather than an exhaustive list of names.
_MAX_TABLES = 40
_MAX_COLUMNS_PER_TABLE = 25

#: Items and role assignments are one line each and answer questions no other
#: section can - which item serves Gold, when it last ran, who may write here.
_MAX_ITEMS = 60
_MAX_ROLES = 30

#: Bounds on a semantic-model summary. A relationship is one line and cheap; a
#: measure carries a DAX expression, so it is clipped per measure as well.
_MAX_MODEL_RELATIONSHIPS = 60
_MAX_MODEL_MEASURES = 40
_MAX_MEASURE_CHARS = 300

#: A dataflow/staging artefact: a 32-hex-digit name, usually with a URL-encoded
#: GUID suffix (``_002D`` is an encoded ``-``). ``is_platform_table`` matches
#: known platform *names*, so it cannot catch these - they are named after a
#: run, not a thing. They sort to the top of an alphabetical list because they
#: start with digits, so on a real estate they filled the sample the model sees
#: with rows like "(1 cols): column1" while the actual solution tables were cut.
#:
#: The encoded-GUID alternative requires the run-id prefix rather than matching
#: ``_002d`` anywhere: unanchored, it would hide a real table that merely
#: contains that sequence, and hiding evidence from the judge fails closed.
_GENERATED_TABLE = re.compile(r"^[0-9a-f]{32}(?:_|$)", re.IGNORECASE)


def _is_noise(name: str) -> bool:
    """True for a table that tells a reviewer nothing about the solution."""
    from ..core.check._tables import is_platform_table

    leaf = (name or "").split(".")[-1]
    return is_platform_table(name) or bool(_GENERATED_TABLE.search(leaf))


def _workspace_summary(workspace: WorkspaceContext) -> str:
    """A compact structural view for the workspace-scope modeling/DQ checks.

    **Columns, not just table names.** An earlier version listed table names
    only, which made the workspace-scope advisory checks unanswerable: a check
    asking "do tables carry audit columns (created_date, batch_id)?" was handed a
    list of table names and no columns, so the model could not judge it at all -
    strictly less information than the deterministic rule it was meant to
    improve on. Column names are the evidence those checks are actually about.

    Platform bookkeeping and dataflow staging artefacts are skipped. On a real
    estate they outnumbered the solution tables two to one and, being named after
    GUIDs, sorted to the front of the sample - so the model saw pages of
    ``(1 cols): column1`` and none of the tables the check was about.

    **The sample is spread, and declared as a sample.** Taking the first 40 names
    alphabetically shows one end of the estate: tables are not randomly named, so
    an A-to-D slice can contain every staging table and no dimension, and the
    model would then score "do tables carry audit columns?" across the whole
    workspace having seen one corner of it. Every *n*-th table is taken instead,
    and the header says how many of how many - so a verdict can be qualified
    rather than overstated.
    """
    # The short sections go first. The table dump is the only unbounded part, so
    # anything after it is what a truncation silently removes - and losing the
    # semantic-model and SQL-view lists costs a check its context entirely.
    lines: list[str] = []

    # Items with their run stamps. Several workspace checks ask which item is a
    # serving store, a monitoring job or an orchestrator, and how recently it
    # ran; without the list they can only be answered from a name vocabulary,
    # which is the very failure that put them on the advisory list.
    items = list(workspace.items or [])
    if items:
        lines.append(f"ITEMS ({len(items)}):")
        for item in items[:_MAX_ITEMS]:
            run = f"  last_run={item.last_run_utc}" if item.last_run_utc else ""
            label = f" [{item.sensitivity_label}]" if item.sensitivity_label else ""
            lines.append(f"  {item.display_name} ({item.type}){label}{run}")
        if len(items) > _MAX_ITEMS:
            lines.append(f"  ... and {len(items) - _MAX_ITEMS} more item(s)")

    if workspace.notebooks:
        lines.append(
            f"NOTEBOOKS ({len(workspace.notebooks)}): "
            + ", ".join(sorted(workspace.notebooks)[:40])
        )
    if workspace.pipelines:
        lines.append(
            f"PIPELINES ({len(workspace.pipelines)}): "
            + ", ".join(sorted(workspace.pipelines)[:40])
        )

    # Who can write here. The metadata-access check cannot judge "only service
    # identities write" without seeing the principals.
    roles = list(workspace.role_assignments or [])
    if roles:
        lines.append(f"ROLE ASSIGNMENTS ({len(roles)}):")
        for role in roles[:_MAX_ROLES]:
            lines.append(
                f"  {role.display_name} ({role.principal_type}) = {role.role}"
            )
        if len(roles) > _MAX_ROLES:
            lines.append(f"  ... and {len(roles) - _MAX_ROLES} more")

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

    tables = workspace.tables or {}
    if tables:
        solution = [n for n in sorted(tables) if not _is_noise(n)]
        skipped = len(tables) - len(solution)
        if len(solution) > _MAX_TABLES:
            step = len(solution) / _MAX_TABLES
            sample = [solution[int(i * step)] for i in range(_MAX_TABLES)]
            header = (f"TABLES ({len(solution)} solution table(s)"
                      + (f", {skipped} platform/staging table(s) omitted" if skipped else "")
                      + f"; SAMPLE of {len(sample)} spread across the estate - "
                      + "judge accordingly and say so if the sample is not enough):")
        else:
            sample = solution
            header = (f"TABLES (all {len(solution)} solution table(s)"
                      + (f", {skipped} platform/staging table(s) omitted" if skipped else "")
                      + ", with their columns):")
        lines.append(header)
        for name in sample:
            lines.append(f"  {name}{_columns_line(tables.get(name) or {})}")
    return "\n".join(lines)


def _columns_line(table: dict) -> str:
    """``(n cols): a, b, c`` for one table, marking any column that is masked.

    The masking flag matters on its own: the Dynamic Data Masking check asks
    whether sensitive columns are protected, and without it the reader sees the
    column names and has no way to tell.
    """
    from ..core.check._tables import columns

    cols = columns(table)
    if not cols:
        return ": (columns not readable)"
    shown = []
    for col in cols[:_MAX_COLUMNS_PER_TABLE]:
        name = str(col.get("name") or "")
        shown.append(f"{name} [masked]" if col.get("is_masked") else name)
    more = (f", +{len(cols) - _MAX_COLUMNS_PER_TABLE} more"
            if len(cols) > _MAX_COLUMNS_PER_TABLE else "")
    return f" ({len(cols)} cols): {', '.join(shown)}{more}"


def _clip(text: str, limit: int = _MAX_EVIDENCE_CHARS) -> str:
    """Cut to ``limit``, saying so when anything was removed.

    A bare slice looks complete to a model: a summary cut at exactly the limit
    reads as the whole estate, and the verdict is stated with confidence it has
    not earned.
    """
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated: {len(text) - limit} more characters]"
