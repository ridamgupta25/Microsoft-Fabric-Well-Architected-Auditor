"""Scoring: coverage bands, rating bands, and the roll-up.

Mirrors the rubric in ``01-scoring-rubric.md``:

* band — 100% -> 3, 80-99% -> 2, 50-79% -> 1, below 50% -> 0
* rating — 0-40 Critical, 41-60 High, 61-75 Medium, 76-90 Good, 91-100 Excellent

Roll-up is **weighted**::

    percentage = Σ(score × weight) / Σ(MAX_SCORE × weight) × 100

Every check currently carries ``weight = 1.0``, so this reduces exactly to the
unweighted mean the tool has always produced. The mechanism exists so per-check
or per-area weighting can be introduced later without a rewrite; changing the
actual weights is a scoring-policy decision, not a refactor.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from .enums import Layer, Pillar, Status
from .models import MAX_SCORE, CheckResult


def band_from_coverage(coverage: float) -> int:
    """Map a 0..1 compliance ratio onto the 0-3 rubric band."""
    if coverage >= 1.0:
        return 3
    if coverage >= 0.8:
        return 2
    if coverage >= 0.5:
        return 1
    return 0


def status_from_score(score: int) -> Status:
    if score >= MAX_SCORE:
        return Status.PASS
    if score >= 1:
        return Status.PARTIAL
    return Status.FAIL


def rating(pct: float | None) -> tuple[str, str]:
    """Return ``(label, emoji)`` for a percentage.

    These are **risk** bands, so the label runs opposite to the number: a
    "Critical" rating means critical risk, i.e. a score of 40% or below.
    """
    if pct is None:
        return ("Not assessed", "⚪")
    if pct >= 91:
        return ("Excellent", "🔵")
    if pct >= 76:
        return ("Good", "🟢")
    if pct >= 61:
        return ("Medium", "🟡")
    if pct >= 41:
        return ("High", "🟠")
    return ("Critical", "🔴")


def scored_only(results: Iterable[CheckResult]) -> list[CheckResult]:
    """The results that carry a score — informational rows and errors excluded."""
    return [r for r in results if r.counts_toward_score]


def percentage(results: Iterable[CheckResult]) -> float | None:
    """Weighted score of a result set, or ``None`` when nothing was assessed.

    ``None`` and ``0.0`` mean different things and must stay distinguishable:
    *not assessed* is not the same as *assessed and failed everything*.
    """
    scored = scored_only(results)
    if not scored:
        return None
    earned = sum((r.score or 0) * r.weight for r in scored)
    possible = sum(MAX_SCORE * r.weight for r in scored)
    if possible <= 0:
        return None
    return earned / possible * 100.0


def aggregate(results: Sequence[CheckResult]) -> dict:
    """Compute every number the scorecard, reports, and API expose.

    Returns overall, per-pillar, per-workspace, per-layer, and the pillar×layer
    matrix — the last two being the "inner pillar" view: how each layer of the
    architecture scores against each pillar.
    """
    scored = scored_only(results)

    by_pillar = {}
    for pillar in Pillar.scored():
        subset = [r for r in scored if r.pillar is pillar]
        by_pillar[pillar.value] = {"pct": percentage(subset), "count": len(subset)}

    by_workspace = {}
    for name in sorted({r.workspace for r in scored}):
        subset = [r for r in scored if r.workspace == name]
        layer = next((r.layer for r in results if r.workspace == name), Layer.MIXED)
        by_workspace[name] = {
            "role": layer.value,
            "layer": layer.value,
            "pct": percentage(subset),
            "count": len(subset),
            "by_pillar": {
                p.value: percentage([r for r in subset if r.pillar is p])
                for p in Pillar.scored()
            },
        }

    present_layers = [layer for layer in Layer.assignable()
                      if any(r.layer is layer for r in scored)]

    by_layer = {}
    for layer in present_layers:
        subset = [r for r in scored if r.layer is layer]
        by_layer[layer.value] = {"pct": percentage(subset), "count": len(subset)}

    # The pillar x layer matrix — rows are pillars, columns are layers.
    matrix = {
        p.value: {
            layer.value: percentage(
                [r for r in scored if r.pillar is p and r.layer is layer]
            )
            for layer in present_layers
        }
        for p in Pillar.scored()
    }

    counts = {status.value: sum(1 for r in results if r.status is status) for status in Status}

    return {
        "overall": percentage(scored),
        "by_pillar": by_pillar,
        "by_workspace": by_workspace,
        "by_layer": by_layer,
        "matrix": matrix,
        "layers": [layer.value for layer in present_layers],
        "counts": counts,
        "total_scored": len(scored),
    }
