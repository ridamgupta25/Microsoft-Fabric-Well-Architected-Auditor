"""The Workspace Knowledge Graph — the single source of truth for auditing.

A :class:`KnowledgeGraph` is a directed property graph: discovered artifacts are
:class:`Node` objects, relationships are :class:`Edge` objects. It is the Digital
Twin of one Fabric workspace. Checks read *only* from it, so the same audit runs
identically against a freshly-crawled tenant or a persisted snapshot.

Design choices that matter:

* **Deterministic ids** (:func:`make_node_id`) mean re-crawling or merging a
  second discovery source updates the same node instead of duplicating it.
* **No dangling edges**: :meth:`KnowledgeGraph.add_edge` silently refuses an edge
  whose endpoints are not both present, so a partial crawl can never produce a
  corrupt graph.
* **Merge-friendly**: :meth:`KnowledgeGraph.merge` unions two graphs, which is
  how enrichment from multiple discovery sources is combined.
* **Round-trippable**: :meth:`to_dict` / :meth:`from_dict` give lossless JSON for
  the snapshot store.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .types import DiscoverySource, EdgeType, NodeType


def make_node_id(node_type: NodeType, native_id: str) -> str:
    """A stable, collision-free node id: ``"<Type>:<native id>"``."""
    return f"{node_type.value}:{native_id}"


@dataclass(slots=True)
class Node:
    """One discovered artifact."""

    id: str
    type: NodeType
    name: str
    source: DiscoverySource = DiscoverySource.DERIVED
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "source": self.source.value,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            name=data.get("name", ""),
            source=DiscoverySource(data.get("source", DiscoverySource.DERIVED.value)),
            properties=dict(data.get("properties") or {}),
        )


@dataclass(slots=True)
class Edge:
    """One directed relationship between two nodes."""

    src: str
    dst: str
    type: EdgeType
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity of an edge — one relationship of a kind between two nodes."""
        return (self.src, self.dst, self.type.value)

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "dst": self.dst,
            "type": self.type.value,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Edge:
        return cls(
            src=data["src"],
            dst=data["dst"],
            type=EdgeType(data["type"]),
            properties=dict(data.get("properties") or {}),
        )


class KnowledgeGraph:
    """A directed property graph describing one Fabric workspace."""

    def __init__(self, workspace_id: str, properties: dict[str, Any] | None = None):
        self.workspace_id = workspace_id
        self.properties: dict[str, Any] = dict(properties or {})
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}

    # -- nodes ---------------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        """Add a node, or merge into an existing one with the same id.

        Merging keeps the first non-empty name and layers in any new (non-null)
        properties, so a later, richer discovery source enriches rather than
        overwrites what an earlier one found.
        """
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
            return node
        for key, value in node.properties.items():
            if value is not None:
                existing.properties[key] = value
        if node.name and existing.name in ("", existing.id):
            existing.name = node.name
        return existing

    def node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def nodes_of_type(self, node_type: NodeType) -> list[Node]:
        return [n for n in self._nodes.values() if n.type is node_type]

    def nodes_by_source(self, source: DiscoverySource) -> list[Node]:
        """Every node learned from a given source — the raw/derived split."""
        return [n for n in self._nodes.values() if n.source is source]

    def derived_insights(self) -> list[Node]:
        """Phase 2 (AI-derived) knowledge, kept separate from raw metadata."""
        return self.nodes_of_type(NodeType.DERIVED_INSIGHT)

    # -- edges ---------------------------------------------------------------
    def add_edge(
        self, src: str, dst: str, edge_type: EdgeType, **properties: Any
    ) -> Edge | None:
        """Connect two existing nodes. Returns ``None`` if either endpoint is absent."""
        if src not in self._nodes or dst not in self._nodes:
            return None
        edge = Edge(src, dst, edge_type, dict(properties))
        return self._edges.setdefault(edge.key, edge)

    def edges(self) -> list[Edge]:
        return list(self._edges.values())

    def out_edges(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e for e in self._edges.values()
            if e.src == node_id and (edge_type is None or e.type is edge_type)
        ]

    def in_edges(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            e for e in self._edges.values()
            if e.dst == node_id and (edge_type is None or e.type is edge_type)
        ]

    def neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None = None,
        direction: str = "out",
    ) -> list[Node]:
        """Nodes reachable from ``node_id`` (``direction`` = ``out``/``in``/``any``)."""
        found: dict[str, Node] = {}
        if direction in ("out", "any"):
            for edge in self.out_edges(node_id, edge_type):
                target = self._nodes.get(edge.dst)
                if target is not None:
                    found[target.id] = target
        if direction in ("in", "any"):
            for edge in self.in_edges(node_id, edge_type):
                target = self._nodes.get(edge.src)
                if target is not None:
                    found[target.id] = target
        return list(found.values())

    # -- summaries -----------------------------------------------------------
    def counts_by_type(self) -> dict[str, int]:
        return dict(Counter(n.type.value for n in self._nodes.values()))

    def findings(self) -> list[Node]:
        """Every access/permission finding recorded during discovery."""
        return self.nodes_of_type(NodeType.ACCESS_FINDING)

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    # -- combine & persist ---------------------------------------------------
    def merge(self, other: KnowledgeGraph) -> None:
        """Union another graph into this one (nodes first, then edges)."""
        for node in other.nodes():
            self.add_node(node)
        for edge in other.edges():
            self.add_edge(edge.src, edge.dst, edge.type, **edge.properties)
        for key, value in other.properties.items():
            self.properties.setdefault(key, value)

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "properties": self.properties,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeGraph:
        graph = cls(data.get("workspace_id", ""), data.get("properties"))
        for raw in data.get("nodes", []):
            graph.add_node(Node.from_dict(raw))
        for raw in data.get("edges", []):
            edge = Edge.from_dict(raw)
            graph.add_edge(edge.src, edge.dst, edge.type, **edge.properties)
        return graph
