"""Optional LangGraph ``StateGraph`` wrapper over the existing Node 1-6 agents.

Additive and opt-in: the plain-Python :func:`pipeline.run_check` stays the default
and the source of truth. This wraps the **same** node functions in a graph so the
flow can be visualised, streamed, and paused for HITL via ``interrupt_before``
*without changing any node logic* (plan Decision 3). Importing it requires the
``graph`` extra (``pip install .[graph]``); the base install never imports it.

Parity: the edges below mirror :func:`pipeline.run_check` exactly — same guardrail
short-circuit, same router short-circuit, same identify -> (codegen | fetch |
pending) branch, the same KB-fetch-failed -> PROCESSED_CUSTOM fallback, and the
same ``PROCESSED_CUSTOM``/``KB_AUGMENTED`` gate into code-gen.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents import (
    code_gen_agent,
    fetch_code_gen_agent,
    guardrails_agent,
    kb_identifier_agent,
    kb_updater_agent,
)
from ..rag import semantic_router
from . import is_enabled
from .ai_config import AiConfig
from .state import CustomCheck, CustomCheckSession, FetchErrorClass, LifecycleStatus

Router = Callable[[CustomCheck], CustomCheck]

_READY_FOR_CODEGEN = (LifecycleStatus.PROCESSED_CUSTOM, LifecycleStatus.KB_AUGMENTED)

#: The node interrupted *before* for human review, so HITL can approve/reject
#: between code-gen and the report. Compile with a checkpointer to use it.
HITL_INTERRUPT_NODE = "approval"


class CheckState(TypedDict):
    """The single object threaded through the graph (mutated in place by the nodes)."""

    check: CustomCheck


def build_check_graph(
    session: CustomCheckSession,
    *,
    provider: object | None = None,
    router: Router | None = None,
    generator=code_gen_agent.default_generator,
    reviewer=code_gen_agent.default_reviewer,
    max_attempts: int = 3,
    ai: AiConfig | None = None,
    code_cache: dict[str, str] | None = None,
) -> StateGraph:
    """Build (uncompiled) the custom-checks graph wrapping the existing agents.

    Runtime dependencies are bound via closures so :class:`CheckState` stays a plain
    ``{check}`` — the nodes call the identical agent functions the plain pipeline uses.
    """

    def n_guardrail(state: CheckState) -> CheckState:
        guardrails_agent.screen(state["check"])
        return {"check": state["check"]}

    def n_router(state: CheckState) -> CheckState:
        check = state["check"]
        if router is not None:
            router(check)
        else:
            semantic_router.route(check, ai=ai)
        return {"check": check}

    def n_identify(state: CheckState) -> CheckState:
        kb_identifier_agent.plan(state["check"], session, ai=ai)
        return {"check": state["check"]}

    def n_fetch_code(state: CheckState) -> CheckState:
        check = state["check"]
        if check.fetch_plan is not None:
            fetch_code_gen_agent.generate_fetch_code(check, ai=ai)
        return {"check": check}

    def n_augment(state: CheckState) -> CheckState:
        check = state["check"]
        if (
            check.lifecycle_status is LifecycleStatus.PENDING
            and check.fetch_plan is not None
            and provider is not None
        ):
            if hasattr(provider, "bind_check"):
                provider.bind_check(check)
            kb_updater_agent.augment(check, provider, session)
        if check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED and is_enabled(ai):
            diag = check.kb_update.diagnostic if check.kb_update else None
            if diag in (
                FetchErrorClass.METADATA_UNAVAILABLE,
                FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED,
            ):
                check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
        return {"check": check}

    def n_codegen(state: CheckState) -> CheckState:
        code_gen_agent.generate(
            state["check"], session, generator=generator, reviewer=reviewer,
            max_attempts=max_attempts, ai=ai, code_cache=code_cache,
        )
        return {"check": state["check"]}

    def n_approval(state: CheckState) -> CheckState:
        # HITL pause point — interrupt_before this node hands control back for review.
        return {"check": state["check"]}

    def after_guardrail(state: CheckState) -> str:
        dropped = state["check"].lifecycle_status is LifecycleStatus.DROPPED_GUARDRAIL
        return "end" if dropped else "route"

    def after_router(state: CheckState) -> str:
        routed = state["check"].lifecycle_status is LifecycleStatus.ROUTED_DEFAULT
        return "end" if routed else "identify"

    def after_identify(state: CheckState) -> str:
        check = state["check"]
        if check.lifecycle_status in _READY_FOR_CODEGEN:
            return "codegen"
        if check.fetch_plan is not None:
            return "fetch_code"
        return "end"  # unrecognised — mirrors the plain pipeline (left PENDING)

    def after_augment(state: CheckState) -> str:
        return "codegen" if state["check"].lifecycle_status in _READY_FOR_CODEGEN else "end"

    graph = StateGraph(CheckState)
    graph.add_node("guardrail", n_guardrail)
    graph.add_node("route", n_router)
    graph.add_node("identify", n_identify)
    graph.add_node("fetch_code", n_fetch_code)
    graph.add_node("augment", n_augment)
    graph.add_node("codegen", n_codegen)
    graph.add_node(HITL_INTERRUPT_NODE, n_approval)

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", after_guardrail, {"route": "route", "end": END})
    graph.add_conditional_edges("route", after_router, {"identify": "identify", "end": END})
    graph.add_conditional_edges(
        "identify", after_identify,
        {"codegen": "codegen", "fetch_code": "fetch_code", "end": END},
    )
    graph.add_edge("fetch_code", "augment")
    graph.add_conditional_edges("augment", after_augment, {"codegen": "codegen", "end": END})
    graph.add_edge("codegen", HITL_INTERRUPT_NODE)
    graph.add_edge(HITL_INTERRUPT_NODE, END)
    return graph


def run_check_graph(
    check: CustomCheck,
    session: CustomCheckSession,
    *,
    provider: object | None = None,
    router: Router | None = None,
    generator=code_gen_agent.default_generator,
    reviewer=code_gen_agent.default_reviewer,
    max_attempts: int = 3,
    ai: AiConfig | None = None,
    code_cache: dict[str, str] | None = None,
) -> CustomCheck:
    """Run one check end-to-end through the compiled graph (no HITL interrupt).

    Returns the same mutated :class:`CustomCheck` the plain pipeline would, so this
    is a drop-in alternative for callers that want the graph runtime.
    """
    graph = build_check_graph(
        session, provider=provider, router=router, generator=generator,
        reviewer=reviewer, max_attempts=max_attempts, ai=ai, code_cache=code_cache,
    ).compile()
    result = graph.invoke({"check": check})
    return result["check"]


def compile_with_hitl(
    session: CustomCheckSession,
    **deps: object,
):
    """Compile the graph with an in-memory checkpointer + ``interrupt_before`` the
    approval node, so a run pauses for human review and can be resumed.

    Drive it with a ``thread_id`` config: ``invoke({"check": c}, config)`` runs to
    the interrupt, then ``invoke(None, config)`` resumes to the end.
    """
    from langgraph.checkpoint.memory import MemorySaver

    return build_check_graph(session, **deps).compile(
        checkpointer=MemorySaver(), interrupt_before=[HITL_INTERRUPT_NODE]
    )


__all__ = [
    "CheckState",
    "HITL_INTERRUPT_NODE",
    "build_check_graph",
    "run_check_graph",
    "compile_with_hitl",
]
