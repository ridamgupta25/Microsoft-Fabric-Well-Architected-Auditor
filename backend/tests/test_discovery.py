"""Tests for multi-source discovery: orchestrator, adapters, Scanner parser, coverage."""
from __future__ import annotations

from auditfast.core.graph import (
    DiscoverySource,
    EdgeType,
    KnowledgeGraph,
    Node,
    NodeType,
    build_graph,
    make_node_id,
)
from auditfast.core.models import Item, WorkspaceContext
from auditfast.discovery import (
    COVERAGE,
    UNOBTAINABLE,
    DiscoveryOrchestrator,
    FabricRestDiscoverer,
    GraphIdentityEnricher,
    ScannerApiDiscoverer,
    coverage_report,
    enrich_principals,
    is_obtainable,
    scan_result_to_graph,
    sources_for,
)

_SCAN = {
    "workspaces": [{
        "id": "ws-1", "name": "Sales WS", "type": "Workspace", "state": "Active",
        "isOnDedicatedCapacity": True, "capacityId": "cap-1",
        "datasets": [{
            "id": "sm-1", "name": "Sales Model", "configuredBy": "a@b.com",
            "sensitivityLabel": {"labelId": "lbl-conf"},
            "endorsementDetails": {"endorsement": "Certified"},
            "tables": [
                {"name": "Sales", "columns": [{"name": "Amount", "dataType": "Double"}],
                 "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])",
                               "description": "sum"}]},
                {"name": "Date", "columns": [{"name": "Date", "dataType": "DateTime"}],
                 "measures": []},
            ],
            "relationships": [{"fromTable": "Sales", "fromColumn": "DateKey",
                               "toTable": "Date", "toColumn": "DateKey"}],
            "datasourceUsages": [{"datasourceInstanceId": "ds-1"}],
        }],
        "reports": [{"id": "rep-1", "name": "Sales Report", "datasetId": "sm-1",
                     "reportType": "PowerBIReport"}],
        "dashboards": [{"id": "dash-1", "displayName": "Exec"}],
        "dataflows": [{"objectId": "df-1", "name": "Prep"}],
        "users": [{"displayName": "Alice", "emailAddress": "alice@b.com",
                   "principalType": "User", "graphId": "aad-alice",
                   "workspaceUserAccessRight": "Admin"}],
    }],
    "datasourceInstances": [{"datasourceId": "ds-1", "datasourceType": "Sql",
                             "connectionDetails": {"server": "s", "database": "d"}}],
}


# -- Scanner parser -----------------------------------------------------------


def test_scan_result_maps_datasets_reports_users_lineage():
    graph = scan_result_to_graph(_SCAN)
    assert graph.workspace_id == "ws-1"

    model = graph.node(make_node_id(NodeType.SEMANTIC_MODEL, "sm-1"))
    assert model is not None
    assert model.properties["endorsement"] == "Certified"
    assert model.properties["sensitivity_label"] == "lbl-conf"

    total = next(m for m in graph.nodes_of_type(NodeType.MEASURE) if m.name == "Total")
    assert total.properties["expression"] == "SUM(Sales[Amount])"
    assert graph.nodes_of_type(NodeType.COLUMN)
    assert graph.nodes_of_type(NodeType.RELATIONSHIP)

    report = graph.node(make_node_id(NodeType.REPORT, "rep-1"))
    assert report is not None
    # report -> dataset lineage edge
    assert model.id in {n.id for n in graph.neighbors(report.id, EdgeType.DEPENDS_ON)}
    # dataset -> datasource lineage edge
    connection = graph.node(make_node_id(NodeType.CONNECTION, "ds-1"))
    assert connection is not None
    assert connection.id in {n.id for n in graph.neighbors(model.id, EdgeType.DEPENDS_ON)}

    assert graph.nodes_of_type(NodeType.DASHBOARD)
    assert graph.nodes_of_type(NodeType.DATAFLOW)
    alice = next(p for p in graph.nodes_of_type(NodeType.PRINCIPAL) if p.name == "Alice")
    assert alice.properties["aad_id"] == "aad-alice"
    assert alice.properties["access_right"] == "Admin"


