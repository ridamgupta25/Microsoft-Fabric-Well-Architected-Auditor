"""Gated, read-only live FetchProvider for Node 3b.

Serves the existing KB-updater fetch loop from **live Fabric** instead of only the
offline snapshot — but only when the feature gate is on. Every live GET passes the
same anti-SSRF path screen, call budget, and size cap as the fetch executor, and
the gate defaults OFF, so the pipeline stays fully offline unless a caller both
enables it and supplies a signed-in read-only ``getter``.

This reuses the hardened, well-tested ``kb_updater_agent.augment`` loop (3
strategies, diagnostics, safe merge, provenance) — it only changes where the data
comes from, never how it is validated or merged.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..agents.kb_updater_agent import FetchResponse
from ..custom_runtime.live_fetch import _is_safe_path

log = logging.getLogger("auditfast.custom_checks")

#: ``getter(path) -> (status, body)`` — a read-only Fabric GET (e.g. the live
#: provider's own ``_get``). The provider never builds URLs itself beyond the
#: check's planned endpoint, which is path-screened before use.
Getter = Callable[[str], "tuple[int | None, Any]"]


class LiveFetchProvider:
    """A read-only :class:`FetchProvider` that answers Node 3b from live Fabric."""

    def __init__(
        self,
        getter: Getter,
        *,
        enabled: bool,
        max_calls: int = 20,
        max_bytes: int = 2_000_000,
    ) -> None:
        self._get = getter
        self._enabled = enabled
        self._max_calls = max_calls
        self._max_bytes = max_bytes
        self._calls = 0

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        # Only the item-level REST strategy is served live. Gate off, wrong
        # strategy, or an unsafe/empty endpoint all behave as "not available"
        # (404) so the updater's loop advances exactly as it does offline.
        if not self._enabled or strategy != "item_rest":
            return FetchResponse(404)
        path = plan.endpoint or ""
        if not path or not _is_safe_path(path):
            return FetchResponse(404)
        self._calls += 1
        if self._calls > self._max_calls:
            log.warning("live fetch call budget exhausted", extra={"budget": self._max_calls})
            return FetchResponse(429)
        status, body = self._get(path)
        if status == 200 and body is not None:
            size = len(json.dumps(body, default=str).encode("utf-8"))
            if size > self._max_bytes:
                log.warning("live fetch response too large", extra={"path": path, "bytes": size})
                return FetchResponse(200, None)  # oversize -> treated as metadata-unavailable
            log.info("live fetch", extra={"path": path, "bytes": size, "call": self._calls})
        return FetchResponse(status or 0, body=body)


class ChainedFetchProvider:
    """Try providers in order; the first ``200`` wins, else the last response."""

    def __init__(self, *providers) -> None:
        self._providers = providers

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        last = FetchResponse(404)
        for provider in self._providers:
            resp = provider.fetch(plan, strategy)
            if resp.status == 200:
                return resp
            last = resp
        return last


__all__ = ["LiveFetchProvider", "ChainedFetchProvider", "Getter"]
