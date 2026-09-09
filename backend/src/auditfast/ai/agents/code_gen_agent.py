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

import logging
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

_LOG = logging.getLogger("auditfast.custom_checks")

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
    "\n\nDATA-AVAILABILITY RULE: distinguish 'the field is present but empty on one object' "
    "(that single object fails the check) from 'the field is absent on EVERY relevant "
    "object' (the crawl does not capture it). If the attribute you need to evaluate is "
    "missing on every object, do NOT score them all as failing; instead return status "
    "'N/A' with score 100 and a finding like 'the knowledge base does not capture "
    "<field> for <object type>, so this check cannot be evaluated'. Only report real "
    "failures when the field genuinely exists on some objects and is empty/non-compliant "
    "on others. "
    "\n\nALWAYS populate 'findings' with concise evidence — how many items were checked "
    "and how many passed/failed (e.g. '10 of 12 notebooks have a description'), naming "
    "the specific failing objects; include a positive evidence line even when the check "
    "fully passes. ALWAYS populate 'recommendations' with concrete next steps whenever "
    "the score is below 100. "
    "\n\nMULTI-WORKSPACE EVIDENCE: when kb has more than one workspace, report findings "
    "PER workspace, not as a single aggregate. Prefix each finding with the workspace's "
    "'display_name' (e.g. \"Marketing: 2 of 3 notebooks have a description\"; \"Finance: "
    "no role assignments captured\"), and add one line per workspace so a reader can tell "
    "exactly which workspace each result came from. Iterate `for ws in kb.values():` and "
    "use `ws.get('display_name')` as the label. A short overall summary line may follow, "
    "but the per-workspace lines are required. "
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


#: A `class X(...)` header, capturing the name and the (possibly empty) base list.
_CLASS_HEADER = re.compile(r"class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:")

#: A model-invented stub ``class BaseAuditCheck: ...`` block (header + indented body
#: up to the next top-level line). Weak models often redefine the base "to be safe",
#: which shadows the real one injected into the sandbox and makes the generated
#: subclass unrecognisable. Stripping it lets the real base take effect.
_STUB_BASE = re.compile(r"^class\s+BaseAuditCheck\b.*?(?=^\S|\Z)", re.MULTILINE | re.DOTALL)


def _coerce_to_check(source: str) -> str:
    """Rescue common weak-model mistakes so a valid check still loads.

    Handles: (1) a model-defined stub ``class BaseAuditCheck`` that shadows the real
    base; (2) a check class that forgot to subclass ``BaseAuditCheck``; (3) a bare
    top-level ``def evaluate(...)`` with no class. The result is still AST-validated
    and smoke-run, so the safety contract is unchanged.
    """
    source = _STUB_BASE.sub("", source).lstrip("\n")
    if "def evaluate" not in source:
        return source  # no check method to rescue
    if re.search(r"class\s+\w+\s*\([^)]*\bBaseAuditCheck\b", source):
        return source  # already a proper subclass of the real base
    m = _CLASS_HEADER.search(source)
    if m:  # a class exists but forgot the base -> inject it
        bases = (m.group(2) or "").strip()
        new_bases = "BaseAuditCheck" if not bases else f"{bases}, BaseAuditCheck"
        return f"{source[:m.start()]}class {m.group(1)}({new_bases}):{source[m.end():]}"
    # No class at all, but a top-level `def evaluate(...)` -> wrap it in a subclass.
    dm = re.search(r"^def\s+evaluate\s*\(\s*([^)]*)\)", source, re.MULTILINE)
    if dm:
        params = dm.group(1).strip()
        if not params.split(",")[0].strip() == "self":  # bare evaluate(kb) -> add self
            source = re.sub(
                r"^def\s+evaluate\s*\(\s*",
                "def evaluate(self, " if params else "def evaluate(self",
                source, count=1, flags=re.MULTILINE,
            )
        body = "\n".join(("    " + ln) if ln.strip() else ln for ln in source.splitlines())
        return 'class GeneratedCheck(BaseAuditCheck):\n    check_id = "chk_generated"\n' + body
    return source


def default_generator(prompt: str, feedback: str, *, ai: AiConfig | None = None) -> str | None:
    """LLM-backed generator. ``None`` when AI is off."""
    if not is_enabled(ai):
        return None
    user = f"Check to implement: {prompt!r}."
    if feedback:
        user += f"\n\nYour previous attempt was rejected. Fix this and try again:\n{feedback}"
    raw = complete(_GEN_SYSTEM, user, max_tokens=900, ai=ai)
    return _coerce_to_check(_extract_code(raw)) if raw else None


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
            _LOG.warning(
                "code-gen rejected (static, attempt %s): %s\n--- generated source ---\n%s\n--- end ---",
                attempt, reason, source[:2000],
            )
            feedback = f"Static safety check failed: {reason}. Return only safe, read-only code."
            log.stage_failed, log.reason = "static", reason
            continue

        # Stage 2 - functional (load + smoke run against the shared KB).
        try:
            check_cls = load_check(source)
        except UnsafeCodeError as exc:
            _LOG.warning(
                "code-gen load rejected (attempt %s): %s\n--- generated source ---\n%s\n--- end ---",
                attempt, exc, source[:2000],
            )
            feedback = f"Rejected as unsafe: {exc}."
            log.stage_failed, log.reason = "static", str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 - exec-time load failure (e.g. blocked import)
            feedback = (
                f"Code failed to load ({type(exc).__name__}: {exc}). Write self-contained "
                "read-only code with NO import statements and no __import__; the KB is passed in."
            )
            log.stage_failed, log.reason = "load", str(exc)
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
