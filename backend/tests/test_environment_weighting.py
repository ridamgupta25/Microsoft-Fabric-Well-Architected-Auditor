"""Step 3: cross-workspace environment weighting.

The roll-up already multiplies each check by its ``weight``; environment
weighting sets that weight from a workspace's environment level (1..10). These
tests pin the two invariants the feature promises:

* an *unweighted* run (no weights, or all weights 1.0) is byte-for-byte the
  unweighted mean the tool has always produced, and
* a *weighted* run makes a high-environment workspace's checks dominate the
  overall exactly as the ``Σ(score×weight) / Σ(MAX×weight)`` formula predicts,
  while each workspace's own score is unchanged.
"""
from __future__ import annotations

from auditfast.core.enums import Layer, Pillar, Status
from auditfast.core.models import MAX_SCORE, CheckResult
from auditfast.core.scoring import aggregate, percentage
from auditfast.services.audit_service import _resolve_weights


def _result(workspace: str, score: int, weight: float = 1.0) -> CheckResult:
    return CheckResult(
        check_id=f"C-{workspace}-{score}", ref="1.1", title="t",
        pillar=Pillar.SECURITY, status=Status.PASS if score >= MAX_SCORE else Status.FAIL,
        score=score, workspace=workspace, layer=Layer.MIXED, weight=weight,
    )


# -- _resolve_weights ---------------------------------------------------------

def test_weights_are_none_when_disabled():
    ws = [{"id": "a", "environment_level": 10}]
    assert _resolve_weights(ws, enabled=False) is None


def test_weights_are_none_without_levels():
    ws = [{"id": "a"}, {"id": "b", "role": "Mixed"}]
    assert _resolve_weights(ws, enabled=True) is None


def test_weights_map_level_to_weight_one_to_one():
    ws = [
        {"id": "dev", "environment_level": 1},
        {"id": "prod", "environment_level": 10},
        {"id": "solo"},  # isolated — no level, defaults to 1.0 (absent from map)
    ]
    assert _resolve_weights(ws, enabled=True) == {"dev": 1.0, "prod": 10.0}


# -- the weighting maths (the worked example) ---------------------------------

def test_unweighted_is_the_plain_mean():
    """DEV perfect, PROD failing, equal weights -> 50%."""
    results = [_result("DEV", 3) for _ in range(4)] + [_result("PROD", 0) for _ in range(4)]
    assert percentage(results) == 50.0


def test_prod_failing_drags_the_weighted_overall_down():
    """DEV(=1) perfect, PROD(=10) failing -> 12 / 132 == 9.09%."""
    results = (
        [_result("DEV", 3, weight=1.0) for _ in range(4)]
        + [_result("PROD", 0, weight=10.0) for _ in range(4)]
    )
    assert percentage(results) == 12 / 132 * 100


def test_prod_healthy_lifts_the_weighted_overall():
    """DEV(=1) failing, PROD(=10) perfect -> 120 / 132 == 90.9%."""
    results = (
        [_result("DEV", 0, weight=1.0) for _ in range(4)]
        + [_result("PROD", 3, weight=10.0) for _ in range(4)]
    )
    assert percentage(results) == 120 / 132 * 100


def test_per_workspace_scores_are_unchanged_by_weighting():
    """A uniform weight across a workspace cancels in that workspace's own score."""
    results = (
        [_result("DEV", 3, weight=1.0) for _ in range(4)]
        + [_result("PROD", 0, weight=10.0) for _ in range(4)]
    )
    by_workspace = aggregate(results)["by_workspace"]
    assert by_workspace["DEV"]["pct"] == 100.0
    assert by_workspace["PROD"]["pct"] == 0.0
