"""SQL-aligned stakeholder Markdown report for Fabric Well-Architected audits."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..core.enums import Pillar, Severity, Status
from ..core.scoring import percentage, rating
from ..core.validation import PENDING_LABEL, VALIDATED_LABEL
from .structure import (
    RISK_PROFILE,
    assessment_weight,
    consolidate,
    executive_narrative,
    findings,
    pillar_controls,
    severity_counts,
    strengths,
    validation_counts,
    workspace_control_score,
    workspace_ids,
)


def _fmt(pct):
    return "N/A" if pct is None else f"{pct:.1f}%"


def _score(value: float) -> str:
    return f"{value:.2f}"


def _cell(value) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _table(lines: list[str], headers, rows) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    lines.append("")


def build_markdown(
    project_name: str,
    agg: dict,
    results: list,
    errors: list | None = None,
) -> str:
    """Build the same stakeholder flow as the client-approved SQL report."""
    controls = consolidate(results)
    workspace_id_by_name = workspace_ids(results)
    consolidated_findings = findings(controls)
    validated, total_controls = validation_counts(controls)
    severity_total = severity_counts(controls)
    reported_severities = (
        Severity.CRITICAL,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.LOW,
    )
    if severity_total[Severity.INFO]:
        reported_severities += (Severity.INFO,)
    pillar_number = {pillar: index for index, pillar in enumerate(Pillar.scored(), start=1)}
    overall_label, overall_emoji = rating(agg["overall"])
    lines: list[str] = [
        "# Fabric Well-Architected Audit",
        "",
        f"**Project:** {project_name}  ",
        f"**Date:** {date.today().isoformat()}  ",
        "**Basis:** deterministic, rule-based assessment; no AI in scoring  ",
        "**Deployment mode:** Microsoft Fabric SaaS",
        "",
        "## Executive Summary",
        "",
        f"**Overall score:** {_fmt(agg['overall'])}  ",
        f"**Risk rating:** {overall_emoji} {overall_label}",
        "",
        executive_narrative(agg, controls),
        "",
        "### Area Scorecard",
        "",
    ]

    score_rows = []
    for pillar in Pillar.scored():
        pillar_agg = agg["by_pillar"][pillar]
        label, emoji = rating(pillar_agg["pct"])
        score_rows.append(
            (
                pillar_number[pillar],
                pillar.value,
                f"{assessment_weight(results, pillar):.1%}",
                _fmt(pillar_agg["pct"]),
                f"{emoji} {label}",
                len(pillar_controls(controls, pillar)),
            )
        )
    _table(
        lines,
        ("#", "Area", "Assessment Weight", "Score", "Risk Rating", "Controls"),
        score_rows,
    )

    lines += ["### Coverage and Validation", ""]
    control_counts = {status: sum(c.status is status for c in controls) for status in Status}
    _table(
        lines,
        ("Measure", "Value"),
        (
            ("Consolidated controls", total_controls),
            ("Asset-level results assessed", len(results)),
            ("Passing controls", control_counts[Status.PASS]),
            ("Partial controls", control_counts[Status.PARTIAL]),
            ("Failing controls", control_counts[Status.FAIL]),
            ("Not assessed", control_counts[Status.NA]),
            (VALIDATED_LABEL, validated),
            (PENDING_LABEL, total_controls - validated),
            (
                "Validation coverage",
                f"{validated / total_controls:.1%}" if total_controls else "N/A",
            ),
        ),
    )

    if errors:
        lines += ["### Assessment Completeness", ""]
        lines.append(
            "The following reads did not complete. Affected controls are reported "
            "as not assessed rather than failed."
        )
        lines.append("")
        _table(
            lines,
            ("Workspace", "Read limitation"),
            (
                (
                    getattr(error, "workspace", ""),
                    getattr(error, "evidence", ""),
                )
                for error in errors
            ),
        )

    lines += ["### Key Strengths", ""]
    _table(
        lines,
        ("Ref", "Area", "Strength", "Evidence", "Assets Assessed"),
        (
            (
                control.ref,
                control.pillar.value,
                control.title,
                control.impacted_evidence,
                control.assets_assessed,
            )
            for control in strengths(controls)[:10]
        ),
    )

    lines += ["### Key Risks", ""]
    _table(
        lines,
        ("Ref", "Severity", "Area", "Finding", "Recommendation"),
        (
            (
                control.ref,
                control.severity.value,
                control.pillar.value,
                control.finding,
                control.recommendation,
            )
            for control in consolidated_findings[:10]
        ),
    )

    lines += ["### Remediation Roadmap", ""]
    _table(
        lines,
        ("Priority", "Findings", "Remediation SLA", "Treatment", "Status"),
        (
            (
                severity.value,
                severity_total[severity],
                RISK_PROFILE[severity][3],
                "Mitigate",
                "Open",
            )
            for severity in reported_severities
        ),
    )

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

    lines += ["## Area Detail", ""]
    for pillar in Pillar.scored():
        rows = pillar_controls(controls, pillar)
        pillar_results = [result for control in rows for result in control.results]
        pillar_score = percentage(pillar_results)
        pillar_label, pillar_emoji = rating(pillar_score)
        lines += [
            f"### Area {pillar_number[pillar]}: {pillar.value}",
            "",
            f"**Area score:** {_fmt(pillar_score)} - {pillar_emoji} {pillar_label}  ",
            f"**Assessment weight:** {assessment_weight(results, pillar):.1%}",
            "",
        ]

        category_map = defaultdict(list)
        for control in rows:
            category_map[control.category].append(control)
        category_rows = []
        for category, category_controls in category_map.items():
            category_results = [
                result for control in category_controls for result in control.results
            ]
            category_score = percentage(category_results)
            category_label, _ = rating(category_score)
            category_rows.append(
                (
                    category,
                    _fmt(category_score),
                    category_label,
                    sum(control.validation == VALIDATED_LABEL for control in category_controls),
                    len(category_controls),
                )
            )
        _table(
            lines,
            ("Category", "Score", "Rating", "Validated", "Controls"),
            category_rows,
        )

        pillar_strengths = strengths(rows)
        lines += [f"#### Strengths ({len(pillar_strengths)})", ""]
        _table(
            lines,
            ("Ref", "Strength", "Validation", "Evidence", "Assets Assessed"),
            (
                (
                    control.ref,
                    control.title,
                    control.validation,
                    control.impacted_evidence,
                    control.assets_assessed,
                )
                for control in pillar_strengths
            ),
        )

        pillar_findings = findings(rows)
        lines += [f"#### Findings ({len(pillar_findings)})", ""]
        _table(
            lines,
            (
                "Ref",
                "Severity",
                "Score",
                "Impacted Assets",
                "Non-Impacted Assets",
                "Not Assessed / Reason",
                "Finding",
                "Recommendation",
            ),
            (
                (
                    control.ref,
                    control.severity.value,
                    control.score_summary,
                    control.impacted_assets,
                    control.non_impacted_assets,
                    control.not_assessed,
                    control.finding,
                    control.recommendation,
                )
                for control in pillar_findings
            ),
        )

    lines += ["## Checklist", ""]
    _table(
        lines,
        (
            "Check ID",
            "Ref",
            "Area",
            "Category",
            "Check Description",
            "Produced By",
            "Confidence",
            "Severity",
            "Validation",
            *workspace_id_by_name.values(),
        ),
        (
            (
                control.check_id,
                control.ref,
                pillar_number.get(control.pillar, ""),
                control.category,
                control.title,
                "Rule-based",
                "Deterministic",
                control.severity.value,
                control.validation,
                *(
                    (
                        _score(value)
                        if isinstance(
                            value := workspace_control_score(control, workspace),
                            float,
                        )
                        else value
                    )
                    for workspace in workspace_id_by_name
                ),
            )
            for control in controls
        ),
    )

    lines += [f"## Findings ({len(consolidated_findings)})", ""]
    _table(
        lines,
        (
            "Ref",
            "Severity",
            "Risk",
            "Likelihood",
            "Impact",
            "Impacted Assets",
            "Non-Impacted Assets",
            "Not Assessed / Reason",
            "Finding",
            "Recommendation",
            "Status",
        ),
        (
            (
                control.ref,
                control.severity.value,
                control.risk_profile[2],
                control.risk_profile[0],
                control.risk_profile[1],
                control.impacted_assets,
                control.non_impacted_assets,
                control.not_assessed,
                control.finding,
                control.recommendation,
                "Open",
            )
            for control in consolidated_findings
        ),
    )

    lines += ["## Risk Register", ""]
    _table(
        lines,
        ("Severity", "Count", "% of Findings", "Remediation SLA"),
        (
            (
                severity.value,
                severity_total[severity],
                (
                    f"{severity_total[severity] / len(consolidated_findings):.1%}"
                    if consolidated_findings
                    else "0.0%"
                ),
                RISK_PROFILE[severity][3],
            )
            for severity in reported_severities
        ),
    )
    risk_headers = (
        "Risk ID",
        "Audit Phase",
        "Area #",
        "Area",
        "Category",
        "Checklist Ref",
        "Check ID",
        "Finding / Current Evidence",
        "Scope",
        "Impacted Assets",
        "Non-Impacted Assets",
        "Not Assessed / Reason",
        "Severity",
        "Likelihood",
        "Impact",
        "Risk Score",
        "Remediation SLA",
        "Recommendation",
        "Owner",
        "Target Date",
        "Treatment",
        "Status",
        "Actual Closure Date",
        "Closure Evidence",
        "Verification Status",
        "Notes",
    )
    _table(
        lines,
        risk_headers,
        (
            (
                f"R-{index:03d}",
                "Automated assessment",
                pillar_number.get(control.pillar, ""),
                control.pillar.value,
                control.category,
                control.ref,
                control.check_id,
                control.impacted_evidence,
                control.scopes,
                control.impacted_assets,
                control.non_impacted_assets,
                control.not_assessed,
                control.severity.value,
                control.risk_profile[0],
                control.risk_profile[1],
                control.risk_profile[2],
                control.risk_profile[3],
                control.recommendation,
                "",
                "",
                "Mitigate",
                "Open",
                "",
                "",
                "Not verified",
                "",
            )
            for index, control in enumerate(consolidated_findings, start=1)
        ),
    )

    lines += ["## Invent", ""]
    workspaces = sorted({result.workspace for result in results if result.workspace})
    _table(
        lines,
        (
            "Workspace ID",
            "Name",
            "Layer Role",
            "Objects Assessed",
            "Asset-Level Results",
            "Consolidated Controls",
        ),
        (
            (
                workspace_id_by_name[workspace],
                workspace,
                next(
                    result.workspace_role
                    for result in results
                    if result.workspace == workspace
                ),
                len(
                    {
                        result.obj
                        for result in results
                        if result.workspace == workspace and result.obj
                    }
                ),
                sum(result.workspace == workspace for result in results),
                len(
                    {
                        (result.check_id, result.ref)
                        for result in results
                        if result.workspace == workspace
                    }
                ),
            )
            for workspace in workspaces
        ),
    )

    lines += [
        "---",
        "",
        "> Scope: rule-based Fabric architecture and best-practice assessment. "
        "Repeated asset-level verdicts are consolidated by control for stakeholder "
        "reporting; deterministic asset-level results remain the basis of every score.",
        "",
    ]
    return "\n".join(lines)
