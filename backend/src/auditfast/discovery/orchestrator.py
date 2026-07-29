"""Run every discovery source and merge them into one Digital Twin.

Discoverers run first and their slices are merged (deterministic node ids mean a
second source enriches the same nodes rather than duplicating them); enrichers
run last against the merged graph. Every source's outcome — ran / skipped / error
plus how much it added — is recorded on the graph as provenance, so the report
can show exactly which sources contributed and which need credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.graph import KnowledgeGraph
from .base import Discoverer, Enricher, SourceOutcome


@dataclass(slots=True)
class DiscoveryReport:
    """The merged twin plus a per-source account of what happened."""

    graph: KnowledgeGraph
    outcomes: list[SourceOutcome] = field(default_factory=list)

    def ran(self) -> list[str]:
        return [o.source.value for o in self.outcomes if o.ran]

    def skipped(self) -> dict[str, str]:
        return {o.source.value: (o.skipped_reason or o.error or "")
                for o in self.outcomes if not o.ran}


class DiscoveryOrchestrator:
    """Runs discoverers + enrichers, merging everything into one graph."""

    def __init__(self, discoverers: list[Discoverer] | None = None,
                 enrichers: list[Enricher] | None = None):
        self._discoverers = list(discoverers or [])
        self._enrichers = list(enrichers or [])

    def run(self, workspace_id: str) -> DiscoveryReport:
        combined = KnowledgeGraph(workspace_id)
        outcomes: list[SourceOutcome] = []

        for discoverer in self._discoverers:
            outcomes.append(self._run_discoverer(discoverer, workspace_id, combined))
        for enricher in self._enrichers:
            outcomes.append(self._run_enricher(enricher, combined))

        combined.properties["discovery"] = [o.to_dict() for o in outcomes]
        return DiscoveryReport(combined, outcomes)

    def _run_discoverer(self, discoverer, workspace_id, combined) -> SourceOutcome:
        try:
            ok, reason = discoverer.available()
        except Exception as exc:  # noqa: BLE001 - a bad adapter must not kill the crawl
            return SourceOutcome(discoverer.source, ran=False,
                                 error=f"availability check failed: {exc}")
        if not ok:
            return SourceOutcome(discoverer.source, ran=False, skipped_reason=reason)

        before_nodes, before_edges = len(combined), len(combined.edges())
        try:
            slice_ = discoverer.discover(workspace_id)
        except Exception as exc:  # noqa: BLE001
            return SourceOutcome(discoverer.source, ran=False, error=str(exc))
        combined.merge(slice_)
        return SourceOutcome(
            discoverer.source, ran=True,
            nodes_added=len(combined) - before_nodes,
            edges_added=len(combined.edges()) - before_edges,
        )

    def _run_enricher(self, enricher, combined) -> SourceOutcome:
        try:
            ok, reason = enricher.available()
        except Exception as exc:  # noqa: BLE001
            return SourceOutcome(enricher.source, ran=False,
                                 error=f"availability check failed: {exc}")
        if not ok:
            return SourceOutcome(enricher.source, ran=False, skipped_reason=reason)
        try:
            enriched = enricher.enrich(combined)
        except Exception as exc:  # noqa: BLE001
            return SourceOutcome(enricher.source, ran=False, error=str(exc))
        return SourceOutcome(enricher.source, ran=True, enriched=enriched)
