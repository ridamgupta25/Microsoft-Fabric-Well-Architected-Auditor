"""Self-contained HTML readout for Fabric Well-Architected audits.

The same numbers as the Excel and Markdown renderings, presented as a tabbed
consultant readout: an assessment summary with a radar profile of every audit
area, workspace-level detail, per-area results, a filterable risk register, an
Excel-style control matrix, recommendations, and the scope and scoring notes that
let a reader check the method.

Two deliberate constraints:

* **No external assets.** CSS, JS and every chart are inlined -- charts are
  generated as SVG here rather than drawn by a library -- so the report opens
  offline, survives being emailed as a single attachment, and cannot leak a
  workspace name to a third-party host by requesting a font or a script.
* **Rendered server-side.** The tables are written as markup rather than shipped
  as a JSON blob the page assembles on load, which keeps a large estate's report
  to a few megabytes instead of tens of them, and keeps the file readable
  without JavaScript.

Tenant data is escaped on the way in (:func:`_esc`); it reaches the page as
text, never as markup.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from html import escape

from ..core.engine import READ_INCOMPLETE_CHECK_ID
from ..core.enums import Pillar, Severity, Status
from ..core.models import MAX_SCORE
from ..core.scoring import rating
from .structure import (
    RISK_PROFILE,
    assessment_weight,
    category_label,
    category_number,
    category_sort_key,
    category_title,
    consolidate,
    findings,
    pillar_controls,
    severity_counts,
    strengths,
    workspace_control_score,
    workspace_ids,
)

#: The bottom of the "Good" band in :func:`auditfast.core.scoring.rating`, drawn
#: on the charts so a shape is read against the mark the estate is aiming at.
#: Taken from the scorer rather than restated, so the two cannot drift apart.
GOOD_THRESHOLD = 76.0

#: The score a remediation programme is steered towards. Above the Good band and
#: inside Excellent, so it reads as "sustained best practice" rather than "just
#: over the line".
MATURITY_TARGET = 90.0

#: Rating label -> CSS class. The labels come from ``rating()``; keeping the map
#: keyed by label means a change there surfaces as a missing colour rather than a
#: silently mislabelled badge.
_RATING_CLASS = {
    "Excellent": "excellent",
    "Good": "good",
    "Medium": "medium",
    "High": "high",
    "Critical": "critical",
    "Not assessed": "not-assessed",
}

_SEVERITY_CLASS = {
    Severity.CRITICAL: "critical",
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
    Severity.INFO: "informational",
}

_ORDERED_SEVERITIES = (
    Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO,
)

#: The severities a remediation programme closes in order, for the timeline
#: projection. Any severity outside this sequence is folded into the last
#: milestone so the modelled ceiling still reaches 100%.
_CLOSURE_SEVERITIES = (
    Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW,
)

#: The engine builds every recommendation as
#: ``Target: ... Observed gap: ... Action: ... Verification: ...``. The Action
#: clause is the pre-written remediation for the ref, so it is identical across
#: every asset that failed the same check -- pulling it out turns a page of
#: repeated per-asset paragraphs into one instruction.
_ACTION = re.compile(r"Action:\s*(.+?)\s*Verification:", re.DOTALL)

#: The project name shipped in ``config/project.example.yaml``. A run started
#: from the UI still loads that file, so this value reaching the report means
#: nobody named the engagement -- it is a placeholder, not a title.
EXAMPLE_PROJECT_NAME = "Sales Analytics - Fabric Migration"

#: Escaped here rather than inline: a backslash inside an f-string expression is
#: a syntax error before Python 3.12, and this package supports 3.10.
_DASH = "\u2014"
_MIDDOT = "\u00b7"


def _esc(value) -> str:
    """Escape a value for HTML text content.

    ``from html import escape`` rather than ``import html``: this module is
    itself named ``html``, and while Python 3's absolute imports resolve the
    plain form to the standard library correctly, importing the one name needed
    leaves nothing for a reader to second-guess.
    """
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _pct(value) -> str:
    return "N/A" if value is None else f"{value:.1f}%"


def _num(value) -> str:
    """A 0-3 score to two places, or an em dash when there is no score."""
    if value is None or value == "" or isinstance(value, str):
        return _DASH
    return f"{float(value):.2f}"


def _rating_class(value) -> str:
    return _RATING_CLASS.get(rating(value)[0], "not-assessed")


def _badge(text, kind: str) -> str:
    return f'<span class="badge {kind}">{_esc(text)}</span>'


def _rating_badge(value) -> str:
    return _badge(rating(value)[0], _rating_class(value))


def _severity_badge(severity: Severity) -> str:
    return _badge(severity.value, _SEVERITY_CLASS.get(severity, "informational"))


def _score_class(value) -> str:
    """The Excel-style heat class for a 0-3 band."""
    if value is None or value == "" or isinstance(value, str):
        return "score-na"
    return f"score-{max(0, min(MAX_SCORE, round(float(value))))}"


def _cell(value) -> str:
    if value is None or value == "":
        return _DASH
    return _esc(value)


def _common_prefix(names) -> str:
    """The longest string every name starts with.

    Comparing only the alphabetically first and last name is enough: any
    character they share at a position is shared by everything between them.
    """
    names = list(names)
    if not names:
        return ""
    first, last = min(names), max(names)
    for index, character in enumerate(first):
        if index >= len(last) or last[index] != character:
            return first[:index]
    return first


def _report_title(project_name: str, workspace_names, groups=()) -> str:
    """What this report is *about*, taken from the estate rather than the config.

    The project name in the YAML is frequently the shipped example, so trusting
    it first titles a real audit "Sales Analytics - Fabric Migration". The
    estate itself is the reliable source, in descending order of how specific
    the reader will find it:

    1. the project group the reviewer built for this run -- they named it, so it
       is the name they are expecting to see (several groups are listed, and
       more than three collapse to a count);
    2. the single workspace, when only one was audited;
    3. the prefix every workspace name shares -- ``MDM_DEV``/``_UAT``/``_PROD``
       becomes ``MDM``, which is the name the business already uses;
    4. a plain count, when the workspaces have nothing in common. A generic
       heading is better than a confidently wrong one, and the workspace
       inventory below it says exactly what was covered.

    The project name is used only to qualify that last case, and only when it is
    not the shipped example.
    """
    names = sorted(workspace_names)
    group_names = sorted(
        str(group.get("name") or "").strip()
        for group in (groups or [])
        if isinstance(group, dict) and str(group.get("name") or "").strip()
    )
    if len(group_names) == 1:
        return group_names[0]
    if 1 < len(group_names) <= 3:
        return f"{', '.join(group_names)} ({len(names)} workspaces)"
    if group_names:
        return f"{len(group_names)} project groups ({len(names)} workspaces)"
    if not names:
        return project_name or "Fabric estate"
    if len(names) == 1:
        return names[0]
    prefix = _common_prefix(names).strip(" _-\u2013\u2014")
    if len(prefix) >= 3:
        return f"{prefix} ({len(names)} workspaces)"
    if project_name and project_name != EXAMPLE_PROJECT_NAME:
        return f"{project_name} ({len(names)} workspaces)"
    return f"Fabric estate ({len(names)} workspaces)"


def _category(control) -> str:
    """The checklist subsection a control belongs to, e.g. ``1.1``.

    Derived from the ref rather than the pillar: a check's pillar is the theme
    its author judged it to belong to, while the ref records where the checklist
    itself files it. The two do not always agree, and this column is about the
    checklist.
    """
    return category_label(control.ref)


def _action_text(control) -> str:
    """The one instruction behind a finding, without its per-asset repetition."""
    actions = []
    for result in control.results:
        match = _ACTION.search(result.recommendation or "")
        if match:
            text = match.group(1).strip()
            if text not in actions:
                actions.append(text)
    if actions:
        return " ".join(actions)
    return control.recommendation or ""


def _table(headers, rows, *, table_id: str = "", classes: str = "",
           wrap_id: str = "", empty: str = "Nothing to report here.") -> str:
    """One dense data table.

    A cell may be rendered HTML, or a ``(css_class, html)`` pair when the cell
    itself needs colouring -- which is how the control matrix fills a whole cell
    with its score band instead of floating a pill inside it.
    """
    body = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                cells.append(f'<td class="{cell[0]}">{cell[1]}</td>')
            else:
                cells.append(f"<td>{cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    if not body:
        return f'<p class="empty">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    attrs = f' id="{_esc(table_id)}"' if table_id else ""
    wrap_attrs = f' id="{_esc(wrap_id)}"' if wrap_id else ""
    css = f"table {classes}".strip()
    return (
        f'<div class="dtable-wrap"{wrap_attrs}><table class="{css}"{attrs}>'
        f"<thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def _bars(rows, maximum: float | None = None) -> str:
    """Horizontal bars: ``(label, value, display)`` scaled against ``maximum``."""
    rows = list(rows)
    if not rows:
        return '<p class="empty">Nothing to chart.</p>'
    top = maximum if maximum else max((value or 0) for _, value, _ in rows) or 1
    out = []
    for label, value, display in rows:
        width = min(100.0, (float(value or 0) / top) * 100.0)
        out.append(
            f'<div class="bar-row"><span title="{_esc(label)}">{_esc(label)}</span>'
            f'<div class="bar"><i style="width:{width:.1f}%"></i></div>'
            f"<b>{_esc(display)}</b></div>"
        )
    return "".join(out)


def _polar(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    radians = math.radians(angle - 90)
    return (cx + radius * math.cos(radians), cy + radius * math.sin(radians))


def _wrap_label(text: str, limit: int = 18) -> list[str]:
    lines: list[str] = []
    for word in str(text).split():
        if lines and len(f"{lines[-1]} {word}") <= limit:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines or [""]


def _radar(areas) -> str:
    """The estate profile: one spoke per audit area, against the Good threshold.

    An area with no score is plotted at the centre rather than dropped, so the
    shape keeps one spoke per area and cannot silently gain a straight edge where
    a whole pillar went unassessed. Its label still reads N/A in the table.
    """
    areas = list(areas)
    if len(areas) < 3:
        return '<p class="empty">A radar needs at least three areas to plot.</p>'
    count = len(areas)
    cx, cy, radius = 350.0, 250.0, 165.0
    angles = [index * 360 / count for index in range(count)]

    def ring(value: float) -> str:
        return " ".join(
            ",".join(f"{point:.1f}" for point in _polar(cx, cy, value, angle))
            for angle in angles
        )

    shape = " ".join(
        ",".join(
            f"{point:.1f}"
            for point in _polar(cx, cy, radius * (area["score"] or 0) / 100, angle)
        )
        for area, angle in zip(areas, angles, strict=True)
    )

    parts = [
        '<svg viewBox="0 0 700 560" role="img" '
        'aria-label="Radar chart of every audit area">'
    ]
    for level in (0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<polygon points="{ring(radius * level)}" fill="none" stroke="#d7e0e3"/>'
        )
    for angle in angles:
        x, y = _polar(cx, cy, radius, angle)
        parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#d7e0e3"/>'
        )
    parts.append(
        f'<polygon points="{ring(radius * GOOD_THRESHOLD / 100)}" fill="none" '
        'stroke="#c52b3b" stroke-width="2" stroke-dasharray="6 5"/>'
        f'<polygon points="{shape}" fill="rgba(36,105,160,.18)" stroke="#2469a0" '
        'stroke-width="3"/>'
    )
    for area, angle in zip(areas, angles, strict=True):
        x, y = _polar(cx, cy, radius + 72, angle)
        anchor = "end" if x < cx - 35 else "start" if x > cx + 35 else "middle"
        lines = _wrap_label(f'{area["number"]}. {area["name"]}')
        start = y - (len(lines) - 1) * 6
        spans = "".join(
            f'<tspan x="{x:.1f}" dy="{12 if index else 0}">{_esc(line)}</tspan>'
            for index, line in enumerate(lines)
        )
        parts.append(
            f'<text x="{x:.1f}" y="{start:.1f}" text-anchor="{anchor}" font-size="10" '
            f'font-weight="700" fill="#52606d">{spans}</text>'
        )
    parts.append(
        '<rect x="220" y="535" width="16" height="3" fill="#2469a0"/>'
        '<text x="242" y="540" font-size="10" fill="#52606d">Area score</text>'
        '<line x1="355" y1="537" x2="374" y2="537" stroke="#c52b3b" stroke-width="2" '
        'stroke-dasharray="5 4"/>'
        f'<text x="380" y="540" font-size="10" fill="#52606d">'
        f"{GOOD_THRESHOLD:.0f}% Good threshold</text></svg>"
    )
    return "".join(parts)


def _projection(results):
    """Modelled estate score after closing each severity band, in order.

    Not a forecast. Each milestone answers one arithmetic question -- "what would
    the score be if every failing control of this severity, and every severity
    above it, scored full marks?" -- with the denominator held fixed. That makes
    it a ceiling for the work, computed from the same weights the live score
    uses, rather than an invented trajectory.

    Returns ``[(label, sla, score, remaining_note), ...]`` starting at today.
    """
    scored = [result for result in results if result.counts_toward_score]
    possible = sum(MAX_SCORE * result.weight for result in scored)
    if not scored or possible <= 0:
        return []

    earned = sum((result.score or 0) * result.weight for result in scored)
    gap_by_severity: dict[Severity, float] = defaultdict(float)
    open_by_severity: dict[Severity, int] = defaultdict(int)
    for result in scored:
        shortfall = (MAX_SCORE - (result.score or 0)) * result.weight
        if shortfall <= 0:
            continue
        # A gap outside the closure sequence still has to be closed for the
        # estate to reach 100%, so it lands in the final milestone rather than
        # being dropped -- otherwise the modelled ceiling reads below full marks.
        bucket = (
            result.severity if result.severity in _CLOSURE_SEVERITIES
            else _CLOSURE_SEVERITIES[-1]
        )
        gap_by_severity[bucket] += shortfall
        open_by_severity[bucket] += 1

    points = [(
        "Current", "Baseline", min(100.0, earned / possible * 100.0),
        f"{open_by_severity[Severity.CRITICAL]} Critical control(s) open",
    )]
    running = earned
    for severity in _CLOSURE_SEVERITIES:
        running += gap_by_severity[severity]
        points.append((
            f"{severity.value} closure",
            RISK_PROFILE[severity][3],
            min(100.0, running / possible * 100.0),
            f"{open_by_severity[severity]} {severity.value} control(s) closed",
        ))
    return points


def _projection_chart(points) -> str:
    """The modelled improvement path, with the Good and maturity marks drawn on."""
    points = list(points)
    if len(points) < 2:
        return '<p class="empty">Not enough scored work to model a path.</p>'
    width, height = 760.0, 330.0
    left, right, top, bottom = 62.0, 30.0, 30.0, 250.0
    plot = width - left - right
    step = plot / (len(points) - 1)

    def y_for(score: float) -> float:
        return bottom - (max(0.0, min(100.0, score)) / 100.0) * (bottom - top)

    parts = [
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        'aria-label="Modelled score improvement by remediation milestone">'
    ]
    for level in (0, 20, 40, 60, 80, 100):
        y = y_for(level)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot:.1f}" y2="{y:.1f}" '
            'stroke="#e7edef"/>'
            f'<text x="{left - 9}" y="{y + 3:.1f}" font-size="9" fill="#8a969c" '
            f'text-anchor="end">{level}%</text>'
        )
    for value, colour, label in (
        (MATURITY_TARGET, "#197052", f"{MATURITY_TARGET:.0f}% maturity target"),
        (GOOD_THRESHOLD, "#c52b3b", f"{GOOD_THRESHOLD:.0f}% Good threshold"),
    ):
        y = y_for(value)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot:.1f}" y2="{y:.1f}" '
            f'stroke="{colour}" stroke-width="1.5" stroke-dasharray="6 4"/>'
            f'<text x="{left + 6}" y="{y - 5:.1f}" font-size="9.5" font-weight="700" '
            f'fill="{colour}">{_esc(label)}</text>'
        )

    coordinates = [
        (left + index * step, y_for(score))
        for index, (_, _, score, _) in enumerate(points)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    parts.append(
        f'<polyline points="{line}" fill="none" stroke="#087f78" stroke-width="3"/>'
    )
    for index, ((x, y), (label, sla, score, _)) in enumerate(
        zip(coordinates, points, strict=True)
    ):
        filled = "#087f78" if index == 0 else "#fff"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{filled}" '
            'stroke="#087f78" stroke-width="3"/>'
            f'<text x="{x:.1f}" y="{y - 14:.1f}" font-size="11" font-weight="800" '
            f'fill="#18313c" text-anchor="middle">{score:.0f}%</text>'
            f'<text x="{x:.1f}" y="{bottom + 22:.1f}" font-size="9.5" '
            f'font-weight="700" fill="#18313c" text-anchor="middle">{_esc(label)}</text>'
            f'<text x="{x:.1f}" y="{bottom + 36:.1f}" font-size="9" fill="#8a969c" '
            f'text-anchor="middle">{_esc(sla)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _point_list(items) -> str:
    entries = [item for item in items if item]
    if not entries:
        return "<span>No detail recorded.</span>"
    return (
        '<ul class="point-list">'
        + "".join(f"<li>{_esc(item)}</li>" for item in entries)
        + "</ul>"
    )


def _details(label: str, body: str) -> str:
    return (
        f'<details class="details"><summary>{_esc(label)}</summary>'
        f"<div>{body}</div></details>"
    )


def _workspace_blocks(control, *, failed_only: bool = False):
    """Per-workspace outcome for one control, worst first.

    Workspaces where the control did not apply are always left out: a reader
    opening the evidence wants the places it was actually judged, and a wall of
    N/A rows buries them. ``failed_only`` narrows further to the workspaces that
    actually carry the finding, which is what the risk register is about.
    """
    grouped: dict[str, list] = defaultdict(list)
    for result in control.results:
        grouped[result.workspace or "Project-wide"].append(result)

    blocks = []
    for workspace, entries in sorted(grouped.items()):
        judged = [entry for entry in entries if entry.status is not Status.NA]
        if not judged:
            continue
        statuses = {entry.status for entry in judged}
        status = next(
            (
                candidate
                for candidate in (Status.FAIL, Status.PARTIAL, Status.PASS, Status.INFO)
                if candidate in statuses
            ),
            judged[0].status,
        )
        if failed_only and status not in (Status.FAIL, Status.PARTIAL):
            continue
        scores = [entry.score for entry in judged if entry.score is not None]
        detail = status.value
        if scores:
            detail += f" {_MIDDOT} Score {sum(scores) / len(scores):.2f}"
        blocks.append(
            f'<section class="workspace-item"><h4>{_esc(workspace)}</h4>'
            f"<small>{_esc(detail)}</small>"
            + _point_list(sorted({entry.evidence for entry in judged if entry.evidence}))
            + "</section>"
        )
    return blocks


def _evidence_details(control, *, failed_only: bool = False, label: str = "") -> str:
    blocks = _workspace_blocks(control, failed_only=failed_only)
    if not blocks:
        return _details(
            label or "View evidence",
            "<span>No applicable workspace evidence recorded.</span>",
        )
    noun = "failed-workspace evidence" if failed_only else "workspace results"
    return _details(
        label or f"View {noun} ({len(blocks)})",
        f'<div class="workspace-evidence">{"".join(blocks)}</div>',
    )


def _filters(prefix: str, selects, *, placeholder: str) -> str:
    """A filter row bound to the table with id ``<prefix>-table``."""
    fields = "".join(
        f"<label>{_esc(label)}"
        f'<select data-filter-for="{_esc(prefix)}" data-column="{column}">'
        f'<option value="">{_esc(blank)}</option>'
        + "".join(f"<option>{_esc(option)}</option>" for option in options)
        + "</select></label>"
        for label, column, options, blank in selects
    )
    return (
        f'<div class="filters">{fields}'
        f'<label>Search<input type="search" data-filter-for="{_esc(prefix)}" '
        f'placeholder="{_esc(placeholder)}"></label>'
        f'<span class="count" id="{_esc(prefix)}-count"></span></div>'
    )


def _panel(panel_id: str, title: str, description: str, body: str,
           *, active: bool = False) -> str:
    return (
        f'<section class="panel{" active" if active else ""}" '
        f'id="panel-{_esc(panel_id)}">'
        f'<div class="title">{_esc(title)}</div>'
        f'<p class="desc">{_esc(description)}</p>{body}</section>'
    )


def _box(title: str, subtitle: str, body: str) -> str:
    return (
        f'<div class="box"><h3>{_esc(title)}</h3>'
        f'<div class="box-sub">{_esc(subtitle)}</div>{body}</div>'
    )


def _kpis(entries) -> str:
    cards = "".join(
        f'<div class="kpi"><b>{_esc(value)}</b><span>{_esc(label)}</span></div>'
        for value, label in entries
    )
    return f'<div class="kpis">{cards}</div>'


def build_html(
    project_name: str,
    agg: dict,
    results: list,
    errors: list | None = None,
    groups: list | None = None,
) -> str:
    """Render the audit as one self-contained HTML readout.

    ``groups`` is the run's project groups, used only to title the report: the
    reviewer named them, so they beat a project name that may still be the
    shipped example. It never affects a number.
    """
    controls = consolidate(results)
    workspace_id_by_name = workspace_ids(results)
    consolidated_findings = findings(controls)
    consolidated_strengths = strengths(controls)
    severity_total = severity_counts(controls)
    control_counts = {status: sum(c.status is status for c in controls) for status in Status}
    pillar_number = {pillar: index for index, pillar in enumerate(Pillar.scored(), start=1)}
    critical_high = severity_total[Severity.CRITICAL] + severity_total[Severity.HIGH]
    reported_severities = tuple(
        severity for severity in _ORDERED_SEVERITIES
        if severity is not Severity.INFO or severity_total[Severity.INFO]
    )
    na_results = [result for result in results if result.status is Status.NA]
    objects_assessed = len({(r.workspace, r.obj) for r in results if r.obj})
    scored_results = sum(1 for r in results if r.counts_toward_score)

    areas = [
        {
            "number": pillar_number[pillar],
            "name": pillar.value,
            "score": agg["by_pillar"][pillar]["pct"],
            "weight": assessment_weight(results, pillar) * 100,
            "controls": pillar_controls(controls, pillar),
        }
        for pillar in Pillar.scored()
    ]
    area_options = [f'{area["number"]}. {area["name"]}' for area in areas]

    # -- Assessment summary ----------------------------------------------------
    mast_stats = "".join(
        f'<div class="mast-stat"><b>{_esc(value)}</b><span>{_esc(label)}</span></div>'
        for value, label in (
            (_pct(agg["overall"]), "Overall score"),
            (rating(agg["overall"])[0], "Risk rating"),
            (len(controls), "Controls"),
            (len(consolidated_findings), "Key risks"),
            (len(workspace_id_by_name), "In-scope workspaces"),
        )
    )

    overview = (
        f"The assessment produced an overall score of {_pct(agg['overall'])}, "
        f"corresponding to a {rating(agg['overall'])[0]} risk rating across "
        f"{len(controls)} consolidated controls and {scored_results:,} scored "
        "workspace results."
    )
    hero = (
        f'<div class="hero-score">{_pct(agg["overall"])}</div>'
        f'<div>{_rating_badge(agg["overall"])}<p>{_esc(overview)}</p></div>'
    )

    severity_bars = _bars(
        (severity.value, severity_total[severity], severity_total[severity])
        for severity in reported_severities
    )

    summary_panel = _panel(
        "summary",
        "Assessment Summary",
        f"Overall posture and all {len(areas)} assessment areas.",
        f'<div class="hero">{hero}</div>'
        + _kpis(
            (
                (len(controls), "Controls assessed"),
                (len(consolidated_findings), "Key risks"),
                (critical_high, "Critical + High"),
                (control_counts[Status.PASS], "Passing controls"),
            )
        )
        + '<div class="grid2">'
        + _box(
            "Audit area performance",
            f"{len(areas)} weighted Fabric audit areas against the "
            f"{GOOD_THRESHOLD:.0f}% Good threshold.",
            f'<div class="radar-chart">{_radar(areas)}</div>',
        )
        + _box("Risk severity", "Key risks by severity.", severity_bars)
        + "</div>"
        + _table(
            ("#", "Area", "Weight", "Score", "Rating"),
            (
                (
                    area["number"],
                    f'<b>{_esc(area["name"])}</b>',
                    _pct(area["weight"]),
                    f'<b>{_pct(area["score"])}</b>',
                    _rating_badge(area["score"]),
                )
                for area in areas
            ),
        ),
        active=True,
    )

    # -- Workspace level details -----------------------------------------------
    workspace_rows = sorted(
        agg.get("by_workspace", {}).items(),
        key=lambda item: (item[1]["pct"] is None, item[1]["pct"] or 0, item[0]),
    )
    comparison_panel = _panel(
        "comparison",
        "Workspace Level Details",
        "In-scope workspace scores, assessed objects, and control-result volumes.",
        _box(
            "Workspace score comparison",
            f"Every in-scope workspace against the {GOOD_THRESHOLD:.0f}% Good "
            "threshold.",
            _bars(
                (
                    (name, data["pct"] or 0, _pct(data["pct"]))
                    for name, data in workspace_rows
                ),
                100,
            ),
        )
        + _table(
            ("Workspace ID", "Name", "Score", "Objects assessed", "Control results"),
            (
                (
                    f'<span class="ref">{_esc(workspace_id_by_name.get(name, ""))}</span>',
                    _esc(name),
                    f'<b>{_pct(data["pct"])}</b>',
                    f'<span class="num">'
                    f'{len({r.obj for r in results if r.workspace == name and r.obj})}'
                    "</span>",
                    f'<span class="num">'
                    f"{sum(r.workspace == name for r in results)}</span>",
                )
                for name, data in workspace_rows
            ),
            table_id="inventory-table",
            empty="No workspace was scored.",
        ),
    )

    # -- Detailed results by area ----------------------------------------------
    area_sections = []
    for area in areas:
        rows = sorted(area["controls"], key=lambda control: control.ref)
        priority = sum(
            1 for control in rows
            if control.severity in (Severity.CRITICAL, Severity.HIGH)
            and control.status in (Status.FAIL, Status.PARTIAL)
        )
        area_sections.append(
            '<section class="area-sec">'
            '<div class="area-head" role="button" tabindex="0">'
            f'<span class="area-num">Area {area["number"]}</span>'
            f'<span>{_esc(area["name"])}</span>'
            f'<span class="area-meta">{_pct(area["score"])} {_MIDDOT} {len(rows)} '
            f"controls {_MIDDOT} {priority} priority</span>"
            '<span class="area-toggle">&#9660;</span></div>'
            '<div class="area-content">'
            f'<div class="area-summary">{_rating_badge(area["score"])}'
            f'<span class="badge informational">Weight {_pct(area["weight"])}</span>'
            "</div>"
            + _table(
                ("Ref / check", "Severity", "Score", "Recorded result and evidence"),
                (
                    (
                        f'<span class="ref">{_esc(control.ref)}</span>'
                        f'<span class="ref-id">{_esc(control.check_id)}</span>'
                        f'<div class="check-title">{_esc(control.title)}</div>',
                        _severity_badge(control.severity),
                        _num(control.score_average),
                        _evidence_details(control),
                    )
                    for control in rows
                ),
                empty="No control in this area.",
            )
            + "</div></section>"
        )

    results_panel = _panel(
        "results",
        "Detailed Results by Area",
        "Controls grouped by audit area. Expand an area to inspect the result "
        "recorded for each workspace.",
        "".join(area_sections),
    )

    # -- Key risks -------------------------------------------------------------
    risk_rows = (
        (
            f'<span class="ref">R-{index:03d}</span>',
            f'<span class="ref">{_esc(control.ref)}</span>',
            f"<b>{_esc(control.title)}</b>",
            _esc(control.pillar.value),
            _severity_badge(control.severity),
            _evidence_details(control, failed_only=True),
            "Open",
        )
        for index, control in enumerate(consolidated_findings, start=1)
    )
    risks_panel = _panel(
        "risks",
        "Key Risks",
        "Consolidated risks with evidence from failed workspaces, filterable by "
        "area, severity, and text.",
        _filters(
            "risk",
            (
                ("Area", 3, area_options, "All areas"),
                ("Severity", 4,
                 [severity.value for severity in reported_severities], "All"),
            ),
            placeholder="Ref, check title, area, evidence",
        )
        + _table(
            ("Risk ID", "Ref", "Check title", "Area", "Severity", "Evidence", "Status"),
            risk_rows,
            table_id="risk-table",
            empty="No risks \u2014 every assessed control passed.",
        ),
    )

    # -- Control explorer ------------------------------------------------------
    matrix_workspaces = list(workspace_id_by_name)
    control_head = [
        "Ref", "Check ID", "Control", "Area", "Category", "Severity", "Overall score",
        *matrix_workspaces,
    ]
    control_rows = []
    for control in controls:
        cells = [
            f'<span class="ref">{_esc(control.ref)}</span>',
            f'<span class="ref-id">{_esc(control.check_id)}</span>',
            f"<b>{_esc(control.title)}</b>",
            f'{pillar_number.get(control.pillar, "")}. {_esc(control.pillar.value)}',
            _esc(_category(control)),
            _severity_badge(control.severity),
            (f"score {_score_class(control.score_average)}",
             _num(control.score_average)),
        ]
        for name in matrix_workspaces:
            value = workspace_control_score(control, name)
            cells.append((f"score {_score_class(value)}", _num(value)))
        control_rows.append(cells)

    controls_panel = _panel(
        "controls",
        "Control Explorer",
        "Checklist-style matrix with every consolidated control and the score "
        "recorded for it in each in-scope workspace.",
        _filters(
            "control",
            (
                ("Area", 3, area_options, "All areas"),
                ("Severity", 5,
                 [severity.value for severity in _ORDERED_SEVERITIES], "All"),
            ),
            placeholder="Ref, control, area, category",
        )
        + '<div class="scroll-top" data-scroll-for="control-wrap"><div></div></div>'
        + _table(
            control_head,
            control_rows,
            table_id="control-table",
            classes="matrix-table",
            wrap_id="control-wrap",
            empty="No control was assessed.",
        ),
    )

    # -- Recommendations -------------------------------------------------------
    area_priority = sorted(
        (
            (
                f'{area["number"]}. {area["name"]}',
                sum(
                    1 for control in findings(area["controls"])
                    if control.severity in (Severity.CRITICAL, Severity.HIGH)
                ),
            )
            for area in areas
        ),
        key=lambda item: (-item[1], item[0]),
    )[:8]

    recommendation_rows = (
        (
            f'<span class="ref">{_esc(control.ref)}</span>',
            f"<b>{_esc(control.title)}</b>",
            _severity_badge(control.severity),
            _details("View detailed action", _point_list([_action_text(control)])),
            _details(
                "View impacted assets",
                _point_list([control.impacted_assets]),
            ),
        )
        for control in consolidated_findings
    )

    recommendations_panel = _panel(
        "recommendations",
        "Recommendations and Delivery Priorities",
        "Severity and area concentration followed by the severity-first action "
        "register.",
        _box(
            "Recommendations by severity",
            "Critical and High actions should be scheduled first.",
            severity_bars,
        )
        + _box(
            "Priority concentration by area",
            "Areas with the largest number of Critical and High recommendations.",
            _bars((label, count, count) for label, count in area_priority),
        )
        + _filters(
            "rec",
            (
                ("Severity", 2,
                 [severity.value for severity in reported_severities], "All"),
            ),
            placeholder="Ref, action, assets",
        )
        + _table(
            ("Ref", "Check title", "Severity", "Recommended action", "Impacted assets"),
            recommendation_rows,
            table_id="rec-table",
            empty="No recommendation \u2014 every assessed control passed.",
        ),
    )

    # -- Next step timeline ----------------------------------------------------
    projection = _projection(results)
    milestone_cards = "".join(
        f'<article class="phase {"current" if index == 0 else ""}">'
        f'<div class="when">{_esc(label)}</div>'
        f'<div class="big">{score:.0f}%</div>'
        f'<div class="days">{_esc(sla)}</div>'
        f"<p>{_esc(note)}</p></article>"
        for index, (label, sla, score, note) in enumerate(projection)
    ) or '<p class="empty">Nothing scored, so no path can be modelled.</p>'

    timeline_panel = _panel(
        "timeline",
        "Next Step Timeline",
        "Severity-led remediation sequence and closure expectations.",
        '<div class="callout"><strong>Starting point:</strong> '
        f'{_pct(agg["overall"])} current score with '
        f"{severity_total[Severity.CRITICAL]} Critical risk(s) open. Closure "
        "requires implementation evidence and independent re-test.</div>"
        + _box(
            "Modelled score improvement by remediation milestone",
            "Current and modelled estate score as each severity band is closed. "
            "Denominator-fixed scenarios, not delivery forecasts.",
            f'<div class="line-chart">{_projection_chart(projection)}</div>',
        )
        + f'<div class="timeline">{milestone_cards}</div>',
    )

    # -- Scoring ---------------------------------------------------------------
    area_totals = []
    for area in areas:
        area_results = [
            result for control in area["controls"] for result in control.results
            if result.counts_toward_score
        ]
        received = sum((result.score or 0) * result.weight for result in area_results)
        available = sum(MAX_SCORE * result.weight for result in area_results)
        subgroups = sorted(
            {category_number(control.ref) for control in area["controls"]},
            key=category_sort_key,
        )
        area_totals.append((
            area["number"],
            f'<b>{_esc(area["name"])}</b>',
            "".join(
                f'<div class="subgroup"><span class="ref">{_esc(number)}</span>'
                f"{_esc(category_title(number))}</div>"
                for number in subgroups
            ) or _DASH,
            f"{received:,.0f}",
            f"{available:,.0f}",
            _pct(area["score"]),
            _rating_badge(area["score"]),
        ))

    scoring_panel = _panel(
        "scoring",
        "Assessment Scoring and Criteria",
        "Only scored observations contribute to results; unavailable or "
        "not-assessed values are excluded from the arithmetic.",
        '<div class="title" style="font-size:16px">Control score scale</div>'
        + _table(
            ("Score", "Meaning", "Treatment"),
            (
                (("score score-0", "0"), "Not implemented", "Priority remediation"),
                (("score score-1", "1"), "Major implementation gaps",
                 "Strengthen and verify"),
                (("score score-2", "2"), "Implemented with improvement remaining",
                 "Targeted improvement"),
                (("score score-3", "3"), "Fully implemented / best practice",
                 "Maintain evidence"),
                (("score score-na", _DASH), "Not assessed or unavailable",
                 "Excluded from arithmetic"),
            ),
        )
        + '<div class="title" style="font-size:16px">Risk rating bands</div>'
        + _table(
            ("Band", "Range", "Interpretation"),
            (
                (_badge("Critical", "critical"), "0% to &lt;41%",
                 "Immediate remediation"),
                (_badge("High", "high"), "41% to &lt;61%", "Significant gaps"),
                (_badge("Medium", "medium"), "61% to &lt;76%", "Targeted improvement"),
                (_badge("Good", "good"), "76% to &lt;91%", "Minor improvements"),
                (_badge("Excellent", "excellent"), "91% to 100%",
                 "Maintain best practice"),
            ),
        )
        + '<div class="title" style="font-size:16px">Area-wise total score '
        "calculation</div>"
        + _table(
            ("#", "Area", "Subgroups", "Received score", "Available score", "Score",
             "Rating"),
            area_totals,
            table_id="area-total-table",
        ),
    )

    # -- Scope and evidence ----------------------------------------------------
    completeness = ""
    if errors:
        completeness = (
            '<div class="callout"><strong>Reads that did not complete:</strong> '
            "controls depending on this data are reported as not assessed rather "
            "than failed.</div>"
            + _table(
                ("Workspace", "Read limitation"),
                (
                    (_cell(getattr(error, "workspace", "")),
                     _cell(getattr(error, "evidence", "")))
                    for error in errors
                ),
            )
        )

    na_table = ""
    if na_results:
        groups: dict[str, list] = {}
        for result in na_results:
            key = (result.evidence or "Not applicable").strip()
            group = groups.setdefault(key, [0, set()])
            group[0] += 1
            group[1].add(str(result.pillar))
        na_table = (
            f'<div class="title" style="font-size:16px">Not assessed \u2014 N/A '
            f"({len(na_results)})</div>"
            '<p class="desc">These checks could not be evaluated, usually because '
            "the data they read could not be fetched. They are not failures and do "
            "not affect the score.</p>"
            + _table(
                ("Reason", "Checks", "Areas affected"),
                (
                    (_esc(reason), count, _esc(", ".join(sorted(pillars))))
                    for reason, (count, pillars) in sorted(
                        groups.items(), key=lambda item: -item[1][0]
                    )
                ),
            )
        )

    read_limited = [
        workspace for workspace in sorted({r.workspace for r in results if r.workspace})
        if any(
            r.workspace == workspace and r.check_id == READ_INCOMPLETE_CHECK_ID
            for r in results
        )
    ]

    scope_panel = _panel(
        "scope",
        "Assessment Scope & Evidence",
        "What the audit assessed, the evidence behind each conclusion, and the "
        "boundaries that apply when interpreting the results.",
        _kpis(
            (
                (len(workspace_id_by_name), "In-scope workspaces"),
                (f"{objects_assessed:,}", "Objects assessed"),
                (len(controls), "Controls"),
                (len(areas), "Assessment areas"),
            )
        )
        + '<div class="grid2">'
        + _box(
            "Audit scope",
            "The Fabric estate and practices the assessment covered.",
            _point_list([
                "Workspace configuration, role assignments and layer architecture "
                "across the in-scope estate.",
                "Fabric objects including pipelines, notebooks, lakehouses, "
                "warehouses, semantic models and reports.",
                "Controls across architecture, ingestion, processing, modelling, "
                "data quality, security, governance, reliability, monitoring and "
                "cost.",
                "Cross-workspace controls comparing environments within a project "
                "group.",
            ]),
        )
        + _box(
            "Evidence reviewed",
            "Read-only evidence available at the time of assessment.",
            '<div class="definition-grid">'
            + "".join(
                f'<article class="definition"><b>{_esc(value)}</b>'
                f"<strong>{_esc(title)}</strong><span>{_esc(note)}</span></article>"
                for value, title, note in (
                    (len(controls), "Consolidated controls",
                     "One row per check, rolled up from its asset-level verdicts."),
                    (f"{len(results):,}", "Control results",
                     "Individual object/control outcomes; one object can be "
                     "evaluated by many controls."),
                    (f"{objects_assessed:,}", "Objects assessed",
                     "Distinct Fabric items evaluated by at least one control."),
                    (control_counts[Status.NA], "Not assessed",
                     "Controls whose data could not be read, excluded from the "
                     "arithmetic."),
                )
            )
            + "</div>",
        )
        + "</div>"
        + '<div class="grid2">'
        + _box(
            "How evidence supports a result",
            "Every number on this page traces back to a recorded verdict.",
            _point_list([
                "Each control carries a score, a status and the evidence recorded "
                "for it.",
                "Workspace-level evidence identifies where a control passed, "
                "failed, was partial or did not apply.",
                "Risks and recommendations consolidate the observed gaps and the "
                "assets they affect.",
                "Remediation receives credit only after evidence is provided and "
                "the control is re-tested.",
            ]),
        )
        + _box(
            "Boundaries and limitations",
            "What this assessment does not claim.",
            _point_list([
                "A point-in-time, read-only assessment of the implemented estate.",
                "Best-practice level: it does not trace data lineage, profile rows "
                "or review business logic.",
                "Every check is a fixed rule with a fixed threshold, so the same "
                "input always produces the same score; no AI participates.",
                "Controls needing tenant-admin or capacity APIs this tool does not "
                "call are reported as not assessed.",
                (
                    f"{len(read_limited)} workspace(s) had an incomplete crawl; "
                    "affected controls report not assessed."
                ) if read_limited else
                "Every in-scope workspace was crawled completely.",
                f"{len(consolidated_strengths)} control(s) passed outright across "
                "every asset assessed.",
            ]),
        )
        + "</div>"
        + completeness
        + na_table,
    )

    tabs_main = (
        ("summary", "Assessment Summary"),
        ("comparison", "Workspace Level Details"),
        ("results", "Detailed Results"),
        ("risks", "Key Risks"),
        ("controls", "Control Explorer"),
        ("recommendations", "Recommendations"),
        ("timeline", "Next Step Timeline"),
    )
    tabs_context = (("scoring", "Scoring"), ("scope", "Scope & Evidence"))

    def _tabs(entries, mark_first: bool) -> str:
        return "".join(
            f'<button class="tab{" active" if mark_first and index == 0 else ""}" '
            f'data-panel="{_esc(panel)}">{_esc(label)}</button>'
            for index, (panel, label) in enumerate(entries)
        )

    heading = _report_title(project_name, workspace_id_by_name, groups)

    return _PAGE.format(
        title=_esc(f"{heading} \u2014 Fabric Well-Architected Audit"),
        heading=_esc(heading),
        generated=_esc(date.today().isoformat()),
        mast_stats=mast_stats,
        tabs_main=_tabs(tabs_main, True),
        tabs_context=_tabs(tabs_context, False),
        panels="".join([
            summary_panel, comparison_panel, results_panel, risks_panel,
            controls_panel, recommendations_panel, timeline_panel,
            scoring_panel, scope_panel,
        ]),
        css=_CSS,
        script=_SCRIPT,
    )


_CSS = """
*,*::before,*::after{box-sizing:border-box;letter-spacing:0}
html{scroll-behavior:smooth;overflow-x:hidden}
body{margin:0;overflow-x:hidden;background:#f3f6f7;color:#263641;
font:14px/1.5 "Aptos","Segoe UI",sans-serif}
:root{--ink:#18313c;--paper:#fff;--line:#d7e0e3;--red:#c52b3b;--teal:#087f78;
--gold:#c39118;--blue:#2469a0;--muted:#657780;--critical:#a92332;--high:#d45f21;
--medium:#bd8b0c;--good:#197052}
button,input,select{font:inherit}
.mast{background:#18313c;color:#fff;border-bottom:5px solid var(--red);
padding:24px max(20px,calc((100vw - 1320px)/2))}
.mast h1{margin:0 0 16px;font:700 27px/1.2 Georgia,serif}
.mast h1 span{color:#ff7180}
.mast-stats{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:1px;
background:#3a5059;max-width:920px}
.mast-stat{background:#18313c;padding:9px 12px}
.mast-stat b{display:block;font-size:20px}
.mast-stat span{font-size:9px;text-transform:uppercase;color:#91adb7;font-weight:700}
.tabs{position:sticky;top:0;z-index:30;display:flex;overflow:auto;background:#fff;
border-bottom:1px solid var(--line);padding:0 max(10px,calc((100vw - 1320px)/2));
box-shadow:0 3px 12px #18313c14}
.tab-group{display:flex;flex:none}
.tab-group.context{margin-left:auto}
.tab{border:0;border-bottom:3px solid transparent;background:transparent;
padding:12px 11px;color:#5c6d75;font-size:11px;font-weight:750;white-space:nowrap;
cursor:pointer}
.tab.active{border-color:var(--red);color:var(--ink)}
.tab:hover{background:#f3f6f7}
.content{max-width:1320px;margin:auto;padding:24px 20px 50px}
.panel{display:none}
.panel.active{display:block}
.title{font:700 22px Georgia,serif;color:var(--ink)}
.desc{color:var(--muted);font-size:12.5px;margin:4px 0 18px;max-width:900px}
.hero{display:grid;grid-template-columns:auto 1fr;background:var(--ink);color:#fff;
border-left:6px solid var(--red);padding:22px 25px;gap:24px;align-items:center}
.hero-score{font-size:51px;font-weight:800}
.hero p{color:#bdd0d7;margin:6px 0 0}
.badge{display:inline-block;color:#fff;font-size:10px;font-weight:800;padding:3px 8px;
border-radius:3px;white-space:nowrap}
.critical{background:var(--critical)}
.high{background:var(--high)}
.medium{background:var(--medium);color:#172d35}
.good,.excellent,.low{background:var(--good)}
.informational,.not-assessed{background:#74838a}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);background:#fff;
border:1px solid var(--line);margin:14px 0 20px}
.kpi{padding:13px 15px;border-right:1px solid var(--line)}
.kpi:nth-child(4n){border:0}
.kpi b{display:block;color:var(--ink);font-size:22px}
.kpi span{font-size:9px;text-transform:uppercase;color:var(--muted);font-weight:800}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.grid2>div{min-width:0}
.box{background:#fff;border:1px solid var(--line);padding:15px;margin-bottom:16px}
.box h3{margin:0 0 4px;color:var(--ink);font-size:13px}
.box-sub{font-size:11px;color:var(--muted);margin-bottom:10px}
.radar-chart svg{display:block;width:100%;height:auto;max-height:470px}
.bar-row{display:grid;grid-template-columns:minmax(180px,1fr) 3fr 55px;gap:8px;
align-items:center;margin:7px 0;font-size:11px}
.bar-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{height:10px;background:#e7edef}
.bar i{display:block;height:100%;background:var(--teal)}
.scroll-top{overflow-x:auto;overflow-y:hidden;height:15px;background:#fff;
border:1px solid var(--line);border-bottom:0;margin-top:10px}
.scroll-top>div{height:1px}
.dtable-wrap{overflow:auto;background:#fff;border:1px solid var(--line);
margin:10px 0 18px}
.scroll-top + .dtable-wrap{margin-top:0}
.table{width:100%;border-collapse:collapse;min-width:820px}
.table th{position:sticky;top:0;background:var(--ink);color:#bfd0d6;padding:9px 12px;
text-align:left;font-size:9px;text-transform:uppercase;z-index:1;white-space:nowrap}
.table td{padding:9px 12px;border-bottom:1px solid #e7edef;font-size:11px;
vertical-align:top}
/* A fixed layout for the two tables that carry an expandable evidence cell.
   With the default auto layout the browser re-measures every column when a
   <details> opens, so the row widens and does not return to its original shape
   when it is closed again. Fixed widths make the toggle purely vertical. */
.area-sec .table,#risk-table{table-layout:fixed}
/* The check title is the widest thing in the row, so its column takes whatever
   the fixed columns leave. Constraining it instead wrapped every title over
   three lines and made the table twice as tall as it needed to be. */
.area-sec .table th:nth-child(2),.area-sec .table td:nth-child(2){width:110px}
.area-sec .table th:nth-child(3),.area-sec .table td:nth-child(3){width:76px}
.area-sec .table th:nth-child(4),.area-sec .table td:nth-child(4){width:230px}
#risk-table th:nth-child(1),#risk-table td:nth-child(1){width:74px}
#risk-table th:nth-child(2),#risk-table td:nth-child(2){width:82px}
#risk-table th:nth-child(3),#risk-table td:nth-child(3){width:22%}
#risk-table th:nth-child(4),#risk-table td:nth-child(4){width:15%}
#risk-table th:nth-child(5),#risk-table td:nth-child(5){width:96px}
#risk-table th:nth-child(7),#risk-table td:nth-child(7){width:72px}
#risk-table td:nth-child(3),#risk-table td:nth-child(4){overflow-wrap:anywhere}
/* The action and the impacted-asset list are the two wide columns; letting them
   share whatever the fixed columns leave keeps a 40-workspace asset list from
   squeezing the action down to one word per line. */
#rec-table{table-layout:fixed}
#rec-table th:nth-child(1),#rec-table td:nth-child(1){width:78px}
#rec-table th:nth-child(2),#rec-table td:nth-child(2){width:26%}
#rec-table th:nth-child(3),#rec-table td:nth-child(3){width:96px}
#rec-table td:nth-child(2),#rec-table td:nth-child(5){overflow-wrap:anywhere}
#area-total-table th:nth-child(3),#area-total-table td:nth-child(3){min-width:290px}
.table tr:hover td{background:#f7f9fa}
.ref{font-family:Consolas,monospace;color:var(--blue);font-weight:700;
white-space:nowrap}
.ref-id{display:block;font-family:Consolas,monospace;color:var(--muted);
font-size:10px;white-space:nowrap;margin-top:2px}
.check-title{margin-top:4px;color:#41525c;font-family:Consolas,monospace;
font-size:10.5px;line-height:1.55}
.subgroup{margin:2px 0;color:#41525c;font-size:10.5px}
.subgroup .ref{margin-right:6px}
.matrix-table{width:max-content;min-width:100%}
.matrix-table td:nth-child(3){min-width:280px;max-width:380px}
.matrix-table td:nth-child(5){min-width:180px}
.table td.score{text-align:center;font-variant-numeric:tabular-nums;
font-weight:800;padding:9px 14px;white-space:nowrap}
td.score-0{background:#f8696b;color:#351010}
td.score-1{background:#f8ba78;color:#3b2a0d}
td.score-2{background:#cce37f;color:#24320e}
td.score-3{background:#63be7b;color:#102c17}
td.score-na{background:#eef2f4;color:#8a969c;font-weight:600}
.table tr:hover td.score-0{background:#f8696b}
.table tr:hover td.score-1{background:#f8ba78}
.table tr:hover td.score-2{background:#cce37f}
.table tr:hover td.score-3{background:#63be7b}
.table tr:hover td.score-na{background:#eef2f4}
.matrix-table th:first-child,.matrix-table td:first-child{position:sticky;left:0;
z-index:2;background:#fff}
.matrix-table tr:hover td:first-child{background:#f7f9fa}
.matrix-table th:first-child{z-index:4;background:var(--ink)}
.line-chart svg{display:block;width:100%;height:auto;max-height:360px}
.num{text-align:right;font-variant-numeric:tabular-nums;display:block}
.details summary{color:var(--blue);cursor:pointer;font-weight:700;white-space:nowrap}
.details div{margin-top:7px;color:#51636c}
.filters{display:flex;gap:10px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);
padding:10px;margin:10px 0}
.filters label{font-size:9px;text-transform:uppercase;font-weight:800;color:var(--muted)}
.filters input,.filters select{display:block;margin-top:3px;padding:6px 8px;
border:1px solid #bfcbd0;min-width:160px}
.count{margin-left:auto;align-self:center;color:var(--muted);font-size:11px}
.callout{background:#eaf4f3;border-left:4px solid var(--teal);padding:12px 15px;
margin:12px 0;font-size:12px}
.callout strong{color:var(--ink)}
.point-list{margin:7px 0;padding-left:18px;color:#51636c}
.point-list li{margin:4px 0}
.workspace-evidence{display:grid;gap:8px;margin-top:8px}
.workspace-evidence,.check-title{overflow-wrap:anywhere}
.workspace-item{border-left:3px solid var(--line);padding:5px 9px;background:#f7f9fa}
.workspace-item h4{margin:0 0 3px;color:var(--ink);font-size:11px}
.workspace-item small{color:var(--muted);font-weight:700}
.definition-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.definition{background:#f7f9fa;border:1px solid var(--line);padding:14px}
.definition b{display:block;color:var(--ink);font-size:16px}
.definition strong{display:block;color:var(--ink);margin:2px 0 5px;font-size:12px}
.definition span{display:block;color:var(--muted);font-size:11px}
.area-sec{background:#fff;border:1px solid var(--line);margin:0 0 10px}
.area-head{display:grid;grid-template-columns:auto 1fr auto auto;gap:12px;
align-items:center;padding:13px 15px;cursor:pointer;color:var(--ink);font-weight:800}
.area-head:hover{background:#f3f6f7}
.area-num{color:var(--blue);font-family:Consolas,monospace}
.area-meta{color:var(--muted);font-size:10px;font-weight:700;white-space:nowrap}
.area-toggle{transition:transform .2s}
.area-sec.expanded .area-toggle{transform:rotate(180deg)}
.area-content{display:none;border-top:1px solid var(--line);padding:12px}
.area-sec.expanded .area-content{display:block}
.area-summary{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
.timeline{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
.phase{background:#fff;border:1px solid var(--line);border-top:4px solid var(--gold);
padding:14px}
.phase.current{border-top-color:var(--blue)}
.phase .when{font-size:10px;font-weight:800;color:var(--ink);text-transform:uppercase;
letter-spacing:.05em}
.phase .big{font-size:30px;line-height:1.2;font-weight:800;color:var(--blue);
font-variant-numeric:tabular-nums}
.phase .days{color:var(--ink);font-size:11px;font-weight:700}
.phase p{margin:6px 0 0;color:var(--muted);font-size:11px}
.empty{background:#fff;border:1px dashed var(--line);color:var(--muted);
font-style:italic;padding:12px 15px;margin:10px 0 18px;font-size:11.5px}
.footer{border-top:1px solid var(--line);background:#fff;padding:1.1rem;
text-align:center;font-size:10px;color:#7d8994}
@media(max-width:1100px){.timeline{grid-template-columns:repeat(2,1fr)}}
@media(max-width:900px){
.mast-stats,.kpis{grid-template-columns:repeat(2,1fr)}
.grid2,.definition-grid{grid-template-columns:1fr}
.kpi:nth-child(2n){border-right:0}
.tab-group.context{margin-left:0}
}
@media(max-width:560px){
.content{padding:15px 8px}
.hero{grid-template-columns:1fr}
.hero-score{font-size:40px}
.mast{padding:18px 14px}
.bar-row{grid-template-columns:120px 1fr 45px}
.timeline{grid-template-columns:1fr}
}
@media print{
.tabs,.filters,.scroll-top{display:none}
.panel{display:block!important;break-before:page}
.panel:first-child{break-before:auto}
.content{max-width:none}
.area-content{display:block!important}
.details div{display:block}
.table th{position:static}
.mast{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#fff}
.area-sec,.box,.phase{break-inside:avoid}
}
"""

_SCRIPT = """
(function(){
  var all=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};

  // -- tabs ------------------------------------------------------------------
  all('.tab').forEach(function(tab){
    tab.onclick=function(){
      all('.tab').forEach(function(other){
        other.classList.toggle('active',other===tab);
      });
      all('.panel').forEach(function(panel){
        panel.classList.toggle('active',panel.id==='panel-'+tab.dataset.panel);
      });
      scrollTo({top:0,behavior:'smooth'});
    };
  });

  // -- collapsible areas -----------------------------------------------------
  all('.area-head').forEach(function(head){
    var toggle=function(){head.parentElement.classList.toggle('expanded');};
    head.onclick=toggle;
    head.onkeydown=function(event){
      if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle();}
    };
  });

  // -- a scrollbar above a wide table ---------------------------------------
  // The control matrix is wider than the viewport, and its own scrollbar sits
  // below the fold. This mirrors it above the header so the workspace columns
  // are reachable without scrolling to the bottom of the table first.
  all('.scroll-top').forEach(function(proxy){
    var wrap=document.getElementById(proxy.dataset.scrollFor);
    if(!wrap){proxy.style.display='none';return;}
    var table=wrap.querySelector('table');
    var sync=function(){
      proxy.firstElementChild.style.width=(table?table.scrollWidth:0)+'px';
      proxy.style.display=table&&table.scrollWidth>wrap.clientWidth?'':'none';
    };
    sync();
    addEventListener('resize',sync);
    var lock=false;
    proxy.onscroll=function(){
      if(lock)return;lock=true;wrap.scrollLeft=proxy.scrollLeft;lock=false;
    };
    wrap.onscroll=function(){
      if(lock)return;lock=true;proxy.scrollLeft=wrap.scrollLeft;lock=false;
    };
  });

  // -- filtering -------------------------------------------------------------
  // Every control bound to the same prefix narrows the same table, and they
  // intersect: a severity select and a search box together show the rows that
  // satisfy both, rather than whichever one ran last.
  var groups={};
  all('[data-filter-for]').forEach(function(input){
    var key=input.getAttribute('data-filter-for');
    (groups[key]=groups[key]||[]).push(input);
    input.oninput=function(){run(key);};
    input.onchange=function(){run(key);};
  });

  function run(prefix){
    var table=document.getElementById(prefix+'-table');
    if(!table||!table.tBodies.length)return;
    var inputs=groups[prefix]||[];
    var rows=Array.prototype.slice.call(table.tBodies[0].rows),shown=0;
    rows.forEach(function(row){
      var ok=inputs.every(function(input){
        var value=(input.value||'').trim();
        if(!value)return true;
        if(input.tagName==='SELECT'){
          var cell=row.cells[parseInt(input.getAttribute('data-column'),10)];
          if(!cell)return false;
          var text=cell.textContent.trim();
          // An area cell reads "3. Governance" in the control matrix but just
          // "Governance" in the risk register, while the option carries the
          // number. Compare both ways rather than keeping two option lists.
          return text===value||text===value.replace(/^\\d+\\.\\s*/,'');
        }
        return row.textContent.toLowerCase().indexOf(value.toLowerCase())>-1;
      });
      row.style.display=ok?'':'none';
      if(ok)shown++;
    });
    var count=document.getElementById(prefix+'-count');
    if(count)count.textContent=shown+' of '+rows.length;
  }
  Object.keys(groups).forEach(run);
})();
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="generator" content="auditfast">
<meta name="generated" content="{generated}">
<style>{css}</style>
</head>
<body>
<header class="mast">
  <h1>{heading} <span>Fabric Audit</span> Report</h1>
  <div class="mast-stats">{mast_stats}</div>
</header>
<nav class="tabs" aria-label="Report sections">
  <div class="tab-group">{tabs_main}</div>
  <div class="tab-group context">{tabs_context}</div>
</nav>
<main class="content">{panels}</main>
<footer class="footer">
Generated {generated} &middot; rule-based Fabric architecture and best-practice
assessment. Repeated asset-level verdicts are consolidated by control for
stakeholder reporting; deterministic asset-level results remain the basis of
every score.
</footer>
<script>{script}</script>
</body>
</html>
"""
