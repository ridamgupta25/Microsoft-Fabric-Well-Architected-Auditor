"""Phase 2 — AI enrichment: derived knowledge kept separate from raw metadata.

    from auditfast.enrichment import EnrichmentPipeline, NotebookInsightEnricher
    from auditfast.enrichment import HeuristicLLM, LLMClient

Derived output lands as ``DerivedInsight`` nodes (``source = DERIVED_AI``), so
``graph.nodes_by_source(DiscoverySource.DERIVED_AI)`` and
``graph.derived_insights()`` always separate Phase-2 intelligence from Phase-1 fact.
"""
from __future__ import annotations

from .base import AiEnricher, EnrichmentPipeline, HeuristicLLM, LLMClient
from .notebook_insights import NotebookInsightEnricher

__all__ = [
    "AiEnricher",
    "EnrichmentPipeline",
    "HeuristicLLM",
    "LLMClient",
    "NotebookInsightEnricher",
]
