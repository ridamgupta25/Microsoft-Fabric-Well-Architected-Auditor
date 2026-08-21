"""AI-assisted re-judging of the advisory checks (mocked model).

The evaluator is best-effort: on with a working model it rewrites verdicts; off,
or on any failure, it keeps the deterministic verdict. No live model is called.
"""
from __future__ import annotations

import pytest

from auditfast.ai import advisory as ai_advisory
from auditfast.ai import orchestrator
from auditfast.core.advisory import ADVISORY_REFS
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Layer, Scope, Severity, Status
from auditfast.core.models import CheckResult


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
