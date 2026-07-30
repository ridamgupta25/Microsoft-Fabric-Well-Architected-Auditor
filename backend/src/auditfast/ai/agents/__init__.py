"""Task-scoped agents.

Deliberately narrow: a triage agent that clusters related findings, a
prioritisation agent that sequences remediation by effort and risk, a planning
agent that drafts a remediation roadmap. Each takes a finished report and
returns advice — none of them can trigger a re-score.

The **checklist-author agent** lives design-time in
``.github/agents/checklist-author.agent.md``: it drives GitHub Copilot with the
vendored ``fabric-skills/`` and the ``.mcp.json`` tools to turn an uncovered
point into a real ``@check`` + test + remediation. This module only assembles the
deterministic, machine-independent **authoring task** — the ordered steps a human
or that agent follows — so the backend never has to call a model to explain how a
proposal gets promoted.
"""
from __future__ import annotations

from ..authoring import CheckProposal


def authoring_task(proposal: CheckProposal) -> list[str]:
    """The ordered steps to turn a proposal into a merged deterministic check."""
    module = f"core/check/{proposal.pillar.name.lower()}/<layer>/automated.py"
    return [
        f"Open the checklist-author agent (.github/agents/checklist-author.agent.md) "
        f"and give it the point: {proposal.point!r}.",
        f"Implement the evaluator from the skeleton in {module} "
        f"(id {proposal.suggested_id}, scope {proposal.scope.value}).",
        "Report not_applicable when required data is unavailable — never FAIL, so "
        "the run does not regress with a 'could not fetch' failure.",
        "Assign the next checklist ref for the pillar and add remediation text for "
        "it in config/remediation.yaml (tests enforce this).",
        "Run the harness: .venv/Scripts/python -m pytest -q  (and ruff check src).",
        "Update the pinned counts in tests/ (registry total, score, row counts), "
        "then commit — the new check now runs deterministically like every other.",
    ]


__all__ = ["authoring_task", "CheckProposal"]

