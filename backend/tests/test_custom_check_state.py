"""State-layer tests for the custom-checks pipeline (Step 1).

Pure-Python; no AI, no optional extras, no core import. These lock the ledger
shape and the idempotent id so later nodes can rely on them.
"""
from __future__ import annotations

from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    FeasibilityClass,
    FetchErrorClass,
    FetchPlan,
    GuardrailVerdict,
    KbUpdateLog,
    LifecycleStatus,
    RoutingResult,
    make_check_id,
)


def test_lifecycle_status_has_the_seven_documented_states():
    assert {s.value for s in LifecycleStatus} == {
        "PENDING",
        "DROPPED_GUARDRAIL",
        "ROUTED_DEFAULT",
        "PROCESSED_CUSTOM",
        "KB_AUGMENTED",
        "KB_FETCH_FAILED",
        "AI_REQUIRED",
    }


def test_fetch_error_class_has_the_five_documented_classes():
    assert {c.value for c in FetchErrorClass} == {
        "INSUFFICIENT_PERMISSIONS",
        "ITEM_TYPE_NOT_SUPPORTED",
        "RATE_LIMITED",
        "METADATA_UNAVAILABLE",
        "TRANSIENT",
    }


def test_feasibility_class_has_the_four_documented_classes():
    assert {c.value for c in FeasibilityClass} == {
        "FULLY_FEASIBLE",
        "PARTIALLY_FEASIBLE",
        "NOT_FEASIBLE",
        "MANUAL_VALIDATION_REQUIRED",
    }


def test_make_check_id_is_stable_and_prefixed():
    prompt = "Ensure all semantic models have incremental refresh policies"
    first = make_check_id(prompt)
    assert first.startswith("CHK-")
    assert len(first) == len("CHK-") + 8
    assert first == make_check_id(prompt)  # deterministic


def test_make_check_id_normalises_whitespace_and_case():
    a = make_check_id("Ensure   Git integration is  enabled")
    b = make_check_id("ensure git integration is enabled")
    assert a == b


def test_make_check_id_differs_for_different_intent():
    assert make_check_id("enable git integration") != make_check_id(
        "disable git integration"
    )


def test_session_add_is_idempotent_per_prompt():
    session = CustomCheckSession()
    first = session.add("Ensure workspaces use Git integration")
    again = session.add("ensure   workspaces use git integration")
    assert first is again
    assert len(session.checks) == 1


def test_session_add_tracks_distinct_prompts():
    session = CustomCheckSession()
    session.add("enable git integration")
    session.add("disable public access")
    assert len(session.checks) == 2


def test_session_shares_one_kb_and_fetch_cache():
    session = CustomCheckSession()
    assert session.shared_kb == {}
    assert session.fetch_cache == {}
    session.shared_kb["semantic_models"] = [{"id": "sm1"}]
    # a second check in the same batch sees the augmented KB.
    session.add("some check")
    assert session.shared_kb["semantic_models"] == [{"id": "sm1"}]


def test_new_check_defaults_to_pending():
    check = CustomCheck(check_id="CHK-00000000", raw_prompt="x")
    assert check.lifecycle_status is LifecycleStatus.PENDING
    assert check.guardrail is None
    assert check.routing is None


def test_ledger_row_is_json_serialisable_view():
    check = CustomCheck(check_id="CHK-a1b2c3d4", raw_prompt="do a thing")
    check.lifecycle_status = LifecycleStatus.KB_AUGMENTED
    check.guardrail = GuardrailVerdict(passed=True, layer="regex")
    check.routing = RoutingResult(is_duplicate=False, similarity_score=0.28, stage="semantic")
    check.fetch_plan = FetchPlan(field="semantic_models[*].refresh_policy", confidence=0.9)
    check.kb_update = KbUpdateLog(
        attempt_count=1,
        status="SUCCESS",
        apis_called=["GET /v1/workspaces/{id}/semanticModels/{id}"],
        fields_added=["semantic_models[*].refresh_policy"],
    )
    check.feasibility = FeasibilityClass.FULLY_FEASIBLE

    row = check.to_dict()
    assert row["check_id"] == "CHK-a1b2c3d4"
    assert row["lifecycle_status"] == "KB_AUGMENTED"
    assert row["guardrail"]["passed"] is True
    assert row["routing"]["similarity_score"] == 0.28
    assert row["kb_update"]["diagnostic"] is None
    assert row["feasibility"] == "FULLY_FEASIBLE"


def test_kb_update_log_renders_diagnostic_enum_value():
    log = KbUpdateLog(
        attempt_count=3,
        status="FAILED",
        diagnostic=FetchErrorClass.INSUFFICIENT_PERMISSIONS,
        root_cause="service principal lacks Item.Read.All",
        remediation="grant Item.Read.All and re-run",
    )
    assert log.to_dict()["diagnostic"] == "INSUFFICIENT_PERMISSIONS"


def test_session_ledger_returns_one_row_per_check():
    session = CustomCheckSession()
    session.add("enable git integration")
    session.add("disable public access")
    ledger = session.ledger()
    assert len(ledger) == 2
    assert all("lifecycle_status" in row for row in ledger)
