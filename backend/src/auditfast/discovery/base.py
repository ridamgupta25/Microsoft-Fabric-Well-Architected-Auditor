"""The multi-source discovery contract.

A *discoverer* turns one source (Fabric REST, the Scanner API, ...) into a slice
of the Workspace Knowledge Graph. An *enricher* refines the merged graph with a
cross-cutting source (Microsoft Graph identity, for example) that only makes
sense once the primary slices exist. The orchestrator runs discoverers, merges
their slices into one twin, then applies enrichers.

Adding a source is one adapter — the audit engine never changes. Each adapter
reports its own availability, so a source needing credentials the caller lacks
(the admin-only Scanner API, say) is *skipped with a reason* rather than failing
the whole crawl. Completeness is the goal: what we could not read is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..core.graph import DiscoverySource, KnowledgeGraph


@dataclass(slots=True)
class SourceOutcome:
    """What one source contributed (or why it did not) — provenance for the report."""

    source: DiscoverySource
    ran: bool
    nodes_added: int = 0
    edges_added: int = 0
    enriched: int = 0
    skipped_reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source.value,
            "ran": self.ran,
            "nodes_added": self.nodes_added,
            "edges_added": self.edges_added,
            "enriched": self.enriched,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
        }


@runtime_checkable
class Discoverer(Protocol):
    """Produces a fresh graph slice for one workspace from one source."""

    source: DiscoverySource

    def available(self) -> tuple[bool, str]:
        """Return ``(can_run, reason)``; ``reason`` explains a False for the report."""
        ...

    def discover(self, workspace_id: str) -> KnowledgeGraph:
        """Return this source's contribution as a graph slice."""
        ...


@runtime_checkable
class Enricher(Protocol):
    """Refines the already-merged graph with a cross-cutting source."""

    source: DiscoverySource

    def available(self) -> tuple[bool, str]:
        ...

    def enrich(self, graph: KnowledgeGraph) -> int:
        """Mutate ``graph`` in place; return the number of nodes enriched."""
        ...
