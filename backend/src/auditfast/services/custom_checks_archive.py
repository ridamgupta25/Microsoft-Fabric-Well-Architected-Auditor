"""Per-run archive for the custom-checks pipeline.

Mirrors :class:`~auditfast.services.context_store.KBArchive`: every custom-checks
run writes a fresh, timestamped folder so the full history is kept on disk rather
than overwritten. Layout::

    <root>/run_<YYYYMMDD_HHMMSS>/
        manifest.json                run metadata + per-check summary
        generated_checks/<id>.py     the AI-generated audit code (one file per check)
        generated_fetch/<id>.py      the AI-generated READ-ONLY REST-fetch code (artifact)
        fetch/<id>.json              missing-KB fetch record (Node 3b): strategies
                                     tried, endpoints, fields added, provenance
        updated_kb/<workspace>.json  the shared KB after Node 3b augmentation

The folder is gitignored (it holds generated code and tenant metadata). Writing
it never blocks the API response — any failure is logged and swallowed.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("auditfast.custom_checks")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name: str) -> str:
    return _SAFE.sub("_", (name or "").strip()) or "item"


class CustomChecksArchive:
    """Timestamped, non-overwriting archive of each custom-checks run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save_run(
        self,
        ledger: list[dict[str, Any]],
        shared_kb: dict[str, Any],
        *,
        prompts: list[str],
        workspace_ids: list[str],
    ) -> Path:
        """Write one run folder and return its path."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = self.root / f"run_{stamp}"
        (folder / "generated_checks").mkdir(parents=True, exist_ok=True)
        (folder / "generated_fetch").mkdir(parents=True, exist_ok=True)
        (folder / "fetch").mkdir(parents=True, exist_ok=True)
        (folder / "updated_kb").mkdir(parents=True, exist_ok=True)

        checks_summary: list[dict[str, Any]] = []
        for row in ledger:
            cid = _safe(str(row.get("check_id", "check")))

            code = row.get("generated_code")
            if code:
                header = (
                    f"# Auto-generated custom audit check\n"
                    f"# check_id: {row.get('check_id')}\n"
                    f"# prompt:   {row.get('raw_prompt')}\n"
                    f"# status:   {row.get('lifecycle_status')}\n\n"
                )
                (folder / "generated_checks" / f"{cid}.py").write_text(
                    header + code, encoding="utf-8"
                )

            # The read-only REST-fetch code the AI wrote for a missing KB field.
            fetch_code = row.get("fetch_code")
            if fetch_code:
                header = (
                    f"# Auto-generated READ-ONLY KB fetch code (artifact; not executed here)\n"
                    f"# check_id: {row.get('check_id')}\n"
                    f"# prompt:   {row.get('raw_prompt')}\n\n"
                )
                (folder / "generated_fetch" / f"{cid}.py").write_text(
                    header + fetch_code, encoding="utf-8"
                )

            # Missing-KB fetch record (Node 3b) — what was fetched and how.
            if row.get("kb_update") or row.get("fetch_plan"):
                (folder / "fetch" / f"{cid}.json").write_text(
                    json.dumps(
                        {
                            "check_id": row.get("check_id"),
                            "raw_prompt": row.get("raw_prompt"),
                            "fetch_plan": row.get("fetch_plan"),
                            "kb_update": row.get("kb_update"),
                        },
                        indent=2,
                        default=str,
                    ),
                    encoding="utf-8",
                )

            evaluation = row.get("evaluation") or {}
            checks_summary.append(
                {
                    "check_id": row.get("check_id"),
                    "raw_prompt": row.get("raw_prompt"),
                    "lifecycle_status": row.get("lifecycle_status"),
                    "feasibility": row.get("feasibility"),
                    "approved": row.get("approved"),
                    "has_generated_code": bool(code),
                    "has_fetch_code": bool(fetch_code),
                    "code_gen": row.get("code_gen"),
                    "score": evaluation.get("score"),
                    "result_status": evaluation.get("status"),
                }
            )

        # The updated knowledge base (post-augmentation), one file per workspace.
        for ws_id, snapshot in shared_kb.items():
            (folder / "updated_kb" / f"{_safe(str(ws_id))}.json").write_text(
                json.dumps(snapshot, indent=2, default=str), encoding="utf-8"
            )

        (folder / "manifest.json").write_text(
            json.dumps(
                {
                    "created_at": stamp,
                    "prompts": prompts,
                    "workspaces": workspace_ids,
                    "checks": checks_summary,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        log.info("Custom-checks run archived -> %s", folder)
        return folder


__all__ = ["CustomChecksArchive"]
