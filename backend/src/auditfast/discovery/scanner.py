"""Scanner API (admin metadata scanning) discovery adapter.

The Scanner APIs return tenant-wide subartifact metadata a per-item REST crawl
cannot: semantic-model tables/columns/measures + DAX expressions, relationships,
data sources, report->dataset lineage, sensitivity labels, endorsement, and
per-item users. They are **admin-only** (a Fabric Administrator delegated token,
or a service principal with the read-only admin API tenant settings enabled) and
**asynchronous**::

    POST /admin/workspaces/getInfo   -> { "id": scanId }
    GET  /admin/workspaces/scanStatus/{scanId}   (poll until "Succeeded")
    GET  /admin/workspaces/scanResult/{scanId}   -> the metadata

:func:`scan_result_to_graph` is pure and is the tested heart of this module. The
node ids it mints match the Fabric REST builder's (both key on the Fabric item
id), so merging a scan result *enriches* the same nodes rather than duplicating
them.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.graph import (
    DiscoverySource,
    EdgeType,
    KnowledgeGraph,
    Node,
    NodeType,
    make_node_id,
)

log = logging.getLogger("auditfast.scanner")

_BASE = "https://api.powerbi.com/v1.0/myorg/admin"
_SCAN_PARAMS = (
    "?lineage=true&datasourceDetails=true"
    "&datasetSchema=true&datasetExpressions=true&getArtifactUsers=true"
)


def _access_right(user: dict) -> str:
    for key in ("workspaceUserAccessRight", "groupUserAccessRight",
                "datasetUserAccessRight", "reportUserAccessRight"):
        if user.get(key):
            return user[key]
    return ""


def scan_result_to_graph(payload: dict,
                         source: DiscoverySource = DiscoverySource.SCANNER_API) -> KnowledgeGraph:
    """Map a Scanner ``scanResult`` document into a knowledge-graph slice."""
    graph = KnowledgeGraph("")
    if not isinstance(payload, dict):
        return graph

    # Global data-source instances, referenced by dataset usages for lineage.
    ds_index: dict[str, str] = {}
    for datasource in payload.get("datasourceInstances") or []:
        ds_id = datasource.get("datasourceId") or datasource.get("datasourceInstanceId") or ""
        if not ds_id:
            continue
        node_id = make_node_id(NodeType.CONNECTION, ds_id)
        graph.add_node(Node(
            id=node_id, type=NodeType.CONNECTION,
            name=datasource.get("datasourceType", "datasource"), source=source,
            properties={
                "datasource_type": datasource.get("datasourceType", ""),
                "connection_details": datasource.get("connectionDetails", {}),
            },
        ))
        ds_index[ds_id] = node_id

    for workspace in payload.get("workspaces") or []:
        _add_workspace(graph, workspace, ds_index, source)
    return graph


def _add_workspace(graph, workspace, ds_index, source) -> None:
    ws_native = workspace.get("id", "")
    ws_id = make_node_id(NodeType.WORKSPACE, ws_native)
    graph.add_node(Node(
        id=ws_id, type=NodeType.WORKSPACE, name=workspace.get("name", ""), source=source,
        properties={
            "fabric_id": ws_native,
            "state": workspace.get("state", ""),
            "type": workspace.get("type", ""),
            "on_dedicated_capacity": workspace.get("isOnDedicatedCapacity"),
            "capacity_id": workspace.get("capacityId", ""),
        },
    ))
    if not graph.workspace_id:
        graph.workspace_id = ws_native

    for dataset in workspace.get("datasets") or []:
        _add_dataset(graph, ws_id, dataset, ds_index, source)
    for report in workspace.get("reports") or []:
        _add_report(graph, ws_id, report, source)
    for dashboard in workspace.get("dashboards") or []:
        d_id = make_node_id(NodeType.DASHBOARD, dashboard.get("id", ""))
        graph.add_node(Node(id=d_id, type=NodeType.DASHBOARD,
                            name=dashboard.get("displayName") or dashboard.get("name", ""),
                            source=source, properties={"fabric_id": dashboard.get("id", "")}))
        graph.add_edge(ws_id, d_id, EdgeType.CONTAINS)
    for dataflow in workspace.get("dataflows") or []:
        f_native = dataflow.get("objectId") or dataflow.get("id", "")
        f_id = make_node_id(NodeType.DATAFLOW, f_native)
        graph.add_node(Node(id=f_id, type=NodeType.DATAFLOW, name=dataflow.get("name", ""),
                            source=source, properties={"fabric_id": f_native}))
        graph.add_edge(ws_id, f_id, EdgeType.CONTAINS)
    for user in workspace.get("users") or []:
        _add_user(graph, ws_id, user, source)


def _add_dataset(graph, ws_id, dataset, ds_index, source) -> None:
    model_id = make_node_id(NodeType.SEMANTIC_MODEL, dataset.get("id", ""))
    graph.add_node(Node(
        id=model_id, type=NodeType.SEMANTIC_MODEL, name=dataset.get("name", ""), source=source,
        properties={
            "fabric_id": dataset.get("id", ""),
            "configured_by": dataset.get("configuredBy", ""),
            "sensitivity_label": (dataset.get("sensitivityLabel") or {}).get("labelId", ""),
            "endorsement": (dataset.get("endorsementDetails") or {}).get("endorsement", ""),
        },
    ))
    graph.add_edge(ws_id, model_id, EdgeType.CONTAINS)

    for table in dataset.get("tables") or []:
        t_id = f"{model_id}/table/{table.get('name', '')}"
        graph.add_node(Node(id=t_id, type=NodeType.TABLE, name=table.get("name", ""), source=source,
                            properties={"is_hidden": bool(table.get("isHidden", False))}))
        graph.add_edge(model_id, t_id, EdgeType.HAS_TABLE)
        for column in table.get("columns") or []:
            c_id = f"{t_id}/col/{column.get('name', '')}"
            graph.add_node(Node(id=c_id, type=NodeType.COLUMN, name=column.get("name", ""),
                                source=source, properties={"data_type": column.get("dataType", "")}))
            graph.add_edge(t_id, c_id, EdgeType.HAS_COLUMN)
        for measure in table.get("measures") or []:
            m_id = f"{model_id}/measure/{table.get('name', '')}.{measure.get('name', '')}"
            graph.add_node(Node(
                id=m_id, type=NodeType.MEASURE, name=measure.get("name", ""), source=source,
                properties={
                    "table": table.get("name", ""),
                    "expression": measure.get("expression", ""),
                    "description": measure.get("description", "") or "",
                    "has_description": bool(measure.get("description")),
                },
            ))
            graph.add_edge(model_id, m_id, EdgeType.HAS_MEASURE)

    for index, rel in enumerate(dataset.get("relationships") or []):
        r_id = f"{model_id}/rel/{index}"
        graph.add_node(Node(
            id=r_id, type=NodeType.RELATIONSHIP,
            name=f"{rel.get('fromTable', '')} -> {rel.get('toTable', '')}", source=source,
            properties={
                "from_table": rel.get("fromTable", ""), "from_column": rel.get("fromColumn", ""),
                "to_table": rel.get("toTable", ""), "to_column": rel.get("toColumn", ""),
            },
        ))
        graph.add_edge(model_id, r_id, EdgeType.HAS_RELATIONSHIP)

    for usage in dataset.get("datasourceUsages") or []:
        ds_id = usage.get("datasourceInstanceId")
        if ds_id and ds_id in ds_index:
            graph.add_edge(model_id, ds_index[ds_id], EdgeType.DEPENDS_ON)


def _add_report(graph, ws_id, report, source) -> None:
    rep_id = make_node_id(NodeType.REPORT, report.get("id", ""))
    graph.add_node(Node(
        id=rep_id, type=NodeType.REPORT, name=report.get("name", ""), source=source,
        properties={
            "fabric_id": report.get("id", ""),
            "report_type": report.get("reportType", ""),
            "sensitivity_label": (report.get("sensitivityLabel") or {}).get("labelId", ""),
        },
    ))
    graph.add_edge(ws_id, rep_id, EdgeType.CONTAINS)
    dataset_id = report.get("datasetId")
    if dataset_id:
        model_id = make_node_id(NodeType.SEMANTIC_MODEL, dataset_id)
        if graph.has_node(model_id):
            graph.add_edge(rep_id, model_id, EdgeType.DEPENDS_ON)


def _add_user(graph, ws_id, user, source) -> None:
    label = user.get("displayName") or user.get("emailAddress") or user.get("identifier") or "unknown"
    principal_type = user.get("principalType", "")
    p_id = make_node_id(NodeType.PRINCIPAL, f"{principal_type}:{label}")
    graph.add_node(Node(
        id=p_id, type=NodeType.PRINCIPAL, name=label, source=source,
        properties={
            "principal_type": principal_type,
            "email": user.get("emailAddress", ""),
            "aad_id": user.get("graphId", ""),
            "access_right": _access_right(user),
        },
    ))
    graph.add_edge(ws_id, p_id, EdgeType.GRANTED_TO, role=_access_right(user),
                   principal_type=principal_type)


class ScannerApiClient:
    """Drives the async Scanner API scan for a set of workspaces."""

    def __init__(self, admin_token: str, timeout: int = 100,
                 poll_interval: float = 1.0, max_polls: int = 60):
        import requests  # lazy: offline paths need no HTTP stack

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {admin_token}"})
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._max_polls = max_polls

    def _json(self, response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    def scan(self, workspace_ids: list[str]) -> dict:
        """Run getInfo -> poll scanStatus -> return scanResult for the workspaces."""
        started = self._session.post(
            f"{_BASE}/workspaces/getInfo{_SCAN_PARAMS}",
            json={"workspaces": workspace_ids}, timeout=self._timeout,
        )
        if started.status_code not in (200, 202):
            log.warning("Scanner getInfo -> HTTP %s", started.status_code)
            return {}
        scan_id = (self._json(started) or {}).get("id")
        if not scan_id:
            return {}
        for _ in range(self._max_polls):
            time.sleep(self._poll_interval)
            status = self._session.get(
                f"{_BASE}/workspaces/scanStatus/{scan_id}", timeout=self._timeout)
            state = str((self._json(status) or {}).get("status", "")).lower()
            if state == "succeeded":
                result = self._session.get(
                    f"{_BASE}/workspaces/scanResult/{scan_id}", timeout=self._timeout)
                return self._json(result) or {}
            if state in ("failed", "error"):
                log.warning("Scanner scan %s failed", scan_id)
                return {}
        log.warning("Scanner scan %s timed out", scan_id)
        return {}


class ScannerApiDiscoverer:
    """Discovers subartifact metadata + lineage via the admin Scanner APIs."""

    source = DiscoverySource.SCANNER_API

    def __init__(self, admin_token: str | None, client: ScannerApiClient | None = None):
        self._token = admin_token
        self._client = client

    def available(self) -> tuple[bool, str]:
        if self._client is not None or self._token:
            return True, ""
        return False, "needs a Fabric admin token or service principal (Scanner API is admin-only)"

    def discover(self, workspace_id: str) -> KnowledgeGraph:
        client = self._client or ScannerApiClient(self._token or "")
        payload = client.scan([workspace_id])
        graph = scan_result_to_graph(payload)
        if not graph.workspace_id:
            graph.workspace_id = workspace_id
        return graph
