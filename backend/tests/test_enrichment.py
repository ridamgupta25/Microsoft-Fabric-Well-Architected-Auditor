"""Tests for Phase 2 AI enrichment: derived insights, kept separate from raw."""
from __future__ import annotations

from auditfast.core.graph import DiscoverySource, EdgeType, KnowledgeGraph, Node, NodeType
from auditfast.enrichment import EnrichmentPipeline, NotebookInsightEnricher


def _graph_with_notebook(code: str) -> KnowledgeGraph:
    graph = KnowledgeGraph("ws")
    graph.add_node(Node("Notebook:nb", NodeType.NOTEBOOK, "NB", properties={"cell_count": 1}))
    graph.add_node(Node("Notebook:nb/cell/0", NodeType.NOTEBOOK_CELL, "cell",
                        properties={"source_full": code}))
    graph.add_edge("Notebook:nb", "Notebook:nb/cell/0", EdgeType.HAS_CELL)
    return graph


def test_notebook_insight_flags_secret_and_is_marked_derived():
    graph = _graph_with_notebook('password = "hunter2"\nfrom x import *')
    produced = NotebookInsightEnricher().enrich(graph)
    assert produced == 1

    insight = graph.derived_insights()[0]
    assert insight.source is DiscoverySource.DERIVED_AI
    assert insight.properties["derived"] is True
    assert insight.properties["flags"]["hardcoded_secret"] is True
    assert insight.properties["flags"]["wildcard_import"] is True
    assert insight.properties["risk"] == "high"
    # The insight hangs off the notebook it is about.
    assert graph.neighbors("Notebook:nb", EdgeType.HAS_INSIGHT)
    # Derived output is always separable from raw metadata.
    assert graph.nodes_by_source(DiscoverySource.DERIVED_AI) == graph.derived_insights()


def test_clean_notebook_is_low_risk():
    graph = _graph_with_notebook("df = spark.range(10)\ndf.write.saveAsTable('t')")
    NotebookInsightEnricher().enrich(graph)
    assert graph.derived_insights()[0].properties["risk"] == "low"


def test_pipeline_records_counts_and_uses_a_custom_llm():
    class FakeLLM:
        def complete(self, prompt: str) -> str:
            return "A concise summary."

    graph = _graph_with_notebook("x = 1")
    results = EnrichmentPipeline([NotebookInsightEnricher(FakeLLM())]).run(graph)

    assert results["derived_ai"] == 1
    assert graph.derived_insights()[0].properties["summary"] == "A concise summary."
    assert graph.properties["enrichment"]["derived_ai"] == 1
