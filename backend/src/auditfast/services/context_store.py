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
from datetime import datetime
from pathlib import Path

from ..clients.base import ALL_RESOURCES, Provider
from ..core.enums import Layer, Resource
from ..core.errors import WorkspaceAccessError
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
            # An incomplete snapshot (a definition/table read failed, or items /
            # role assignments were unavailable) is never served as if whole —
            # that would freeze a permission or throttle gap into a believable
            # low score. Re-crawl instead, so the missing pieces get another try.
            if (
                cached is not None
                and age is not None
                and age <= self._ttl
                and cached.is_complete
            ):
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


class KBArchive:
    """Permanent, timestamped archive of every crawled workspace snapshot.

    Unlike :class:`ContextStore` (one overwriting file per workspace, used as the
    cache), this never overwrites: each audit run writes a fresh, dated folder so
    the full crawl history is kept on disk. Layout::

        <root>/<workspace>/<workspace>_<YYYYMMDD_HHMMSS>/
            workspace.json   full snapshot (WorkspaceContext.to_dict)
            summary.json     counts, completeness, unavailable, read failures
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def save(self, ctx: WorkspaceContext) -> Path:
        """Write a new dated snapshot folder for ``ctx`` and return its path."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = _safe_name(ctx.name or ctx.id)
        folder = self.root / safe / f"{safe}_{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "workspace.json").write_text(
            json.dumps(ctx.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        (folder / "summary.json").write_text(
            json.dumps(self._summary(ctx, stamp), indent=2, default=str), encoding="utf-8"
        )
        log.info("KB archived %s -> %s", ctx.id, folder)
        return folder

    @staticmethod
    def _summary(ctx: WorkspaceContext, stamp: str) -> dict:
        return {
            "workspace_id": ctx.id,
            "workspace": ctx.name,
            "captured_at": stamp,
            "layer": ctx.layer.value,
            "complete": ctx.is_complete,
            "items": len(ctx.items),
            "notebooks_read": len(ctx.notebooks),
            "pipelines_read": len(ctx.pipelines),
            "tables_read": len(ctx.tables),
            "semantic_models_read": len(ctx.semantic_models),
            "unavailable": sorted(r.value for r in ctx.unavailable),
            "read_failures": ctx.read_failures,
        }

    # -- replay (running the checks over saved snapshots) ----------------------
    def _latest_by_id(self) -> dict[str, dict]:
        """The newest snapshot folder per workspace id.

        Every run appends a fresh dated folder, so a workspace has many. Only the
        most recent matters for replay, and the leaf name ends in
        ``_<YYYYMMDD_HHMMSS>`` — which sorts chronologically — so we read just the
        newest leaf per workspace folder instead of every summary on disk (an
        archive accumulates thousands of folders). Returns a map of
        ``workspace_id -> {row, folder}`` where ``row`` is display metadata and
        ``folder`` is the snapshot directory to load from.
        """
        latest: dict[str, dict] = {}
        if not self.root.exists():
            return latest
        for workspace_dir in self.root.iterdir():
            if not workspace_dir.is_dir():
                continue
            leaves = [leaf for leaf in workspace_dir.iterdir() if leaf.is_dir()]
            if not leaves:
                continue
            newest = max(leaves, key=lambda leaf: leaf.name)
            meta = self._read_meta(newest)
            if meta is None:
                continue
            ws_id = meta["id"]
            current = latest.get(ws_id)
            if current is None or meta["captured_at"] > current["row"]["captured_at"]:
                latest[ws_id] = {"row": meta, "folder": newest}
        return latest

    @staticmethod
    def _read_meta(snapshot: Path) -> dict | None:
        """Display metadata for one snapshot folder, from its summary.

        Falls back to the full ``workspace.json`` when a summary is absent (an
        older capture), and to the folder name for the timestamp when neither
        carries one — a snapshot must never be dropped from the picker just
        because its summary is missing a field.
        """
        summary_path = snapshot / "summary.json"
        workspace_path = snapshot / "workspace.json"
        if not workspace_path.exists():
            return None
        stamp = snapshot.name.rsplit("_", 2)[-2:]
        fallback_stamp = "_".join(stamp) if len(stamp) == 2 else snapshot.name
        try:
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                ws_id = summary.get("workspace_id")
                if not ws_id:
                    return None
                return {
                    "id": ws_id,
                    "name": summary.get("workspace") or ws_id,
                    "role": summary.get("layer", "") or "",
                    "layer": summary.get("layer", "") or "",
                    "items": summary.get("items"),
                    "pipelines": summary.get("pipelines_read"),
                    "complete": bool(summary.get("complete", False)),
                    "captured_at": str(summary.get("captured_at") or fallback_stamp),
                }
            data = json.loads(workspace_path.read_text(encoding="utf-8"))
            ws_id = data.get("id")
            if not ws_id:
                return None
            return {
                "id": ws_id,
                "name": data.get("display_name") or ws_id,
                "role": data.get("layer", "") or "",
                "layer": data.get("layer", "") or "",
                "items": len(data.get("items", [])),
                "pipelines": len(data.get("pipelines", {})),
                "complete": None,
                "captured_at": fallback_stamp,
            }
        except Exception as exc:  # a corrupt snapshot must not hide the others
            log.warning("KB archive snapshot %s is unreadable: %s", snapshot, exc)
            return None

    def index(self) -> list[dict]:
        """One display row per archived workspace (its newest snapshot).

        This is what the "run over saved KB" picker lists — no token, no Fabric
        call, just what has already been crawled to disk.
        """
        rows = [entry["row"] for entry in self._latest_by_id().values()]
        return sorted(rows, key=lambda r: (r["name"] or "").lower())

    def load_latest(self, workspace_id: str) -> WorkspaceContext | None:
        """Rebuild the newest archived context for ``workspace_id``, or ``None``."""
        entry = self._latest_by_id().get(workspace_id)
        if entry is None:
            return None
        workspace_path = entry["folder"] / "workspace.json"
        try:
            data = json.loads(workspace_path.read_text(encoding="utf-8"))
            return WorkspaceContext.from_dict(data)
        except Exception as exc:
            log.warning("KB archive context %s is unreadable: %s", workspace_id, exc)
            return None


class ArchivingProvider:
    """Wraps any provider and archives every context it returns.

    Applied on top of the cache (or the raw live provider) so *every* audit run
    writes a fresh timestamped KB snapshot, whether the data came from disk or a
    live crawl. A failed archive write never breaks an audit.
    """

    def __init__(self, inner: Provider, archive: KBArchive):
        self._inner = inner
        self._archive = archive

    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        ctx = self._inner.fetch(workspace_id, layer, resources)
        try:
            self._archive.save(ctx)
        except Exception as exc:  # noqa: BLE001 - archiving must not fail an audit
            log.warning("KB archive write failed for %s: %s", workspace_id, exc)
        return ctx

    def list_workspaces(self) -> list[dict]:
        return self._inner.list_workspaces()

    def probe(self, *args, **kwargs):
        probe = getattr(self._inner, "probe", None)
        return probe(*args, **kwargs) if probe else {}

    @property
    def served_from_cache(self) -> bool:
        return bool(getattr(self._inner, "served_from_cache", False))


class SnapshotProvider:
    """A :class:`Provider` that serves saved/uploaded snapshots — no live tenant.

    This is what runs the check library over a knowledge base that already
    exists on disk (the archive) or one the reviewer uploaded, without a sign-in
    token and without a single Fabric call. Because the snapshot is frozen, a
    replay is the most reproducible run possible.

    Contexts passed in ``uploaded`` (already-parsed uploads) take precedence; any
    other workspace is loaded lazily from the ``archive`` on first request. A
    workspace neither uploaded nor archived raises :class:`WorkspaceAccessError`,
    which the engine turns into a visible access row — never a silent pass.
    """

    def __init__(
        self,
        *,
        uploaded: dict[str, WorkspaceContext] | None = None,
        archive: KBArchive | None = None,
        rows: list[dict] | None = None,
    ):
        self._uploaded = dict(uploaded or {})
        self._archive = archive
        self._rows = list(rows or [])
        #: KB runs are never served from the live-crawl cache; the flag exists so
        #: the audit service can report provenance uniformly across sources.
        self.served_from_cache = False

    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        ctx = self._uploaded.get(workspace_id)
        if ctx is None and self._archive is not None:
            ctx = self._archive.load_latest(workspace_id)
        if ctx is None:
            # 404 semantics: the snapshot is not in the KB, so it cannot be
            # audited from disk — the reviewer must upload it or crawl it live.
            raise WorkspaceAccessError(workspace_id, 404)
        # Honour the layer the reviewer assigned for this run, exactly as a live
        # crawl does — the layer is an audit-time role, not a property of the
        # captured snapshot.
        if layer is not None and ctx.layer != layer:
            ctx.layer = layer
        return ctx

    def list_workspaces(self) -> list[dict]:
        return list(self._rows)

