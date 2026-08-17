"""Regression tests for ``PL-RETRY-VALUES`` (ref 2.4.2).

Three defects are pinned here, each paired with the case that must keep working
so a rewrite cannot silently reintroduce them:

1. **Nested activities were invisible.** The check read only the top-level
   activity list, so a Copy inside a ``ForEach`` - the commonest Fabric shape -
   was never judged, turning a real verdict into N/A and letting a bad nested
   retry hide behind a good top-level one.
2. **A dynamic retry raised.** ``retry`` may be an expression object, and
   ``1 <= {...} <= 10`` raises ``TypeError``. It must degrade to N/A, never crash
   and never score 0.
3. **The resource guard was missing.** Unreadable pipeline definitions must be
   N/A, per the N/A-not-FAIL rule.

The division of labour with ``PL-RETRY`` (2.4.1) is pinned too: 2.4.1 asks
*whether* an activity retries, 2.4.2 only judges the values of those that
already do. Neither may answer the other's question.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_prep.automated import (
    retry_policy,
    retry_values,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _act(name: str, *, retry=None, interval=None, activity_type: str = "Copy") -> dict:
    policy: dict = {"timeout": "0.12:00:00"}
    if retry is not None:
        policy["retry"] = retry
    if interval is not None:
        policy["retryIntervalInSeconds"] = interval
    return {"name": name, "type": activity_type, "policy": policy}


def _pipe(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


def _foreach(name: str, *children: dict) -> dict:
    return {"name": name, "type": "ForEach",
            "typeProperties": {"activities": list(children)}}


def _if(name: str, *, true_acts=(), false_acts=()) -> dict:
    return {"name": name, "type": "IfCondition",
            "typeProperties": {"ifTrueActivities": list(true_acts),
                               "ifFalseActivities": list(false_acts)}}


def _ctx(obj: dict, *, settings: dict | None = None,
         unavailable: set | None = None) -> CheckContext:
    workspace = WorkspaceContext(id="w", unavailable=unavailable or set())
    return CheckContext(workspace=workspace, settings=settings or {},
                        obj_name="pipeline", obj=obj)


# --------------------------------------------------------------------------
# 1. nested activities
# --------------------------------------------------------------------------

def test_retry_inside_foreach_is_judged_not_ignored():
    """The bug: a retrying Copy nested in a ForEach returned N/A."""
    verdict = retry_values(_ctx(_pipe(
        _foreach("per file", _act("copy", retry=3, interval=30)),
    )))
    assert verdict.status is not Status.NA
    assert verdict.score == 3
    assert "1 of 1" in verdict.evidence


def test_bad_nested_retry_cannot_hide_behind_a_good_top_level_one():
    """The false PASS: judging only the top level scored 1 of 1 instead of 1 of 2."""
    verdict = retry_values(_ctx(_pipe(
        _act("top", retry=3, interval=30),
        _foreach("loop", _act("nested", retry=3, interval=0)),
    )))
    assert "1 of 2" in verdict.evidence
    assert verdict.score is not None and verdict.score < 3


def test_activities_in_both_if_branches_are_judged():
    verdict = retry_values(_ctx(_pipe(
        _if("branch",
            true_acts=[_act("t", retry=2, interval=15)],
            false_acts=[_act("f", retry=2, interval=15)]),
    )))
    assert "2 of 2" in verdict.evidence
    assert verdict.score == 3


# --------------------------------------------------------------------------
# 2. dynamic (expression) retry values
# --------------------------------------------------------------------------

_EXPRESSION = {"value": "@pipeline().parameters.retries", "type": "Expression"}


def test_dynamic_retry_does_not_raise_and_reports_na():
    """The bug: ``1 <= {...} <= 10`` raised TypeError and killed the check."""
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=_EXPRESSION, interval=30))))
    assert verdict.status is Status.NA
    assert "expression" in verdict.evidence.lower()


def test_dynamic_interval_alongside_a_real_retry_is_excluded_not_scored_zero():
    verdict = retry_values(_ctx(_pipe(
        _act("static", retry=3, interval=30),
        _act("dynamic", retry=2, interval=_EXPRESSION),
    )))
    # The dynamic one is not statically knowable, so it is reported, not failed.
    assert "1 of 1" in verdict.evidence
    assert "run-time expression" in verdict.evidence
    assert verdict.score == 3


def test_boolean_retry_is_not_mistaken_for_a_number():
    """``True`` is an ``int`` in Python; it is not a retry count."""
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=True, interval=30))))
    assert verdict.status is Status.NA


# --------------------------------------------------------------------------
# inert configuration: a parameterised interval that can never take effect
# --------------------------------------------------------------------------

def test_dynamic_interval_with_zero_retry_is_surfaced_as_inert():
    """Someone parameterised the back-off and never enabled the retry."""
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=0, interval=_EXPRESSION))))
    assert verdict.status is Status.NA
    assert "can never take effect" in verdict.evidence


def test_inert_config_is_reported_alongside_a_real_verdict():
    verdict = retry_values(_ctx(_pipe(
        _act("real", retry=3, interval=30),
        _act("inert", retry=0, interval=_EXPRESSION),
    )))
    assert verdict.score == 3
    assert "1 of 1" in verdict.evidence
    assert "can never take effect" in verdict.evidence


def test_inert_config_is_never_scored_here():
    """Reporting it must not drag the ratio down - that is PL-RETRY's question."""
    inert_only = retry_values(_ctx(_pipe(
        _act("a", retry=3, interval=30),
        _act("b", retry=0, interval=_EXPRESSION),
        _act("c", retry=0, interval=_EXPRESSION),
    )))
    clean = retry_values(_ctx(_pipe(_act("a", retry=3, interval=30))))
    assert inert_only.score == clean.score == 3
    assert inert_only.coverage == clean.coverage


