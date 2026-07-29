"""The Workspace Knowledge Graph — the Digital Twin at the heart of the platform.

Discovery fills a :class:`KnowledgeGraph`; the audit engine reads from it. Public
surface::

    from auditfast.core.graph import KnowledgeGraph, Node, Edge, build_graph
    from auditfast.core.graph import NodeType, EdgeType, DiscoverySource
"""
from __future__ import annotations

from .builder import build_graph
from .model import Edge, KnowledgeGraph, Node, make_node_id
from .types import DiscoverySource, EdgeType, NodeType, node_type_for_item

__all__ = [
    "KnowledgeGraph",
    "Node",
    "Edge",
    "make_node_id",
    "build_graph",
    "NodeType",
    "EdgeType",
    "DiscoverySource",
    "node_type_for_item",
]
