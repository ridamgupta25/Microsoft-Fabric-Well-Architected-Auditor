"""The self-contained HTML readout of an audit report.

Pins the properties that make it safe to hand to a stakeholder: tenant data is
escaped rather than injected as markup, nothing is fetched from the network when
the file is opened, and the scoring bands it publishes are the ones the scorer
actually applies.
"""
from __future__ import annotations

import re

from auditfast.core.enums import Layer, Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult
from auditfast.core.scoring import aggregate, rating
from auditfast.reporting.html import (
    EXAMPLE_PROJECT_NAME,
    GOOD_THRESHOLD,
    build_html,
)


def _result(**overrides) -> CheckResult:
    base = dict(
        check_id="NB-TEST",
        ref="1.2.3",
        title="Notebook has a retry policy",
        pillar=Pillar.RELIABILITY,
        status=Status.FAIL,
        score=0,
        evidence="No retry policy is configured.",
        recommendation="Configure a bounded retry.",
        severity=Severity.HIGH,
        workspace="Data Prep",
        layer=Layer.PREP,
        obj="NB_Load",
        scope=Scope.NOTEBOOK,
    )
    base.update(overrides)
    return CheckResult(**base)


def _page(results) -> str:
    return build_html("Test project", aggregate(results), results)


def test_report_is_a_complete_html_document():
    page = _page([_result()])
    assert page.startswith("<!DOCTYPE html>")
    assert page.rstrip().endswith("</html>")
    assert "<title>" in page and "</title>" in page


def test_report_carries_every_panel():
    page = _page([_result()])
    for panel in (
        "summary", "comparison", "results", "risks", "controls",
        "recommendations", "timeline", "scoring", "scope",
    ):
        assert f'id="panel-{panel}"' in page, panel
        assert f'data-panel="{panel}"' in page, panel


def test_exactly_one_panel_and_tab_start_active():
    page = _page([_result()])
    assert page.count('class="panel active"') == 1
    assert page.count('class="tab active"') == 1


def test_charts_are_inline_svg():
    """Drawn server-side so the page still fetches nothing."""
    page = _page([_result()])
    assert "<svg" in page and "viewBox=" in page
    assert 'class="radar-chart"' in page


def test_report_is_self_contained():
    """No CDN, no fonts, no analytics.

    An audit report is handed to a client and opened offline. A remote asset
    would both break that and tell a third-party host a workspace was audited.
    """
    page = _page([_result()])
    assert "<style>" in page and "<script>" in page
    assert not re.search(r'\b(?:src|href)\s*=\s*["\']https?://', page)
    assert "@import" not in page


def test_tenant_data_is_escaped_not_injected():
    """A workspace or evidence string is content, never markup."""
    page = _page([
        _result(
            workspace="<script>alert('ws')</script>",
            evidence="Tom & Jerry's \"quoted\" <b>evidence</b>",
            obj="a<img src=x onerror=1>",
        )
    ])
    assert "<script>alert(" not in page
    assert "&lt;script&gt;alert(" in page
    assert "onerror=1>" not in page
    assert "Tom &amp; Jerry" in page


def test_findings_reach_the_risk_register_and_recommendations():
    page = _page([
        _result(),
        _result(
            check_id="NB-OK", ref="4.5.6", title="Notebook logs its run",
            status=Status.PASS, score=3, obj="NB_Report",
        ),
    ])
    assert "Notebook has a retry policy" in page
    assert "Notebook logs its run" in page
    assert "Configure a bounded retry." in page
    assert "R-001" in page


def test_control_matrix_carries_a_score_cell_per_workspace():
    page = _page([
        _result(workspace="Alpha", obj="A"),
        _result(workspace="Beta", obj="B", status=Status.PASS, score=3),
    ])
    matrix = page.split('id="control-table"', 1)[1]
    assert "score score-0" in matrix
    assert "score score-3" in matrix
    # Columns are named for the workspace, not an opaque WS-n id.
    assert "Alpha" in matrix and "Beta" in matrix


def test_the_heading_names_the_estate_not_the_config():
    """One workspace names itself; several are titled by the prefix they share."""
    single = _page([_result(workspace="Sales Analytics", obj="A")])
    assert "Sales Analytics <span>" in single

    many = _page([
        _result(workspace="MLC_Fabric_DEV", obj="A"),
        _result(workspace="MLC_Fabric_UAT", obj="B"),
        _result(workspace="MLC_Fabric_PROD", obj="C"),
    ])
    assert "MLC_Fabric (3 workspaces)" in many


def test_a_project_group_names_the_report():
    """The reviewer named the group, so it beats every other candidate.

    Without this the heading falls back to the workspace prefix, which for an
    estate of unrelated workspaces is nothing at all -- so the run the reviewer
    built and called "MDM" comes out titled after the shipped example project.
    """
    results = [
        _result(workspace="Bonne Terre", obj="A"),
        _result(workspace="Calera", obj="B"),
    ]
    titled = build_html(EXAMPLE_PROJECT_NAME, aggregate(results), results,
                        groups=[{"name": "MDM", "workspaces": []}])
    assert "MDM <span>" in titled


def test_an_unnamed_estate_is_not_titled_after_the_example_project():
    """A stale config name must never be published as if it were the engagement.

    ``project.example.yaml`` ships a name and a UI-started run still loads that
    file, so trusting it titles a real audit after a sample project.
    """
    results = [
        _result(workspace="Bonne Terre", obj="A"),
        _result(workspace="Calera", obj="B"),
    ]
    page = build_html(EXAMPLE_PROJECT_NAME, aggregate(results), results)
    assert EXAMPLE_PROJECT_NAME not in page
    assert "Fabric estate (2 workspaces)" in page

    named = build_html("MDM Programme", aggregate(results), results)
    assert "MDM Programme (2 workspaces)" in named


