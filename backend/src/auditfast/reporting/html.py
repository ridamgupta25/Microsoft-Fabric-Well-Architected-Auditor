"""Self-contained HTML readout for Fabric Well-Architected audits.

The same numbers as the Excel and Markdown renderings, presented as a tabbed
consultant readout: an executive summary with an estate profile chart, a
workspace comparison, per-area detail, a filterable risk register, a control
explorer, and the scope and scoring notes that let a reader check the method.

Two deliberate constraints:

* **No external assets.** CSS, JS and every chart are inlined -- charts are
  generated as SVG here rather than drawn by a library -- so the report opens
  offline, survives being emailed as a single attachment, and cannot leak a
  workspace name to a third-party host by requesting a font or a script.
* **Consolidated rows only.** Like the Excel, the tables are one row per
  *control*, not one per asset-level verdict. A large estate produces tens of
  thousands of verdicts -- the Markdown rendering of one real tenant is 7.5 MB --
  and a browser handles that badly. Every row still names the assets behind it.

Tenant data is escaped on the way in (:func:`_esc`); it reaches the page as
text, never as markup.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape

from ..core.engine import READ_INCOMPLETE_CHECK_ID
from ..core.enums import Pillar, Severity, Status
from ..core.scoring import percentage, rating
from .structure import (
    RISK_PROFILE,
    assessment_weight,
    consolidate,
    executive_narrative,
    findings,
    pillar_controls,
    severity_counts,
    strengths,
    workspace_control_score,
    workspace_ids,
)

#: The bottom of the "Good" band in :func:`auditfast.core.scoring.rating`. Drawn
#: on every chart, so a bar is read against the mark the estate is aiming at.
GOOD_THRESHOLD = 76.0

#: Score bands -> CSS class, mirroring ``rating()`` so a colour in the report can
#: never disagree with the label beside it.
_SCORE_BANDS = ((91, "excellent"), (76, "good"), (61, "medium"), (41, "high"))

_SEVERITY_CLASS = {
    Severity.CRITICAL: "critical",
    Severity.HIGH: "high",
    Severity.MEDIUM: "medium",
    Severity.LOW: "low",
    Severity.INFO: "gray",
}

_STATUS_CLASS = {
    Status.PASS: "good",
    Status.PARTIAL: "medium",
    Status.FAIL: "critical",
    Status.NA: "gray",
    Status.INFO: "blue",
}

_ORDERED_SEVERITIES = (
    Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO,
)

#: Held as a constant because a backslash escape inside an f-string expression is
#: a syntax error before Python 3.12, and this package supports 3.10.
_EN_DASH = "\u2013"


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


def _score_class(value) -> str:
    """The rating band for a 0-100 score, as a CSS class."""
    if value is None:
        return "not-scored"
    for threshold, name in _SCORE_BANDS:
        if value >= threshold:
            return name
    return "critical"


def _rating_badge(value) -> str:
    label, _ = rating(value)
    return f'<span class="rate {_score_class(value)}">{_esc(label)}</span>'


def _badge(text, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{_esc(text)}</span>'


def _severity_badge(severity: Severity) -> str:
    return _badge(severity.value, _SEVERITY_CLASS.get(severity, "gray"))


def _status_badge(status: Status) -> str:
    return _badge(status.value, _STATUS_CLASS.get(status, "gray"))


def _chip(value) -> str:
    """A 0-3 band as a coloured chip; anything unscored reads as a dash."""
    if value is None or value == "":
        return '<span class="chip sna">&ndash;</span>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<span class="chip s{max(0, min(3, round(value)))}">{value:g}</span>'
    return f'<span class="chip sna">{_esc(value)}</span>'


def _cell(value) -> str:
    if value is None or value == "":
        return '<span class="muted">&mdash;</span>'
    if isinstance(value, float):
        return _esc(f"{value:g}")
    return _esc(value)


def _table(
    headers,
    rows,
    *,
    table_id: str = "",
    empty: str = "Nothing to report here.",
) -> str:
    """One dense data table. ``rows`` yields sequences of rendered cells."""
    body = [
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    ]
    if not body:
        return f'<p class="empty">{_esc(empty)}</p>'
    head = "".join(
        f'<th><button class="sort-button" type="button">{_esc(header)}'
        f'<span class="sort-indicator"></span></button></th>'
        for header in headers
    )
    attrs = f' id="{_esc(table_id)}"' if table_id else ""
    return (
        f'<div class="dtable-wrap"><table class="dtable"{attrs}>'
        f"<thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def _bar_chart(rows, *, threshold: float | None = GOOD_THRESHOLD) -> str:
    """A horizontal bar chart as inline SVG.

    Drawn here rather than by a charting library so the page keeps its promise
    of fetching nothing. ``rows`` is ``(label, percentage)``; a ``None``
    percentage is an unscored row, drawn as a grey stub so it cannot be misread
    as a zero.
    """
    rows = [(str(label), value) for label, value in rows]
    if not rows:
        return '<p class="empty">Nothing to chart.</p>'
    row_height, top, left, width = 26, 30, 250, 720
    plot = width - left - 62
    height = top + row_height * len(rows) + 18
    marks = []

    for value in (0, 25, 50, 75, 100):
        x = left + plot * value / 100
        marks.append(
            f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 18}" '
            'class="grid"/>'
            f'<text x="{x:.1f}" y="{top - 13}" class="axis" text-anchor="middle">'
            f"{value}%</text>"
        )

    for index, (label, value) in enumerate(rows):
        y = top + index * row_height
        shown = label if len(label) <= 34 else label[:33] + "\u2026"
        marks.append(
            f'<text x="{left - 10}" y="{y + 12}" class="cat" text-anchor="end">'
            f"{_esc(shown)}<title>{_esc(label)}</title></text>"
        )
        if value is None:
            marks.append(
                f'<rect x="{left}" y="{y + 4}" width="24" height="14" class="bar-none"/>'
                f'<text x="{left + 32}" y="{y + 12}" class="val">N/A</text>'
            )
            continue
        bar = plot * max(0.0, min(100.0, value)) / 100
        marks.append(
            f'<rect x="{left}" y="{y + 4}" width="{bar:.1f}" height="14" '
            f'class="bar {_score_class(value)}"/>'
            f'<text x="{left + bar + 8:.1f}" y="{y + 12}" class="val">{value:.1f}%</text>'
        )

    if threshold is not None:
        x = left + plot * threshold / 100
        marks.append(
            f'<line x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 18}" '
            'class="threshold"/>'
            f'<text x="{x:.1f}" y="{height - 4}" class="axis" text-anchor="middle">'
            f"Good {threshold:.0f}%</text>"
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Score by category">{"".join(marks)}</svg>'
    )


def _kpi(value, label, note: str = "") -> str:
    note_html = f'<div class="kpi-note">{_esc(note)}</div>' if note else ""
    return (
        f'<div class="kpi"><div class="kpi-value">{_esc(value)}</div>'
        f'<div class="kpi-label">{_esc(label)}</div>{note_html}</div>'
    )


def _panel(
    panel_id: str, title: str, description: str, body: str, *, active: bool = False
) -> str:
    return (
        f'<section class="panel{" active" if active else ""}" '
        f'id="panel-{_esc(panel_id)}">'
        f'<div class="page-title">{_esc(title)}</div>'
        f'<p class="page-desc">{_esc(description)}</p>{body}</section>'
    )


def _filter_bar(prefix: str, selects, *, placeholder: str) -> str:
    """A labelled filter row wired to the table with id ``<prefix>-table``."""
    fields = "".join(
        f"<label>{_esc(label)}"
        f'<select data-filter-for="{_esc(prefix)}" data-column="{column}">'
        f'<option value="">All {_esc(label.lower())}</option>'
        + "".join(f"<option>{_esc(option)}</option>" for option in options)
        + "</select></label>"
        for label, column, options in selects
    )
    return (
        f'<div class="filter-bar">{fields}'
        f'<label>Search<input type="search" data-filter-for="{_esc(prefix)}" '
        f'placeholder="{_esc(placeholder)}"></label>'
        f'<span class="filter-count" id="{_esc(prefix)}-count"></span></div>'
    )


def build_html(
    project_name: str,
    agg: dict,
    results: list,
    errors: list | None = None,
) -> str:
    """Render the audit as one self-contained HTML readout."""
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
    area_names = [pillar.value for pillar in Pillar.scored()]

    # -- Assessment summary ----------------------------------------------------
    header_stats = "".join(
        f'<div class="hstat"><b>{_esc(value)}</b><span>{_esc(label)}</span></div>'
        for value, label in (
            (_pct(agg["overall"]), "Overall score"),
            (len(controls), "Controls"),
            (len(consolidated_findings), "Findings"),
            (critical_high, "Critical + High"),
            (len(workspace_id_by_name), "Workspaces"),
        )
    )

    hero = (
        f'<div><div class="hero-score">{_pct(agg["overall"])}</div>'
        f'{_rating_badge(agg["overall"])}</div>'
        f"<div><div>{_esc(executive_narrative(agg, controls))}</div>"
        '<div class="hero-meta">Deterministic, rule-based assessment &mdash; no AI '
        "in scoring. Repeated asset-level verdicts are consolidated by control.</div>"
        "</div>"
    )

    kpis = (
        _kpi(control_counts[Status.PASS], "Passing controls",
             f"of {len(controls)} assessed")
        + _kpi(control_counts[Status.FAIL], "Failing controls",
               f"{control_counts[Status.PARTIAL]} partial")
        + _kpi(control_counts[Status.NA], "Not assessed", "data could not be read")
        + _kpi(len(results), "Asset-level results", "before consolidation")
    )

    area_chart = _bar_chart(
        (pillar.value, agg["by_pillar"][pillar]["pct"]) for pillar in Pillar.scored()
    )

    priorities = "".join(
        f'<div class="priority"><div class="priority-rank">Priority {index} '
        f"&middot; {_esc(control.severity.value)}</div>"
        f"<h3>{_esc(control.title)}</h3>"
        f'<p><strong>{_esc(control.ref)}</strong> &middot; '
        f"{_esc(control.pillar.value)} &mdash; {_esc(control.impacted_evidence)}</p>"
        "</div>"
        for index, control in enumerate(consolidated_findings[:4], start=1)
    ) or '<p class="empty">No findings were raised.</p>'

    area_rows = []
    for pillar in Pillar.scored():
        pillar_agg = agg["by_pillar"][pillar]
        rows = pillar_controls(controls, pillar)
        area_rows.append((
            pillar_number[pillar],
            _esc(pillar.value),
            f"{assessment_weight(results, pillar):.1%}",
            _pct(pillar_agg["pct"]),
            _rating_badge(pillar_agg["pct"]),
            len(rows),
            len(findings(rows)),
        ))

    summary_panel = _panel(
        "exec",
        "Assessment Summary",
        "Executive posture, the areas carrying the most exposure, and the controls "
        "with the greatest operational consequence.",
        f'<div class="hero">{hero}</div>'
        f'<div class="kpi-band">{kpis}</div>'
        '<div class="two-col">'
        '<div class="chart-panel"><div class="chart-title">Estate maturity profile'
        "</div>"
        f'<div class="chart-sub">Every scored pillar against the '
        f"{GOOD_THRESHOLD:.0f}% Good threshold.</div>{area_chart}</div>"
        '<div><div class="chart-title">Top priorities</div>'
        '<div class="chart-sub">The highest-severity consolidated findings.</div>'
        f'<div class="priority-list">{priorities}</div></div></div>'
        '<div class="slabel">All audit areas</div>'
        + _table(
            ("#", "Audit area", "Weight", "Score", "Rating", "Controls", "Findings"),
            area_rows,
            table_id="area-table",
        ),
        active=True,
    )

    # -- Workspace comparison --------------------------------------------------
    workspace_rows = sorted(
        agg.get("by_workspace", {}).items(),
        key=lambda item: (item[1]["pct"] is None, item[1]["pct"] or 0, item[0]),
    )
    cards = "".join(
        f'<div class="server-panel"><h3>{_esc(name)}'
        f'{_badge(workspace_id_by_name.get(name, _EN_DASH), "blue")}</h3>'
        f'<div class="server-score">{_pct(data["pct"])}{_rating_badge(data["pct"])}</div>'
        f'<div class="server-meta">{_esc(data.get("role") or "Layer role not set")}'
        f' &middot; {data["count"]} scored check(s)</div></div>'
        for name, data in workspace_rows
    ) or '<p class="empty">No workspace was scored.</p>'

    layers = agg.get("layers") or []
    matrix = agg.get("matrix", {})
    matrix_table = ""
    if layers:
        matrix_table = (
            '<div class="slabel">Pillar &times; layer</div>'
            + _table(
                ("Area", *layers),
                (
                    (
                        _esc(pillar.value),
                        *(
                            _pct(matrix.get(pillar.value, {}).get(layer))
                            for layer in layers
                        ),
                    )
                    for pillar in Pillar.scored()
                ),
                table_id="matrix-table",
            )
        )

    inventory_rows = (
        (
            _esc(workspace_id_by_name[workspace]),
            _esc(workspace),
            _cell(next(
                (r.workspace_role for r in results if r.workspace == workspace), ""
            )),
            len({r.obj for r in results if r.workspace == workspace and r.obj}),
            sum(r.workspace == workspace for r in results),
            len({(r.check_id, r.ref) for r in results if r.workspace == workspace}),
        )
        for workspace in sorted({r.workspace for r in results if r.workspace})
    )

    workspaces_panel = _panel(
        "workspaces",
        "Workspace Comparison",
        "Side-by-side posture for every audited workspace, worst first, with the "
        "layer each one plays in the architecture.",
        f'<div class="server-grid">{cards}</div>'
        '<div class="chart-panel">'
        '<div class="chart-title">Workspace score versus Good threshold</div>'
        f'<div class="chart-sub">The marker sits at {GOOD_THRESHOLD:.0f}%, the start '
        "of the Good band.</div>"
        + _bar_chart((name, data["pct"]) for name, data in workspace_rows)
        + "</div>"
        + matrix_table
        + '<div class="slabel">Inventory</div>'
        + _table(
            ("ID", "Workspace", "Layer role", "Objects", "Asset-level results",
             "Controls"),
            inventory_rows,
            table_id="inventory-table",
        ),
    )

    # -- Detailed results by area ----------------------------------------------
    areas = []
    for pillar in Pillar.scored():
        rows = pillar_controls(controls, pillar)
        pillar_results = [result for control in rows for result in control.results]
        score = percentage(pillar_results)
        pillar_findings = findings(rows)
        pillar_strengths = strengths(rows)

        category_map: dict[str, list] = defaultdict(list)
        for control in rows:
            category_map[control.category].append(control)
        category_rows = []
        for category, category_controls in category_map.items():
            category_score = percentage(
                [r for c in category_controls for r in c.results]
            )
            category_rows.append((
                _esc(category),
                _pct(category_score),
                _rating_badge(category_score),
                len(category_controls),
                len(findings(category_controls)),
            ))

        areas.append(
            f'<div class="area-sec{" expanded" if pillar_findings else ""}">'
            '<div class="area-head" role="button" tabindex="0">'
            f'<span class="area-num">Area {pillar_number[pillar]}</span>'
            f"<span>{_esc(pillar.value)}</span>{_rating_badge(score)}"
            f'<span class="area-meta">{_pct(score)} &middot; {len(rows)} control(s) '
            f"&middot; {len(pillar_findings)} finding(s)</span>"
            '<span class="area-toggle">&#9660;</span></div>'
            '<div class="area-content">'
            + _table(
                ("Category", "Score", "Rating", "Controls", "Findings"),
                category_rows,
                empty="No control in this area.",
            )
            + f'<div class="slabel">Findings ({len(pillar_findings)})</div>'
            + _table(
                ("Ref", "Severity", "Score", "Finding", "Impacted assets",
                 "Not assessed / reason", "Recommendation"),
                (
                    (
                        _esc(control.ref),
                        _severity_badge(control.severity),
                        _esc(control.score_summary),
                        _esc(control.finding),
                        _cell(control.impacted_assets),
                        _cell(control.not_assessed),
                        _cell(control.recommendation),
                    )
                    for control in pillar_findings
                ),
                empty="No findings in this area.",
            )
            + f'<div class="slabel">Strengths ({len(pillar_strengths)})</div>'
            + _table(
                ("Ref", "Strength", "Evidence", "Assets assessed"),
                (
                    (
                        _esc(control.ref),
                        _esc(control.title),
                        _cell(control.impacted_evidence),
                        control.assets_assessed,
                    )
                    for control in pillar_strengths
                ),
                empty="No control passed outright in this area.",
            )
            + "</div></div>"
        )

    results_panel = _panel(
        "results",
        "Detailed Results by Area",
        "Consolidated controls grouped by pillar. Areas carrying findings open "
        "first; select a header to expand or collapse one.",
        '<div class="area-actions">'
        '<button class="linkish" data-areas="expand">Expand all</button>'
        '<button class="linkish" data-areas="collapse">Collapse all</button></div>'
        + "".join(areas),
    )

    # -- Key risks -------------------------------------------------------------
    risk_rows = (
        (
            f"R-{index:03d}",
            _esc(control.ref),
            _esc(control.pillar.value),
            _severity_badge(control.severity),
            _esc(control.risk_profile[2]),
            _esc(control.title),
            _esc(control.impacted_evidence),
            _cell(control.impacted_assets),
            _cell(control.recommendation),
            _esc(RISK_PROFILE[control.severity][3]),
        )
        for index, control in enumerate(consolidated_findings, start=1)
    )
    risks_panel = _panel(
        "risks",
        "Key Risks and Observations",
        "The complete consolidated finding register, filterable by area, severity "
        "and text. Ordered by severity, then by score.",
        _filter_bar(
            "risk",
            (("Area", 2, area_names),
             ("Severity", 3, [s.value for s in reported_severities])),
            placeholder="Ref, title, evidence\u2026",
        )
        + _table(
            ("Risk ID", "Ref", "Area", "Severity", "Risk score", "Finding",
             "Evidence", "Impacted assets", "Recommendation", "SLA"),
            risk_rows,
            table_id="risk-table",
            empty="No findings \u2014 every assessed control passed.",
        )
        + '<div class="slabel">Severity profile</div>'
        + _table(
            ("Severity", "Findings", "% of findings", "Remediation SLA"),
            (
                (
                    _severity_badge(severity),
                    severity_total[severity],
                    (
                        f"{severity_total[severity] / len(consolidated_findings):.1%}"
                        if consolidated_findings else "0.0%"
                    ),
                    _esc(RISK_PROFILE[severity][3]),
                )
                for severity in reported_severities
            ),
        ),
    )

    # -- Control explorer ------------------------------------------------------
    control_rows = (
        (
            _esc(control.check_id),
            _esc(control.ref),
            _esc(control.pillar.value),
            _esc(control.category),
            _esc(control.title),
            _severity_badge(control.severity),
            _status_badge(control.status),
            _chip(control.score_average if control.score_average != "N/A" else None),
            *(
                _chip(workspace_control_score(control, workspace))
                for workspace in workspace_id_by_name
            ),
        )
        for control in controls
    )
    controls_panel = _panel(
        "controls",
        "Control Explorer",
        "Every consolidated control with its per-workspace band (0\u20133). A dash "
        "means the control did not apply there, or its data could not be read.",
        _filter_bar(
            "control",
            (("Area", 2, area_names),
             ("Severity", 5, [s.value for s in _ORDERED_SEVERITIES]),
             ("Status", 6, [s.value for s in Status])),
            placeholder="Check ID, ref, control\u2026",
        )
        + _table(
            ("Check ID", "Ref", "Area", "Category", "Control", "Severity", "Status",
             "Overall", *workspace_id_by_name.values()),
            control_rows,
            table_id="control-table",
        ),
    )

    # -- Strengths -------------------------------------------------------------
    strengths_panel = _panel(
        "strengths",
        f"Strengths ({len(consolidated_strengths)})",
        "Controls that passed outright across every asset assessed. These are the "
        "practices to preserve as the estate changes.",
        _filter_bar(
            "strength",
            (("Area", 1, area_names),),
            placeholder="Ref, strength, evidence\u2026",
        )
        + _table(
            ("Ref", "Area", "Strength", "Evidence", "Assets assessed"),
            (
                (
                    _esc(control.ref),
                    _esc(control.pillar.value),
                    _esc(control.title),
                    _cell(control.impacted_evidence),
                    control.assets_assessed,
                )
                for control in consolidated_strengths
            ),
            table_id="strength-table",
            empty="No control passed outright.",
        ),
    )

    # -- Scope and evidence ----------------------------------------------------
    completeness = ""
    if errors:
        completeness = (
            '<div class="warn"><h3>Reads that did not complete</h3>'
            "<p>Controls depending on this data are reported as not assessed rather "
            "than failed.</p></div>"
            + _table(
                ("Workspace", "Read limitation"),
                (
                    (_cell(getattr(error, "workspace", "")),
                     _cell(getattr(error, "evidence", "")))
                    for error in errors
                ),
                table_id="completeness-table",
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
            f'<div class="slabel">Not assessed \u2014 N/A ({len(na_results)})</div>'
            '<p class="page-desc">These checks could not be evaluated, usually '
            "because the data they read could not be fetched. They are not failures "
            "and do not affect the score.</p>"
            + _table(
                ("Reason", "Checks", "Areas affected"),
                (
                    (_esc(reason), count, _esc(", ".join(sorted(pillars))))
                    for reason, (count, pillars) in sorted(
                        groups.items(), key=lambda item: -item[1][0]
                    )
                ),
                table_id="na-table",
            )
        )

    read_limits = _table(
        ("Workspace", "Read limitation"),
        (
            (
                _esc(workspace),
                _cell("; ".join(
                    r.evidence for r in results
                    if r.workspace == workspace
                    and r.check_id == READ_INCOMPLETE_CHECK_ID
                )),
            )
            for workspace in sorted({r.workspace for r in results if r.workspace})
            if any(
                r.workspace == workspace and r.check_id == READ_INCOMPLETE_CHECK_ID
                for r in results
            )
        ),
        table_id="readlimit-table",
        empty="Every workspace was crawled completely.",
    )

    scope_panel = _panel(
        "scope",
        "Assessment Scope & Evidence",
        "What was read, what could not be, and how much of the estate each number "
        "rests on.",
        _table(
            ("Measure", "Count"),
            (
                ("Workspaces audited", len(workspace_id_by_name)),
                ("Consolidated controls", len(controls)),
                ("Asset-level results", len(results)),
                ("Passing controls", control_counts[Status.PASS]),
                ("Partial controls", control_counts[Status.PARTIAL]),
                ("Failing controls", control_counts[Status.FAIL]),
                ("Not assessed", control_counts[Status.NA]),
                ("Crawl warnings", len(errors or [])),
            ),
            table_id="coverage-table",
        )
        + completeness
        + '<div class="slabel">Crawl completeness</div>'
        + read_limits
        + na_table,
    )

    # -- Scoring ---------------------------------------------------------------
    scoring_panel = _panel(
        "scoring",
        "Assessment Scoring and Criteria",
        "How a verdict becomes a number, and what each band means.",
        '<div class="warn info"><h3>Determinism</h3><p>Every check is a fixed rule '
        "with a fixed threshold, so the same input always produces the same score: "
        "no AI, no sampling and no clock-dependent logic in the scoring path. A "
        "check that cannot read its data reports <b>not assessed</b> rather than a "
        "failure, so a permission gap never masquerades as a misconfiguration.</p>"
        "</div>"
        '<div class="slabel">Verdict bands</div>'
        + _table(
            ("Band", "Meaning", "Contributes"),
            (
                (_chip(3), "Practice fully implemented", "3 of 3"),
                (_chip(2), "Implemented with a material gap", "2 of 3"),
                (_chip(1), "Partially implemented", "1 of 3"),
                (_chip(0), "Not implemented", "0 of 3"),
                (_chip(None), "Not applicable, or data not readable", "excluded"),
            ),
        )
        + '<div class="slabel">Risk ratings</div>'
        + _table(
            ("Rating", "Score range"),
            (
                (_rating_badge(95), "91% and above"),
                (_rating_badge(80), "76% \u2013 90%"),
                (_rating_badge(65), "61% \u2013 75%"),
                (_rating_badge(45), "41% \u2013 60%"),
                (_rating_badge(10), "40% and below"),
            ),
        )
        + '<div class="slabel">Severity and remediation windows</div>'
        + _table(
            ("Severity", "Likelihood", "Impact", "Risk score", "Remediation SLA"),
            (
                (
                    _severity_badge(severity),
                    RISK_PROFILE[severity][0],
                    RISK_PROFILE[severity][1],
                    RISK_PROFILE[severity][2],
                    _esc(RISK_PROFILE[severity][3]),
                )
                for severity in _ORDERED_SEVERITIES
            ),
        ),
    )

    def _tabs(entries, mark_first: bool) -> str:
        return "".join(
            f'<button class="tab{" active" if mark_first and index == 0 else ""}" '
            f'data-panel="{_esc(panel)}">{_esc(label)}</button>'
            for index, (panel, label) in enumerate(entries)
        )

    return _PAGE.format(
        title=_esc(f"Fabric Well-Architected Audit \u2014 {project_name}"),
        project=_esc(project_name),
        generated=_esc(date.today().isoformat()),
        header_stats=header_stats,
        tabs_main=_tabs(
            (
                ("exec", "Assessment Summary"),
                ("workspaces", "Workspace Comparison"),
                ("results", "Detailed Results"),
                ("risks", "Key Risks"),
                ("controls", "Control Explorer"),
                ("strengths", "Strengths"),
            ),
            True,
        ),
        tabs_context=_tabs(
            (("scope", "Scope & Evidence"), ("scoring", "Scoring")), False
        ),
        panels="".join([
            summary_panel, workspaces_panel, results_panel, risks_panel,
            controls_panel, strengths_panel, scope_panel, scoring_panel,
        ]),
        css=_CSS,
        script=_SCRIPT,
    )


_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--red:#E31837;--dark:#17283A;--ink:#283746;--muted:#687583;--light:#F5F7F9;
--line:#DCE2E8;--blue:#1967A3;--blue-soft:#EAF3FA;--green:#147A52;--green-soft:#E8F5EF;
--amber-soft:#FFF3DF;--critical:#B4232F;--high:#D96318;--medium:#C89A00;--good:#168256;
--excellent:#2377B9;--font:'Segoe UI Variable','Segoe UI',Tahoma,sans-serif}
html{scroll-behavior:smooth}
body{font-family:var(--font);background:var(--light);color:var(--ink);font-size:14px;
line-height:1.55}
button,input,select{font:inherit}
.header{background:var(--dark);color:#fff;
padding:1.55rem max(1.25rem,calc((100vw - 1240px)/2));border-bottom:4px solid var(--red)}
.header-eye{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
color:#91ABC3;margin-bottom:.25rem}
.header h1{font-size:24px;font-weight:650}
.header h1 span{color:#FF526A}
.header-sub{font-size:12.5px;color:#B6C5D3;max-width:850px;margin-top:.3rem}
.header-stats{display:grid;grid-template-columns:repeat(5,minmax(100px,1fr));gap:1rem;
margin-top:1.1rem;max-width:920px}
.hstat{border-left:1px solid #486078;padding-left:.8rem}
.hstat b{display:block;font-size:20px;line-height:1.15}
.hstat span{font-size:9.5px;color:#91ABC3;text-transform:uppercase;letter-spacing:.05em}
.tabs{display:flex;background:#fff;border-bottom:1px solid var(--line);
padding:0 max(1rem,calc((100vw - 1280px)/2));position:sticky;top:0;z-index:20;
box-shadow:0 2px 8px rgba(22,40,58,.06);flex-wrap:wrap}
.tab-group{display:flex;flex-wrap:wrap}
.tab-group.context{margin-left:auto}
.tab{border:0;background:transparent;padding:.8rem .62rem;font-size:11.5px;font-weight:600;
color:#66727F;border-bottom:3px solid transparent;cursor:pointer;white-space:nowrap}
.tab:hover,.tab:focus-visible{color:var(--dark);background:#F7F9FB;outline:none}
.tab.active{color:var(--dark);border-bottom-color:var(--red)}
.content{max-width:1240px;margin:0 auto;padding:1.7rem 1.5rem 2.5rem}
.panel{display:none}
.panel.active{display:block;animation:fade .18s ease}
@keyframes fade{from{opacity:.25;transform:translateY(3px)}to{opacity:1;transform:none}}
.page-title{font-size:19px;font-weight:650;color:var(--dark)}
.page-desc{font-size:12.5px;color:var(--muted);margin:.25rem 0 1.2rem;max-width:900px}
.slabel{font-size:10px;font-weight:750;text-transform:uppercase;letter-spacing:.08em;
color:#7D8996;margin:1.1rem 0 .45rem}
.hero{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:1.5rem;
background:var(--dark);padding:1.35rem 1.6rem;color:#fff;border-left:6px solid var(--red)}
.hero-score{font-size:50px;font-weight:750;line-height:1}
.hero-meta{font-size:12px;color:#B7C6D3;margin-top:.5rem}
.rate{display:inline-block;font-weight:750;border-radius:4px;padding:3px 10px;
font-size:11px;color:#fff}
.rate.critical{background:var(--critical)}.rate.high{background:var(--high)}
.rate.medium{background:var(--medium);color:#17283A}.rate.good{background:var(--good)}
.rate.excellent{background:var(--excellent)}.rate.not-scored{background:#88939D}
.kpi-band{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#fff;
border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:1rem 0 1.3rem}
.kpi{padding:.85rem 1rem;border-right:1px solid var(--line)}
.kpi:nth-child(4n){border-right:0}
.kpi-value{font-size:22px;font-weight:720;color:var(--dark)}
.kpi-label{font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;color:#778490;
font-weight:700}
.kpi-note{font-size:10.5px;color:#8A949E;margin-top:.15rem}
.two-col{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(300px,.92fr);
gap:1.25rem;align-items:start}
.chart-panel{background:#fff;border:1px solid var(--line);padding:1rem}
.chart-title{font-size:13px;font-weight:700;color:var(--dark)}
.chart-sub{font-size:10.5px;color:#87919B;margin-bottom:.55rem}
.chart-panel svg{display:block;width:100%;height:auto}
svg .grid{stroke:#E8EDF1;stroke-width:1}
svg .threshold{stroke:var(--red);stroke-width:1.5;stroke-dasharray:4 3}
svg .axis{font-size:9px;fill:#8A949E;font-family:var(--font)}
svg .cat{font-size:10.5px;fill:var(--ink);font-family:var(--font)}
svg .val{font-size:10px;fill:#5B6874;font-family:var(--font);dominant-baseline:middle}
svg .bar.excellent{fill:var(--excellent)}svg .bar.good{fill:var(--good)}
svg .bar.medium{fill:var(--medium)}svg .bar.high{fill:var(--high)}
svg .bar.critical{fill:var(--critical)}svg .bar.not-scored{fill:#A9B4BF}
svg .bar-none{fill:#DFE5EB}
.priority-list{display:flex;flex-direction:column;gap:.55rem}
.priority{background:#fff;border:1px solid var(--line);border-left:4px solid var(--red);
padding:.7rem .8rem}
.priority-rank{font-size:9px;text-transform:uppercase;letter-spacing:.08em;
color:var(--critical);font-weight:800}
.priority h3{font-size:12.5px;color:var(--dark);margin:.15rem 0}
.priority p{font-size:11.2px;color:#5B6874}
.priority strong{color:var(--blue)}
.dtable-wrap{overflow:auto;border:1px solid var(--line);background:#fff;margin:.45rem 0 1rem}
.dtable{width:100%;border-collapse:collapse;min-width:760px}
.dtable th{background:var(--dark);color:#BCD0E1;font-size:9.5px;text-transform:uppercase;
letter-spacing:.05em;text-align:left;padding:8px 10px;position:sticky;top:0;z-index:1}
.dtable td{padding:8px 10px;border-bottom:1px solid #E8EDF1;font-size:11.5px;
vertical-align:top;max-width:460px;overflow-wrap:anywhere}
.dtable tr:last-child td{border-bottom:0}
.dtable tbody tr:hover{background:#F8FAFC}
.sort-button{display:flex;align-items:center;gap:5px;width:100%;border:0;
background:transparent;color:inherit;font:inherit;text-transform:inherit;text-align:left;
cursor:pointer;padding:0}
.sort-button:hover,.sort-button:focus-visible{color:#fff;outline:2px solid #8FB8D8;
outline-offset:2px}
.sort-indicator{font-size:9px;color:#8FB8D8}
.sort-button[data-direction="asc"] .sort-indicator::after{content:'\\25B2'}
.sort-button[data-direction="desc"] .sort-indicator::after{content:'\\25BC'}
.sort-button:not([data-direction]) .sort-indicator::after{content:'\\25C6';opacity:.55}
.badge{display:inline-block;font-size:9.5px;padding:2px 7px;border-radius:10px;
font-weight:750;white-space:nowrap}
.badge-critical{background:#FCE8EA;color:#A41F2B}
.badge-high{background:#FFF0E6;color:#B94E0C}
.badge-medium{background:#FFF5D7;color:#715500}
.badge-good,.badge-low{background:var(--green-soft);color:var(--green)}
.badge-excellent{background:var(--blue-soft);color:var(--blue)}
.badge-gray{background:#EDF0F2;color:#59636D}
.badge-blue{background:var(--blue-soft);color:var(--blue);margin-left:.4rem}
.chip{display:inline-block;min-width:28px;text-align:center;font-weight:800;
border-radius:4px;padding:2px 7px;font-size:10.5px;color:#fff}
.chip.s0{background:#B4232F}.chip.s1{background:#D96318}
.chip.s2{background:#C89A00;color:#17283A}.chip.s3{background:#168256}
.chip.sna{background:#87929C}
.muted{color:#9AA5B0}
.empty{color:#6C7885;font-style:italic;background:#fff;border:1px dashed var(--line);
padding:.8rem 1rem;margin:.45rem 0 1rem;font-size:11.5px}
.server-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
gap:1rem;margin-bottom:1rem}
.server-panel{background:#fff;border:1px solid var(--line);border-top:4px solid var(--blue);
padding:1rem}
.server-panel h3{font-size:12.5px;color:var(--dark);display:flex;align-items:center;
flex-wrap:wrap}
.server-score{font-size:24px;font-weight:720;color:var(--dark);margin:.35rem 0 .2rem;
display:flex;align-items:center;gap:.5rem}
.server-meta{font-size:10.5px;color:#8A949E}
.area-sec{background:#fff;border:1px solid var(--line);margin-bottom:.7rem}
.area-head{display:flex;align-items:center;gap:.65rem;padding:.68rem .9rem;
background:#EEF2F5;color:var(--dark);font-weight:700;cursor:pointer;user-select:none}
.area-sec.expanded .area-head{background:var(--dark);color:#fff}
.area-num{font-size:9px;text-transform:uppercase;letter-spacing:.08em;padding:2px 7px;
background:rgba(23,40,58,.08);color:#647382}
.area-sec.expanded .area-num{background:rgba(255,255,255,.1);color:#B9CCDB}
.area-meta{margin-left:auto;font-size:10px;color:#71808D;font-weight:550}
.area-sec.expanded .area-meta{color:#ADC0D0}
.area-toggle{font-size:10px;transition:transform .15s}
.area-sec.expanded .area-toggle{transform:rotate(180deg)}
.area-content{display:none;padding:.85rem}
.area-sec.expanded .area-content{display:block}
.area-actions{display:flex;gap:.75rem;margin-bottom:.6rem}
.linkish{border:0;background:none;color:var(--blue);text-decoration:underline;
font-weight:650;cursor:pointer;padding:0;font-size:11.5px}
.filter-bar{display:flex;flex-wrap:wrap;gap:.65rem;align-items:end;background:#fff;
border:1px solid var(--line);padding:.75rem;margin-bottom:.8rem}
.filter-bar label{display:flex;flex-direction:column;gap:3px;font-size:9.5px;
font-weight:750;text-transform:uppercase;letter-spacing:.04em;color:#697581}
.filter-bar select,.filter-bar input{border:1px solid #C9D1D9;background:#fff;
padding:5px 8px;min-width:145px;color:var(--dark);border-radius:3px;font-size:11.5px}
.filter-bar input{min-width:230px}
.filter-count{margin-left:auto;align-self:center;font-size:10.5px;color:#7D8996;
font-weight:650}
.warn{background:var(--amber-soft);border-left:4px solid #D89B2B;padding:.8rem 1rem;
margin:.85rem 0}
.warn.info{background:var(--blue-soft);border-left-color:var(--blue)}
.warn h3{font-size:12px;color:var(--dark);margin-bottom:.2rem}
.warn p{font-size:11.5px;color:#5B6874}
.footer{border-top:1px solid var(--line);background:#fff;padding:1.1rem;text-align:center;
font-size:10px;color:#7D8994}
@media(max-width:1050px){
.header-stats{grid-template-columns:repeat(3,1fr)}
.kpi-band{grid-template-columns:repeat(2,1fr)}
.kpi:nth-child(2n){border-right:0}
.two-col{grid-template-columns:1fr}
}
@media(max-width:680px){
.content{padding:1rem .7rem}.header{padding:1.15rem}
.header-stats{grid-template-columns:repeat(2,1fr)}
.hero{grid-template-columns:1fr}.hero-score{font-size:42px}
}
@media print{
.tabs,.filter-bar,.area-actions{display:none!important}
.panel{display:block!important;break-before:page}
.panel:first-child{break-before:auto}
.area-content{display:block!important}
.content{max-width:none}
.dtable th{position:static}
body{background:#fff}
.area-sec,.chart-panel,.server-panel{break-inside:avoid}
}
"""

