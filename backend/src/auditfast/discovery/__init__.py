"""Multi-source discovery — the head of the audit pipeline.

Turns a selected workspace into one merged Knowledge Graph, pulling from every
available source:

* :class:`FabricRestDiscoverer`   — authoritative per-item metadata + code (delegated token)
* :class:`ScannerApiDiscoverer`   — tenant-wide subartifacts + lineage (admin / service principal)
* :class:`GraphIdentityEnricher`  — Entra identity for principals (Graph token)

Sources that lack credentials are skipped with a reason, never silently dropped.
:mod:`.coverage` maps each data need to the source(s) that can supply it.
"""
from __future__ import annotations

from .base import Discoverer, Enricher, SourceOutcome
from .coverage import COVERAGE, UNOBTAINABLE, coverage_report, is_obtainable, sources_for
from .fabric_rest import FabricRestDiscoverer
from .git import GitDiscoverer, notebooks_from_git_files
from .graph_identity import GraphIdentityEnricher, enrich_principals
from .local_files import LocalExportDiscoverer, read_export_folder
from .orchestrator import DiscoveryOrchestrator, DiscoveryReport
from .scanner import ScannerApiDiscoverer, scan_result_to_graph

__all__ = [
    "Discoverer",
    "Enricher",
    "SourceOutcome",
    "DiscoveryOrchestrator",
    "DiscoveryReport",
    "FabricRestDiscoverer",
    "ScannerApiDiscoverer",
    "scan_result_to_graph",
    "GitDiscoverer",
    "notebooks_from_git_files",
    "LocalExportDiscoverer",
    "read_export_folder",
    "GraphIdentityEnricher",
    "enrich_principals",
    "COVERAGE",
    "UNOBTAINABLE",
    "coverage_report",
    "is_obtainable",
    "sources_for",
]