def test_subsections_are_named_from_the_checklist_not_invented():
    """Every subsection our checks use carries a name; anything else stays bare.

    A ref outside the numbered checklist has no name and must not acquire a
    guessed one.
    """
    page = _page([
        _result(ref="1.1.1", check_id="A", obj="A"),
        _result(ref="14.4.1", check_id="B", obj="B"),
        _result(ref="99.9.9", check_id="C", obj="C"),
    ])
    assert "1.1 \u00b7 Solution Architecture" in page
    assert "14.4 \u00b7 Report Consumer Security" in page
    assert "99.9 \u00b7" not in page


def test_every_subsection_in_use_has_a_name():
    """A blank name next to a number reads as missing data, not as "unnamed".

    The two maps are maintained by hand, so a check authored against a new
    subsection would silently publish a number with nothing beside it. This
    fails instead.
    """
    from auditfast.core.check.registry import GROUP_REGISTRY, REGISTRY
    from auditfast.reporting.structure import category_number, category_title

    unnamed = sorted({
        number
        for spec in [*REGISTRY, *GROUP_REGISTRY]
        if (number := category_number(spec.ref)) and not category_title(number)
        and number[0].isdigit()
    })
    assert not unnamed, f"subsections with no name: {unnamed}"


def test_recommendations_separate_the_action_from_the_impacted_assets():
    page = _page([
        _result(workspace="Alpha", obj="X"),
        _result(workspace="Beta", obj="Y"),
    ])
    register = page.split('id="rec-table"', 1)[1]
    assert "View detailed action" in register
    assert "View impacted assets" in register
    assert "Alpha / X" in register and "Beta / Y" in register


def test_recommendations_carry_one_action_not_a_paragraph_per_asset():
    """The engine repeats the same Action clause for every failing asset.

    Left inline that becomes a wall of near-identical text; the readout pulls the
    clause out once so the register stays scannable.
    """
    recommendation = (
        'Target: workspace "A". Observed gap: no retry. '
        "Action: Configure a bounded retry. Verification: Re-run the audit."
    )
    page = _page([
        _result(workspace="A", obj="X", recommendation=recommendation),
        _result(workspace="B", obj="Y", recommendation=recommendation.replace('"A"', '"B"')),
    ])
    register = page.split('id="rec-table"', 1)[1]
    assert register.count("Configure a bounded retry.") == 1
    assert "Verification: Re-run the audit." not in register


def test_not_assessed_is_reported_without_being_scored():
    page = _page([
        _result(),
        _result(
            check_id="NB-NA", ref="7.8.9", status=Status.NA, score=None,
            scored=False, evidence="Definition could not be read",
            obj="NB_Unreadable",
        ),
    ])
    assert "Not assessed" in page
    assert "Definition could not be read" in page


def test_an_empty_run_still_renders():
    """A pillar filter can leave a section with nothing in it."""
    page = build_html("Empty project", aggregate([]), [])
    assert page.startswith("<!DOCTYPE html>")
    assert page.count('class="panel active"') == 1
    assert "No risks" in page


def test_workspace_comparison_is_ordered_worst_first():
    page = _page([
        _result(workspace="Good WS", obj="A", status=Status.PASS, score=3),
        _result(workspace="Bad WS", obj="B", status=Status.FAIL, score=0),
    ])
    table = page.split('id="inventory-table"', 1)[1]
    assert table.index("Bad WS") < table.index("Good WS")


def test_filters_are_bound_to_the_table_they_narrow():
    """Each filter control names a table id that exists, or it does nothing."""
    page = _page([
        _result(),
        _result(
            check_id="NB-OK", ref="4.5.6", title="Notebook logs its run",
            status=Status.PASS, score=3, obj="NB_Report",
        ),
    ])
    for prefix in ("risk", "control", "rec"):
        assert f'data-filter-for="{prefix}"' in page, prefix
        assert f'id="{prefix}-table"' in page, prefix
        assert f'id="{prefix}-count"' in page, prefix


def test_published_bands_match_the_scorer():
    """The Scoring tab must not publish a band the scorer does not apply.

    The band table is written by hand, so it can drift from ``rating()`` without
    anything failing. Checking the boundaries here means a change to the scorer
    surfaces as a test failure rather than a misleading report.
    """
    page = _page([_result()])
    assert rating(GOOD_THRESHOLD)[0] == "Good"
    assert rating(GOOD_THRESHOLD - 0.1)[0] == "Medium"
    for boundary in ("41%", "61%", "76%", "91%"):
        assert boundary in page, boundary
    assert f"{GOOD_THRESHOLD:.0f}% Good threshold" in page


def test_timeline_models_a_ceiling_not_a_forecast():
    """Closing every severity band must land exactly on 100%.

    The projection is arithmetic with the denominator held fixed: if every
    failing control scored full marks the estate would be at 100%. A milestone
    above that, or a final point below it, means the model has drifted from the
    scorer.
    """
    from auditfast.reporting.html import _projection

    points = _projection([
        _result(obj="A", severity=Severity.CRITICAL, score=0, status=Status.FAIL),
        _result(obj="B", severity=Severity.MEDIUM, score=1, status=Status.PARTIAL),
        _result(obj="C", severity=Severity.LOW, score=3, status=Status.PASS),
    ])
    scores = [score for _, _, score, _ in points]
    assert scores == sorted(scores), "a milestone can never lower the score"
    assert scores[-1] == 100.0
    assert scores[0] < 100.0


def test_timeline_is_absent_of_a_month_by_month_plan():
    page = _page([_result()])
    assert "Month-by-month" not in page
    assert "Modelled score improvement" in page
