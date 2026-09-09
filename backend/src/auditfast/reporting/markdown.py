"""SQL-aligned stakeholder Markdown report for Fabric Well-Architected audits."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from ..core.enums import Pillar, Severity, Status
from ..core.models import MAX_SCORE
from ..core.scoring import percentage, rating
from ..core.validation import PENDING_LABEL, VALIDATED_LABEL
from .structure import (
    RISK_PROFILE,
    assessment_weight,
    category_number,
    category_sort_key,
    category_title,
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


def _preamble(title: str, project_name: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"**Project:** {project_name}  ",
        f"**Date:** {date.today().isoformat()}  ",
        "**Basis:** deterministic, rule-based assessment; no AI in scoring  ",
        "**Deployment mode:** Microsoft Fabric SaaS",
        "",
    ]


def _checklist_section(lines, controls, workspace_id_by_name, pillar_number) -> None:
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


def _risk_register_section(
    lines,
    consolidated_findings,
    severity_total,
    reported_severities,
    pillar_number,
) -> None:
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


_AREA_SHORT = [
    "Architecture", "Integration", "Processing", "Modeling", "Quality", "Security",
    "Compliance", "Governance", "Reliability", "Monitoring", "DevOps", "CostMgmt",
    "Documentation",
]


def _weighted_scores(results) -> tuple[float, float]:
    """Return ``(earned, possible)`` weighted raw-score totals for a result set."""
    scored = [result for result in results if result.counts_toward_score]
    earned = sum((result.score or 0) * result.weight for result in scored)
    possible = sum(MAX_SCORE * result.weight for result in scored)
    return earned, possible


def _effort(control) -> str:
    """Scope proxy from the observations that scored below the maximum."""
    gaps = [
        result
        for result in control.results
        if result.counts_toward_score and (result.score or 0) < MAX_SCORE
    ]
    if not gaps:
        return "Low"
    workspaces = {result.workspace for result in gaps if result.workspace}
    if len(gaps) > 100 or len(workspaces) >= 10:
        return "High"
    if len(gaps) > 10 or len(workspaces) >= 2:
        return "Medium"
    return "Low"


def _score_impact(control, area_possible: float, overall_possible: float) -> str:
    """Maximum modeled uplift if every scored observation reached the maximum."""
    earned, possible = _weighted_scores(control.results)
    points = possible - earned
    if points <= 0:
        return "No further uplift"
    area_pp = points / area_possible * 100 if area_possible else 0.0
    overall_pp = points / overall_possible * 100 if overall_possible else 0.0
    return f"+{points:.0f} pts (+{area_pp:.2f} area pp / +{overall_pp:.2f} overall pp)"


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
    overall_earned, overall_possible = _weighted_scores(results)
    risk_id = {
        id(control): f"R-{index:03d}"
        for index, control in enumerate(consolidated_findings, start=1)
    }
    overall_pct = agg["overall"]
    overall_label, _ = rating(overall_pct)
    scored_obs = sum(1 for result in results if result.counts_toward_score)
    critical_risks = severity_total[Severity.CRITICAL]
    high_risks = severity_total[Severity.HIGH]
    lines: list[str] = [
        f"# Audit Report \u2014 {project_name}",
        "",
        f"**Project:** {project_name}  ",
        f"**Generated:** {date.today().isoformat()}  ",
        "**Basis:** deterministic, rule-based assessment; no AI in scoring.",
        "",
        "## 1. Executive Summary",
        "",
        "### 1.1 Overall Health Score",
        "",
    ]
    _aligned_table(
        lines,
        ("Metric", "Value"),
        ("l", "r"),
        (
            ("**Overall Score**", f"**{_fmt(overall_pct)}**"),
            ("Total Score", f"{overall_earned:,.0f}"),
            ("Total Possible Score", f"{overall_possible:,.0f}"),
            ("**Risk Rating**", f"**{overall_label}**"),
            ("Consolidated Controls", total_controls),
            ("Scored Observations", scored_obs),
            ("Findings", len(consolidated_findings)),
            ("Critical Risks", critical_risks),
            ("High Risks", high_risks),
        ),
    )

    lines += ["### 1.2 Area Scorecard", ""]
    scorecard_rows = []
    for pillar in Pillar.scored():
        area_controls = pillar_controls(controls, pillar)
        area_results = [result for control in area_controls for result in control.results]
        earned, possible = _weighted_scores(area_results)
        observations = sum(1 for result in area_results if result.counts_toward_score)
        area_findings = findings(area_controls)
        area_risks = sum(
            1 for control in area_findings
            if control.severity in (Severity.CRITICAL, Severity.HIGH)
        )
        scorecard_rows.append(
            (
                pillar_number[pillar],
                pillar.value,
                f"{assessment_weight(results, pillar):.1%}",
                len(area_controls),
                observations,
                f"{earned:,.0f}",
                f"{possible:,.0f}",
                rating(percentage(area_results))[0],
                len(area_findings),
                area_risks,
            )
        )
    scorecard_rows.append(
        (
            "",
            "**Overall**",
            "**100.0%**",
            f"**{total_controls}**",
            f"**{scored_obs}**",
            f"**{overall_earned:,.0f}**",
            f"**{overall_possible:,.0f}**",
            f"**{overall_label}**",
            f"**{len(consolidated_findings)}**",
            f"**{critical_risks + high_risks}**",
        )
    )
    _aligned_table(
        lines,
        (
            "#", "Area", "Assessment Weight", "Controls", "Results", "Total Score",
            "Total Possible", "Rating", "Findings", "Risks",
        ),
        ("r", "l", "r", "r", "r", "r", "r", "l", "r", "r"),
        scorecard_rows,
    )

    lines += [
        "### 1.3 Radar Chart",
        "",
        "```mermaid",
        "radar-beta",
        "  axis " + ", ".join(_AREA_SHORT),
        '  curve ScorePct["% Score"] { '
        + ", ".join(
            f"{(agg['by_pillar'][pillar]['pct'] or 0):.1f}" for pillar in Pillar.scored()
        )
        + " }",
        "```",
        "",
    ]

    lines += ["### 1.4 Top Priority Findings", ""]
    _aligned_table(
        lines,
        ("#", "Risk ID", "Finding", "Area", "Severity", "Checklist Ref"),
        ("r", "l", "l", "l", "l", "l"),
        (
            (
                index,
                risk_id[id(control)],
                control.finding,
                f"{pillar_number[control.pillar]}. {control.pillar.value}",
                control.severity.value,
                control.ref,
            )
            for index, control in enumerate(consolidated_findings[:10], start=1)
        ),
    )

    lines += ["### 1.5 Top Priority Recommendations", ""]
    _aligned_table(
        lines,
        ("#", "Recommendation", "Addresses", "Effort", "Risk Impact", "Score Impact"),
        ("r", "l", "l", "l", "l", "l"),
        (
            (
                index,
                control.recommendation,
                control.ref,
                _effort(control),
                control.severity.value,
                _score_impact(
                    control,
                    _weighted_scores(
                        [
                            result
                            for peer in pillar_controls(controls, control.pillar)
                            for result in peer.results
                        ]
                    )[1],
                    overall_possible,
                ),
            )
            for index, control in enumerate(consolidated_findings[:10], start=1)
        ),
    )

    lines += ["### 1.6 Coverage", ""]
    _aligned_table(
        lines,
        ("Measure", "Count"),
        ("l", "r"),
        (
            ("Consolidated controls", total_controls),
            ("Scored observations", scored_obs),
            ("Asset-level results assessed", len(results)),
            ("Findings", len(consolidated_findings)),
            ("Critical risks", critical_risks),
            ("High risks", high_risks),
        ),
    )

    lines += ["## 2. Workspace / Solution Overview", ""]
    workspaces = sorted({result.workspace for result in results if result.workspace})
    _aligned_table(
        lines,
        ("Workspace ID", "Name", "Layer Role", "Objects Assessed", "Asset-Level Results"),
        ("l", "l", "l", "r", "r"),
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
            )
            for workspace in workspaces
        ),
    )

    lines += ["### 2.1 Workspace Scores", ""]
    _aligned_table(
        lines,
        ("Workspace", "Numeric Observations", "Score", "Rating"),
        ("l", "r", "r", "l"),
        (
            (
                workspace_id_by_name[workspace],
                agg["by_workspace"].get(workspace, {}).get("count", 0),
                _fmt(agg["by_workspace"].get(workspace, {}).get("pct")),
                rating(agg["by_workspace"].get(workspace, {}).get("pct"))[0],
            )
            for workspace in workspaces
        ),
    )
    lines += ["## 3. Detailed Findings and Recommendations by Area", ""]
    for pillar in Pillar.scored():
        area_controls = pillar_controls(controls, pillar)
        if not area_controls:
            continue
        area_results = [result for control in area_controls for result in control.results]
        area_pct = percentage(area_results)
        observations = sum(1 for result in area_results if result.counts_toward_score)
        area_findings = findings(area_controls)
        area_risks = sum(
            1 for control in area_findings
            if control.severity in (Severity.CRITICAL, Severity.HIGH)
        )
        _, area_possible = _weighted_scores(area_results)
        lines += [
            f"### 3.{pillar_number[pillar]} Area {pillar_number[pillar]}: {pillar.value}",
            "",
            f"**Area score:** {_fmt(area_pct)} | **Rating:** {rating(area_pct)[0]} | "
            f"**Controls:** {len(area_controls)} | **Scored observations:** {observations} | "
            f"**Findings:** {len(area_findings)} | **High-priority risks:** {area_risks}",
            "",
            "#### Category Scorecard",
            "",
        ]
        by_category: dict[str, list] = defaultdict(list)
        for control in area_controls:
            by_category[category_number(control.ref)].append(control)
        category_rows = []
        for number in sorted(by_category, key=category_sort_key):
            category_controls = by_category[number]
            category_results = [
                result for control in category_controls for result in control.results
            ]
            category_earned, category_possible = _weighted_scores(category_results)
            name = category_title(number)
            label = f"{number} {name}" if name else number
            category_rows.append(
                (
                    label,
                    len(category_controls),
                    sum(1 for result in category_results if result.counts_toward_score),
                    f"{category_earned:,.0f}",
                    f"{category_possible:,.0f}",
                    rating(percentage(category_results))[0],
                )
            )
        _aligned_table(
            lines,
            ("Category", "Controls", "Observations", "Total Score", "Total Possible", "Rating"),
            ("l", "r", "r", "r", "r", "l"),
            category_rows,
        )

        area_strengths = strengths(area_controls)
        lines += ["#### Recorded Strengths", ""]
        if area_strengths:
            _aligned_table(
                lines,
                ("Ref", "Control", "Score", "Evidence"),
                ("l", "l", "r", "l"),
                (
                    (control.ref, control.title, control.score_average, control.impacted_evidence)
                    for control in area_strengths
                ),
            )
        else:
            lines += ["No fully scored strength with recorded evidence.", ""]

        lines += ["#### Findings", ""]
        if area_findings:
            _aligned_table(
                lines,
                ("#", "Checklist Ref", "Finding", "Severity", "Score"),
                ("r", "l", "l", "l", "r"),
                (
                    (index, control.ref, control.finding, control.severity.value, _avg_raw_score(control))
                    for index, control in enumerate(area_findings, start=1)
                ),
            )
        else:
            lines += ["No findings recorded for this area.", ""]

        lines += ["#### Recommendations", ""]
        if area_findings:
            _aligned_table(
                lines,
                ("#", "Recommendation", "Addresses", "Effort", "Risk Impact", "Score Impact"),
                ("r", "l", "l", "l", "l", "l"),
                (
                    (
                        index,
                        control.recommendation,
                        control.ref,
                        _effort(control),
                        control.severity.value,
                        _score_impact(control, area_possible, overall_possible),
                    )
                    for index, control in enumerate(area_findings, start=1)
                ),
            )
        else:
            lines += ["No remediation recorded for this area.", ""]
        lines += ["---", ""]

    lines += ["## 4. Risk Overview", ""]
    total_findings = len(consolidated_findings)
    risk_rows = [
        (
            severity.value,
            severity_total[severity],
            f"{severity_total[severity] / total_findings:.1%}" if total_findings else "0.0%",
        )
        for severity in reported_severities
        if severity_total[severity]
    ]
    risk_rows.append(("**Total**", f"**{total_findings}**", "**100.0%**"))
    _aligned_table(lines, ("Severity", "Risks", "% of Total"), ("l", "r", "r"), risk_rows)

    lines += ["## 5. Glossary", ""]
    _table(
        lines,
        ("Term", "Definition"),
        (
            ("Assessment observation", "One numeric 0-3 result at workspace or asset level."),
            ("Consolidated control", "All asset-level verdicts for one checklist control, combined by Ref."),
            ("Assessment weight", "The area's share of all numeric scored observations."),
            ("Total Score / Possible", "Weighted sum of achieved scores versus the maximum (3 x weight)."),
            (
                "Score Impact",
                "Maximum modeled uplift if every scored observation for the control reached 3, "
                "with the equivalent area and overall percentage-point gain.",
            ),
            (
                "Effort",
                "Scope proxy from observations below 3: High > 100 gap observations or 10 workspaces; "
                "Medium > 10 gap observations or 2 workspaces; otherwise Low.",
            ),
            ("N/A / Not assessed", "Excluded from score denominators because no numeric value is available."),
        ),
    )

    return "\n".join(lines)


_LEGEND = (
    "0 = Not Implemented | 1 = Partial | 2 = Implemented | 3 = Best Practice | "
    "N/A = Not Assessed/Not Applicable"
)


def _aligned_table(lines: list[str], headers, aligns, rows) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    separators = {"r": "---:", "c": ":---:"}
    lines.append("|" + "|".join(separators.get(a, "---") for a in aligns) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    lines.append("")


def _avg_raw_score(control) -> str:
    scores = [result.score for result in control.results if result.score is not None]
    if not scores:
        return "N/A"
    return f"{sum(scores) / len(scores):.2f}"


def _notes(control) -> str:
    parts = []
    rationale = control.rationale
    if rationale and rationale != "-":
        parts.append(rationale if rationale.endswith((".", "!", "?")) else f"{rationale}.")
    recommendation = control.recommendation
    if recommendation and recommendation != "-":
        parts.append(f"Action: {recommendation}")
    return " ".join(parts) or "-"


def _checklist_by_category(lines: list[str], controls) -> None:
    pillars = Pillar.scored()
    area_number = {pillar: index for index, pillar in enumerate(pillars, start=1)}

    lines += ["## Checklist Statistics", ""]
    stat_rows = []
    total_controls = 0
    total_observations = 0
    for pillar in pillars:
        pillar_group = pillar_controls(controls, pillar)
        if not pillar_group:
            continue
        categories = {category_number(control.ref) for control in pillar_group}
        results = [result for control in pillar_group for result in control.results]
        scored = [result for result in results if result.counts_toward_score]
        pct = percentage(results)
        stat_rows.append(
            (
                f"{area_number[pillar]}. {pillar.value}",
                len(categories),
                len(pillar_group),
                len(scored),
                _fmt(pct),
                rating(pct)[0],
            )
        )
        total_controls += len(pillar_group)
        total_observations += len(scored)
    all_results = [result for control in controls for result in control.results]
    overall_pct = percentage(all_results)
    stat_rows.append(
        (
            "**Total**",
            "",
            f"**{total_controls}**",
            f"**{total_observations}**",
            f"**{_fmt(overall_pct)}**",
            f"**{rating(overall_pct)[0]}**",
        )
    )
    _aligned_table(
        lines,
        ("Area", "Categories", "Consolidated Controls", "Scored Observations", "Score", "Rating"),
        ("l", "r", "r", "r", "r", "l"),
        stat_rows,
    )

    for pillar in pillars:
        pillar_group = pillar_controls(controls, pillar)
        if not pillar_group:
            continue
        results = [result for control in pillar_group for result in control.results]
        scored = [result for result in results if result.counts_toward_score]
        pct = percentage(results)
        lines += [
            f"## Area {area_number[pillar]}: {pillar.value}",
            "",
            f"**Area score:** {_fmt(pct)} | **Rating:** {rating(pct)[0]} | "
            f"**Scored observations:** {len(scored)}",
            "",
        ]
        by_category: dict[str, list] = defaultdict(list)
        for control in pillar_group:
            by_category[category_number(control.ref)].append(control)
        for number in sorted(by_category, key=category_sort_key):
            category_group = by_category[number]
            category_results = [
                result for control in category_group for result in control.results
            ]
            category_pct = percentage(category_results)
            name = category_title(number)
            heading = f"### {number} {name}" if name else f"### {number}"
            lines += [
                heading,
                "",
                f"**Category score:** {_fmt(category_pct)} | "
                f"**Rating:** {rating(category_pct)[0]}",
                "",
            ]
            _aligned_table(
                lines,
                ("#", "Checklist Item", "Score", "Notes / Evidence"),
                ("l", "l", "r", "l"),
                (
                    (control.ref, control.title, _avg_raw_score(control), _notes(control))
                    for control in category_group
                ),
            )


def _risk_register_by_severity(lines: list[str], consolidated_findings, severity_total) -> None:
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    if severity_total.get(Severity.INFO):
        order.append(Severity.INFO)
    indexed = list(enumerate(consolidated_findings, start=1))
    for severity in order:
        rows = [(index, control) for index, control in indexed if control.severity is severity]
        if not rows:
            continue
        lines += [f"### {severity.value} Risks", ""]
        _aligned_table(
            lines,
            ("Risk ID", "Ref ID", "Check ID", "Finding", "Area", "Impact", "Recommendation"),
            ("l", "l", "l", "l", "l", "r", "l"),
            (
                (
                    f"R-{index:03d}",
                    control.ref,
                    control.check_id,
                    control.finding,
                    control.pillar.value,
                    control.risk_profile[1],
                    control.recommendation,
                )
                for index, control in rows
            ),
        )
    total = len(consolidated_findings)
    summary_rows = [
        (
            severity.value,
            severity_total.get(severity, 0),
            f"{severity_total.get(severity, 0) / total:.1%}" if total else "0.0%",
        )
        for severity in order
        if severity_total.get(severity, 0)
    ]
    summary_rows.append(("**Total**", f"**{total}**", "**100.0%**"))
    lines += ["## Severity Summary", ""]
    _aligned_table(lines, ("Severity", "Count", "Percentage"), ("l", "r", "r"), summary_rows)


def build_checklist_markdown(project_name: str, results: list) -> str:
    """Standalone Markdown of the audit Checklist, grouped by area and category."""
    controls = consolidate(results)
    lines = [
        f"# Audit Checklist \u2014 {project_name}",
        "",
        f"**Project:** {project_name}  ",
        f"**Generated:** {date.today().isoformat()}  ",
        "**Structure:** Category-wise layout aligned with the checklist; controls, "
        "scores, and evidence are deterministic (no AI in scoring).  ",
        f"**Scoring legend:** {_LEGEND}",
        "",
        "> **Aggregation rule:** Detailed rows are consolidated by Ref. N/A, blanks, "
        "and non-numeric values are excluded from scoring.",
        "",
    ]
    _checklist_by_category(lines, controls)
    return "\n".join(lines)


def build_risk_register_markdown(project_name: str, results: list) -> str:
    """Standalone Markdown of the Risk Register, grouped by severity."""
    controls = consolidate(results)
    consolidated_findings = findings(controls)
    severity_total = severity_counts(controls)
    lines = [
        f"# Risk Register - {project_name}",
        "",
        f"**Project:** {project_name}  ",
        f"**Generated:** {date.today().isoformat()}  ",
        f"**Consolidated findings:** {len(consolidated_findings)}  ",
        "**Publication rule:** All Critical, High, Medium, and Low findings with at "
        "least one failed or partial result.",
        "",
    ]
    _risk_register_by_severity(lines, consolidated_findings, severity_total)
    return "\n".join(lines)
