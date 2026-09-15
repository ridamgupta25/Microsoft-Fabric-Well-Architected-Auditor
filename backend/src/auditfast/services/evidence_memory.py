"""Cross-run memory for approved manual (document-evidenced) checks.

Unlike custom checks, a manual check carries no code to re-run — the AI already
read the document and produced a score. So this store keeps the **approved result
itself** (score, quotes, recommendations), scoped to the workspaces it was
attested for, so an audit report can fold in a "Manual checks" section for exactly
those workspaces.

A single gitignored JSON file keyed by check ``ref``. Writes are atomic and
best-effort — a memory failure never breaks a run or an audit.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("auditfast.evidence_checks")


class EvidenceMemory:
    """A durable, JSON-backed memory of approved manual-check results."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

    def record(self, rows: list[dict[str, Any]], workspace_ids: list[str] | None) -> None:
        """Upsert each approved row (keyed by ref), scoped to ``workspace_ids``.

        Only DRAFTED rows with a score are remembered; a row is stored against the
        workspaces it was attested for so it is recalled only there.
        """
        scope = sorted({str(w) for w in (workspace_ids or []) if w})
        with self._lock:
            store = self._load()
            for row in rows:
                ref = row.get("ref")
                if not ref or row.get("score") is None:
                    continue
                store[ref] = {
                    "ref": ref,
                    "title": row.get("title"),
                    "score": row.get("score"),
                    "rung_matched": row.get("rung_matched"),
                    "citations": row.get("citations") or [],
                    "recommendations": row.get("recommendations") or [],
                    "source": row.get("source", "ai"),
                    "workspaces": scope,
                }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(store, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)  # atomic on the same filesystem

    def approved_for(self, workspace_ids: list[str] | None) -> list[dict[str, Any]]:
        """Approved rows whose attested scope covers ``workspace_ids``.

        A row with no recorded scope (older data) is treated as unrestricted.
        """
        wanted = {str(w) for w in (workspace_ids or []) if w}
        out: list[dict[str, Any]] = []
        for entry in self._load().values():
            scope = {str(w) for w in (entry.get("workspaces") or [])}
            if not wanted or not scope or wanted & scope:
                out.append(entry)
        return sorted(out, key=lambda e: str(e.get("ref")))

    def previously_approved(self, workspace_ids: list[str] | None) -> set[str]:
        """Refs already approved for a scope covering all of ``workspace_ids``.

        A check counts as previously approved only when the current selection is
        within the workspaces its approval covered — so an approval on one
        workspace is not recalled on a different one.
        """
        wanted = {str(w) for w in (workspace_ids or []) if w}
        if not wanted:
            return set()
        out: set[str] = set()
        for entry in self._load().values():
            scope = {str(w) for w in (entry.get("workspaces") or [])}
            if not scope or wanted <= scope:
                out.add(str(entry.get("ref")))
        return out


__all__ = ["EvidenceMemory"]
