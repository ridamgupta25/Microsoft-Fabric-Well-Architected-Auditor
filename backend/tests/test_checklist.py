"""Checklist-intake pipeline — matching, proposal drafting, and the endpoint.

Everything here is deterministic and offline: the intake path is token-free and
AI is disabled by default, so these assertions do not depend on a tenant or a
model. The most important guarantee is negative — assessing a point must never
register a check, so the pinned registry count and score cannot move.
"""
from __future__ import annotations

from auditfast.ai import authoring, matching
from auditfast.core.check.registry import REGISTRY
from auditfast.services import intake_service

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
    match = matching.best_match("Point about ref 11.1.2")
    assert match is not None
    assert match.spec.ref == "11.1.2"


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
