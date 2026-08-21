"""Custom-checks service — run plain-English checks over the offline KB.

The front door for the custom-checks pipeline (Nodes 1-6). It loads the same
on-disk ``kb-cache`` snapshots a normal audit uses and runs the prompts through
the pipeline **read-only** - no new Fabric calls, no token, and never a write.

It is deliberately additive: the deterministic registry, score, and check count
are untouched. Custom checks are scored 0-100 in their own report section, kept
apart from the deterministic 0-3 scorecard.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..ai.orchestrator import pipeline
from ..config.settings import get_settings
from .context_store import ContextStore


def _kb_store() -> ContextStore:
    settings = get_settings()
    return ContextStore(settings.resolve(settings.cache_dir))


def run_custom_checks(
    prompts: Sequence[str],
    *,
    workspace_ids: Sequence[str] | None = None,
    approved_check_ids: Sequence[str] | None = None,
) -> dict:
    """Run ``prompts`` against the offline KB and return the ledger + report.

    ``workspace_ids`` defaults to every crawled workspace. ``approved_check_ids``
    marks those checks approved before the report is rendered (the HITL step);
    call once to review the ledger, then again with approvals to finalise.
    """
    store = _kb_store()
    ids = store.workspaces() if workspace_ids is None else list(workspace_ids)
    contexts = [ctx for wid in ids if (ctx := store.load(wid)) is not None]

    session = pipeline.run_custom_checks(list(prompts), contexts)

    if approved_check_ids:
        approved = set(approved_check_ids)
        for check in session.checks:
            if check.check_id in approved:
                pipeline.approve(check)

    counts: dict[str, int] = {}
    for check in session.checks:
        key = check.lifecycle_status.value
        counts[key] = counts.get(key, 0) + 1

    return {
        "prompts": len(session.checks),
        "workspaces": len(contexts),
        "summary": counts,
        "ledger": session.ledger(),
        "pending_review_ids": [c.check_id for c in pipeline.pending_review(session)],
        "report_markdown": pipeline.render_report(session),
    }


__all__ = ["run_custom_checks"]
