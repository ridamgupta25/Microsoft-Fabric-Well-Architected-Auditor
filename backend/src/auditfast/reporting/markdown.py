"""Markdown report generator (WAF-style scorecard + findings)."""
from __future__ import annotations

from datetime import date

from ..core.enums import SEVERITY_RANK, Pillar, Status
from ..core.scoring import rating
from ..core.validation import PENDING_LABEL, VALIDATED_LABEL, is_validated, validation_label


def _fmt(pct):
    return "—" if pct is None else f"{pct:.1f}%"


def build_markdown(project_name: str, agg: dict, results: list, errors: list | None = None) -> str:
    overall = agg["overall"]
    o_label, o_emoji = rating(overall)
    counts = agg["counts"]
    na_count = sum(1 for r in results if r.status == Status.NA)
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
                 f"{counts[Status.FAIL]} fail · {na_count} not assessed · "
                 f"{agg['total_scored']} checks scored")
    lines.append("")

    # Validation coverage — how many of the checks in this report have completed
    # Phase 1 validation. Reads the single source of truth (core.validation).
    # Keyed by ref (the checklist ref id).
    distinct_checks = {(r.check_id, r.ref) for r in results}
    n_validated = sum(1 for _, ref in distinct_checks if is_validated(ref))
    n_total = len(distinct_checks)
    lines.append(f"**Validation:** {n_validated} of {n_total} checks in this report are "
                 f"**{VALIDATED_LABEL}** (Phase 1); the remaining {n_total - n_validated} are "
                 f"**{PENDING_LABEL}** for the next phase.")
    lines.append("")

    # Crawl completeness — access + partial-read warnings, up top so an
    # incomplete crawl is never mistaken for a genuinely low score.
    errors = errors or []
    if errors:
        lines.append("## \u26a0\ufe0f Crawl completeness")
        lines.append("")
        lines.append(f"{len(errors)} workspace/resource read(s) did not complete. "
                     "Affected checks are reported N/A, not failed — the score below "
                     "reflects only what could actually be read from the tenant.")
        lines.append("")
        lines.append("| Workspace | What could not be read |")
        lines.append("|-----------|------------------------|")
        for r in errors:
            msg = (getattr(r, "evidence", "") or "").replace("|", "\\|")
            lines.append(f"| {getattr(r, 'workspace', '')} | {msg} |")
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
        lines.append("| Severity | Ref | Check | Validation | Pillar | Workspace | Object | Status | Evidence | Recommendation |")
        lines.append("|----------|-----|-------|------------|--------|-----------|--------|--------|----------|----------------|")
        for r in findings:
            obj = r.obj or "—"
            rec = (r.recommendation or "").replace("|", "\\|")
            ev = (r.evidence or "").replace("|", "\\|")
            lines.append(
                f"| {r.severity.value} | {r.ref} | {r.title} | {validation_label(r.ref)} | "
                f"{r.pillar} | {r.workspace} | {obj} | {r.status.value} | {ev} | {rec} |")
    lines.append("")

    # External checks (from CSV) — separate section to distinguish from automated
    external = [r for r in results if r.source == "external"]
    if external:
        lines.append("## External Checks (from CSV)")
        lines.append("")
        lines.append("These checks were loaded from an external CSV file (e.g., AdminChecks.csv) "
                     "and are included in the overall score. They are marked here for clarity.")
        lines.append("")
        ext_findings = [r for r in external if r.status in (Status.FAIL, Status.PARTIAL)]
        if ext_findings:
            lines.append(f"### Failing / Partial ({len(ext_findings)})")
            lines.append("")
            lines.append("| Severity | Ref | Check | Pillar | Workspace | Status | Evidence |")
            lines.append("|----------|-----|-------|--------|-----------|--------|----------|")
            for r in ext_findings:
                ev = (r.evidence or "").replace("|", "\\|")
                lines.append(
                    f"| {r.severity.value} | {r.ref} | {r.title} | "
                    f"{r.pillar} | {r.workspace} | {r.status.value} | {ev} |")
            lines.append("")
        
        ext_passing = [r for r in external if r.status == Status.PASS]
        if ext_passing:
            lines.append(f"### Passing ({len(ext_passing)})")
            lines.append("")
            lines.append("| Ref | Check | Pillar | Workspace |")
            lines.append("|-----|-------|--------|-----------|")
            for r in ext_passing:
                lines.append(
                    f"| {r.ref} | {r.title} | {r.pillar} | {r.workspace} |")
            lines.append("")

    # Not assessed (N/A): checks whose data could not be read. These are the
    # "why are checks missing?" answer — grouped by reason so a permission or
    # access gap is visible instead of the checks silently vanishing.
    na_results = [r for r in results if r.status == Status.NA]
    if na_results:
        lines.append(f"## Not assessed — N/A ({len(na_results)})")
        lines.append("")
        lines.append("These checks could not be evaluated — usually because the data they "
                     "read could not be fetched (most often a sign-in token that lacks the "
                     "scope to read role assignments, Git, or item definitions via "
                     "`getDefinition`). They are **not** failures and do not affect the score. "
                     "Re-sign-in with full consent (Item.ReadWrite.All + Workspace.Read.All), "
                     "then re-run to assess them.")
        lines.append("")
        lines.append("| Reason | Checks | Pillars affected |")
        lines.append("|--------|-------:|------------------|")
        groups: dict[str, list] = {}
        for r in na_results:
            key = (r.evidence or "Not applicable").strip()
            group = groups.setdefault(key, [0, set()])
            group[0] += 1
            group[1].add(str(r.pillar))
        for reason, (count, pillars) in sorted(groups.items(), key=lambda kv: -kv[1][0]):
            reason_txt = reason.replace("|", "\\|")
            lines.append(f"| {reason_txt} | {count} | {', '.join(sorted(pillars))} |")
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
    lines.append("> Scope: rule-based best-practice / architecture level across **workspace, "
                 "pipeline, and notebook (Spark/Delta)** checks. Items shown as **N/A** could "
                 "not be read from the tenant with the current sign-in (see *Not assessed* "
                 "above) — they are not failures. Deep-dive items (data profiling, line-by-line "
                 "code, semantic-model/report internals) and document-based items (DR, "
                 "compliance, runbooks) are completed in the Excel checklist.")
    lines.append("")
    return "\n".join(lines)
