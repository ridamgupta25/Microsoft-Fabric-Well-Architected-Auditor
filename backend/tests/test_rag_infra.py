"""Step 3 - semantic infrastructure (embeddings + vector store) tests.

No optional extras installed, so ``embed`` returns ``None`` (the keyword-fallback
signal). The cosine maths and the in-memory store are exercised directly.
"""
from __future__ import annotations

from auditfast.ai.rag.embeddings import cosine_similarity, embed
from auditfast.ai.rag.vector_store import Neighbor, VectorStore

# -- embeddings ----------------------------------------------------------------

def test_embed_returns_none_when_ai_is_disabled():
    # Base install: ai_enabled defaults to False -> keyword fallback.
    assert embed("ensure incremental refresh is configured") is None


def test_embed_returns_none_for_empty_text():
    assert embed("") is None
    assert embed("   ") is None


def test_cosine_identical_vectors_is_one():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0


def test_cosine_handles_zero_and_mismatched_vectors():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


# -- vector store --------------------------------------------------------------

def test_index_and_nearest_orders_by_score():
    store = VectorStore()
    store.index(
        "c",
        [
            ("a", [1.0, 0.0], {"title": "A"}),
            ("b", [0.0, 1.0], {"title": "B"}),
            ("c", [0.9, 0.1], {"title": "C"}),
        ],
        version="v1",
    )
    hits = store.nearest("c", [1.0, 0.0], k=2)
    assert [h.id for h in hits] == ["a", "c"]
    assert isinstance(hits[0], Neighbor)
    assert hits[0].score > hits[1].score
    assert hits[0].metadata["title"] == "A"


def test_nearest_empty_when_collection_missing_or_query_empty():
    store = VectorStore()
    assert store.nearest("missing", [1.0], k=3) == []
    store.index("c", [("a", [1.0], {})], version="v1")
    assert store.nearest("c", [], k=3) == []


def test_version_stamp_drives_reindex():
    store = VectorStore()
    assert store.needs_reindex("c", "v1") is True          # missing
    store.index("c", [("a", [1.0], {})], version="v1")
    assert store.needs_reindex("c", "v1") is False         # same version
    assert store.needs_reindex("c", "v2") is True          # changed version
    assert store.version("c") == "v1"
    assert store.count("c") == 1


def test_index_replaces_previous_contents():
    store = VectorStore()
    store.index("c", [("a", [1.0], {})], version="v1")
    store.index("c", [("b", [1.0], {}), ("d", [1.0], {})], version="v2")
    assert store.count("c") == 2
    assert {h.id for h in store.nearest("c", [1.0], k=5)} == {"b", "d"}


def test_clear_removes_a_collection():
    store = VectorStore()
    store.index("c", [("a", [1.0], {})], version="v1")
    store.clear("c")
    assert store.count("c") == 0
