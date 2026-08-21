"""Feed the custom-checks pipeline from already-crawled KB snapshots.

The auditor already crawls each workspace **read-only** into a
:class:`~auditfast.core.models.WorkspaceContext` (served from the on-disk KB by
:class:`~auditfast.services.context_store.ContextStore`). This module reuses that
output for the custom-checks pipeline instead of issuing any new calls:

* :func:`seed_session` pre-loads a session's shared KB from one or more crawled
  contexts, so Node 3a finds already-present data and skips fetching;
* :class:`SnapshotFetchProvider` answers Node 3b's read-only fetches from the full
  snapshot for any field the seed did not include.

Both are strictly read-only - they only *read* an existing snapshot dict, so the
zero-write guarantee holds by construction (there is no HTTP path here at all).
"""
from __future__ import annotations

from typing import Any

from ..agents.kb_updater_agent import FetchResponse
from ..rag.kb_field_catalog import MISSING, field_value
from .state import CustomCheckSession

#: Values that count as "absent" even when the key exists in a snapshot.
_EMPTY = (None, {}, [], "")


def _to_snapshot(context: Any) -> dict:
    """A plain snapshot dict from a ``WorkspaceContext`` or an existing dict."""
    if hasattr(context, "to_dict"):
        return context.to_dict()
    return dict(context)


def _as_map(contexts: Any) -> dict[str, dict]:
    items = contexts if isinstance(contexts, (list, tuple)) else [contexts]
    out: dict[str, dict] = {}
    for context in items:
        snap = _to_snapshot(context)
        out[str(snap.get("id") or f"ws-{len(out)}")] = snap
    return out


def seed_session(session: CustomCheckSession, contexts: Any) -> CustomCheckSession:
    """Pre-load ``session.shared_kb`` from crawled ``contexts`` (keyed by id)."""
    session.shared_kb.update(_as_map(contexts))
    return session


class SnapshotFetchProvider:
    """A read-only :class:`FetchProvider` served entirely from crawl snapshots."""

    def __init__(self, contexts: Any) -> None:
        self._snapshot = _as_map(contexts)

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        value = field_value(self._snapshot, plan.field)
        if value is MISSING or value in _EMPTY:
            return FetchResponse(404)  # not present in the snapshot -> not supported
        return FetchResponse(200, body=value)


__all__ = ["seed_session", "SnapshotFetchProvider"]
