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
from ..custom_runtime.live_fetch import _is_safe_path, run_fetch_code

log = logging.getLogger("auditfast.custom_checks")

#: ``getter(path) -> (status, body)`` — a read-only Fabric GET (e.g. the live
#: provider's own ``_get``). The provider never builds URLs itself beyond the
#: check's planned endpoint, which is path-screened before use.
Getter = Callable[[str], "tuple[int | None, Any]"]

#: HTTP verbs a catalog endpoint template may carry as a prefix. Only ``GET`` is
#: ever resolved — the read-only guarantee refuses every other verb.
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})


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
        #: Run workspace id(s), bound before the run so ``{id}`` templates resolve.
        self._workspace_ids: list[str] = []

    def bind_workspaces(self, workspace_ids) -> None:
        """Record the run's workspace id(s) so ``{id}`` endpoints can resolve."""
        self._workspace_ids = [str(w) for w in (workspace_ids or []) if w]

    def _resolve_by_ws(self, endpoint: str | None, ws_ids: list[str]) -> list[tuple[str, str]]:
        """Turn a catalog endpoint template into ``(workspace_id, path)`` pairs.

        Strips the HTTP method prefix (only ``GET`` is resolved — the read-only
        guarantee), drops a leading ``/v1`` (the getter's base URL already carries
        it), and expands the single workspace-level ``{id}`` into **one pair per
        given workspace** (the ``workspace_id`` is empty for a workspace-independent
        endpoint). Per-item templates (more than one ``{id}``) yield nothing.
        """
        ep = (endpoint or "").strip()
        if not ep:
            return []
        head, sep, rest = ep.partition(" ")
        if sep and head in _HTTP_METHODS:
            if head != "GET":
                return []
            ep = rest.strip()
        for prefix in ("/v1/", "v1/"):
            if ep.startswith(prefix):
                ep = "/" + ep[len(prefix):]
                break
        placeholders = ep.count("{id}")
        if placeholders == 0:
            return [("", ep)] if ep else []
        if placeholders == 1 and ws_ids:
            return [(ws, ep.replace("{id}", ws)) for ws in ws_ids]
        return []  # per-item (two {id}) or no workspace -> decline

    def _resolve_paths(self, endpoint: str | None) -> list[str]:
        """Concrete GET-only REST paths for the bound workspaces (path list only)."""
        return [path for _ws, path in self._resolve_by_ws(endpoint, self._workspace_ids)]

    @staticmethod
    def _combine(bodies: list[Any]) -> Any:
        """Combine one field fetched across several workspaces into one value.

        Collection endpoints return ``{"value": [...]}``; their rows are
        concatenated so the check sees every workspace's data. A single body is
        returned unchanged (the common one-workspace case); any other shape is
        wrapped as ``{"value": [...]}`` so nothing is lost.
        """
        if len(bodies) == 1:
            return bodies[0]
        if all(isinstance(b, dict) and isinstance(b.get("value"), list) for b in bodies):
            merged: list[Any] = []
            for b in bodies:
                merged.extend(b["value"])
            return {"value": merged}
        return {"value": bodies}

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        # Only the item-level REST strategy is served live. Gate off, wrong
        # strategy, or an unsafe/unresolved endpoint all behave as "not available"
        # (404) so the updater's loop advances exactly as it does offline.
        if not self._enabled or strategy != "item_rest":
            return FetchResponse(404)
        # Fetch only the plan's target workspaces (the ones missing the field) when
        # given, else every bound workspace.
        ws_ids = list(getattr(plan, "workspace_ids", None) or []) or self._workspace_ids
        pairs = [(ws, p) for ws, p in self._resolve_by_ws(plan.endpoint, ws_ids) if _is_safe_path(p)]
        if not pairs:
            return FetchResponse(404)
        bodies: list[Any] = []
        by_ws: dict[str, Any] = {}
        last_status = 404
        for ws, path in pairs:
            self._calls += 1
            if self._calls > self._max_calls:
                log.warning("live fetch call budget exhausted", extra={"budget": self._max_calls})
                return FetchResponse(429)
            status, body = self._get(path)
            last_status = status or 0
            if status == 200 and body is not None:
                size = len(json.dumps(body, default=str).encode("utf-8"))
                if size > self._max_bytes:
                    log.warning("live fetch response too large", extra={"path": path, "bytes": size})
                    continue  # skip this workspace's oversize body, keep the rest
                log.info("live fetch path=%s bytes=%s call=%s", path, size, self._calls)
                bodies.append(body)
                if ws:
                    by_ws[ws] = body
        if not bodies:
            return FetchResponse(last_status or 0)
        return FetchResponse(200, body=self._combine(bodies), by_workspace=by_ws or None)


