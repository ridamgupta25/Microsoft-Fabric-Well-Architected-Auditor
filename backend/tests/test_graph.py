"""Tests for the Workspace Knowledge Graph (Digital Twin): model, builder, store."""
from __future__ import annotations

from auditfast.core.enums import Resource
from auditfast.core.graph import (
    DiscoverySource,
    Edge,
    EdgeType,
    KnowledgeGraph,
    Node,
    NodeType,
    build_graph,
    make_node_id,
)
from auditfast.core.models import WorkspaceContext
from auditfast.services import twin_service
from auditfast.services.graph_store import GraphStore

# -- model --------------------------------------------------------------------


def test_node_and_edge_round_trip():
    node = Node(id="Workspace:1", type=NodeType.WORKSPACE, name="W",
                source=DiscoverySource.FABRIC_REST, properties={"a": 1})
    assert Node.from_dict(node.to_dict()) == node

    edge = Edge("Workspace:1", "Capacity:2", EdgeType.ASSIGNED_TO_CAPACITY, {"x": "y"})
    assert Edge.from_dict(edge.to_dict()) == edge


def test_graph_rejects_dangling_edges():
    graph = KnowledgeGraph("w")
    graph.add_node(Node("A", NodeType.WORKSPACE, "A"))
    assert graph.add_edge("A", "B", EdgeType.CONTAINS) is None
    assert graph.edges() == []


def test_add_node_merges_without_clobbering():
    graph = KnowledgeGraph("w")
    graph.add_node(Node("W", NodeType.WORKSPACE, "", properties={"a": 1}))
    graph.add_node(Node("W", NodeType.WORKSPACE, "Real name", properties={"b": 2}))
    node = graph.node("W")
    assert node.name == "Real name"
    assert node.properties == {"a": 1, "b": 2}
    assert len(graph) == 1


def test_neighbors_follow_edge_direction():
    graph = KnowledgeGraph("w")
    graph.add_node(Node("W", NodeType.WORKSPACE, "W"))
    graph.add_node(Node("N", NodeType.NOTEBOOK, "N"))
    graph.add_edge("W", "N", EdgeType.CONTAINS)
    assert [n.id for n in graph.neighbors("W")] == ["N"]
    assert [n.id for n in graph.neighbors("N", direction="in")] == ["W"]
    assert graph.neighbors("W", EdgeType.HAS_TABLE) == []


def test_graph_serialization_is_lossless():
    graph = KnowledgeGraph("w", {"layer": "Mixed"})
    graph.add_node(Node("W", NodeType.WORKSPACE, "W"))
    graph.add_node(Node("N", NodeType.NOTEBOOK, "N"))
    graph.add_edge("W", "N", EdgeType.CONTAINS)
    clone = KnowledgeGraph.from_dict(graph.to_dict())
    assert clone.workspace_id == "w"
    assert len(clone) == 2
    assert len(clone.edges()) == 1
    assert clone.counts_by_type() == graph.counts_by_type()


def test_merge_unions_two_graphs():
    a = KnowledgeGraph("w")
    a.add_node(Node("W", NodeType.WORKSPACE, "W"))
    b = KnowledgeGraph("w")
    b.add_node(Node("W", NodeType.WORKSPACE, "W"))
    b.add_node(Node("L", NodeType.LAKEHOUSE, "L"))
    b.add_edge("W", "L", EdgeType.CONTAINS)
    a.merge(b)
    assert len(a) == 2
    assert len(a.edges()) == 1


# -- builder ------------------------------------------------------------------


def test_build_graph_maps_the_workspace(provider):
    ctx = provider.fetch("ws-prep-01")
    graph = build_graph(ctx)

    ws_id = make_node_id(NodeType.WORKSPACE, "ws-prep-01")
    assert graph.node(ws_id) is not None
    assert len(graph.nodes_of_type(NodeType.WORKSPACE)) == 1

    # capacity discovered and linked
    assert graph.nodes_of_type(NodeType.CAPACITY)
    assert graph.neighbors(ws_id, EdgeType.ASSIGNED_TO_CAPACITY)

    # pipelines become nodes
    pipe_names = {n.name for n in graph.nodes_of_type(NodeType.DATA_PIPELINE)}
    assert {"PL_Bronze_Load", "PL_Silver_Merge"} <= pipe_names


def test_build_graph_expands_notebook_cells(provider):
    ctx = provider.fetch("ws-prep-01")
    graph = build_graph(ctx)

    notebook = next(n for n in graph.nodes_of_type(NodeType.NOTEBOOK)
                    if n.name == "NB_Gold_Build")
    expected_cells = len(ctx.notebooks["NB_Gold_Build"]["cells"])
    assert notebook.properties["cell_count"] == expected_cells
    assert len(graph.neighbors(notebook.id, EdgeType.HAS_CELL)) == expected_cells
    assert graph.nodes_of_type(NodeType.NOTEBOOK_CELL)


