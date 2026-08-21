"""Node 3b (KB Updater) tests.

A fake read-only provider returns canned responses per strategy so the 3-strategy
loop, 429 backoff, diagnostics, feasibility, provenance, copy-on-write merge, and
fetch-cache dedup are all exercised without any network.
"""
from __future__ import annotations

import pytest

from auditfast.ai.agents.kb_updater_agent import (
    STRATEGIES,
    FetchResponse,
    augment,
    classify_error,
)
from auditfast.ai.orchestrator.state import (
    CustomCheck,
    CustomCheckSession,
    FeasibilityClass,
    FetchErrorClass,
    FetchPlan,
    LifecycleStatus,
)


class FakeProvider:
    """Returns queued/canned responses per strategy and records call order."""

    def __init__(self, responses: dict, default: FetchResponse | None = None):
        self._responses = responses
        self._default = default or FetchResponse(404)
        self.calls: list[str] = []

    def fetch(self, plan, strategy):
        self.calls.append(strategy)
        r = self._responses.get(strategy, self._default)
        if isinstance(r, list):
            return r.pop(0) if r else self._default
        return r


def _check(field_path: str = "refresh_schedules", *, mandatory: bool = True) -> CustomCheck:
    check = CustomCheck(check_id="CHK-1", raw_prompt="ensure refresh")
    check.fetch_plan = FetchPlan(
        field=field_path,
        resource="SEMANTIC_MODEL",
        endpoint="GET /v1/x",
        confidence=0.8,
        mandatory=mandatory,
    )
    return check


_VALID = {"Model A": {"enabled": True}}


def _noop(_seconds):
    return None


_NOOP = _noop


# -- success paths -------------------------------------------------------------

def test_success_on_first_strategy_augments_kb():
    provider = FakeProvider({"item_rest": FetchResponse(200, body=_VALID)})
    session = CustomCheckSession()
    check = augment(_check(), provider, session, sleeper=_NOOP)

    assert check.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert check.feasibility is FeasibilityClass.FULLY_FEASIBLE
    assert session.shared_kb["refresh_schedules"] == _VALID
    assert check.kb_update.status == "SUCCESS"
    assert check.kb_update.attempt_count == 1
    assert check.kb_update.fields_added == ["refresh_schedules"]
    assert check.kb_update.provenance[0]["source"] == "item_rest"
    assert session.fetch_cache["refresh_schedules"] is True


def test_success_on_second_strategy_after_first_fails():
    provider = FakeProvider(
        {"item_rest": FetchResponse(404), "git_artifact": FetchResponse(200, body=_VALID)}
    )
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert check.kb_update.attempt_count == 2
    assert provider.calls == ["item_rest", "git_artifact"]


# -- 429 backoff ---------------------------------------------------------------

def test_429_retries_same_strategy_then_succeeds(monkeypatch):
    slept: list[float] = []
    provider = FakeProvider(
        {"item_rest": [FetchResponse(429, retry_after=0), FetchResponse(429, retry_after=0),
                       FetchResponse(200, body=_VALID)]}
    )
    check = augment(_check(), provider, CustomCheckSession(), sleeper=slept.append)
    assert check.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert check.kb_update.attempt_count == 1          # 429 did not burn a strategy
    assert provider.calls == ["item_rest", "item_rest", "item_rest"]
    assert len(slept) == 2


def test_persistent_429_reports_rate_limited():
    provider = FakeProvider({}, default=FetchResponse(429, retry_after=0))
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED
    assert check.kb_update.diagnostic is FetchErrorClass.RATE_LIMITED
    assert check.feasibility is FeasibilityClass.NOT_FEASIBLE


# -- failure diagnostics -------------------------------------------------------

def test_403_all_trials_is_insufficient_permissions():
    provider = FakeProvider({}, default=FetchResponse(403))
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.kb_update.diagnostic is FetchErrorClass.INSUFFICIENT_PERMISSIONS
    assert check.feasibility is FeasibilityClass.NOT_FEASIBLE
    assert "permission" in check.kb_update.remediation.lower()


