"""Embeddings for meaning-based matching (shared by Nodes 2 and 3a).

Turns a sentence into a vector so the router and identifier can compare *meaning*
rather than words. Everything here is **optional**: :func:`embed` returns ``None``
when AI is off or the local embedding runtime (FastEmbed) is not installed, and
every caller falls back to the always-on deterministic matcher. That keeps the
pipeline fully working on a base install.

The model is pinned in ``settings.embedding_model`` because a cosine threshold is
only meaningful against one model; changing the model invalidates any built index.
"""
from __future__ import annotations

import math
from functools import lru_cache

from ...config.settings import get_settings
from ..orchestrator import is_enabled

#: A single sentence turned into coordinates for meaning.
Vector = list[float]


@lru_cache(maxsize=1)
def _model():  # pragma: no cover - requires the optional FastEmbed runtime
    """Lazily construct the pinned FastEmbed model, or ``None`` if unavailable."""
    try:
        from fastembed import TextEmbedding
    except Exception:  # noqa: BLE001 - extra absent -> deterministic fallback
        return None
    try:
        return TextEmbedding(model_name=get_settings().embedding_model)
    except Exception:  # noqa: BLE001 - bad model / offline -> deterministic fallback
        return None


def embed(text: str) -> Vector | None:
    """Embed ``text``, or ``None`` when AI is off or no runtime is installed.

    ``None`` is the signal to fall back to keyword matching; it is never an error.
    """
    if not text or not text.strip():
        return None
    if not is_enabled():
        return None
    model = _model()
    if model is None:  # pragma: no cover - depends on the optional extra
        return None
    try:  # pragma: no cover - exercised only with FastEmbed installed
        vector = next(iter(model.embed([text])))
        return [float(x) for x in vector]
    except Exception:  # noqa: BLE001 - embedding must never break the request
        return None


def cosine_similarity(a: Vector, b: Vector) -> float:
    """Cosine similarity in ``[-1, 1]``; ``0.0`` for a zero or mismatched vector."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


__all__ = ["Vector", "embed", "cosine_similarity"]
