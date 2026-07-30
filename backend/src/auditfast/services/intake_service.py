"""Checklist intake — assess one user-supplied point against the tool.

This is the service behind the "submit a checklist point" feature. It answers a
single question deterministically first: **is this point already covered by a
registered check?** If yes, it returns the matching check(s) so the user can run
them. If no, it drafts a check proposal and an advisory assessment, and lists the
steps to promote it into the deterministic engine.

Why it lives in :mod:`auditfast.services` and not :mod:`auditfast.core`:

* it may import :mod:`auditfast.ai` (the core never can), and
* it is an **additive front door** — it never registers a check, never runs the
  audit engine, and never touches a score. The existing deterministic audit path
  is completely unaffected, which is the whole point: adding this cannot make an
  existing checkpoint fail or emit a "could not fetch" error.

The assessment is token-free and never contacts Fabric, so it always returns a
result — matching and authoring both work purely from registered metadata plus
(optionally) a model.
"""
from __future__ import annotations

from ..ai import authoring, matching
from ..ai.agents import authoring_task
from ..ai.orchestrator import advisory_for_point, is_enabled
from ..core.check.registry import REGISTRY, CheckRegistry


def _covered_next_steps(top: matching.CheckMatch) -> list[str]:
    return [
        f"This point is already assessed by {top.spec.id} (ref {top.spec.ref}).",
        f"Run it from the Checks page, or POST /api/v1/audit/check with "
        f'check_id="{top.spec.id}" against a workspace to see its live verdict.',
        "No new check is needed — the deterministic engine already scores this.",
    ]


def assess_point(
    point: str,
    *,
    registry: CheckRegistry = REGISTRY,
    threshold: float = matching.DEFAULT_MATCH_THRESHOLD,
) -> dict:
    """Assess one checklist point. Pure of side effects and never raises on I/O.

    Returns a JSON-safe dict describing coverage, the closest existing checks,
    and — when not covered — a draft proposal plus the promotion steps.
    """
    text = (point or "").strip()
    if not text:
        return {
            "point": "",
            "status": "invalid",
            "covered": False,
            "ai_enabled": is_enabled(),
            "matches": [],
            "proposal": None,
            "advisory": "Provide a checklist point to assess.",
            "next_steps": [],
        }

    matches = matching.match_point(text, registry)
    covered = matching.is_covered(matches, threshold=threshold)

    if covered:
        top = matches[0]
        advisory = advisory_for_point(text, covered=True) or (
            f"Covered by {top.spec.id} — {top.spec.title} (ref {top.spec.ref}, "
            f"{top.spec.pillar.value}). {top.reason.capitalize()}."
        )
        return {
            "point": text,
            "status": "covered",
            "covered": True,
            "ai_enabled": is_enabled(),
            "matches": [m.to_dict() for m in matches],
            "proposal": None,
            "advisory": advisory,
            "next_steps": _covered_next_steps(top),
        }

    proposal = authoring.draft_proposal(text)
    steps = authoring_task(proposal)
    advisory = advisory_for_point(
        text, covered=False, context=f"Proposed pillar: {proposal.pillar.value}."
    ) or (
        f"Not yet a deterministic check. Best guess: a {proposal.pillar.value} / "
        f"{proposal.scope.value} check ({proposal.suggested_id}). Until promoted, "
        f"assess it manually: {proposal.remediation_stub}"
    )
    return {
        "point": text,
        "status": "not_covered",
        "covered": False,
        "ai_enabled": is_enabled(),
        "matches": [m.to_dict() for m in matches],
        "proposal": proposal.to_dict(),
        "advisory": advisory,
        "next_steps": steps,
    }