def test_scanner_node_ids_align_with_rest_for_merge():
    # A REST-discovered semantic-model item and the Scanner's dataset share a node.
    rest = build_graph(WorkspaceContext(
        id="ws-1", items=[Item(id="sm-1", type="SemanticModel", display_name="Sales Model")]))
    rest.merge(scan_result_to_graph(_SCAN))
    model = rest.node(make_node_id(NodeType.SEMANTIC_MODEL, "sm-1"))
    assert model is not None
    # The scanner's measures now hang off the very same node the REST crawl made.
    assert rest.neighbors(model.id, EdgeType.HAS_MEASURE)


# -- orchestrator -------------------------------------------------------------


def test_orchestrator_runs_rest_and_skips_admin_sources(provider):
    orchestrator = DiscoveryOrchestrator(
        discoverers=[FabricRestDiscoverer(provider), ScannerApiDiscoverer(None)],
        enrichers=[GraphIdentityEnricher(None)],
    )
    report = orchestrator.run("ws-prep-01")
    assert "fabric_rest" in report.ran()
    assert "scanner_api" in report.skipped()
    assert "ms_graph" in report.skipped()
    assert len(report.graph) > 0
    assert report.graph.properties["discovery"]  # provenance recorded


def test_orchestrator_isolates_a_failing_source():
    class Good:
        source = DiscoverySource.FABRIC_REST
        def available(self):
            return True, ""
        def discover(self, workspace_id):
            graph = KnowledgeGraph(workspace_id)
            graph.add_node(Node("Workspace:w", NodeType.WORKSPACE, "W"))
            return graph

    class Bad:
        source = DiscoverySource.SCANNER_API
        def available(self):
            return True, ""
        def discover(self, workspace_id):
            raise RuntimeError("boom")

    report = DiscoveryOrchestrator([Good(), Bad()]).run("w")
    assert "fabric_rest" in report.ran()
    assert report.skipped()["scanner_api"] == "boom"
    assert len(report.graph) == 1  # the good source still contributed


# -- Graph identity enrichment ------------------------------------------------


def test_graph_identity_enriches_principals_by_aad_id():
    graph = KnowledgeGraph("w")
    graph.add_node(Node("Principal:User:Alice", NodeType.PRINCIPAL, "Alice",
                        properties={"aad_id": "aad-1"}))
    graph.add_node(Node("Principal:User:Bob", NodeType.PRINCIPAL, "Bob",
                        properties={"aad_id": "aad-2"}))

    enriched = enrich_principals(graph, {"aad-1": {
        "displayName": "Alice Anderson", "userPrincipalName": "alice@x.com",
        "accountEnabled": True, "@odata.type": "#microsoft.graph.user"}})

    assert enriched == 1
    alice = graph.node("Principal:User:Alice")
    assert alice.properties["user_principal_name"] == "alice@x.com"
    assert alice.properties["directory_type"] == "user"
    assert alice.name == "Alice Anderson"


def test_admin_sources_report_why_they_are_skipped():
    ok, reason = ScannerApiDiscoverer(None).available()
    assert ok is False and "admin" in reason.lower()
    ok, reason = GraphIdentityEnricher(None).available()
    assert ok is False and "graph" in reason.lower()


# -- coverage map -------------------------------------------------------------


def test_coverage_map_is_consistent_and_serializable():
    assert set(COVERAGE) & set(UNOBTAINABLE) == set()
    assert is_obtainable("notebook_code")
    assert not is_obtainable("row_level_data_quality")
    assert sources_for("principal_identity") == [DiscoverySource.MS_GRAPH]

    report = coverage_report()
    assert "obtainable" in report and "unobtainable" in report
    valid = {s.value for s in DiscoverySource}
    for sources in report["obtainable"].values():
        assert set(sources) <= valid
