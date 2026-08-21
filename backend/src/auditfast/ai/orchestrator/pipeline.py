"""Node 6 - pipeline wiring, HITL gate, and the custom-checks report.

Runs a batch of plain-English checks through every node in order, stopping each at
its terminal status, then exposes the full ledger for human approval and renders a
**separate** custom-checks report. Custom scores are 0-100 and never mix with the
deterministic 0-3 scorecard.

This is the graph. It deliberately does not depend on LangGraph - the nodes are
plain functions, so the pipeline is a readable, testable sequence. (A LangGraph
``TypedDict`` wrapper with ``interrupt_before`` can wrap these later without
changing them.)
"""
from __future__ import annotations

from collections.abc import Callable

from ..agents import code_gen_agent, guardrails_agent, kb_identifier_agent, kb_updater_agent
from ..agents.kb_updater_agent import FetchProvider
from ..custom_runtime.local_runner import load_and_run
from ..rag import semantic_router
from . import kb_source
from .state import CustomCheck, CustomCheckSession, LifecycleStatus

Router = Callable[[CustomCheck], CustomCheck]

_READY_FOR_CODEGEN = (LifecycleStatus.PROCESSED_CUSTOM, LifecycleStatus.KB_AUGMENTED)


def run_check(
    check: CustomCheck,
    session: CustomCheckSession,
    *,
    provider: FetchProvider | None = None,
    router: Router = semantic_router.route,
    generator=code_gen_agent.default_generator,
    reviewer=code_gen_agent.default_reviewer,
    max_attempts: int = 3,
) -> CustomCheck:
    """Drive one check through Nodes 1 -> 4, stopping at its terminal status."""
    guardrails_agent.screen(check)  # Node 1
    if check.lifecycle_status is LifecycleStatus.DROPPED_GUARDRAIL:
        return check

    router(check)  # Node 2
    if check.lifecycle_status is LifecycleStatus.ROUTED_DEFAULT:
        return check

    kb_identifier_agent.plan(check, session)  # Node 3a

    if (
        check.lifecycle_status is LifecycleStatus.PENDING
        and check.fetch_plan is not None
        and provider is not None
    ):
        kb_updater_agent.augment(check, provider, session)  # Node 3b
    # No provider -> the check stays PENDING (data needed, none available).

    if check.lifecycle_status in _READY_FOR_CODEGEN:
        code_gen_agent.generate(  # Node 4
            check, session, generator=generator, reviewer=reviewer, max_attempts=max_attempts
        )
    return check


def run_batch(
    prompts: list[str],
    *,
    session: CustomCheckSession | None = None,
    provider: FetchProvider | None = None,
    router: Router = semantic_router.route,
    generator=code_gen_agent.default_generator,
    reviewer=code_gen_agent.default_reviewer,
    max_attempts: int = 3,
) -> CustomCheckSession:
    """Run every prompt through the pipeline into one shared session."""
    session = session or CustomCheckSession()
    for prompt in prompts:
        check = session.add(prompt)
        run_check(
            check, session, provider=provider, router=router,
            generator=generator, reviewer=reviewer, max_attempts=max_attempts,
        )
    return session


def run_custom_checks(
    prompts: list[str],
    contexts: object,
    *,
    seed: bool = True,
    router: Router = semantic_router.route,
    generator=code_gen_agent.default_generator,
    reviewer=code_gen_agent.default_reviewer,
    max_attempts: int = 3,
) -> CustomCheckSession:
    """Run ``prompts`` against already-crawled ``contexts`` (read-only, no HTTP).

    Seeds the shared KB from the crawled snapshot(s) and serves any missing field
    from the same snapshot via :class:`SnapshotFetchProvider` - so the pipeline
    works against real workspace data without issuing a single new API call.
    """
    session = CustomCheckSession()
    if seed:
        kb_source.seed_session(session, contexts)
    provider = kb_source.SnapshotFetchProvider(contexts)
    return run_batch(
        prompts, session=session, provider=provider, router=router,
        generator=generator, reviewer=reviewer, max_attempts=max_attempts,
    )


