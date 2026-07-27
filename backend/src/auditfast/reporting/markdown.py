"""Markdown report generator (WAF-style scorecard + findings)."""
from __future__ import annotations

from datetime import date

from ..core.enums import SEVERITY_RANK, Pillar, Status
from ..core.scoring import rating


def _fmt(pct):
    return "—" if pct is None else f"{pct:.1f}%"


def build_markdown(project_name: str, agg: dict, results: list) -> str:
    overall = agg["overall"]
    o_label, o_emoji = rating(overall)
    counts = agg["counts"]
    lines: list[str] = []

    lines.append("# Fabric Well-Architected Audit — AuditFAST Core")
    lines.append("")
    lines.append(f"**Project:** {project_name}  ")
    lines.append(f"**Date:** {date.today().isoformat()}  ")
    lines.append("**Basis:** rule-based, no AI  ")
    lines.append("**Depth:** architecture / best-practice level (not a deep-dive)")
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(f"**{_fmt(overall)} — {o_emoji} {o_label}**  ")
    lines.append(f"{counts[Status.PASS]} pass · {counts[Status.PARTIAL]} partial · "
                 f"{counts[Status.FAIL]} fail · {agg['total_scored']} checks scored")
    lines.append("")

    # Pillar scorecard
    lines.append("## Pillar Scorecard")
    lines.append("")
    lines.append("| Pillar | Score | Rating | Checks |")
    lines.append("|--------|------:|--------|-------:|")
    for p in Pillar.scored():
        pv = agg["by_pillar"][p]
        lbl, em = rating(pv["pct"])
        note = "" if pv["count"] else " _(Phase 2 / Excel)_"
        lines.append(f"| {p} | {_fmt(pv['pct'])} | {em} {lbl}{note} | {pv['count']} |")
    lines.append("")

    # Per-workspace breakdown
    lines.append("## Per-Workspace Breakdown")
    lines.append("")
    lines.append("| Workspace | Layer role | Score | Rating |")
    lines.append("|-----------|------------|------:|--------|")
    for ws, wv in agg["by_workspace"].items():
        lbl, em = rating(wv["pct"])
        lines.append(f"| {ws} | {wv.get('role','')} | {_fmt(wv['pct'])} | {em} {lbl} |")
    lines.append("")

    # Findings (fails + partials), most severe first
    findings = [r for r in results if r.status in (Status.FAIL, Status.PARTIAL)]
    findings.sort(key=lambda r: (SEVERITY_RANK.get(r.severity, 9),
                                 r.score if r.score is not None else 9))
    lines.append(f"## Findings ({len(findings)})")
    lines.append("")
    if not findings:
        lines.append("_No failing or partial checks — every scored best practice passed._")
    else:
        lines.append("| Severity | Ref | Check | Pillar | Workspace | Object | Status | Evidence | Recommendation |")
        lines.append("|----------|-----|-------|--------|-----------|--------|--------|----------|----------------|")
        for r in findings:
            obj = r.obj or "—"
            rec = (r.recommendation or "").replace("|", "\\|")
            ev = (r.evidence or "").replace("|", "\\|")
            lines.append(
                f"| {r.severity.value} | {r.ref} | {r.title} | {r.pillar} | "
                f"{r.workspace} | {obj} | {r.status.value} | {ev} | {rec} |")
    lines.append("")

    # Inventory (informational)
    info = [r for r in results if r.status == Status.INFO]
    if info:
        lines.append("## Workspace Inventory")
        lines.append("")
        lines.append("| Workspace | Layer role | Items |")
        lines.append("|-----------|------------|-------|")
        for r in info:
            lines.append(f"| {r.workspace} | {r.workspace_role} | {r.evidence} |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> Scope: this report covers the **best-practice / architecture level** for the "
                 "**Pipelines + workspace** checks automated in Phase 1. Deep-dive items "
                 "(data profiling, line-by-line code, semantic-model/report internals) and "
                 "document-based items (DR, compliance, runbooks) are completed in the Excel checklist.")
    lines.append("")
    return "\n".join(lines)