def test_build_graph_maps_tables_and_columns(provider):
    ctx = provider.fetch("ws-store-01")
    graph = build_graph(ctx)

    table_names = {n.name for n in graph.nodes_of_type(NodeType.TABLE)}
    assert {"fact_sales", "dim_customer", "dim_date", "StagingTemp"} <= table_names
    assert graph.nodes_of_type(NodeType.COLUMN)

    lakehouses = graph.nodes_of_type(NodeType.LAKEHOUSE)
    assert len(lakehouses) == 1
    assert len(graph.neighbors(lakehouses[0].id, EdgeType.HAS_TABLE)) == len(ctx.tables)


def test_unread_resources_become_access_findings():
    ctx = WorkspaceContext(
        id="ws-x", display_name="X",
        unavailable={Resource.GIT, Resource.ROLE_ASSIGNMENTS},
    )
    graph = build_graph(ctx)
    resources = {f.properties["resource"] for f in graph.findings()}
    assert resources == {"git", "roleAssignments"}
    ws_id = make_node_id(NodeType.WORKSPACE, "ws-x")
    assert len(graph.neighbors(ws_id, EdgeType.HAS_FINDING)) == 2


def test_unknown_item_type_is_not_dropped():
    from auditfast.core.models import Item
    ctx = WorkspaceContext(id="ws-x", items=[Item(id="1", type="BrandNewItem", display_name="X")])
    graph = build_graph(ctx)
    node = graph.node(make_node_id(NodeType.ITEM, "1"))
    assert node is not None
    assert node.properties["fabric_type"] == "BrandNewItem"


def test_build_graph_maps_semantic_model_measures_and_relationships():
    from auditfast.core.models import Item
    ctx = WorkspaceContext(
        id="ws-r", display_name="R",
        items=[Item(id="sm1", type="SemanticModel", display_name="SM_Sales")],
        semantic_models={"SM_Sales": {
            "tables": ["Sales", "Date"],
            "measures": [
                {"name": "Total", "table": "Sales", "expression": "SUM(x)", "description": "d"},
                {"name": "NoDesc", "table": "Sales", "expression": "1", "description": ""},
            ],
            "relationships": [
                {"from_table": "Sales", "from_column": "DateKey",
                 "to_table": "Date", "to_column": "DateKey", "is_active": True},
            ],
        }},
    )
    graph = build_graph(ctx)
    model = graph.node(make_node_id(NodeType.SEMANTIC_MODEL, "sm1"))
    assert model is not None
    assert model.properties["measure_count"] == 2
    assert {m.name for m in graph.nodes_of_type(NodeType.MEASURE)} == {"Total", "NoDesc"}
    assert len(graph.neighbors(model.id, EdgeType.HAS_MEASURE)) == 2
    assert len(graph.nodes_of_type(NodeType.RELATIONSHIP)) == 1
    assert len(graph.neighbors(model.id, EdgeType.HAS_RELATIONSHIP)) == 1
    total = next(m for m in graph.nodes_of_type(NodeType.MEASURE) if m.name == "Total")
    assert total.properties["has_description"] is True


def test_build_graph_maps_shortcuts():
    from auditfast.core.models import Item
    ctx = WorkspaceContext(
        id="ws-s", display_name="S",
        items=[Item(id="lh1", type="Lakehouse", display_name="LH")],
        shortcuts={"LH": [
            {"name": "ext_s3", "path": "Files/raw", "target_type": "AmazonS3"},
            {"name": "onelake_ref", "path": "Tables/dim", "target_type": "OneLake"},
        ]},
    )
    graph = build_graph(ctx)
    shortcuts = graph.nodes_of_type(NodeType.SHORTCUT)
    assert {s.name for s in shortcuts} == {"ext_s3", "onelake_ref"}
    lakehouse = graph.node(make_node_id(NodeType.LAKEHOUSE, "lh1"))
    assert len(graph.neighbors(lakehouse.id, EdgeType.HAS_SHORTCUT)) == 2
    external = next(s for s in shortcuts if s.name == "ext_s3")
    assert external.properties["target_type"] == "AmazonS3"


# -- store & service ----------------------------------------------------------


def test_graph_store_round_trip(tmp_path, provider):
    graph = build_graph(provider.fetch("ws-store-01"))
    store = GraphStore(tmp_path)
    store.save(graph)

    loaded = store.load("ws-store-01")
    assert loaded is not None
    assert len(loaded) == len(graph)
    assert loaded.counts_by_type() == graph.counts_by_type()
    assert store.age_seconds("ws-store-01") is not None

    assert store.delete("ws-store-01")
    assert store.load("ws-store-01") is None


def test_build_twin_and_summary(provider):
    graph = twin_service.build_twin("ws-prep-01", provider)
    assert graph.workspace_id == "ws-prep-01"
    summary = twin_service.twin_summary(graph)
    assert summary["node_count"] == len(graph)
    assert summary["nodes_by_type"].get("Workspace") == 1


def test_get_twin_builds_then_serves_from_cache(tmp_path, provider):
    store = GraphStore(tmp_path)
    # Nothing built yet and no provider to build with.
    assert twin_service.get_twin("ws-ops-01", store) is None
    # A provider triggers discovery + persistence.
    built = twin_service.get_twin("ws-ops-01", store, provider=provider)
    assert built is not None
    # A later read with no provider is served from the snapshot.
    cached = twin_service.get_twin("ws-ops-01", store)
    assert cached is not None
    assert len(cached) == len(built)
