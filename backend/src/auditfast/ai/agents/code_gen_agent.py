"""Node 4 - the Code Generator.

Turns a feasible custom check into a real, runnable ``BaseAuditCheck`` subclass via
a **bounded generate -> validate -> AI review -> fix** loop (max 3 attempts). Each
attempt runs three validation stages; a failure feeds concrete feedback into the
next attempt:

1. **static/safety** - the local runner's AST allow-list (:func:`validate_source`).
2. **functional** - load + smoke-run against the shared KB; the result must match
   the ``{status, score, findings, recommendations}`` contract.
3. **AI review** - an optional LLM critic confirms the code matches the check's
   intent and stays audit-only.

Fetched KB data is treated as untrusted when it feeds the generator (indirect
prompt injection). With AI off the node cannot generate, so the check is marked
``AI_REQUIRED`` rather than failing.

Design source: ``local/Planning/Generate Code - Node``.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from ..custom_runtime.local_runner import (
    UnsafeCodeError,
    load_check,
    run_check,
    validate_source,
)
from ..orchestrator import complete, is_enabled
from ..orchestrator.ai_config import AiConfig
from ..orchestrator.state import (
    CodeGenLog,
    CustomCheck,
    FeasibilityClass,
    LifecycleStatus,
)

#: Lifecycle states from which a check is ready for code generation.
_ELIGIBLE = (LifecycleStatus.PROCESSED_CUSTOM, LifecycleStatus.KB_AUGMENTED)

_CODE_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    """An AI reviewer's decision on a generated implementation."""

    approved: bool
    reason: str = ""


#: A generator maps ``(prompt, feedback)`` to source, or ``None`` when AI is off.
Generator = Callable[[str, str], "str | None"]
#: A reviewer maps ``(prompt, source)`` to a verdict, or ``None`` to skip review.
Reviewer = Callable[[str, str], "ReviewVerdict | None"]


_GEN_SYSTEM = (
    "You write a single Python class for a Microsoft Fabric audit check. The class "
    "MUST subclass BaseAuditCheck and implement evaluate(self, kb) returning a dict "
    "{'status': str, 'score': float 0-100, 'findings': list, 'recommendations': list}. "
    "\n\nKB SHAPE (read it exactly like this): kb is a dict keyed by WORKSPACE ID; each "
    "value is one workspace snapshot. Always iterate workspaces with `for ws in "
    "kb.values():` — never read item collections at the top level of kb. Inside a "
    "workspace snapshot: 'display_name' (str), 'layer' (str), 'git_connected' (bool), "
    "'deployment_pipeline' (bool); the collections 'notebooks', 'pipelines', "
    "'semantic_models', 'environments', 'tables', 'refresh_schedules', 'warehouse_audit', "
    "'activators' are DICTS keyed by item name (iterate with .items() or .values()); "
    "'items', 'reports', 'role_assignments', 'connections', 'sql_views' are LISTS of dicts. "
    "\n\nKEY FIELDS (use these exact names — do not guess): a report dict is "
    "{id, name, dataset_id, dataset_workspace_id} and IS connected to a semantic model "
    "when its 'dataset_id' is a non-empty string; read a report's name from 'name'. An "
    "item is {id, display_name, type}. A role assignment is {principal, role} (or similar). "
    "For notebooks/pipelines/semantic_models, use the collection KEY as the object name "
    "(the value is a definition dict that may be empty {} when only presence is known). "
    "A notebook/pipeline/model 'definition' value may be an empty dict when only its "
    "presence is known. Treat a missing key as absent, and if the KB contains zero of the "
    "relevant objects, return status 'N/A' with score 100 and a finding saying none were "
    "found — do NOT silently pass as if all objects complied. "
    "\n\nALWAYS populate 'findings' with concise evidence — how many items were checked "
    "and how many passed/failed (e.g. '10 of 12 notebooks have a description'), naming "
    "the specific failing objects; include a positive evidence line even when the check "
    "fully passes. ALWAYS populate 'recommendations' with concrete next steps whenever "
    "the score is below 100. "
    "\n\nThe code is READ-ONLY: it may only read the kb dict. Do NOT import os/sys/"
    "subprocess/socket/requests, do NOT open files, do NOT use eval/exec/getattr or "
    "dunder attributes. Return only the code."
)

_REVIEW_SYSTEM = (
    "You review a generated Microsoft Fabric audit check. Confirm it implements the "
    "user's intent, is read-only/audit-only, and returns the required result shape. "
    'Reply with strict JSON: {"approved": true|false, "reason": "<one sentence>"}.'
)


def _extract_code(raw: str) -> str:
    match = _CODE_FENCE.search(raw)
    return (match.group(1) if match else raw).strip()


