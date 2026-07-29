"""Phase 2 — AI enrichment.

Phase 1 ingests *authoritative* facts (Fabric REST, Scanner, Graph, Git). Phase 2
layers *derived* knowledge on top — notebook summaries, risk findings, dependency
explanations — produced by reasoning over those facts. Derived output is written
as :class:`~auditfast.core.graph.NodeType.DERIVED_INSIGHT` nodes tagged
``source = DERIVED_AI`` and ``derived = True``, so it is always separable from the
raw metadata (``graph.nodes_by_source`` / ``graph.derived_insights``).

The reasoning is behind an :class:`LLMClient` seam. :class:`HeuristicLLM` is a
deterministic, offline stand-in so the whole pipeline is testable without a model
endpoint; a real Azure OpenAI / Foundry client that satisfies the protocol drops
in unchanged.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..core.graph import DiscoverySource, KnowledgeGraph


@runtime_checkable
class LLMClient(Protocol):
    """Any text-in / text-out model."""

    def complete(self, prompt: str) -> str:
        ...


class HeuristicLLM:
    """A deterministic, offline stand-in for a real LLM.

    Returns a stable, rule-based summary so enrichment can be exercised and tested
    without a network model. Replace with a real client for genuine reasoning.
    """

    def complete(self, prompt: str) -> str:
        first_line = next((line for line in prompt.splitlines() if line.strip()), "")
        return f"[heuristic summary] {first_line.strip()[:160]}"


@runtime_checkable
class AiEnricher(Protocol):
    """Produces derived knowledge over the already-ingested graph."""

    source: DiscoverySource

    def available(self) -> tuple[bool, str]:
        ...

    def enrich(self, graph: KnowledgeGraph) -> int:
        """Attach derived-insight nodes; return how many were produced."""
        ...


class EnrichmentPipeline:
    """Runs Phase-2 enrichers over a graph, recording per-enricher counts."""

    def __init__(self, enrichers: list[AiEnricher]):
        self._enrichers = list(enrichers)

    def run(self, graph: KnowledgeGraph) -> dict[str, int]:
        results: dict[str, int] = {}
        for enricher in self._enrichers:
            try:
                ok, _reason = enricher.available()
                if not ok:
                    continue
                results[enricher.source.value] = enricher.enrich(graph)
            except Exception:  # noqa: BLE001 - a bad enricher must not fail the run
                continue
        if results:
            enrichment = dict(graph.properties.get("enrichment") or {})
            enrichment.update(results)
            graph.properties["enrichment"] = enrichment
        return results
