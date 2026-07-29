"""Fabric REST discovery adapter — the authoritative per-item source.

Wraps the existing REST provider + graph builder as a pluggable discoverer, so
the live tenant crawl becomes one source among several in the orchestrator.
"""
from __future__ import annotations

from ..clients.base import ALL_RESOURCES, Provider
from ..core.enums import Layer
from ..core.graph import DiscoverySource, KnowledgeGraph, build_graph


class FabricRestDiscoverer:
    """Discovers a workspace via the Fabric REST APIs (delegated token)."""

    source = DiscoverySource.FABRIC_REST

    def __init__(self, provider: Provider | None, layer: Layer = Layer.MIXED):
        self._provider = provider
        self._layer = layer

    def available(self) -> tuple[bool, str]:
        if self._provider is None:
            return False, "no Fabric provider (sign in first)"
        return True, ""

    def discover(self, workspace_id: str) -> KnowledgeGraph:
        context = self._provider.fetch(workspace_id, self._layer, ALL_RESOURCES)
        return build_graph(context, source=DiscoverySource.FABRIC_REST)
