"""Custom-checks service — run plain-English checks over the offline KB.

The front door for the custom-checks pipeline (Nodes 1-6). It loads the same
on-disk ``kb-cache`` snapshots a normal audit uses and runs the prompts through
the pipeline **read-only** - no new Fabric calls, no token, and never a write.

It is deliberately additive: the deterministic registry, score, and check count
are untouched. Custom checks are scored 0-100 in their own report section, kept
apart from the deterministic 0-3 scorecard.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from ..ai.custom_runtime.local_runner import load_and_run
from ..ai.orchestrator import complete, is_enabled
from ..ai.orchestrator import pipeline
from ..ai.orchestrator.ai_config import AiConfig
from ..config.settings import get_settings
from .context_store import ContextStore
from .custom_checks_archive import CustomChecksArchive
from .custom_checks_memory import CustomChecksMemory

log = logging.getLogger("auditfast.custom_checks")


def _kb_store() -> ContextStore:
    settings = get_settings()
    return ContextStore(settings.resolve(settings.cache_dir))


def _memory() -> CustomChecksMemory | None:
    """The cross-run memory store, or ``None`` when disabled."""
    settings = get_settings()
    if not settings.custom_checks_memory_enabled:
        return None
    return CustomChecksMemory(settings.resolve(settings.custom_checks_memory_file))


def run_custom_checks(
    prompts: Sequence[str],
    *,
    workspace_ids: Sequence[str] | None = None,
    approved_check_ids: Sequence[str] | None = None,
    ai: AiConfig | None = None,
) -> dict:
    """Run ``prompts`` against the offline KB and return the ledger + report.

    ``workspace_ids`` defaults to every crawled workspace. ``approved_check_ids``
    marks those checks approved before the report is rendered (the HITL step);
    call once to review the ledger, then again with approvals to finalise.
    ``ai`` is an optional per-request key; when ``None`` the pipeline is
    deterministic (AI off).
    """
    store = _kb_store()
    ids = store.workspaces() if workspace_ids is None else list(workspace_ids)
    contexts = [ctx for wid in ids if (ctx := store.load(wid)) is not None]

    # Cross-run memory: reuse validated generated code, skipping redundant LLM calls.
    memory = _memory()
    code_cache = memory.code_cache() if memory else None

    session = pipeline.run_custom_checks(list(prompts), contexts, ai=ai, code_cache=code_cache)

    if approved_check_ids:
        approved = set(approved_check_ids)
        for check in session.checks:
            if check.check_id in approved:
                pipeline.approve(check)

    counts: dict[str, int] = {}
    for check in session.checks:
        key = check.lifecycle_status.value
        counts[key] = counts.get(key, 0) + 1

    # Evaluate every generated check now so the UI can show its 0-100 score,
    # findings (evidence), and recommendations without waiting for approval.
    ledger = session.ledger()
    for row in ledger:
        code = row.get("generated_code")
        if code:
            row["evaluation"] = load_and_run(code, session.shared_kb)

    _archive_run(ledger, session.shared_kb, list(prompts), ids)
    if memory:
        try:
            memory.record(ledger)
        except Exception:  # noqa: BLE001 - memory is best-effort, never fatal
            log.exception("Failed to record custom-checks memory")

    return {
        "prompts": len(session.checks),
        "workspaces": len(contexts),
        "summary": counts,
        "ledger": ledger,
        "pending_review_ids": [c.check_id for c in pipeline.pending_review(session)],
        "report_markdown": pipeline.render_report(session),
    }


def _archive_run(
    ledger: list[dict],
    shared_kb: dict,
    prompts: list[str],
    workspace_ids: list[str],
) -> None:
    """Write the per-run archive folder. Never breaks the response."""
    settings = get_settings()
    if not settings.custom_checks_archive_enabled:
        return
    try:
        archive = CustomChecksArchive(settings.resolve(settings.custom_checks_archive_dir))
        archive.save_run(ledger, shared_kb, prompts=prompts, workspace_ids=workspace_ids)
    except Exception:  # noqa: BLE001 - archiving is best-effort, never fatal
        log.exception("Failed to archive custom-checks run")


def verify_ai(ai: AiConfig) -> dict:
    """Check a supplied AI config can reach a model. Never echoes the key."""
    if not is_enabled(ai):
        return {
            "ok": False,
            "message": "Config is incomplete — provider, key, model, and base URL/endpoint are all required.",
        }
    import importlib.util

    if importlib.util.find_spec("openai") is None:
        return {
            "ok": False,
            "message": "The server is missing the 'openai' package. Install it on the backend: pip install openai",
        }
    reply = complete("Reply with the single word: ok", "ping", max_tokens=5, ai=ai)
    if reply:
        model = ai.model or ai.deployment or "model"
        return {"ok": True, "message": f"AI reachable ({model})."}
    return {
        "ok": False,
        "message": "Reached the server but the model call failed — check the base URL, model/deployment name, and key.",
    }


__all__ = ["run_custom_checks", "verify_ai"]
