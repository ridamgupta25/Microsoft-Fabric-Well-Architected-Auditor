"""Node 2 (Semantic Match Router + LLM Intent Critic) tests.

Stage 1 is exercised against the real registry. Stages 2 and 3 use an injected
fake embedder (a small synonym map so zero-shared-word synonyms land close), a
fresh in-memory store, and an injected fake critic - no optional extras needed.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from auditfast.ai.orchestrator.state import (
    CustomCheck,
    LifecycleStatus,
    make_check_id,
)
from auditfast.ai.rag import semantic_router
from auditfast.ai.rag.semantic_router import IntentDecision, SemanticRouter
from auditfast.ai.rag.vector_store import VectorStore
from auditfast.core.check.registry import REGISTRY


def _check(prompt: str) -> CustomCheck:
    return CustomCheck(check_id=make_check_id(prompt), raw_prompt=prompt)


def _spec(id_: str, ref: str, title: str, description: str = ""):
    return SimpleNamespace(
        id=id_,
        ref=ref,
        title=title,
        description=description,
        pillar=SimpleNamespace(value="security"),
        scope=SimpleNamespace(value="workspace"),
    )


# A tiny synonym-projecting embedder: reload/incremental -> refresh, etc. Lets
# zero-shared-word synonyms score high without any real model.
_SYNONYMS = {
    "reload": "refresh", "reloads": "refresh", "incremental": "refresh", "refresh": "refresh",
    "public": "public", "access": "public", "private": "public",
    "git": "git", "integration": "git", "source": "git", "control": "git",
}
_AXES = ["refresh", "public", "git"]


def _fake_embed(text: str | None):
    if not text:
        return None
    mapped = {_SYNONYMS.get(w) for w in re.findall(r"[a-z]+", text.lower())}
    vec = [1.0 if a in mapped else 0.0 for a in _AXES]
    return vec if any(vec) else None


# -- Stage 1: deterministic ----------------------------------------------------

def test_exact_title_routes_default_via_stage1():
    spec = next(iter(REGISTRY))
    router = SemanticRouter(registry=REGISTRY, embedder=lambda _t: None, store=VectorStore())
    check = router.route(_check(spec.title))
    assert check.routing.is_duplicate is True
    assert check.routing.stage == "deterministic"
    assert check.routing.matched_default_id == spec.id
    assert check.lifecycle_status is LifecycleStatus.ROUTED_DEFAULT


def test_novel_prompt_is_unique_when_ai_off():
    router = SemanticRouter(registry=REGISTRY, embedder=lambda _t: None, store=VectorStore())
    check = router.route(_check("frobnicate the quux widgets xyzzy"))
    assert check.routing.is_duplicate is False
    assert check.routing.stage == "deterministic"
    assert check.lifecycle_status is LifecycleStatus.PENDING


# -- Stage 3: LLM Intent Critic ------------------------------------------------

def _synonym_router(critic):
    registry = [_spec("REFRESH-1", "9.1", "Incremental refresh")]
    return SemanticRouter(
        registry=registry, embedder=_fake_embed, store=VectorStore(), critic=critic
    )


def test_intent_critic_confirms_a_semantic_duplicate():
    def critic(_prompt, _candidates):
        return IntentDecision(True, "REFRESH-1", "same refresh intent")

    router = _synonym_router(critic)
    check = router.route(_check("reload without full reloads"))
    assert check.routing.is_duplicate is True
    assert check.routing.stage == "intent_critic"
    assert check.routing.matched_default_id == "REFRESH-1"
    assert check.lifecycle_status is LifecycleStatus.ROUTED_DEFAULT


def test_intent_critic_rejects_high_similarity_wrong_intent():
    # High cosine but the critic says the intent differs (the enabled/disabled trap).
    def critic(_prompt, _candidates):
        return IntentDecision(False, None, "opposite direction")

    router = _synonym_router(critic)
    check = router.route(_check("reload without full reloads"))
    assert check.routing.is_duplicate is False
    assert check.routing.stage == "intent_critic"
    assert check.lifecycle_status is LifecycleStatus.PENDING


# -- Stage 2: plain cosine when no critic --------------------------------------

def test_semantic_threshold_routes_default_without_a_critic():
    router = SemanticRouter(
        registry=[_spec("REFRESH-1", "9.1", "Incremental refresh")],
        embedder=_fake_embed,
        store=VectorStore(),
        critic=None,
    )
    check = router.route(_check("reload without full reloads"))
    assert check.routing.is_duplicate is True
    assert check.routing.stage == "semantic"
    assert check.routing.matched_default_id == "REFRESH-1"


def test_below_retrieve_threshold_is_unique():
    router = SemanticRouter(
        registry=[_spec("GIT-1", "11.1", "Git integration")],
        embedder=_fake_embed,
        store=VectorStore(),
        critic=None,
    )
    # 'refresh' concept vs a 'git' default -> orthogonal -> no candidate.
    check = router.route(_check("reload without full reloads"))
    assert check.routing.is_duplicate is False
    assert check.routing.stage == "semantic"
    assert check.lifecycle_status is LifecycleStatus.PENDING


# -- critic JSON parsing -------------------------------------------------------

def test_parse_decision_accepts_valid_json_and_validates_id(monkeypatch):
    monkeypatch.setattr(semantic_router, "is_enabled", lambda _ai=None: True)
    monkeypatch.setattr(
        semantic_router,
        "complete",
        lambda *_a, **_k: '{"is_match": true, "matched_id": "REFRESH-1", "reasoning": "ok"}',
    )
    from auditfast.ai.rag.vector_store import Neighbor

    decision = semantic_router.llm_intent_critic(
        "reload", [Neighbor("REFRESH-1", 0.9, {"title": "Incremental refresh"})]
    )
    assert decision.is_match is True
    assert decision.matched_id == "REFRESH-1"


def test_parse_decision_rejects_unknown_matched_id(monkeypatch):
    monkeypatch.setattr(semantic_router, "is_enabled", lambda _ai=None: True)
    monkeypatch.setattr(
        semantic_router,
        "complete",
        lambda *_a, **_k: '{"is_match": true, "matched_id": "NOPE", "reasoning": "x"}',
    )
    from auditfast.ai.rag.vector_store import Neighbor

    decision = semantic_router.llm_intent_critic(
        "reload", [Neighbor("REFRESH-1", 0.9, {"title": "t"})]
    )
    assert decision.is_match is False
    assert decision.matched_id is None


def test_critic_returns_none_when_ai_disabled():
    from auditfast.ai.rag.vector_store import Neighbor

    assert semantic_router.llm_intent_critic("x", [Neighbor("A", 0.9, {})]) is None
