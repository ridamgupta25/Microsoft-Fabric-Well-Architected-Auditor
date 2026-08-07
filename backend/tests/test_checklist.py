"""Checklist-intake pipeline — matching, proposal drafting, and the endpoint.

Everything here is deterministic and offline: the intake path is token-free and
AI is disabled by default, so these assertions do not depend on a tenant or a
model. The most important guarantee is negative — assessing a point must never
register a check, so the pinned registry count and score cannot move.
"""
from __future__ import annotations

import pytest

from auditfast.ai import authoring, matching
from auditfast.core.check.registry import REGISTRY
from auditfast.services import checklist_batch, intake_service
from auditfast.services.checklist_batch import ChecklistParseError, ChecklistPoint

# -- matching ------------------------------------------------------------------

def test_known_point_matches_the_existing_check():
    result = intake_service.assess_point("Git integration is enabled for the workspace")
    assert result["status"] == "covered"
    assert result["covered"] is True
    assert result["matches"][0]["check_id"] == "WS-GIT"
    assert result["proposal"] is None


def test_matching_is_deterministic():
    a = intake_service.assess_point("Capacity is assigned to the workspace")
    b = intake_service.assess_point("Capacity is assigned to the workspace")
    assert a == b


def test_ref_in_text_boosts_the_matching_check():
    match = matching.best_match("Point about ref 11.1.1")
    assert match is not None
    assert match.spec.ref == "11.1.1"


# -- proposal drafting ---------------------------------------------------------

def test_novel_point_yields_a_proposal_not_a_match():
    result = intake_service.assess_point(
        "Notebooks broadcast small dimension tables to avoid shuffle joins"
    )
    assert result["status"] == "not_covered"
    assert result["covered"] is False
    proposal = result["proposal"]
    assert proposal is not None
    assert proposal["scope"] == "notebook"
    assert proposal["suggested_id"].startswith("NB-")
    assert "@check" in proposal["code_skeleton"]
    assert result["next_steps"]  # promotion guidance is always present


def test_scope_and_pillar_inference():
    proposal = authoring.draft_proposal("Enforce row-level security on the semantic model")
    assert proposal.pillar.value == "Security"
    assert proposal.severity.value == "High"


def test_empty_point_is_invalid():
    result = intake_service.assess_point("   ")
    assert result["status"] == "invalid"
    assert result["proposal"] is None


# -- the additive guarantee ----------------------------------------------------

def test_assessing_never_mutates_the_registry():
    before = len(REGISTRY)
    intake_service.assess_point("Some brand new governance retention policy point")
    intake_service.assess_point("Git integration is enabled")
    assert len(REGISTRY) == before


def test_ai_is_disabled_by_default():
    result = intake_service.assess_point("Delta tables are vacuumed regularly")
    assert result["ai_enabled"] is False
    assert result["advisory"]  # deterministic fallback text is always non-empty


# -- the endpoint --------------------------------------------------------------

