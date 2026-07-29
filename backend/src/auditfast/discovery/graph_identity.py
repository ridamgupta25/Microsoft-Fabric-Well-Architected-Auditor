"""Microsoft Graph identity enrichment adapter.

Fabric role assignments name a principal by GUID; Microsoft Graph turns that GUID
into a display name, UPN, account state, and type (user / group / service
principal). This is an *enricher*: it refines ``Principal`` nodes the primary
sources already discovered, matching on the ``aad_id`` property.

``enrich_principals`` is pure and tested; the client wraps the batched
``/directoryObjects/getByIds`` Graph call.
"""
from __future__ import annotations

import logging
from typing import Any

from ..core.graph import DiscoverySource, KnowledgeGraph, NodeType

log = logging.getLogger("auditfast.graph")

_GRAPH = "https://graph.microsoft.com/v1.0"


def enrich_principals(graph: KnowledgeGraph, objects_by_id: dict[str, dict]) -> int:
    """Fold Graph directory details into ``Principal`` nodes keyed by ``aad_id``."""
    enriched = 0
    for node in graph.nodes_of_type(NodeType.PRINCIPAL):
        aad_id = node.properties.get("aad_id")
        details = objects_by_id.get(aad_id) if aad_id else None
        if not details:
            continue
        node.properties["display_name"] = details.get("displayName", node.name)
        node.properties["user_principal_name"] = details.get("userPrincipalName", "")
        node.properties["account_enabled"] = details.get("accountEnabled")
        node.properties["directory_type"] = details.get("@odata.type", "").rsplit(".", 1)[-1]
        if details.get("displayName"):
            node.name = details["displayName"]
        enriched += 1
    return enriched


def principal_ids(graph: KnowledgeGraph) -> list[str]:
    """Every non-empty ``aad_id`` on the graph's principals (deduped)."""
    ids = {n.properties.get("aad_id") for n in graph.nodes_of_type(NodeType.PRINCIPAL)}
    return sorted(i for i in ids if i)


class GraphIdentityClient:
    """Resolves Entra directory objects by id via Microsoft Graph."""

    def __init__(self, graph_token: str, timeout: int = 30):
        import requests

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {graph_token}"})
        self._timeout = timeout

    def get_by_ids(self, ids: list[str]) -> dict[str, dict]:
        """Batch-resolve object ids to directory objects (chunks of 1000)."""
        resolved: dict[str, dict] = {}
        for start in range(0, len(ids), 1000):
            chunk = ids[start:start + 1000]
            try:
                response = self._session.post(
                    f"{_GRAPH}/directoryObjects/getByIds",
                    json={"ids": chunk,
                          "types": ["user", "group", "servicePrincipal"]},
                    timeout=self._timeout,
                )
            except Exception as exc:
                log.warning("Graph getByIds transport error: %s", exc)
                continue
            if response.status_code != 200:
                log.warning("Graph getByIds -> HTTP %s", response.status_code)
                continue
            body: Any = None
            try:
                body = response.json()
            except ValueError:
                continue
            for obj in (body or {}).get("value", []):
                if obj.get("id"):
                    resolved[obj["id"]] = obj
        return resolved


class GraphIdentityEnricher:
    """Enriches principal nodes with Microsoft Graph identity details."""

    source = DiscoverySource.MS_GRAPH

    def __init__(self, graph_token: str | None, client: GraphIdentityClient | None = None):
        self._token = graph_token
        self._client = client

    def available(self) -> tuple[bool, str]:
        if self._client is not None or self._token:
            return True, ""
        return False, "needs a Microsoft Graph token (Directory.Read.All)"

    def enrich(self, graph: KnowledgeGraph) -> int:
        ids = principal_ids(graph)
        if not ids:
            return 0
        client = self._client or GraphIdentityClient(self._token or "")
        return enrich_principals(graph, client.get_by_ids(ids))
