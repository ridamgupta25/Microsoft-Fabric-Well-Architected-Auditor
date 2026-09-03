"""Cross-run memory for the custom-checks pipeline.

The pipeline is stateless per request: it re-guardrails, re-routes, and re-generates
every check on every run. This store gives it a **durable memory** so repeat runs are
cheaper and consistent:

* **Generated-code reuse** — a check whose code was generated and validated before is
  reused instead of paying for another LLM round-trip (the reused code is still
  re-validated and smoke-run, so safety is unchanged).
* **Decision recall** — the last human approve/reject decision per check is remembered
  and surfaced, so a reviewer is not asked the same question twice.

It is a single gitignored JSON file keyed by ``check_id`` (a stable hash of the
prompt), holding only non-secret metadata + the generated code. Writing is atomic and
best-effort — a memory failure never breaks a run.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("auditfast.custom_checks")


class CustomChecksMemory:
    """A durable, JSON-backed memory of past custom-checks outcomes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def record(self, ledger: list[dict[str, Any]], workspace_ids: list[str] | None = None) -> None:
        """Upsert each ledger row into memory (keyed by ``check_id``).

        ``workspace_ids`` is the scope of *this* run; an approval is remembered
        against exactly those workspaces so a check is only recalled where it was
        actually approved.
        """
        scope = sorted({str(w) for w in (workspace_ids or []) if w})
        with self._lock:
            store = self._load()
            for row in ledger:
                cid = row.get("check_id")
                if not cid:
                    continue
                prev = store.get(cid, {})
                # A fresh run reports approved=None (no decision made this run);
                # that must not erase a decision the reviewer made earlier.
                new_approved = row.get("approved")
                approved = new_approved if new_approved is not None else prev.get("approved")
                # The workspaces an approval covers: this run's scope when the
                # reviewer just approved, otherwise whatever was remembered.
                if new_approved is True:
                    approved_ws = scope
                elif new_approved is False:
                    approved_ws = []
                else:
                    approved_ws = prev.get("approved_workspaces", [])
                store[cid] = {
                    "check_id": cid,
                    "raw_prompt": row.get("raw_prompt"),
                    "lifecycle_status": row.get("lifecycle_status"),
                    "feasibility": row.get("feasibility"),
                    "approved": approved,
                    "approved_workspaces": approved_ws,
                    # Keep the most recent non-empty generated code.
                    "generated_code": row.get("generated_code") or prev.get("generated_code"),
                    "runs": int(prev.get("runs", 0)) + 1,
                }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(store, indent=2, default=str), encoding="utf-8")
            tmp.replace(self.path)  # atomic on the same filesystem

    def code_cache(self) -> dict[str, str]:
        """``check_id -> generated_code`` for every remembered check that has code."""
        return {
            cid: entry["generated_code"]
            for cid, entry in self._load().items()
            if entry.get("generated_code")
        }

    def prior_decisions(self) -> dict[str, bool]:
        """``check_id -> approved`` for every check with a remembered decision."""
        return {
            cid: bool(entry["approved"])
            for cid, entry in self._load().items()
            if entry.get("approved") is not None
        }

    def previously_approved(self, workspace_ids: list[str] | None) -> set[str]:
        """Checks approved for a scope covering all of ``workspace_ids``.

        A check counts as previously approved only when the current selection is
        within the workspaces its approval covered — so an approval on one
        workspace is not recalled on a different one. An approval with no recorded
        scope (older data) is treated as unrestricted for backward compatibility.
        """
        wanted = {str(w) for w in (workspace_ids or []) if w}
        if not wanted:
            return set()
        out: set[str] = set()
        for cid, entry in self._load().items():
            if not entry.get("approved"):
                continue
            scope = {str(w) for w in (entry.get("approved_workspaces") or [])}
            if not scope or wanted <= scope:
                out.add(cid)
        return out

    def approved_checks(self) -> list[dict[str, Any]]:
        """Every reviewer-approved check that still has validated code to run."""
        return [
            {
                "check_id": cid,
                "raw_prompt": entry.get("raw_prompt"),
                "generated_code": entry["generated_code"],
                "approved_workspaces": list(entry.get("approved_workspaces") or []),
            }
            for cid, entry in self._load().items()
            if entry.get("approved") and entry.get("generated_code")
        ]


__all__ = ["CustomChecksMemory"]
