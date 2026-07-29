"""Digital Twin orchestration: discover a workspace, build its graph, cache it.

This is the ``Selected Workspace -> Discovery -> Knowledge Graph`` head of the
pipeline. Everything downstream (search index, audit execution, evidence) reads
the twin this module produces.

Discovery always asks the provider for *every* resource (``ALL_RESOURCES``):
completeness is the goal, not minimising calls. A refresh rebuilds and re-caches
the twin; a plain read returns the cached twin and only rebuilds when it is
missing or older than the caller's freshness bound.
"""
from __future__ import annotations

from ..clients.base import ALL_RESOURCES, Provider
from ..core.enums import Layer
from ..core.graph import KnowledgeGraph, build_graph
from ..core.graph.types import DiscoverySource
from .graph_store import GraphStore


def build_twin(
    workspace_id: str,
    provider: Provider,
    layer: Layer = Layer.MIXED,
    source: DiscoverySource = DiscoverySource.FABRIC_REST,
) -> KnowledgeGraph:
    """Crawl one workspace and return its Digital Twin (not persisted)."""
    context = provider.fetch(workspace_id, layer, ALL_RESOURCES)
    return build_graph(context, source=source)


def refresh_twin(
    workspace_id: str,
    provider: Provider,
    store: GraphStore,
    layer: Layer = Layer.MIXED,
    source: DiscoverySource = DiscoverySource.FABRIC_REST,
) -> KnowledgeGraph:
    """Rebuild a workspace's twin from live discovery and persist the snapshot."""
    graph = build_twin(workspace_id, provider, layer=layer, source=source)
    store.save(graph)
    return graph


def get_twin(
    workspace_id: str,
    store: GraphStore,
    provider: Provider | None = None,
    max_age_seconds: float | None = None,
    layer: Layer = Layer.MIXED,
) -> KnowledgeGraph | None:
    """Return a workspace's twin, refreshing only when necessary.

    * Serves the cached/persisted twin when present and fresh enough.
    * Rebuilds via ``provider`` when the twin is missing or stale (and a provider
      was supplied).
    * Returns ``None`` when no twin exists and no provider was given to build one.
    """
    cached = store.load(workspace_id)
    if cached is not None:
        if max_age_seconds is None:
            return cached
        age = store.age_seconds(workspace_id)
        if age is not None and age <= max_age_seconds:
            return cached
    if provider is not None:
        return refresh_twin(workspace_id, provider, store, layer=layer)
    return cached


def twin_summary(graph: KnowledgeGraph) -> dict:
    """A compact, report-ready overview of a twin."""
    return {
        "workspace_id": graph.workspace_id,
        "node_count": len(graph),
        "edge_count": len(graph.edges()),
        "nodes_by_type": graph.counts_by_type(),
        "access_findings": [n.to_dict() for n in graph.findings()],
        "discovery": graph.properties.get("discovery", []),
    }


def discover_twin(
    workspace_id: str,
    provider: Provider | None,
    layer: Layer = Layer.MIXED,
    admin_token: str | None = None,
    graph_token: str | None = None,
    git_details: dict | None = None,
    git_reader=None,
    name_to_id: dict[str, str] | None = None,
    enrich: bool = True,
    llm=None,
    store: "GraphStore | None" = None,
):
    """Build a twin from *every* available source, then AI-enrich it (Phase 2).

    Phase 1 (authoritative ingestion): Fabric REST from the delegated ``provider``;
    the admin-only Scanner API when ``admin_token`` is given; Git item source when
    a ``git_reader`` is given; Microsoft Graph identity when ``graph_token`` is
    given. Unavailable sources are recorded, not dropped.

    Phase 2 (derived): when ``enrich`` is set, notebook summaries and risk findings
    are produced (via ``llm``, defaulting to the offline heuristic) and stored as
    separate ``DerivedInsight`` nodes. Returns the orchestrator's report.
    """
    from ..discovery import (
        DiscoveryOrchestrator,
        FabricRestDiscoverer,
        GraphIdentityEnricher,
        ScannerApiDiscoverer,
    )
    from ..discovery.git import GitDiscoverer

    orchestrator = DiscoveryOrchestrator(
        discoverers=[
            FabricRestDiscoverer(provider, layer),
            ScannerApiDiscoverer(admin_token),
            GitDiscoverer(git_details, git_reader, name_to_id),
        ],
        enrichers=[GraphIdentityEnricher(graph_token)],
    )
    report = orchestrator.run(workspace_id)

    if enrich:
        from ..enrichment import EnrichmentPipeline, NotebookInsightEnricher

        EnrichmentPipeline([NotebookInsightEnricher(llm)]).run(report.graph)

    if store is not None:
        store.save(report.graph)
    return report
