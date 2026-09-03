"""Tests for the vector-store backend factory and the Qdrant implementation.

The factory tests are dependency-free. The Qdrant round-trip tests skip when the
``qdrant`` extra is not installed, so the suite stays green on a base install.
"""
from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

from auditfast.ai.rag import vector_store

_HAS_QDRANT = importlib.util.find_spec("qdrant_client") is not None


# -- factory ------------------------------------------------------------------

def test_factory_defaults_to_in_memory(monkeypatch):
    from auditfast.config import settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", lambda: SimpleNamespace(vector_store_backend="memory"))
    store = vector_store.create_store()
    assert isinstance(store, vector_store.VectorStore)


def test_factory_falls_back_to_memory_when_backend_unknown(monkeypatch):
    from auditfast.config import settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", lambda: SimpleNamespace(vector_store_backend="nope"))
    assert isinstance(vector_store.create_store(), vector_store.VectorStore)


# -- Qdrant backend -----------------------------------------------------------

@pytest.mark.skipif(not _HAS_QDRANT, reason="qdrant extra not installed")
def test_qdrant_index_and_nearest_roundtrip(tmp_path):
    from auditfast.ai.rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(str(tmp_path / "q"))
    items = [
        ("a", [1.0, 0.0, 0.0], {"ref": "A"}),
        ("b", [0.0, 1.0, 0.0], {"ref": "B"}),
        ("c", [0.9, 0.1, 0.0], {"ref": "C"}),
    ]
    store.index("checks", items, version="v1")
    assert store.count("checks") == 3
    assert store.version("checks") == "v1"
    assert store.needs_reindex("checks", "v1") is False
    assert store.needs_reindex("checks", "v2") is True

    hits = store.nearest("checks", [1.0, 0.0, 0.0], k=2)
    assert [h.id for h in hits][:1] == ["a"]  # closest to itself
    assert hits[0].metadata == {"ref": "A"}


@pytest.mark.skipif(not _HAS_QDRANT, reason="qdrant extra not installed")
def test_qdrant_reindex_replaces_old_version(tmp_path):
    from auditfast.ai.rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(str(tmp_path / "q"))
    store.index("checks", [("a", [1.0, 0.0], {})], version="v1")
    store.index("checks", [("b", [0.0, 1.0], {}), ("c", [1.0, 1.0], {})], version="v2")
    assert store.version("checks") == "v2"
    assert store.count("checks") == 2
    ids = {h.id for h in store.nearest("checks", [0.0, 1.0], k=5)}
    assert "a" not in ids  # the old version was dropped


@pytest.mark.skipif(not _HAS_QDRANT, reason="qdrant extra not installed")
def test_qdrant_clear(tmp_path):
    from auditfast.ai.rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(str(tmp_path / "q"))
    store.index("checks", [("a", [1.0, 0.0], {})], version="v1")
    store.clear("checks")
    assert store.count("checks") == 0
    assert store.nearest("checks", [1.0, 0.0], k=1) == []
