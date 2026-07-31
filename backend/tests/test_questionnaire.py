"""Interactive (self-assessed) checklist points and answer merging.

Interactive checks are the Azure Well-Architected Review model: the reviewer
chooses a scored option during the audit for a point a machine cannot read from
the workspace. These tests pin that the questionnaire is derived (never
hard-coded), that answers score and fan out per applicable workspace, that a
skip records N/A and does not score, and that merging is idempotent so the KB
background refresh cannot double-count.
"""
from __future__ import annotations

from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Automation, Status
from auditfast.services.questionnaire_service import (
    SKIP_VALUE,
    build_questionnaire,
    interactive_specs,
    merge_answers_into_report,
)

from .conftest import FIXTURE_SETTINGS, FIXTURE_TARGETS


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
        pillars=["Security"],
        workspaces=[{"id": "w", "role": "Reporting / Semantic"}],
    )
    assert q, "expected at least one security questionnaire item"
    assert all(item["pillar"] == "Security" for item in q)
    ids = {item["id"] for item in q}
    # RLS applies to the Reporting / Semantic layer, so it must be offered.
    assert "Q-SEC-RLS" in ids
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
    assert all(item["pillar"] == "Security" for item in q)


# -- answers score and fan out per applicable workspace ------------------------

def test_answers_score_and_fan_out(provider):
    report = _report(provider)
    workspaces = {row["workspace"] for row in report["results"] if row["workspace"]}

    merged = merge_answers_into_report(
        report,
        {"Q-OPS-DR": "tested", "Q-OPS-RUNBOOK": SKIP_VALUE},
        ["Q-OPS-DR", "Q-OPS-RUNBOOK"],
    )
    rows = merged["results"]

    dr = [r for r in rows if r["check_id"] == "Q-OPS-DR"]
    runbook = [r for r in rows if r["check_id"] == "Q-OPS-RUNBOOK"]

    # Q-OPS-DR applies to every layer, so one scored result per audited workspace.
    assert {r["workspace"] for r in dr} == workspaces
    assert all(r["status"] == Status.PASS.value for r in dr)
    assert all(r["score"] == 3 for r in dr)
    assert all(r["scored"] is True for r in dr)
    assert all("Self-assessed" in r["evidence"] for r in dr)

    # A skipped question is recorded as N/A for every workspace and never scored.
    assert {r["workspace"] for r in runbook} == workspaces
    assert all(r["status"] == Status.NA.value for r in runbook)
    assert all(r["scored"] is False for r in runbook)


def test_low_option_carries_guidance_as_recommendation(provider):
    report = _report(provider)
    merged = merge_answers_into_report(report, {"Q-OPS-DR": "none"}, ["Q-OPS-DR"])
    dr = [r for r in merged["results"] if r["check_id"] == "Q-OPS-DR"]
    assert dr
    spec = REGISTRY.get("Q-OPS-DR")
    guidance = next(o.guidance for o in spec.options if o.value == "none")
    assert all(r["status"] == Status.FAIL.value for r in dr)
    assert all(r["recommendation"] == guidance for r in dr)


# -- merging is idempotent -----------------------------------------------------

def test_merge_is_idempotent(provider):
    report = _report(provider)
    once = merge_answers_into_report(report, {"Q-OPS-DR": "tested"}, ["Q-OPS-DR"])
    twice = merge_answers_into_report(once, {"Q-OPS-DR": "tested"}, ["Q-OPS-DR"])
    assert len(once["results"]) == len(twice["results"])
    assert once["total_scored"] == twice["total_scored"]
    assert once["overall"] == twice["overall"]


def test_merging_only_adds_results(provider):
    report = _report(provider)
    before = len(report["results"])
    merged = merge_answers_into_report(report, {"Q-OPS-DR": "tested"}, ["Q-OPS-DR"])
    dr = [r for r in merged["results"] if r["check_id"] == "Q-OPS-DR"]
    assert len(merged["results"]) == before + len(dr)
