"""Node 3a - the Semantic KB Identifier.

Given a unique custom check (one Node 2 did not route to a default), decide *by
meaning* which read-only KB field answers it, and whether that field is already
present and trustworthy in the shared knowledge base.

- Present and quality-valid -> ``PROCESSED_CUSTOM`` (straight to code-gen).
- Absent, or present but stale/empty/wrong-shape -> attach a :class:`FetchPlan`
  and leave the check ``PENDING`` for Node 3b to augment read-only.

Matching is meaning-first (embeddings) with an always-on keyword fallback over the
:data:`KB_FIELD_CATALOG`, so it works with AI off. Low-confidence identifications
are flagged (via the plan's confidence) rather than dropped.

Design source: ``local/Planning/Knowledge Base - Node`` (Phases 1-3).
"""
from __future__ import annotations

from ...config.settings import get_settings
from ..orchestrator.state import (
    CustomCheck,
    FetchPlan,
    LifecycleStatus,
)
from ..rag.embeddings import Vector, cosine_similarity, embed
from ..rag.kb_field_catalog import (
    KB_FIELD_CATALOG,
    MISSING,
    KbField,
    field_value,
)
from ..rag.kb_field_catalog import _tokens as _catalog_tokens


def identify(
    prompt: str,
    *,
    catalog: tuple[KbField, ...] = KB_FIELD_CATALOG,
    embedder=embed,
) -> tuple[KbField | None, float, str]:
    """The best-matching KB field for ``prompt``: ``(field, confidence, stage)``.

    Tries meaning (embeddings) first; falls back to keyword overlap. Returns
    ``(None, 0.0, ...)`` when nothing in the catalog overlaps at all.
    """
    query_vec = embedder(prompt)
    if query_vec is not None:
        best, score = _nearest_by_meaning(query_vec, catalog, embedder)
        if best is not None:
            return best, score, "semantic"
    return (*_nearest_by_keyword(prompt, catalog), "keyword")


def _nearest_by_meaning(
    query_vec: Vector, catalog: tuple[KbField, ...], embedder
) -> tuple[KbField | None, float]:
    best: KbField | None = None
    best_score = -1.0
    for f in catalog:
        field_vec = embedder(f.meaning_description)
        if field_vec is None:
            return None, 0.0  # embedder went unavailable -> use keyword path
        score = cosine_similarity(query_vec, field_vec)
        if score > best_score:
            best, best_score = f, score
    return best, max(0.0, best_score)


def _nearest_by_keyword(
    prompt: str, catalog: tuple[KbField, ...]
) -> tuple[KbField | None, float]:
    query = _catalog_tokens(prompt)
    if not query:
        return None, 0.0
    best: KbField | None = None
    best_score = 0.0
    for f in catalog:
        overlap = query & f.search_tokens()
        if not overlap:
            continue
        score = len(overlap) / len(query)
        if score > best_score:
            best, best_score = f, score
    return best, best_score


def plan(check: CustomCheck, session) -> CustomCheck:
    """Run Node 3a on ``check`` in place, reading ``session.shared_kb``.

    Only acts on a ``PENDING`` check (one Node 2 passed through as unique); a check
    already dropped or routed is returned untouched.
    """
    if check.lifecycle_status is not LifecycleStatus.PENDING:
        return check

    field, confidence, stage = identify(check.raw_prompt)
    if field is None:
        return check  # nothing recognised; left PENDING, surfaced for manual review

    value = field_value(session.shared_kb, field.path)
    present = value is not MISSING and field.validator(value)
    if present:
        check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
        return check

    min_confidence = get_settings().kb_identifier_min_confidence
    check.fetch_plan = FetchPlan(
        field=field.path,
        resource=field.resource,
        endpoint=field.endpoint,
        confidence=round(confidence, 3),
        mandatory=field.mandatory and confidence >= min_confidence,
    )
    return check  # left PENDING for Node 3b to augment


__all__ = ["identify", "plan"]
