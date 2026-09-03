"""The self-contained HTML rendering of an audit report.

Pins the two properties that make it safe to hand to a stakeholder: tenant data
is escaped rather than injected as markup, and nothing is fetched from the
network when the file is opened.
"""
from __future__ import annotations

import re

from auditfast.core.enums import Layer, Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult
from auditfast.core.scoring import aggregate
from auditfast.reporting.html import build_html


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
        "exec", "workspaces", "results", "risks", "controls", "strengths",
        "scope", "scoring",
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
    assert 'class="threshold"' in page


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


def test_findings_and_scores_are_rendered():
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
    assert "Strengths (1)" in page
    # The finding reaches the risk register with a generated id.
    assert "R-001" in page


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
    assert "No findings" in page


def test_workspace_comparison_is_ordered_worst_first():
    page = _page([
        _result(workspace="Good WS", obj="A", status=Status.PASS, score=3),
        _result(workspace="Bad WS", obj="B", status=Status.FAIL, score=0),
    ])
    grid = page.split('class="server-grid"', 1)[1]
    assert grid.index("Bad WS") < grid.index("Good WS")


def test_filters_are_bound_to_the_table_they_narrow():
    """Each filter control names the table id it filters, or it silently does nothing."""
    page = _page([
        _result(),
        _result(
            check_id="NB-OK", ref="4.5.6", title="Notebook logs its run",
            status=Status.PASS, score=3, obj="NB_Report",
        ),
    ])
    for prefix in ("risk", "control", "strength"):
        assert f'data-filter-for="{prefix}"' in page, prefix
        assert f'id="{prefix}-table"' in page, prefix
        assert f'id="{prefix}-count"' in page, prefix
