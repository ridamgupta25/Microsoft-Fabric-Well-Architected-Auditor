"""Node 3a (Semantic KB Identifier) tests.

Keyword path runs with AI off; the semantic path is exercised with an injected
fake embedder. Presence/quality checks use small hand-built shared-KB dicts.
"""
from __future__ import annotations

from auditfast.ai.agents import kb_identifier_agent
from auditfast.ai.agents.kb_identifier_agent import identify, plan
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    LifecycleStatus,
    make_check_id,
)
from auditfast.ai.rag.kb_field_catalog import KB_FIELD_CATALOG, MISSING, field_value


def _check(prompt: str) -> CustomCheck:
    return CustomCheck(check_id=make_check_id(prompt), raw_prompt=prompt)


# -- identify (keyword) --------------------------------------------------------

def test_identify_maps_refresh_prompt_to_refresh_field():
    field, confidence, stage = identify(
        "Ensure all semantic models have incremental refresh policies"
    )
    assert field.key == "refresh_schedule"
    assert stage == "keyword"
    assert confidence > 0.0


def test_identify_maps_git_prompt_to_git_field():
    field, _c, _s = identify("Check that every workspace has git source control enabled")
    assert field.key == "git_integration"


def test_identify_maps_access_prompt_to_role_field():
    field, _c, _s = identify("Who has admin access and permissions to the data")
    assert field.key == "role_assignments"


def test_identify_returns_none_when_nothing_overlaps():
    field, confidence, _s = identify("xyzzy frobnicate quux widgets")
    assert field is None
    assert confidence == 0.0


# -- identify (semantic) -------------------------------------------------------

def test_identify_uses_semantic_when_embedder_available():
    # Fake embedder: the git field's description and the prompt map to the same axis.
    def fake_embed(text: str):
        t = text.lower()
        if "git" in t or "source control" in t:
            return [1.0, 0.0]
        return [0.0, 1.0]

    field, confidence, stage = identify(
        "connect the workspace to source control", embedder=fake_embed
    )
    assert stage == "semantic"
    assert field.key == "git_integration"
    assert confidence > 0.9


# -- plan: present vs missing --------------------------------------------------

def test_plan_marks_processed_custom_when_field_present():
    session = CustomCheckSession()
    session.shared_kb = {"git_connected": True}
    check = plan(_check("verify git source control is on"), session)
    assert check.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM
    assert check.fetch_plan is None


def test_plan_builds_fetch_plan_when_field_absent():
    session = CustomCheckSession()  # empty KB
    check = plan(_check("Ensure semantic models have incremental refresh"), session)
    assert check.lifecycle_status is LifecycleStatus.PENDING
    assert check.fetch_plan is not None
    assert check.fetch_plan.field == "refresh_schedules"
    assert check.fetch_plan.endpoint.startswith("GET ")


def test_plan_treats_empty_value_as_missing():
    session = CustomCheckSession()
    session.shared_kb = {"refresh_schedules": {}}  # present key, but empty -> not usable
    check = plan(_check("Ensure semantic models have incremental refresh"), session)
    assert check.lifecycle_status is LifecycleStatus.PENDING
    assert check.fetch_plan is not None


def test_plan_resolves_field_one_level_down_for_workspace_map():
    session = CustomCheckSession()
    session.shared_kb = {"ws-123": {"git_connected": True}}
    check = plan(_check("verify git integration"), session)
    assert check.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM


def test_plan_ignores_a_non_pending_check():
    session = CustomCheckSession()
    check = _check("Ensure semantic models have incremental refresh")
    check.lifecycle_status = LifecycleStatus.ROUTED_DEFAULT
    out = plan(check, session)
    assert out.lifecycle_status is LifecycleStatus.ROUTED_DEFAULT
    assert out.fetch_plan is None


def test_plan_low_confidence_marks_fetch_plan_optional(monkeypatch):
    # Force a very low identification confidence -> mandatory flips to False.
    from auditfast.ai.rag.kb_field_catalog import KB_FIELD_CATALOG as CAT

    monkeypatch.setattr(
        kb_identifier_agent,
        "identify",
        lambda _p: (CAT[3], 0.05, "keyword"),  # refresh_schedule field, tiny confidence
    )
    session = CustomCheckSession()
    check = plan(_check("something vague"), session)
    assert check.fetch_plan is not None
    assert check.fetch_plan.mandatory is False


# -- catalog resolver ----------------------------------------------------------

def test_field_value_returns_missing_when_absent():
    assert field_value({}, "git_connected") is MISSING


def test_catalog_is_non_empty_and_unique_keyed():
    keys = [f.key for f in KB_FIELD_CATALOG]
    assert len(keys) == len(set(keys))
    assert len(keys) >= 10