_SCRIPT = """
(function(){
  var $=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s));};

  // -- tabs ------------------------------------------------------------------
  $('.tab').forEach(function(tab){
    tab.addEventListener('click',function(){
      $('.tab').forEach(function(t){t.classList.remove('active');});
      $('.panel').forEach(function(p){p.classList.remove('active');});
      tab.classList.add('active');
      var panel=document.getElementById('panel-'+tab.dataset.panel);
      if(panel)panel.classList.add('active');
      window.scrollTo({top:0,behavior:'smooth'});
    });
  });

  // -- collapsible areas -----------------------------------------------------
  $('.area-head').forEach(function(head){
    function toggle(){head.parentNode.classList.toggle('expanded');}
    head.addEventListener('click',toggle);
    head.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}
    });
  });
  $('[data-areas]').forEach(function(button){
    button.addEventListener('click',function(){
      var expand=button.dataset.areas==='expand';
      $('.area-sec').forEach(function(sec){sec.classList.toggle('expanded',expand);});
    });
  });

  // -- filtering -------------------------------------------------------------
  // Every control bound to the same prefix narrows the same table, and they
  // intersect: a severity select and a search box together show the rows that
  // satisfy both, rather than whichever one ran last.
  var groups={};
  $('[data-filter-for]').forEach(function(input){
    var key=input.getAttribute('data-filter-for');
    (groups[key]=groups[key]||[]).push(input);
    input.addEventListener('input',function(){run(key);});
    input.addEventListener('change',function(){run(key);});
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
          return !!cell&&cell.textContent.trim()===value;
        }
        return row.textContent.toLowerCase().indexOf(value.toLowerCase())>-1;
      });
      row.style.display=ok?'':'none';
      if(ok)shown++;
    });
    var count=document.getElementById(prefix+'-count');
    if(count)count.textContent=shown+' of '+rows.length+' shown';
  }
  Object.keys(groups).forEach(run);

  // -- sorting ---------------------------------------------------------------
  // A column of numbers sorts numerically so "12" never lands before "9";
  // anything else sorts with a locale-aware natural compare.
  $('.sort-button').forEach(function(button){
    button.addEventListener('click',function(){
      var th=button.parentNode,tr=th.parentNode,table=th.closest('table');
      if(!table||!table.tBodies.length)return;
      var index=Array.prototype.indexOf.call(tr.children,th);
      var desc=button.getAttribute('data-direction')==='asc';
      $('.sort-button',table).forEach(function(other){
        if(other!==button)other.removeAttribute('data-direction');
      });
      button.setAttribute('data-direction',desc?'desc':'asc');
      var body=table.tBodies[0];
      Array.prototype.slice.call(body.rows).sort(function(a,b){
        var x=cell(a,index),y=cell(b,index);
        var nx=parseFloat(x.replace(/[%,]/g,'')),ny=parseFloat(y.replace(/[%,]/g,''));
        var numeric=!isNaN(nx)&&!isNaN(ny)
          &&/^[\\s%.,\\-0-9]+$/.test(x)&&/^[\\s%.,\\-0-9]+$/.test(y);
        var cmp=numeric?nx-ny:x.localeCompare(y,undefined,{numeric:true});
        return desc?-cmp:cmp;
      }).forEach(function(row){body.appendChild(row);});
    });
  });

  function cell(row,index){
    var td=row.cells[index];
    return td?td.textContent.trim():'';
  }
})();
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="generator" content="auditfast">
<style>{css}</style>
</head>
<body>
<header class="header">
  <div class="header-eye">Microsoft Fabric Well-Architected</div>
  <h1>Audit <span>Readout</span></h1>
  <div class="header-sub">{project} &middot; {generated} &middot; deterministic,
  rule-based assessment of the implemented estate against the Well-Architected
  checklist. No AI participates in scoring.</div>
  <div class="header-stats">{header_stats}</div>
</header>
<nav class="tabs" aria-label="Report sections">
  <div class="tab-group">{tabs_main}</div>
  <div class="tab-group context">{tabs_context}</div>
</nav>
<main class="content">{panels}</main>
<footer class="footer">
Scope: rule-based Fabric architecture and best-practice assessment. Repeated
asset-level verdicts are consolidated by control for stakeholder reporting;
deterministic asset-level results remain the basis of every score.
</footer>
<script>{script}</script>
</body>
</html>
"""