# --------------------------------------------------------------------------
# 3. the resource guard and the N/A-not-FAIL rule
# --------------------------------------------------------------------------

def test_unreadable_definitions_are_na_not_fail():
    verdict = retry_values(_ctx(
        _pipe(_act("copy", retry=99, interval=0)),
        unavailable={Resource.PIPELINE_DEFINITIONS},
    ))
    assert verdict.status is Status.NA
    assert "could not be read" in verdict.evidence


def test_no_retry_anywhere_is_na_not_fail():
    """Whether to retry at all is PL-RETRY's question, not this one's."""
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=0, interval=30))))
    assert verdict.status is Status.NA
    assert verdict.score is None


# --------------------------------------------------------------------------
# the values actually judged
# --------------------------------------------------------------------------

def test_missing_interval_fails():
    """The reachable failure: the property is absent, not zero.

    Fabric's portal rejects a Copy interval outside 30-86400, so ``interval: 0``
    is not portal-authorable. An *absent* interval still is - and is what a
    definition arriving through the REST API or Git can carry.
    """
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=3))))
    assert verdict.score == 0
    assert "0 of 1" in verdict.evidence


def test_count_above_the_house_limit_fails():
    verdict = retry_values(_ctx(_pipe(_act("copy", retry=50, interval=30))))
    assert verdict.score == 0


def test_house_limit_is_project_overridable():
    pipeline = _pipe(_act("copy", retry=50, interval=30))
    assert retry_values(_ctx(pipeline, settings={"max_retry_count": 100})).score == 3
    # A nonsense setting falls back to the default rather than raising.
    assert retry_values(_ctx(pipeline, settings={"max_retry_count": "abc"})).score == 0


# --------------------------------------------------------------------------
# dedup: 2.4.1 and 2.4.2 must not answer each other's question
# --------------------------------------------------------------------------

def test_241_and_242_are_not_duplicates():
    """No retry configured: 2.4.1 must FAIL it, 2.4.2 must abstain."""
    pipeline = _pipe(_act("copy", retry=0, interval=30))
    assert retry_policy(_ctx(pipeline)).score == 0
    assert retry_values(_ctx(pipeline)).status is Status.NA

    # Retry configured but with a broken interval: 2.4.1 is satisfied, 2.4.2 fails.
    pipeline = _pipe(_act("copy", retry=3, interval=0))
    assert retry_policy(_ctx(pipeline)).score == 3
    assert retry_values(_ctx(pipeline)).score == 0
