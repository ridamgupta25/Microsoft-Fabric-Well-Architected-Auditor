"""Node 2 - the Semantic Match Router (+ LLM Intent Critic).

Answers *"do we already assess this?"* so a custom check that duplicates a default
check is reused instead of regenerated. Three stages, cheapest first, each
degrading safely when AI is off:

1. **Deterministic matcher** (always on) - token/ref overlap against the registered
   ``CheckSpec`` metadata via :mod:`auditfast.ai.matching`. Catches ref and
   title paraphrases for free.
2. **Semantic retrieve** (AI on) - embed the prompt and pull the top-k nearest
   default checks as *candidates* (a low cosine floor to gather, not to decide).
3. **LLM Intent Critic** (AI on) - a structured-output judge that confirms the
   candidate audits the *exact same condition and direction*, so "public access
   enabled" is never deduplicated against "public access disabled". With no critic
   available the router falls back to a plain cosine threshold on stage 2.

Design source: ``local/Planning/Semantic Search.md``.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from ...config.settings import get_settings
from ...core.check.registry import REGISTRY, CheckRegistry
from ...core.models import CheckSpec
from ..matching import match_point
from ..orchestrator import complete, is_enabled
from ..orchestrator.state import CustomCheck, LifecycleStatus, RoutingResult
from .embeddings import Vector, embed
from .vector_store import STORE, Neighbor, VectorStore

_DEFAULT_CHECKS = "default_checks"

Embedder = Callable[[str], "Vector | None"]


@dataclass(frozen=True, slots=True)
class IntentDecision:
    """The critic's verdict: does a candidate share the prompt's exact intent?"""

    is_match: bool
    matched_id: str | None
    reasoning: str


#: A critic takes the prompt and the retrieved candidates and returns a decision,
#: or ``None`` when no judge is available (AI off) so the router uses a threshold.
Critic = Callable[[str, "list[Neighbor]"], "IntentDecision | None"]


def _searchable_text(spec: CheckSpec) -> str:
    return f"{spec.title}. {spec.description}".strip()


_CRITIC_SYSTEM = (
    "You judge whether a user's Microsoft Fabric audit check duplicates an existing "
    "default check. A duplicate must assess the exact same condition AND the same "
    "direction; treat opposite conditions (e.g. 'enabled' vs 'disabled') as NOT a "
    "match. Reply with strict JSON only."
)


def llm_intent_critic(prompt: str, candidates: list[Neighbor]) -> IntentDecision | None:
    """Default critic backed by the orchestrator LLM. ``None`` when AI is off."""
    if not is_enabled() or not candidates:
        return None
    listing = "\n".join(f"- {n.id}: {n.metadata.get('title', '')}" for n in candidates)
    user = (
        f'Custom check: "{prompt}"\n\n'
        f"Candidate default checks:\n{listing}\n\n"
        'Return JSON: {"is_match": true|false, "matched_id": "<id or null>", '
        '"reasoning": "<one sentence>"}. Set is_match true only for an exact '
        "same-direction match, and matched_id to that candidate's id."
    )
    raw = complete(_CRITIC_SYSTEM, user, max_tokens=200)
    if not raw:
        return None
    return _parse_decision(raw, {n.id for n in candidates})


def _parse_decision(raw: str, valid_ids: set[str]) -> IntentDecision | None:
    """Parse the critic's JSON tolerantly; ``None`` if it isn't usable."""
    text = raw.strip()
    if text.startswith("```"):  # strip a ```json ... ``` fence
        text = text.strip("`")
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    matched_id = data.get("matched_id")
    is_match = bool(data.get("is_match")) and matched_id in valid_ids
    return IntentDecision(
        is_match=is_match,
        matched_id=matched_id if is_match else None,
        reasoning=str(data.get("reasoning", "")),
    )


class SemanticRouter:
    """Routes a custom check to an existing default check or onward as unique."""

    def __init__(
        self,
        *,
        registry: CheckRegistry = REGISTRY,
        embedder: Embedder = embed,
        store: VectorStore | None = None,
        critic: Critic | None = llm_intent_critic,
    ) -> None:
        settings = get_settings()
        self.registry = registry
        self.embedder = embedder
        self.store = store if store is not None else STORE
        self.critic = critic
        self.reuse_threshold = settings.router_reuse_threshold
        self.retrieve_threshold = settings.router_retrieve_threshold
        self.semantic_threshold = settings.router_semantic_threshold
        self.top_k = settings.router_top_k
        self._model = settings.embedding_model

    def _index_version(self) -> str:
        # Rebuild when the model changes or the catalog grows/shrinks.
        return f"{self._model}:{len(self.registry)}"

    def _ensure_index(self) -> bool:
        """Build the ``default_checks`` collection if stale. False if no embedder."""
        version = self._index_version()
        if not self.store.needs_reindex(_DEFAULT_CHECKS, version):
            return self.store.count(_DEFAULT_CHECKS) > 0
        items: list[tuple[str, Vector, dict]] = []
        for spec in self.registry:
            vector = self.embedder(_searchable_text(spec))
            if vector is None:  # embedder unavailable -> semantic stage is off
                return False
            items.append((spec.id, vector, {"ref": spec.ref, "title": spec.title}))
        self.store.index(_DEFAULT_CHECKS, items, version=version)
        return bool(items)

    def route(self, check: CustomCheck) -> CustomCheck:
        """Run Node 2 on ``check`` in place; sets ``ROUTED_DEFAULT`` or leaves it."""
        prompt = check.raw_prompt

        # Stage 1 - deterministic matcher (always on).
        matches = match_point(prompt, self.registry)
        if matches and matches[0].confidence >= self.reuse_threshold:
            top = matches[0]
            return self._route_default(
                check, top.spec.id, top.confidence, "deterministic", top.reason
            )

        # Stage 2 - semantic retrieve (AI on).
        vector = self.embedder(prompt)
        if vector is None or not self._ensure_index():
            return self._route_unique(check, 0.0, "deterministic")

        neighbors = self.store.nearest(_DEFAULT_CHECKS, vector, self.top_k)
        best_score = neighbors[0].score if neighbors else 0.0
        candidates = [n for n in neighbors if n.score >= self.retrieve_threshold]
        if not candidates:
            return self._route_unique(check, best_score, "semantic")

        # Stage 3 - LLM Intent Critic (AI on), else plain cosine threshold.
        decision = self.critic(prompt, candidates) if self.critic else None
        if decision is not None:
            if decision.is_match and decision.matched_id:
                return self._route_default(
                    check, decision.matched_id, best_score, "intent_critic", decision.reasoning
                )
            return self._route_unique(check, best_score, "intent_critic")

        if candidates[0].score >= self.semantic_threshold:
            top = candidates[0]
            return self._route_default(
                check, top.id, top.score, "semantic", top.metadata.get("title", "")
            )
        return self._route_unique(check, best_score, "semantic")

    @staticmethod
    def _route_default(
        check: CustomCheck, matched_id: str, score: float, stage: str, reasoning: str
    ) -> CustomCheck:
        check.routing = RoutingResult(
            is_duplicate=True,
            matched_default_id=matched_id,
            similarity_score=round(float(score), 3),
            stage=stage,
            reasoning=reasoning,
        )
        check.lifecycle_status = LifecycleStatus.ROUTED_DEFAULT
        return check

    @staticmethod
    def _route_unique(check: CustomCheck, score: float, stage: str) -> CustomCheck:
        check.routing = RoutingResult(
            is_duplicate=False,
            similarity_score=round(float(score), 3),
            stage=stage,
        )
        return check  # left PENDING for Node 3a


@lru_cache(maxsize=1)
def _default_router() -> SemanticRouter:
    return SemanticRouter()


def route(check: CustomCheck) -> CustomCheck:
    """Convenience: route ``check`` with the process-default router."""
    return _default_router().route(check)


__all__ = ["SemanticRouter", "IntentDecision", "llm_intent_critic", "route"]
