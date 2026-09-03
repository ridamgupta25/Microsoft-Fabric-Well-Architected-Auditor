"""AI-assisted re-judging of the advisory checks (mocked model).

The evaluator is best-effort: on with a working model it rewrites verdicts; off,
or on any failure, it keeps the deterministic verdict. No live model is called.
"""
from __future__ import annotations

import json
import re

import pytest

from auditfast.ai import advisory as ai_advisory
from auditfast.ai import orchestrator
from auditfast.core.advisory import ADVISORY_REFS
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Layer, Scope, Severity, Status
from auditfast.core.models import CheckResult, WorkspaceContext


def _advisory_result() -> CheckResult:
    spec = next(s for s in REGISTRY.all() if s.ref in ADVISORY_REFS)
    return CheckResult(
        check_id=spec.id,
        ref=spec.ref,
        title=spec.title,
        pillar=spec.pillar,
        status=Status.FAIL,
        score=0,
        evidence="heuristic said no pattern found",
        recommendation="deterministic recommendation",
        severity=Severity.MEDIUM,
        workspace="Workspace A",
        layer=Layer.MIXED,
        obj="",
        scope=Scope.WORKSPACE,
    )


def test_ai_off_leaves_advisory_results_unchanged(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: False)
    result = _advisory_result()

    out = ai_advisory.evaluate([result], {})

    assert out == [result]


def test_ai_on_rewrites_the_verdict(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(
        orchestrator,
        "complete",
        lambda system, user, **kw: (
            '{"score": 2, "evidence": "star schema mostly followed", '
            '"recommendation": "split the wide table", "confidence": "high"}'
        ),
    )
    result = _advisory_result()

    (judged,) = ai_advisory.evaluate([result], {})

    assert judged.score == 2
    assert judged.status is Status.PARTIAL
    assert judged.source == "advisory-ai"
    assert "star schema mostly followed" in judged.evidence
    assert "high confidence" in judged.evidence
    assert judged.recommendation == "split the wide table"


def test_bad_json_falls_back_to_the_deterministic_verdict(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", lambda system, user, **kw: "not json at all")
    result = _advisory_result()

    (out,) = ai_advisory.evaluate([result], {})

    assert out == result
    assert out.source == "automated"


def test_out_of_range_score_is_rejected(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", lambda system, user, **kw: '{"score": 9}')
    result = _advisory_result()

    (out,) = ai_advisory.evaluate([result], {})

    assert out == result


@pytest.mark.parametrize("score,status", [(3, Status.PASS), (1, Status.PARTIAL), (0, Status.FAIL)])
def test_score_maps_to_status(monkeypatch, score, status):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(
        orchestrator, "complete", lambda system, user, **kw: f'{{"score": {score}}}'
    )

    (out,) = ai_advisory.evaluate([_advisory_result()], {})

    assert out.score == score
    assert out.status is status


# -- the guide-driven path ----------------------------------------------------
#
# Where a check has a judging guide the keyed path must behave like the offline
# agent: the model returns LABELS and `classify` does the arithmetic. These
# tests pin that split, because it is the whole reason the model cannot invent a
# number here.

STAR = "TB-STARSCHEMA"


def _star_workspace() -> WorkspaceContext:
    return WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.DimCustomer": {"columns": [
            {"name": "customer_key"}, {"name": "city"}, {"name": "country"},
        ]},
        "dbo.FactSales": {"columns": [
            {"name": "sales_key"}, {"name": "customer_key"},
            {"name": "product_key"}, {"name": "amount", "type": "decimal"},
        ]},
    })


def _star_result() -> CheckResult:
    spec = REGISTRY.get(STAR)
    return CheckResult(
        check_id=STAR, ref=spec.ref, title=spec.title, pillar=spec.pillar,
        status=Status.FAIL, score=0, evidence="Star-schema naming not detected",
        severity=Severity.MEDIUM, workspace="WS", layer=Layer.MIXED,
        obj="", scope=Scope.WORKSPACE,
    )


def _label_reply(decide):
    """A model that answers with labels only - never a score.

    Object ids are read back out of the prompt rather than hard-coded, so the
    test does not depend on how the evidence builder happens to name a table.
    """
    def _complete(system, user, **kw):
        ids = re.findall(r"^--- OBJECT: (.*)$", user, re.MULTILINE)
        rows = [
            {"object": obj, "label": decide(obj), "reason": "test", "confidence": "high"}
            for obj in ids
            if decide(obj)
        ]
        return json.dumps(rows)
    return _complete


def _star_label(obj: str) -> str:
    lowered = obj.lower()
    if "fact" in lowered:
        return "fact"
    if "dim" in lowered:
        return "dimension"
    return "neither"


def test_a_guided_check_is_scored_from_labels_not_from_the_model(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", _label_reply(_star_label))

    (judged,) = ai_advisory.evaluate([_star_result()], {"WS": _star_workspace()})

    # The reply carried no score at all, so a score here can only have come from
    # `classify`: both halves of the fact/dimension pair were labelled.
    assert judged.score == 3
    assert judged.status is Status.PASS
    assert judged.source != "automated"


def test_a_label_outside_the_guide_is_ignored(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", _label_reply(lambda obj: "not_a_real_label"))

    (out,) = ai_advisory.evaluate([_star_result()], {"WS": _star_workspace()})

    # Nothing survived validation, so the deterministic verdict stands rather
    # than a made-up one.
    assert out.score == 0
    assert out.status is Status.FAIL


def test_an_unparseable_reply_keeps_the_deterministic_verdict(monkeypatch):
    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", lambda system, user, **kw: "sorry, no")

    (out,) = ai_advisory.evaluate([_star_result()], {"WS": _star_workspace()})

    assert out.score == 0
    assert out.status is Status.FAIL


def test_the_model_is_never_asked_for_a_score_on_a_guided_check(monkeypatch):
    seen: list[str] = []

    def _capture(system, user, **kw):
        seen.append(system)
        return "[]"

    monkeypatch.setattr(orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "complete", _capture)

    ai_advisory.evaluate([_star_result()], {"WS": _star_workspace()})

    assert seen, "the model should have been called"
    for system in seen:
        assert "scoring is not your job" in system
        assert '"score"' not in system