def default_generator(prompt: str, feedback: str, *, ai: AiConfig | None = None) -> str | None:
    """LLM-backed generator. ``None`` when AI is off."""
    if not is_enabled(ai):
        return None
    user = f"Check to implement: {prompt!r}."
    if feedback:
        user += f"\n\nYour previous attempt was rejected. Fix this and try again:\n{feedback}"
    raw = complete(_GEN_SYSTEM, user, max_tokens=900, ai=ai)
    return _extract_code(raw) if raw else None


def default_reviewer(prompt: str, source: str, *, ai: AiConfig | None = None) -> ReviewVerdict | None:
    """LLM-backed reviewer. ``None`` when AI is off (review is skipped)."""
    if not is_enabled(ai):
        return None
    import json

    raw = complete(_REVIEW_SYSTEM, f"Intent: {prompt!r}\n\nCode:\n{source}", max_tokens=200, ai=ai)
    if not raw:
        return None
    text = raw.strip().strip("`")
    if "{" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return ReviewVerdict(approved=bool(data.get("approved")), reason=str(data.get("reason", "")))


def generate(
    check: CustomCheck,
    session,
    *,
    generator: Generator = default_generator,
    reviewer: Reviewer | None = default_reviewer,
    max_attempts: int = 3,
    timeout: float = 5.0,
    ai: AiConfig | None = None,
    code_cache: dict[str, str] | None = None,
) -> CustomCheck:
    """Run Node 4 on ``check`` in place, using ``session.shared_kb`` for the smoke run.

    ``code_cache`` (from cross-run memory) lets a previously-validated check reuse its
    code instead of paying for another LLM round-trip. Reused code is **still**
    re-validated and smoke-run, so the safety contract is unchanged.
    """
    if check.lifecycle_status not in _ELIGIBLE:
        return check

    # Memory: reuse validated code for this exact check when available.
    if code_cache and check.check_id in code_cache:
        cached = code_cache[check.check_id]
        ok, _ = validate_source(cached)
        if ok:
            try:
                cached_cls = load_check(cached)
                cached_result = run_check(cached_cls, session.shared_kb, timeout=timeout)
            except UnsafeCodeError:
                cached_result = {"error": True}
            if not cached_result.get("error"):
                check.code_gen = CodeGenLog(attempts=0, status="GENERATED", reason="reused from memory")
                check.generated_code = cached
                check.feasibility = FeasibilityClass.FULLY_FEASIBLE
                return check

    # Bind the per-request key into the default generator/reviewer only.
    if generator is default_generator:
        generator = partial(default_generator, ai=ai)
    if reviewer is default_reviewer:
        reviewer = partial(default_reviewer, ai=ai)

    kb = session.shared_kb
    log = CodeGenLog()
    feedback = ""

    for attempt in range(1, max_attempts + 1):
        log.attempts = attempt
        source = generator(check.raw_prompt, feedback)
        if source is None:  # AI unavailable -> cannot generate
            check.code_gen = CodeGenLog(attempts=attempt - 1, status="AI_REQUIRED",
                                        reason="code generation requires an LLM")
            check.lifecycle_status = LifecycleStatus.AI_REQUIRED
            return check

        # Stage 1 - static/safety.
        ok, reason = validate_source(source)
        if not ok:
            feedback = f"Static safety check failed: {reason}. Return only safe, read-only code."
            log.stage_failed, log.reason = "static", reason
            continue

        # Stage 2 - functional (load + smoke run against the shared KB).
        try:
            check_cls = load_check(source)
        except UnsafeCodeError as exc:
            feedback = f"Rejected as unsafe: {exc}."
            log.stage_failed, log.reason = "static", str(exc)
            continue
        result = run_check(check_cls, kb, timeout=timeout)
        if result.get("error"):
            feedback = (
                f"Functional check failed: {result['findings']}. evaluate(kb) must return "
                "{status, score 0-100, findings, recommendations}."
            )
            log.stage_failed, log.reason = "functional", str(result["findings"])
            continue

        # Stage 3 - AI review.
        verdict = reviewer(check.raw_prompt, source) if reviewer else None
        if verdict is not None and not verdict.approved:
            feedback = f"Reviewer rejected it: {verdict.reason}."
            log.stage_failed, log.reason = "review", verdict.reason
            continue

        log.status, log.stage_failed, log.reason = "GENERATED", "", ""
        check.code_gen = log
        check.generated_code = source
        check.feasibility = FeasibilityClass.FULLY_FEASIBLE
        return check

    log.status = "FAILED"
    check.code_gen = log
    if check.feasibility is None:
        check.feasibility = FeasibilityClass.NOT_FEASIBLE
    return check


__all__ = ["generate", "ReviewVerdict", "default_generator", "default_reviewer"]
