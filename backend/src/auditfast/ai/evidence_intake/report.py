"""Render an evidence-check run as Markdown for the response and the report.

Kept separate from the deterministic audit report: these results are a distinct,
document-evidenced, human-confirmed section that never blends into the 0-3
scorecard. Only approved DRAFTED rows show a score; everything else is listed with
what it still needs.
"""
from __future__ import annotations

from collections.abc import Sequence

from .state import EvidenceCheck, EvidenceSession, EvidenceStatus

_HEADING = "## Document-evidenced checks (AI-assisted, human-confirmed)"


def _row_markdown(row: EvidenceCheck) -> str:
    score = "N/A" if row.score is None else f"{row.score}/3"
    if row.source == "manual":
        evidence = "_manually attested_"
    else:
        evidence = "; ".join(f'"{c.quote}" ({c.source} p.{c.page})' for c in row.citations) or "—"
    recs = ("; ".join(row.recommendations)) or "—"
    return f"| {row.ref} | {row.title} | {score} | {row.rung_matched or '—'} | {evidence} | {recs} |"


_TABLE_HEADER = "| Ref | Title | Score | Rung | Evidence | Recommendations |"
_TABLE_SEP = "|-----|-------|-------|------|----------|-----------------|"


def render_report(session: EvidenceSession, *, workspaces: Sequence[str] = ()) -> str:
    """A Markdown report of approved results, represented per workspace.

    When workspaces are given, the approved evidence + recommendations are shown
    under a section per workspace (each workspace attested standalone). When none
    are given, a single table is rendered.
    """
    approved = sorted(
        (r for r in session.rows.values() if r.status == EvidenceStatus.DRAFTED and r.approved is True),
        key=lambda r: r.ref,
    )
    lines = [_HEADING, ""]
    if workspaces:
        lines += [f"**Attested for workspaces:** {', '.join(workspaces)}", ""]

    if not approved:
        lines.append("_No approved document-evidenced results yet._")
    elif workspaces:
        for ws in workspaces:
            lines += [f"### Workspace: {ws}", "", _TABLE_HEADER, _TABLE_SEP]
            lines += [_row_markdown(r) for r in approved]
            lines += [""]
    else:
        lines += [_TABLE_HEADER, _TABLE_SEP]
        lines += [_row_markdown(r) for r in approved]

    not_scored = [r for r in session.rows.values() if not (r.status == EvidenceStatus.DRAFTED and r.approved is True)]
    if not_scored:
        lines += ["", "### Not scored", ""]
        for r in sorted(not_scored, key=lambda r: r.ref):
            reason = "; ".join(r.gaps) or r.status.value
            lines.append(f"- **{r.ref}** {r.title} — {r.status.value}: {reason}")
    return "\n".join(lines) + "\n"


__all__ = ["render_report"]
