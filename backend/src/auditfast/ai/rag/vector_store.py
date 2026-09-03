"""Vector store wrapper (shared by Nodes 2 and 3a).

A thin ``index()`` / ``nearest()`` interface so the node code never names a
concrete engine. The default backend is a **pure-Python in-memory cosine store** —
dependency-free, deterministic, and fine for the small collections here (a few
hundred default checks, a few dozen KB fields). The production swap is Qdrant
local mode + FastEmbed behind this same interface; because callers only touch
``index()`` / ``nearest()``, that swap never reaches a node.

Each collection carries a **version stamp**: when the source it was built from
changes (e.g. the check registry grows), the version differs and the collection is
rebuilt instead of serving stale vectors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .embeddings import Vector, cosine_similarity


@dataclass(frozen=True, slots=True)
class Neighbor:
    """One nearest-neighbour hit from :meth:`VectorStore.nearest`."""

    id: str
    score: float
    metadata: dict[str, Any]


@dataclass(slots=True)
class _Collection:
    version: str = ""
    ids: list[str] = field(default_factory=list)
    vectors: list[Vector] = field(default_factory=list)
    metadata: list[dict[str, Any]] = field(default_factory=list)


class VectorStore:
    """An in-memory, cosine-similarity vector store keyed by collection name."""

    def __init__(self) -> None:
        self._collections: dict[str, _Collection] = {}

    def version(self, collection: str) -> str:
        """The version stamp a collection was last built with (``""`` if absent)."""
        col = self._collections.get(collection)
        return col.version if col else ""

    def needs_reindex(self, collection: str, version: str) -> bool:
        """True when ``collection`` is missing or built from a different version."""
        return self.version(collection) != version

    def index(
        self,
        collection: str,
        items: list[tuple[str, Vector, dict[str, Any]]],
        *,
        version: str = "",
    ) -> None:
        """Replace ``collection`` with ``items`` (``id, vector, metadata``)."""
        col = _Collection(version=version)
        for item_id, vector, meta in items:
            col.ids.append(item_id)
            col.vectors.append(list(vector))
            col.metadata.append(dict(meta))
        self._collections[collection] = col

    def nearest(self, collection: str, vector: Vector, k: int = 5) -> list[Neighbor]:
        """The ``k`` most similar items to ``vector``, highest score first."""
        col = self._collections.get(collection)
        if col is None or not col.vectors or not vector:
            return []
        scored = [
            Neighbor(id=col.ids[i], score=cosine_similarity(vector, col.vectors[i]), metadata=col.metadata[i])
            for i in range(len(col.vectors))
        ]
        # Stable order: score desc, then id asc, so ties are deterministic.
        scored.sort(key=lambda n: (-n.score, n.id))
        return scored[: max(0, k)]

    def count(self, collection: str) -> int:
        col = self._collections.get(collection)
        return len(col.ids) if col else 0

    def clear(self, collection: str | None = None) -> None:
        if collection is None:
            self._collections.clear()
        else:
            self._collections.pop(collection, None)


#: Process-wide default store. Nodes may inject their own for isolation/tests.
STORE = VectorStore()


def create_store():
    """Build the configured vector-store backend, falling back to in-memory.

    Reads ``vector_store_backend`` from settings: ``"qdrant"`` builds a persistent
    Qdrant local store (requires the ``qdrant`` extra); anything else — or an
    import/setup failure — returns the always-available in-memory store, so the
    router/identifier never break because an optional backend is missing.
    """
    import logging

    from ...config.settings import get_settings

    settings = get_settings()
    if getattr(settings, "vector_store_backend", "memory") != "qdrant":
        return VectorStore()
    try:
        from .qdrant_store import QdrantVectorStore

        path = str(settings.resolve(settings.vector_store_dir))
        return QdrantVectorStore(path)
    except Exception:  # noqa: BLE001 - optional backend must never break startup
        logging.getLogger("auditfast.custom_checks").warning(
            "Qdrant backend unavailable; using the in-memory vector store.", exc_info=True
        )
        return VectorStore()


#: Rebind the process-wide store to the configured backend (in-memory by default).
STORE = create_store()


__all__ = ["VectorStore", "Neighbor", "STORE", "create_store"]
