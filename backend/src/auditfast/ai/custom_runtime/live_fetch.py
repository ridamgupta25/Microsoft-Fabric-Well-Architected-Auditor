"""Stage-A live-fetch executor (gated, offline-tested).

Runs an AI-authored, already-validated read-only ``fetch(client, workspace_id)``
in the same hardened sandbox as generated checks, injecting a caller-supplied
READ-ONLY client. It performs no network of its own and imports no network
library; the only outbound capability is whatever the injected client exposes.

Safety gate: this never enables itself. A caller must pass ``enabled=True`` (wired
to the ``custom_checks_live_fetch_enabled`` setting, which defaults to OFF) or the
executor refuses to run. With the gate off — the default — the custom-checks
pipeline stays entirely offline.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Protocol, runtime_checkable

from ..agents.fetch_code_gen_agent import validate_fetch_source
from .local_runner import UnsafeCodeError, _run_with_timeout, _safe_builtins

log = logging.getLogger("auditfast.custom_checks")

#: A relative Fabric REST path: no scheme, no protocol-relative host, no spaces.
#: Blocking ``://`` and a leading ``//`` is the core anti-SSRF rule — the fetch
#: code can never redirect the client at an arbitrary host.
_SAFE_PATH = re.compile(r"^/?[A-Za-z0-9][A-Za-z0-9\-_./{}%?=&]*$")


def _is_safe_path(path: str) -> bool:
    p = str(path)
    if "://" in p or p.startswith("//") or ".." in p:
        return False
    return bool(_SAFE_PATH.match(p))


@runtime_checkable
class ReadOnlyFabricClient(Protocol):
    """The minimal read-only surface injected into fetch code.

    Implementations expose only safe read methods (e.g. ``get``); the sandbox
    blocks the fetch code from importing any network library itself, so this
    injected object is its single, controlled outbound capability.
    """

    def get(self, path: str) -> Any:  # noqa: D401 - protocol method
        """Read one Fabric REST resource by path and return parsed JSON."""
        ...


class LiveFetchDisabledError(RuntimeError):
    """Raised when live fetch is attempted while the feature gate is off."""


class FetchBudgetError(RuntimeError):
    """Raised when fetch code exceeds a call/size budget or targets a bad path."""


class GuardedClient:
    """Wraps a read-only client to enforce path, call-count, and size limits.

    Defense in depth: even though the fetch code is AST-screened and mutation-free,
    this caps how much it can read and where from, and logs every call — the
    controls that make executing AI-authored code against live Fabric acceptable.
    """

    def __init__(
        self,
        inner: ReadOnlyFabricClient,
        *,
        max_calls: int,
        max_bytes: int,
        path_ok: Callable[[str], bool] = _is_safe_path,
    ) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self._max_bytes = max_bytes
        self._path_ok = path_ok
        self.calls = 0

    def get(self, path: str) -> Any:
        p = str(path)
        if not self._path_ok(p):
            raise FetchBudgetError(f"path not allowed: {p!r}")
        self.calls += 1
        if self.calls > self._max_calls:
            raise FetchBudgetError(f"exceeded fetch call budget ({self._max_calls})")
        data = self._inner.get(p)
        size = len(json.dumps(data, default=str).encode("utf-8")) if data is not None else 0
        if size > self._max_bytes:
            raise FetchBudgetError(f"response {size}B exceeds {self._max_bytes}B cap")
        log.info(
            "live fetch get",
            extra={"path": p, "call": self.calls, "bytes": size},
        )
        return data


def load_fetch(source: str) -> Callable[[Any, str], Any]:
    """Validate then load the ``fetch(client, workspace_id)`` defined in ``source``.

    Raises :class:`UnsafeCodeError` if the source fails the read-only safety screen
    or defines no ``fetch`` function.
    """
    ok, reason = validate_fetch_source(source)
    if not ok:
        raise UnsafeCodeError(reason)
    namespace: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "custom_fetch",
    }
    exec(compile(source, "<custom_fetch>", "exec"), namespace)  # noqa: S102 - AST-gated + sandboxed
    fn = namespace.get("fetch")
    if not callable(fn):
        raise UnsafeCodeError("source defines no fetch(client, workspace_id) function")
    return fn


def run_fetch_code(
    source: str,
    client: ReadOnlyFabricClient,
    workspace_id: str,
    *,
    enabled: bool,
    max_calls: int = 20,
    max_bytes: int = 2_000_000,
    timeout: float = 5.0,
) -> tuple[Any, str | None]:
    """Execute validated fetch ``source`` against ``client`` under strict limits.

    The client is wrapped in a :class:`GuardedClient` enforcing an anti-SSRF path
    screen, a call-count budget, and a response-size cap, with every call logged.
    Returns ``(data, None)`` on success or ``(None, reason)`` on any failure. Raises
    :class:`LiveFetchDisabledError` when ``enabled`` is false — the gate is checked
    before the code is even loaded, so a disabled run never touches the client.
    """
    if not enabled:
        raise LiveFetchDisabledError(
            "live fetch is disabled (custom_checks_live_fetch_enabled=False)"
        )
    try:
        fn = load_fetch(source)
    except UnsafeCodeError as exc:
        return None, f"rejected: {exc}"
    guarded = GuardedClient(client, max_calls=max_calls, max_bytes=max_bytes)
    value, err = _run_with_timeout(lambda: fn(guarded, workspace_id), timeout)
    if err is not None:
        return None, f"{type(err).__name__}: {err}"
    return value, None


__all__ = [
    "ReadOnlyFabricClient",
    "GuardedClient",
    "LiveFetchDisabledError",
    "FetchBudgetError",
    "load_fetch",
    "run_fetch_code",
]
