"""Snapshot persistence and caching for the Workspace Knowledge Graph.

The Digital Twin is expensive to build (a full workspace crawl), so it is cached:
once per process in memory, and durably as one JSON file per workspace. This is
the foundation of the Refresh/Cache manager — an audit reads the twin from here,
and only a discovery/refresh rebuilds it.

Thread-safe: builds run in a worker thread, so reads and writes take a lock.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from ..core.graph import KnowledgeGraph

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_name(workspace_id: str) -> str:
    """A filesystem-safe file stem for a workspace id."""
    return _SAFE.sub("_", workspace_id.strip()) or "workspace"


class GraphStore:
    """Durable + in-memory store of workspace Digital Twins."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        #: workspace_id -> (saved_at_epoch, graph)
        self._cache: dict[str, tuple[float, KnowledgeGraph]] = {}

    def path_for(self, workspace_id: str) -> Path:
        return self.root / f"{_safe_name(workspace_id)}.json"

    def save(self, graph: KnowledgeGraph) -> Path:
        """Persist a twin and refresh the in-memory cache."""
        path = self.path_for(graph.workspace_id)
        payload = {"saved_at": time.time(), "graph": graph.to_dict()}
        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)  # atomic on the same filesystem
            self._cache[graph.workspace_id] = (payload["saved_at"], graph)
        return path

    def load(self, workspace_id: str) -> KnowledgeGraph | None:
        """Return the cached or on-disk twin, or ``None`` if never built."""
        with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                return cached[1]
            path = self.path_for(workspace_id)
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            graph = KnowledgeGraph.from_dict(payload.get("graph", {}))
            self._cache[workspace_id] = (payload.get("saved_at", 0.0), graph)
            return graph

    def saved_at(self, workspace_id: str) -> float | None:
        """Epoch seconds the twin was last saved, or ``None``."""
        with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                return cached[0]
            path = self.path_for(workspace_id)
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("saved_at")

    def age_seconds(self, workspace_id: str) -> float | None:
        """How old the persisted twin is, or ``None`` if it does not exist."""
        saved = self.saved_at(workspace_id)
        return None if saved is None else max(0.0, time.time() - saved)

    def delete(self, workspace_id: str) -> bool:
        """Drop a twin from cache and disk. Returns whether anything was removed."""
        with self._lock:
            removed = self._cache.pop(workspace_id, None) is not None
            path = self.path_for(workspace_id)
            if path.exists():
                path.unlink()
                removed = True
            return removed

    def workspaces(self) -> list[str]:
        """Every workspace id with a persisted twin."""
        with self._lock:
            ids = {p.stem for p in self.root.glob("*.json")}
            ids.update(self._cache)
            return sorted(ids)
