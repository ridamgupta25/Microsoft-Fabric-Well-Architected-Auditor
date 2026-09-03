"""Qdrant-backed vector store (optional, ``pip install .[qdrant]``).

Implements the exact :class:`~auditfast.ai.rag.vector_store.VectorStore` surface —
``version`` / ``needs_reindex`` / ``index`` / ``nearest`` / ``count`` / ``clear`` —
so Nodes 2 and 3a never know which backend they are talking to. It runs Qdrant in
**local mode** (an on-disk path, no server, no token), which persists the index
across restarts and scales past the in-memory store.

Version handling: Qdrant has no native collection version, so the version stamp is
encoded in the collection name (``<name>__<version>``). A reindex simply writes a
new versioned collection and drops the old one, so a stale index is never served.
"""
from __future__ import annotations

from typing import Any

from .embeddings import Vector
from .vector_store import Neighbor


def _qdrant():
    """Import qdrant lazily so the base install never needs it."""
    from qdrant_client import QdrantClient  # type: ignore[import-not-found]
    from qdrant_client import models  # type: ignore[import-not-found]

    return QdrantClient, models


class QdrantVectorStore:
    """A persistent, Qdrant-local-mode store matching the in-memory interface."""

    def __init__(self, path: str) -> None:
        QdrantClient, _ = _qdrant()
        self._client = QdrantClient(path=path)

    def _physical(self, collection: str, version: str) -> str:
        return f"{collection}__{version}" if version else collection

    def _existing(self, collection: str) -> str | None:
        """The physical collection name currently backing ``collection``, if any."""
        prefix = f"{collection}__"
        for got in self._client.get_collections().collections:
            name = got.name
            if name == collection or name.startswith(prefix):
                return name
        return None

    def version(self, collection: str) -> str:
        name = self._existing(collection)
        if not name:
            return ""
        _, _, version = name.partition("__")
        return version

    def needs_reindex(self, collection: str, version: str) -> bool:
        return self.version(collection) != version

    def index(
        self,
        collection: str,
        items: list[tuple[str, Vector, dict[str, Any]]],
        *,
        version: str = "",
    ) -> None:
        _, models = _qdrant()
        # Drop any prior physical collection(s) for this logical name first.
        stale = self._existing(collection)
        if stale is not None:
            self._client.delete_collection(stale)
        if not items:
            return
        dim = len(items[0][1])
        physical = self._physical(collection, version)
        if self._client.collection_exists(physical):
            self._client.delete_collection(physical)
        self._client.create_collection(
            collection_name=physical,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        points = [
            models.PointStruct(id=i, vector=list(vec), payload={"ext_id": ext_id, "meta": meta})
            for i, (ext_id, vec, meta) in enumerate(items)
        ]
        self._client.upsert(collection_name=physical, points=points)

    def nearest(self, collection: str, vector: Vector, k: int = 5) -> list[Neighbor]:
        name = self._existing(collection)
        if not name or not vector:
            return []
        result = self._client.query_points(
            collection_name=name, query=list(vector), limit=max(0, k)
        )
        out: list[Neighbor] = []
        for hit in result.points:
            payload = hit.payload or {}
            out.append(
                Neighbor(
                    id=str(payload.get("ext_id", hit.id)),
                    score=float(hit.score),
                    metadata=dict(payload.get("meta") or {}),
                )
            )
        return out

    def count(self, collection: str) -> int:
        name = self._existing(collection)
        if not name:
            return 0
        return int(self._client.count(collection_name=name).count)

    def clear(self, collection: str | None = None) -> None:
        if collection is None:
            for got in self._client.get_collections().collections:
                self._client.delete_collection(got.name)
            return
        name = self._existing(collection)
        if name is not None:
            self._client.delete_collection(name)


__all__ = ["QdrantVectorStore"]