class _BodyGetterClient:
    """Adapts a ``(status, body)`` getter to the ``.get(path) -> body`` read-only
    client the AI fetch code expects.

    Strips a leading ``/v1`` (the getter's base URL already carries it, so keeping
    it would 404 as ``/v1/v1``) and returns the parsed body on a ``200``, else
    ``None`` so the fetch code simply sees "no data".
    """

    __slots__ = ("_getter",)

    def __init__(self, getter: Getter) -> None:
        self._getter = getter

    def get(self, path: str) -> Any:  # noqa: D401 - read-only client protocol
        p = str(path).strip()
        for prefix in ("/v1/", "v1/"):
            if p.startswith(prefix):
                p = "/" + p[len(prefix):]
                break
        status, body = self._getter(p)
        return body if status == 200 else None


class CodeFetchProvider:
    """A read-only FetchProvider that *executes* the AI-generated ``fetch`` code.

    Runs the check's guardrail-validated ``fetch(client, workspace_id)`` in the same
    hardened sandbox as generated checks (anti-SSRF path screen, call budget, size
    cap, timeout), injecting a read-only Fabric client. Only the ``item_rest``
    strategy is served, and only when enabled *and* a ``fetch_code`` is bound for the
    current check; every other case declines with a ``404`` so the updater's loop
    advances exactly as it does offline. Bind the current check's code with
    :meth:`bind_check` before its Node 3b runs.
    """

    def __init__(
        self,
        getter: Getter,
        *,
        enabled: bool,
        max_calls: int = 20,
        max_bytes: int = 2_000_000,
        timeout: float = 5.0,
    ) -> None:
        self._client = _BodyGetterClient(getter)
        self._enabled = enabled
        self._max_calls = max_calls
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._workspace_ids: list[str] = []
        self._fetch_code: str | None = None

    def bind_workspaces(self, workspace_ids) -> None:
        """Record the run's workspace id(s) so per-workspace fetch code can run."""
        self._workspace_ids = [str(w) for w in (workspace_ids or []) if w]

    def bind_check(self, check) -> None:
        """Bind the current check's AI fetch code before its Node 3b runs."""
        self._fetch_code = getattr(check, "fetch_code", None)

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        # No code / gate off / wrong strategy behaves as "not available" (404) so
        # the updater falls through to the next provider exactly as offline.
        if not self._enabled or strategy != "item_rest" or not self._fetch_code:
            return FetchResponse(404)
        # Run the AI fetch code for the plan's target workspaces (the missing ones)
        # when given, else every bound workspace.
        ws_ids = list(getattr(plan, "workspace_ids", None) or []) or self._workspace_ids or [""]
        bodies: list[Any] = []
        by_ws: dict[str, Any] = {}
        for ws in ws_ids:
            data, err = run_fetch_code(
                self._fetch_code, self._client, ws,
                enabled=True, max_calls=self._max_calls,
                max_bytes=self._max_bytes, timeout=self._timeout,
            )
            if err is not None:
                log.info("fetch-code declined ws=%s: %s", ws, err)
                continue
            if data is not None:
                size = len(json.dumps(data, default=str).encode("utf-8"))
                log.info("fetch-code run ws=%s bytes=%s", ws, size)
                bodies.append(data)
                if ws:
                    by_ws[ws] = data
        if not bodies:
            return FetchResponse(502)
        return FetchResponse(
            200, body=LiveFetchProvider._combine(bodies), by_workspace=by_ws or None
        )


class ChainedFetchProvider:
    """Try providers in order; the first ``200`` wins, else the last response."""

    def __init__(self, *providers) -> None:
        self._providers = providers

    def bind_workspaces(self, workspace_ids) -> None:
        """Delegate workspace binding to any child provider that supports it."""
        for provider in self._providers:
            if hasattr(provider, "bind_workspaces"):
                provider.bind_workspaces(workspace_ids)

    def bind_check(self, check) -> None:
        """Delegate per-check binding to any child provider that supports it."""
        for provider in self._providers:
            if hasattr(provider, "bind_check"):
                provider.bind_check(check)

    def fetch(self, plan, strategy) -> FetchResponse:  # noqa: D401 - protocol method
        last = FetchResponse(404)
        for provider in self._providers:
            resp = provider.fetch(plan, strategy)
            if resp.status == 200:
                return resp
            last = resp
        return last


__all__ = ["LiveFetchProvider", "CodeFetchProvider", "ChainedFetchProvider", "Getter"]