def test_404_all_trials_is_item_type_not_supported():
    provider = FakeProvider({}, default=FetchResponse(404))
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.kb_update.diagnostic is FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED


def test_200_but_empty_is_metadata_unavailable_and_manual():
    provider = FakeProvider({}, default=FetchResponse(200, body={}))
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.kb_update.diagnostic is FetchErrorClass.METADATA_UNAVAILABLE
    assert check.feasibility is FeasibilityClass.MANUAL_VALIDATION_REQUIRED


def test_200_wrong_shape_fails_validator():
    # git_connected requires a bool; an int is present-but-invalid.
    provider = FakeProvider({}, default=FetchResponse(200, body=123))
    check = augment(_check("git_connected"), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED
    assert check.kb_update.diagnostic is FetchErrorClass.METADATA_UNAVAILABLE


def test_non_json_body_is_rejected_before_merge():
    provider = FakeProvider({}, default=FetchResponse(200, body=object()))
    session = CustomCheckSession()
    check = augment(_check(), provider, session, sleeper=_NOOP)
    assert check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED
    assert "refresh_schedules" not in session.shared_kb   # nothing poisoned the KB


def test_5xx_is_transient():
    provider = FakeProvider({}, default=FetchResponse(503))
    check = augment(_check(), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.kb_update.diagnostic is FetchErrorClass.TRANSIENT


# -- merge / cache / feasibility ----------------------------------------------

def test_merge_is_copy_on_write_by_item_id():
    session = CustomCheckSession()
    session.shared_kb["reports"] = [{"id": "r1", "name": "old"}]
    body = [{"id": "r1", "name": "new"}, {"id": "r2", "name": "added"}]
    provider = FakeProvider({"item_rest": FetchResponse(200, body=body)})
    augment(_check("reports"), provider, session, sleeper=_NOOP)
    reports = {r["id"]: r["name"] for r in session.shared_kb["reports"]}
    assert reports == {"r1": "new", "r2": "added"}


def test_fetch_cache_dedups_across_checks():
    session = CustomCheckSession()
    provider = FakeProvider({"item_rest": FetchResponse(200, body=_VALID)})
    augment(_check(), provider, session, sleeper=_NOOP)
    assert provider.calls == ["item_rest"]
    # Second check, same field -> served from cache, provider not called again.
    second = augment(_check(), provider, session, sleeper=_NOOP)
    assert provider.calls == ["item_rest"]
    assert second.lifecycle_status is LifecycleStatus.KB_AUGMENTED


def test_optional_field_failure_is_partially_feasible():
    provider = FakeProvider({}, default=FetchResponse(403))
    check = augment(_check(mandatory=False), provider, CustomCheckSession(), sleeper=_NOOP)
    assert check.lifecycle_status is LifecycleStatus.KB_FETCH_FAILED
    assert check.feasibility is FeasibilityClass.PARTIALLY_FEASIBLE


# -- guards --------------------------------------------------------------------

def test_augment_ignores_check_without_a_fetch_plan():
    check = CustomCheck(check_id="CHK-2", raw_prompt="x")  # no fetch_plan
    out = augment(check, FakeProvider({}), CustomCheckSession(), sleeper=_NOOP)
    assert out.kb_update is None
    assert out.lifecycle_status is LifecycleStatus.PENDING


def test_augment_ignores_non_pending_check():
    check = _check()
    check.lifecycle_status = LifecycleStatus.PROCESSED_CUSTOM
    out = augment(check, FakeProvider({}), CustomCheckSession(), sleeper=_NOOP)
    assert out.kb_update is None


# -- classifier ----------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        (403, FetchErrorClass.INSUFFICIENT_PERMISSIONS),
        (400, FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED),
        (404, FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED),
        (429, FetchErrorClass.RATE_LIMITED),
        (500, FetchErrorClass.TRANSIENT),
        (0, FetchErrorClass.TRANSIENT),
        (418, FetchErrorClass.TRANSIENT),
    ],
)
def test_classify_error(status, expected):
    assert classify_error(status) is expected


def test_strategies_are_three_distinct_sources():
    assert STRATEGIES == ("item_rest", "git_artifact", "workspace_bundle")