# -- HITL gate (Node 6) --------------------------------------------------------

def approve(check: CustomCheck) -> CustomCheck:
    check.approved = True
    return check


def reject(check: CustomCheck) -> CustomCheck:
    check.approved = False
    return check


def pending_review(session: CustomCheckSession) -> list[CustomCheck]:
    """Every check still awaiting a human decision."""
    return [c for c in session.checks if c.approved is None]


# -- report --------------------------------------------------------------------

def _status_counts(session: CustomCheckSession) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in session.checks:
        key = check.lifecycle_status.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def render_report(session: CustomCheckSession) -> str:
    """A Markdown custom-checks report: ledger, approved results, exclusions.

    Approved, generated checks are run against the shared KB to produce their
    0-100 score at render time - kept in their own section, never blended with the
    deterministic 0-3 scorecard.
    """
    lines: list[str] = ["# Custom Checks Report", ""]

    counts = _status_counts(session)
    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for status, count in sorted(counts.items()):
        lines.append(f"| `{status}` | {count} |")
    lines.append("")

    lines.append("## Ledger")
    lines.append("")
    lines.append("| Check | Status | Feasibility | Approved |")
    lines.append("|---|---|---|---|")
    for check in session.checks:
        feas = check.feasibility.value if check.feasibility else "-"
        approved = {True: "yes", False: "no", None: "pending"}[check.approved]
        prompt = check.raw_prompt if len(check.raw_prompt) <= 60 else check.raw_prompt[:57] + "..."
        lines.append(f"| {prompt} | `{check.lifecycle_status.value}` | {feas} | {approved} |")
    lines.append("")

    approved_generated = [
        c for c in session.checks if c.approved is True and c.generated_code
    ]
    if approved_generated:
        lines.append("## Custom Checks (0-100, separate from the 0-3 scorecard)")
        lines.append("")
        for check in approved_generated:
            result = load_and_run(check.generated_code, session.shared_kb)
            lines.append(f"### {check.raw_prompt}")
            lines.append("")
            lines.append(f"- **Status:** {result['status']}")
            lines.append(f"- **Score:** {result['score']:.0f} / 100")
            if result.get("findings"):
                lines.append(f"- **Findings:** {'; '.join(map(str, result['findings']))}")
            if result.get("recommendations"):
                lines.append(
                    f"- **Recommendations:** {'; '.join(map(str, result['recommendations']))}"
                )
            lines.append("")

    excluded = [
        c
        for c in session.checks
        if c.lifecycle_status
        in (LifecycleStatus.DROPPED_GUARDRAIL, LifecycleStatus.KB_FETCH_FAILED,
            LifecycleStatus.AI_REQUIRED)
        or c.approved is False
    ]
    if excluded:
        lines.append("## Not evaluated")
        lines.append("")
        for check in excluded:
            reason = _exclusion_reason(check)
            lines.append(f"- **{check.raw_prompt}** — {reason}")
        lines.append("")

    return "\n".join(lines)


def _exclusion_reason(check: CustomCheck) -> str:
    if check.approved is False:
        return "rejected by reviewer"
    if check.lifecycle_status is LifecycleStatus.DROPPED_GUARDRAIL and check.guardrail:
        return f"dropped by guardrail ({check.guardrail.failed_validator}): {check.guardrail.reason}"
    if check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED and check.kb_update:
        return f"data unavailable ({check.kb_update.diagnostic.value if check.kb_update.diagnostic else 'unknown'}): {check.kb_update.remediation}"
    if check.lifecycle_status is LifecycleStatus.AI_REQUIRED:
        return "needs an LLM (AI is disabled)"
    return check.lifecycle_status.value


__all__ = [
    "run_check",
    "run_batch",
    "run_custom_checks",
    "approve",
    "reject",
    "pending_review",
    "render_report",
]