def test_assess_endpoint_reports_coverage(client):
    response = client.post(
        "/api/v1/checklist/assess",
        json={"point": "Git integration is enabled"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["covered"] is True
    assert body["matches"][0]["check_id"] == "WS-GIT"


def test_assess_endpoint_drafts_a_proposal(client):
    response = client.post(
        "/api/v1/checklist/assess",
        json={"point": "Notebooks repartition wide dataframes before shuffle joins"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_covered"
    assert body["proposal"]["scope"] == "notebook"


def test_assess_endpoint_rejects_an_empty_point(client):
    response = client.post("/api/v1/checklist/assess", json={"point": ""})
    assert response.status_code == 422


# =============================================================================
# Batch — parsing a whole uploaded checklist
# =============================================================================

def test_parse_csv_with_headers():
    text = (
        "point,pillar,scope,notes\n"
        "Git integration is enabled,Operations,workspace,expect a match\n"
        "Row-level security is enforced,Security,semantic_model,new\n"
    )
    points = checklist_batch.parse_checklist(text, filename="c.csv")
    assert [p.point for p in points] == [
        "Git integration is enabled",
        "Row-level security is enforced",
    ]
    assert points[0].pillar == "Operations"
    assert points[0].scope == "workspace"
    assert points[1].notes == "new"


def test_parse_csv_without_point_header_uses_first_column():
    text = "Git integration is enabled\nCapacity is assigned\n"
    points = checklist_batch.parse_checklist(text, filename="c.csv")
    assert [p.point for p in points] == [
        "Git integration is enabled",
        "Capacity is assigned",
    ]


def test_parse_json_array_of_strings():
    points = checklist_batch.parse_checklist(
        '["Git integration is enabled", "Capacity is assigned"]', filename="c.json"
    )
    assert [p.point for p in points] == [
        "Git integration is enabled",
        "Capacity is assigned",
    ]


def test_parse_json_objects_and_points_key():
    text = '{"points": [{"point": "Git integration is enabled", "scope": "workspace"}]}'
    points = checklist_batch.parse_checklist(text, filename="c.json")
    assert points[0].point == "Git integration is enabled"
    assert points[0].scope == "workspace"


def test_parse_text_strips_bullets_headings_and_checkboxes():
    text = (
        "# My checklist\n"
        "\n"
        "- [ ] Git integration is enabled\n"
        "* Capacity is assigned\n"
        "1. Row-level security is enforced\n"
        "| --- | --- |\n"
    )
    points = checklist_batch.parse_checklist(text, filename="c.md")
    assert [p.point for p in points] == [
        "Git integration is enabled",
        "Capacity is assigned",
        "Row-level security is enforced",
    ]


def test_parse_detects_format_from_content_without_filename():
    assert checklist_batch.parse_checklist('["A point"]')[0].point == "A point"
    assert checklist_batch.parse_checklist("just one line")[0].point == "just one line"


def test_parse_empty_checklist_raises():
    with pytest.raises(ChecklistParseError):
        checklist_batch.parse_checklist("   \n\n")


# =============================================================================
# Batch — running a checklist (offline, deterministic)
# =============================================================================

def test_run_checklist_classifies_each_point_without_running():
    points = [
        ChecklistPoint("Git integration is enabled"),
        ChecklistPoint("Notebooks broadcast small dimension tables to avoid shuffle joins"),
    ]
    result = checklist_batch.run_checklist(points, run_checks=False)
    assert result["summary"]["total_points"] == 2
    assert result["summary"]["covered"] == 1
    assert result["summary"]["not_covered"] == 1
    assert result["items"][0]["status"] == "covered"
    assert result["items"][0]["matches"][0]["check_id"] == "WS-GIT"
    assert result["items"][0]["evaluations"] == []  # run_checks=False never evaluates
    assert result["items"][1]["status"] == "not_covered"
    assert result["items"][1]["proposal"] is not None


def test_run_checklist_evaluates_covered_check_offline_is_na_without_snapshot():
    # No snapshot for this workspace and no token => a deterministic N/A row,
    # never a FAIL — extending coverage can never invent a failing verdict.
    result = checklist_batch.run_checklist(
        [ChecklistPoint("Git integration is enabled")],
        workspace_ids=["ws-does-not-exist"],
        run_checks=True,
    )
    item = result["items"][0]
    assert item["evaluated_check"] == "WS-GIT"
    assert len(item["evaluations"]) == 1
    evaluation = item["evaluations"][0]
    assert evaluation["source"] == "none"
    assert evaluation["status"] == "N/A"


def test_run_checklist_never_mutates_the_registry():
    before = len(REGISTRY)
    checklist_batch.run_checklist(
        [ChecklistPoint("A brand new retention policy point")], run_checks=False
    )
    assert len(REGISTRY) == before


# =============================================================================
# Batch — the endpoint
# =============================================================================

def test_batch_endpoint_parses_content(client):
    response = client.post(
        "/api/v1/checklist/batch",
        json={
            "content": "Git integration is enabled\nRow-level security is enforced\n",
            "filename": "checklist.md",
            "run_checks": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_points"] == 2
    assert body["items"][0]["matches"][0]["check_id"] == "WS-GIT"


def test_batch_endpoint_accepts_points_list(client):
    response = client.post(
        "/api/v1/checklist/batch",
        json={"points": ["Git integration is enabled"], "run_checks": False},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "covered"


def test_batch_endpoint_rejects_missing_content_and_points(client):
    response = client.post("/api/v1/checklist/batch", json={"run_checks": False})
    assert response.status_code == 422


def test_batch_endpoint_rejects_unparseable_content(client):
    response = client.post(
        "/api/v1/checklist/batch",
        json={"content": "   \n\n", "run_checks": False},
    )
    assert response.status_code == 422

