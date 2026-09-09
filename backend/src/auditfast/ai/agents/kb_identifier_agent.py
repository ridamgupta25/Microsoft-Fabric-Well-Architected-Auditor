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

from functools import partial

from ...config.settings import get_settings
from ..orchestrator import is_enabled
from ..orchestrator.ai_config import AiConfig
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
    field_in_workspace,
    field_value,
)
from ..rag.kb_field_catalog import _tokens as _catalog_tokens


def identify(
    prompt: str,
    *,
    catalog: tuple[KbField, ...] = KB_FIELD_CATALOG,
    embedder=embed,
    ai: AiConfig | None = None,
) -> tuple[KbField | None, float, str]:
    """The best-matching KB field for ``prompt``: ``(field, confidence, stage)``.

    Tries meaning (embeddings) first; falls back to keyword overlap. Returns
    ``(None, 0.0, ...)`` when nothing in the catalog overlaps at all.
    """
    emb = partial(embed, ai=ai) if embedder is embed else embedder
    query_vec = emb(prompt)
    if query_vec is not None:
        best, score = _nearest_by_meaning(query_vec, catalog, emb)
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


def plan(check: CustomCheck, session, *, ai: AiConfig | None = None) -> CustomCheck:
    """Run Node 3a on ``check`` in place, reading ``session.shared_kb``.

    Only acts on a ``PENDING`` check (one Node 2 passed through as unique); a check
    already dropped or routed is returned untouched.
    """
    if check.lifecycle_status is not LifecycleStatus.PENDING:
        return check

    field, confidence, stage = identify(check.raw_prompt, ai=ai)
    if field is None:
        # Open-ended custom check: it matched no curated catalog field. Custom checks
        # are arbitrary, so rather than dead-ending at PENDING, when AI is on hand it a
        # generic FetchPlan. The pipeline then generates read-only fetch code for it
        # and either executes it live (CodeFetchProvider) or, offline, falls through to
        # code-gen against the crawled KB — letting the AI read whatever the check needs.
        if is_enabled(ai):
            check.fetch_plan = FetchPlan(
                field=f"custom_{check.check_id}",
                confidence=0.0,
                mandatory=False,
            )
        return check  # AI off -> nothing recognised, left PENDING for manual review

    min_confidence = get_settings().kb_identifier_min_confidence

    # Per-workspace presence: a field may be captured for some workspaces but not
    # others (e.g. a partial crawl, or a workspace the API didn't expose it on).
    # When the KB is a {workspace_id: snapshot} map, decide presence per workspace
    # and fetch only the ones that lack it. If every workspace has it -> present.
    workspaces = {
        ws_id: snap
        for ws_id, snap in session.shared_kb.items()
        if isinstance(snap, dict) and ("display_name" in snap or "id" in snap)
    }
    if workspaces:
        missing = [
            ws_id for ws_id, snap in workspaces.items()
            if not _present_in(snap, field)
        ]
        if not missing:
            check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
            return check
        check.fetch_plan = FetchPlan(
            field=field.path,
            resource=field.resource,
            endpoint=field.endpoint,
            confidence=round(confidence, 3),
            mandatory=field.mandatory and confidence >= min_confidence,
            workspace_ids=missing,
        )
        return check  # left PENDING for Node 3b to augment the missing workspaces

    # Fallback: a flat snapshot (no per-workspace map) — whole-KB presence check.
    value = field_value(session.shared_kb, field.path)
    present = value is not MISSING and field.validator(value)
    if present:
        check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
        return check

    check.fetch_plan = FetchPlan(
        field=field.path,
        resource=field.resource,
        endpoint=field.endpoint,
        confidence=round(confidence, 3),
        mandatory=field.mandatory and confidence >= min_confidence,
    )
    return check  # left PENDING for Node 3b to augment


def _present_in(snapshot: dict, field: KbField) -> bool:
    """True when ``field`` is present *and* quality-valid in one workspace snapshot."""
    value = field_in_workspace(snapshot, field.path)
    return value is not MISSING and field.validator(value)


__all__ = ["identify", "plan"]
