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

    def record(self, ledger: list[dict[str, Any]]) -> None:
        """Upsert each ledger row into memory (keyed by ``check_id``)."""
        with self._lock:
            store = self._load()
            for row in ledger:
                cid = row.get("check_id")
                if not cid:
                    continue
                prev = store.get(cid, {})
                store[cid] = {
                    "check_id": cid,
                    "raw_prompt": row.get("raw_prompt"),
                    "lifecycle_status": row.get("lifecycle_status"),
                    "feasibility": row.get("feasibility"),
                    "approved": row.get("approved", prev.get("approved")),
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


__all__ = ["CustomChecksMemory"]
