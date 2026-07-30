"""On-disk knowledge base of workspace snapshots, and the caching provider.

An audit's expensive step is the workspace crawl — one ``getDefinition`` call
per pipeline and per notebook, plus every list endpoint. Repeating it on every
run is what makes a tenant-wide audit slow enough to hit a client timeout.

This module treats a crawled :class:`WorkspaceContext` as a **knowledge base**:

* the first run for a workspace fetches it live and writes it to disk;
* later runs read the cache and never touch Fabric — so checks are answered
  "from the knowledge base", not from repeated API calls;
* when the snapshot ages past a *soft* bound it is still served at once, and a
  background thread refreshes the disk copy so the KB converges to fresh without
  blocking the report;
* only when the snapshot is missing or past the *hard* TTL does a run pay for a
  fresh live crawl.

The cache is keyed by workspace id and holds Fabric *metadata* (not row data);
it is intended for a single operator auditing their own tenant.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from ..clients.base import ALL_RESOURCES, Provider
from ..core.enums import Layer, Resource
from ..core.models import WorkspaceContext

log = logging.getLogger("auditfast.kb")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(workspace_id: str) -> str:
    """A filesystem-safe file stem for a workspace id."""
    return _SAFE.sub("_", workspace_id.strip()) or "workspace"


class ContextStore:
    """Durable + in-memory store of crawled :class:`WorkspaceContext` snapshots."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        #: workspace_id -> (saved_at_epoch, context)
        self._cache: dict[str, tuple[float, WorkspaceContext]] = {}

    def path_for(self, workspace_id: str) -> Path:
        return self.root / f"{_safe_name(workspace_id)}.json"

    def save(self, ctx: WorkspaceContext) -> Path:
        """Persist a snapshot and refresh the in-memory cache (atomic write)."""
        path = self.path_for(ctx.id)
        payload = {"saved_at": time.time(), "context": ctx.to_dict()}
        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)  # atomic on the same filesystem
            self._cache[ctx.id] = (payload["saved_at"], ctx)
        return path

    def load(self, workspace_id: str) -> WorkspaceContext | None:
        """Return the cached or on-disk snapshot, or ``None`` if never crawled."""
        with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                return cached[1]
            path = self.path_for(workspace_id)
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                ctx = WorkspaceContext.from_dict(payload.get("context", {}))
            except Exception as exc:  # a corrupt snapshot must not kill a run
                log.warning("KB snapshot for %s is unreadable: %s", workspace_id, exc)
                return None
            self._cache[workspace_id] = (payload.get("saved_at", 0.0), ctx)
            return ctx

    def saved_at(self, workspace_id: str) -> float | None:
        """Epoch seconds the snapshot was last saved, or ``None``."""
        with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                return cached[0]
            path = self.path_for(workspace_id)
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8")).get("saved_at")
            except Exception:
                return None

    def age_seconds(self, workspace_id: str) -> float | None:
        """How old the snapshot is, or ``None`` if it does not exist."""
        saved = self.saved_at(workspace_id)
        return None if saved is None else max(0.0, time.time() - saved)

    def delete(self, workspace_id: str) -> bool:
        """Drop a snapshot from cache and disk. Returns whether anything went."""
        with self._lock:
            removed = self._cache.pop(workspace_id, None) is not None
            path = self.path_for(workspace_id)
            if path.exists():
                path.unlink()
                removed = True
            return removed

    def workspaces(self) -> list[str]:
        """Every workspace id with a persisted snapshot."""
        with self._lock:
            ids = {p.stem for p in self.root.glob("*.json")}
            ids.update(self._cache)
            return sorted(ids)


class CachingProvider:
    """A :class:`Provider` that serves crawled contexts from a disk KB.

    Wraps the live provider. A read is answered from the knowledge base whenever
    a fresh-enough snapshot exists; otherwise the live provider is crawled for
    *every* resource (so the KB is always complete) and the result is cached.
    """

    def __init__(
        self,
        live: Provider,
        store: ContextStore,
        *,
        ttl_seconds: float = 86_400.0,
        soft_seconds: float = 3_600.0,
        background_refresh: bool = True,
        force_refresh: bool = False,
    ):
        self._live = live
        self._store = store
        self._ttl = ttl_seconds
        self._soft = soft_seconds
        self._bg = background_refresh
        self._force = force_refresh
        self._lock = threading.RLock()
        self._refreshing: set[str] = set()
        #: True once any workspace this session was answered from an existing
        #: snapshot — the signal that a background refresh would add value.
        self.served_from_cache = False

    # -- the provider contract -------------------------------------------------
    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        if not self._force:
            cached = self._store.load(workspace_id)
            age = self._store.age_seconds(workspace_id)
            if cached is not None and age is not None and age <= self._ttl:
                self.served_from_cache = True
                if age > self._soft:
                    self._schedule_refresh(workspace_id, layer)
                return cached
        return self._refresh_now(workspace_id, layer)

    def list_workspaces(self) -> list[dict]:
        return self._live.list_workspaces()

    def probe(self, *args, **kwargs):
        """Delegate diagnostics to the live provider when present."""
        probe = getattr(self._live, "probe", None)
        return probe(*args, **kwargs) if probe else {}

    # -- refresh ---------------------------------------------------------------
    def force_refresh(self, workspace_id: str, layer: Layer = Layer.MIXED) -> WorkspaceContext:
        """Crawl a workspace live and overwrite its snapshot. Rebuilds the KB."""
        return self._refresh_now(workspace_id, layer)

    def _refresh_now(self, workspace_id: str, layer: Layer) -> WorkspaceContext:
        ctx = self._live.fetch(workspace_id, layer, ALL_RESOURCES)
        self._store.save(ctx)
        return ctx

    def _schedule_refresh(self, workspace_id: str, layer: Layer) -> None:
        """Refresh a soft-stale snapshot in the background, one worker per ws."""
        if not self._bg:
            return
        with self._lock:
            if workspace_id in self._refreshing:
                return
            self._refreshing.add(workspace_id)

        def _work() -> None:
            try:
                self._refresh_now(workspace_id, layer)
                log.info("KB background refresh complete for %s", workspace_id)
            except Exception as exc:  # a refresh failure must not surface
                log.warning("KB background refresh failed for %s: %s", workspace_id, exc)
            finally:
                with self._lock:
                    self._refreshing.discard(workspace_id)

        threading.Thread(target=_work, name=f"kb-refresh-{workspace_id}", daemon=True).start()
