"""Interactive (self-assessed) checklist points and answer merging.

Interactive checks are the Azure Well-Architected Review model: the reviewer
chooses a scored option during the audit for a point a machine cannot read from
the workspace. These tests pin that the questionnaire is derived (never
hard-coded), that answers score and fan out per applicable workspace, that a
skip records N/A and does not score, and that merging is idempotent so the KB
background refresh cannot double-count.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Automation, Status
from auditfast.services.questionnaire_service import (
    SKIP_VALUE,
    build_questionnaire,
    interactive_specs,
    merge_answers_into_report,
)

from .conftest import FIXTURE_SETTINGS, FIXTURE_TARGETS

#: This module exercises the interactive (self-assessed) questionnaire. When no
#: interactive checks are registered there is nothing to score, so the whole
#: module skips instead of failing. Re-adding a ``questionnaire_check`` re-enables
#: these tests automatically.
pytestmark = pytest.mark.skipif(
    not interactive_specs(),
    reason="no interactive (self-assessed) checks are registered",
)


def _run(provider, **kwargs):
    from auditfast.core.engine import run_audit

    return run_audit(provider, FIXTURE_TARGETS, FIXTURE_SETTINGS, **kwargs)


def _report(provider) -> dict:
    """A minimal report shaped like the one the runner merges answers into."""
    results = _run(provider)
    return {"project_name": "fixture", "results": [r.to_dict() for r in results]}


# -- the questionnaire is derived, not hard-coded ------------------------------

def test_interactive_specs_are_all_interactive():
    specs = interactive_specs()
    assert specs, "no interactive checks registered"
    assert all(s.interactive for s in specs)
    assert all(s.automation is Automation.INTERACTIVE for s in specs)
    assert all(s.manual for s in specs)  # the engine skips them


def test_build_questionnaire_scopes_by_pillar_and_layer():
    q = build_questionnaire(
        pillars=["Operations & Reliability"],
        workspaces=[{"id": "w", "role": "Data Operations"}],
    )
    assert q, "expected Operations & Reliability questionnaire items"
    assert all(item["pillar"] == "Operations & Reliability" for item in q)
    ids = {item["id"] for item in q}
    assert ids == {
        "WS-DOMAIN-FOLDERS", "PL-SLA-MONITORED", "PL-SLA-ALERTS",
        "OPS-INTEGRATION-TESTS", "OPS-DATA-VALIDATION-TESTS",
        "OPS-GIT-IGNORE", "OPS-GIT-COMMIT-MSG", "OPS-GIT-PR-REVIEW",
        "OPS-GIT-MIN-REVIEWERS", "OPS-WH-SCHEMA-SCM", "OPS-DEPLOY-RULES",
        "OPS-ENV-PARITY", "OPS-SLA-HISTORY",
        "Q-CAT3-RELEASE-GOVERNANCE", "Q-CAT3-DR-READINESS",
        "Q-CAT3-DATA-FRESHNESS", "Q-CAT3-INCIDENT-RESPONSE",
    }
    # Every item is serialized with its question and scored options for the UI.
    for item in q:
        assert item["question"]
        assert item["options"]
        assert item["interactive"] is True


def test_build_questionnaire_omits_unselected_pillars():
    q = build_questionnaire(
        pillars=["Security"],
        workspaces=[{"id": "w", "role": "Mixed"}],
    )
    assert {item["id"] for item in q} == {"Q-CAT3-ACCESS-REVIEWS"}


# -- answers score and fan out per applicable workspace ------------------------

def test_answers_score_and_fan_out(provider):
    report = _report(provider)

    merged = merge_answers_into_report(
        report,
        {"PL-SLA-MONITORED": "yes", "PL-SLA-ALERTS": SKIP_VALUE},
        ["PL-SLA-MONITORED", "PL-SLA-ALERTS"],
    )
    rows = merged["results"]

    pool = [r for r in rows if r["check_id"] == "PL-SLA-MONITORED"]
    alerts = [r for r in rows if r["check_id"] == "PL-SLA-ALERTS"]

    ops_workspaces = {
        r["workspace"] for r in report["results"] if r["layer"] == "Data Operations"
    }
    assert {r["workspace"] for r in pool} == ops_workspaces
    assert all(r["status"] == Status.PASS.value for r in pool)
    assert all(r["score"] == 3 for r in pool)
    assert all(r["scored"] is True for r in pool)
    assert all("Self-assessed" in r["evidence"] for r in pool)

    assert {r["workspace"] for r in alerts} == ops_workspaces
    assert all(r["status"] == Status.NA.value for r in alerts)
    assert all(r["scored"] is False for r in alerts)


def test_low_option_carries_guidance_as_recommendation(provider):
    report = _report(provider)
    merged = merge_answers_into_report(
        report, {"PL-SLA-MONITORED": "no"}, ["PL-SLA-MONITORED"]
    )
    rows = [r for r in merged["results"] if r["check_id"] == "PL-SLA-MONITORED"]
    assert rows
    spec = REGISTRY.get("PL-SLA-MONITORED")
    guidance = next(o.guidance for o in spec.options if o.value == "no")
    assert all(r["status"] == Status.FAIL.value for r in rows)
    assert all(r["recommendation"] == guidance for r in rows)


# -- merging is idempotent -----------------------------------------------------

def test_merge_is_idempotent(provider):
    report = _report(provider)
    once = merge_answers_into_report(
        report, {"PL-SLA-MONITORED": "yes"}, ["PL-SLA-MONITORED"]
    )
    twice = merge_answers_into_report(
        once, {"PL-SLA-MONITORED": "yes"}, ["PL-SLA-MONITORED"]
    )
    assert len(once["results"]) == len(twice["results"])
    assert once["total_scored"] == twice["total_scored"]
    assert once["overall"] == twice["overall"]


def test_merging_only_adds_results(provider):
    report = _report(provider)
    before = len(report["results"])
    merged = merge_answers_into_report(
        report, {"PL-SLA-MONITORED": "yes"}, ["PL-SLA-MONITORED"]
    )
    pool = [r for r in merged["results"] if r["check_id"] == "PL-SLA-MONITORED"]
    assert len(merged["results"]) == before + len(pool)
