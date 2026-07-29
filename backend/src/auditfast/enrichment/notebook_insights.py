"""Derived notebook insights — an AI-enrichment producer.

Reads each notebook's code (from whichever source supplied it — Fabric REST or
Git) out of the graph, asks the LLM for a summary, and records deterministic risk
signals (hard-coded secrets, wildcard imports, full-collect calls). The result is
a single ``DerivedInsight`` node per notebook, tagged as Phase-2 derived output so
it never contaminates the raw metadata.
"""
from __future__ import annotations

import re

from ..core.graph import DiscoverySource, EdgeType, KnowledgeGraph, Node, NodeType
from .base import HeuristicLLM, LLMClient

_SECRET = re.compile(
    r"(password|pwd|secret|api[_-]?key|access[_-]?key|account[_-]?key|"
    r"connection[_-]?string|bearer\s|sas_token)\s*[=:]",
    re.IGNORECASE,
)
_WILDCARD_IMPORT = re.compile(r"^\s*from\s+\S+\s+import\s+\*", re.MULTILINE)
_FULL_COLLECT = re.compile(r"\.(collect|toPandas)\s*\(", re.IGNORECASE)


def _notebook_code(graph: KnowledgeGraph, notebook: Node) -> str:
    parts = []
    for cell in graph.neighbors(notebook.id, EdgeType.HAS_CELL):
        parts.append(cell.properties.get("source_full")
                     or cell.properties.get("source_preview") or "")
    return "\n".join(parts)


class NotebookInsightEnricher:
    """Summarises notebooks and flags code-quality/security risks (Phase 2)."""

    source = DiscoverySource.DERIVED_AI

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm or HeuristicLLM()

    def available(self) -> tuple[bool, str]:
        return True, ""  # the heuristic LLM is always available

    def enrich(self, graph: KnowledgeGraph) -> int:
        produced = 0
        for notebook in graph.nodes_of_type(NodeType.NOTEBOOK):
            code = _notebook_code(graph, notebook)
            if not code.strip() and not notebook.properties.get("cell_count"):
                continue

            summary = self._llm.complete(
                f"Summarise the Microsoft Fabric notebook '{notebook.name}' and its "
                f"purpose in two sentences:\n{code[:4000]}"
            )
            flags = {
                "hardcoded_secret": bool(_SECRET.search(code)),
                "wildcard_import": bool(_WILDCARD_IMPORT.search(code)),
                "full_collect": bool(_FULL_COLLECT.search(code)),
            }
            risk = ("high" if flags["hardcoded_secret"]
                    else "medium" if any(flags.values()) else "low")

            insight_id = f"{notebook.id}/insight/summary"
            graph.add_node(Node(
                id=insight_id, type=NodeType.DERIVED_INSIGHT,
                name=f"AI insight — {notebook.name}", source=DiscoverySource.DERIVED_AI,
                properties={
                    "derived": True,
                    "kind": "notebook_summary",
                    "about": notebook.id,
                    "summary": summary,
                    "flags": flags,
                    "risk": risk,
                },
            ))
            graph.add_edge(notebook.id, insight_id, EdgeType.HAS_INSIGHT)
            produced += 1
        return produced
