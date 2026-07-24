"""Scoring: coverage -> 0-3 band, rating bands, and roll-up aggregation.

Mirrors the rubric in 01-scoring-rubric.md:
  band:  100% -> 3, 80-99% -> 2, 50-79% -> 1, <50% -> 0
  rating: 0-40 Critical, 41-60 High, 61-75 Medium, 76-90 Good, 91-100 Excellent
"""
from __future__ import annotations

from typing import Iterable, Optional

from .models import CheckResult, Status, PILLARS


def band_from_coverage(coverage: float) -> int:
    if coverage >= 1.0:
        return 3
    if coverage >= 0.8:
        return 2
    if coverage >= 0.5:
        return 1
    return 0


def status_from_score(score: int) -> Status:
    if score >= 3:
        return Status.PASS
    if score >= 1:
        return Status.PARTIAL
    return Status.FAIL


def rating(pct: Optional[float]):
    """Return (label, emoji) for a percentage, per the rubric bands."""
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


def _pct(results: Iterable[CheckResult]) -> Optional[float]:
    scored = [r for r in results if r.scored and r.score is not None]
    if not scored:
        return None
    return sum(r.score for r in scored) / (CheckResult.MAX_SCORE * len(scored)) * 100.0


def aggregate(results: list[CheckResult]) -> dict:
    """Compute overall, per-pillar and per-workspace scores + status counts."""
    scored = [r for r in results if r.scored and r.score is not None]

    by_pillar = {}
    for pillar in PILLARS:
        rs = [r for r in scored if r.pillar == pillar]
        by_pillar[pillar] = {"pct": _pct(rs), "count": len(rs)}

    workspaces = sorted({r.workspace for r in scored})
    by_workspace = {}
    for ws in workspaces:
        rs = [r for r in scored if r.workspace == ws]
        role = next((r.workspace_role for r in results if r.workspace == ws and r.workspace_role), "")
        by_workspace[ws] = {
            "role": role,
            "pct": _pct(rs),
            "count": len(rs),
            "by_pillar": {p: _pct([r for r in rs if r.pillar == p]) for p in PILLARS},
        }

    counts = {s: sum(1 for r in results if r.status == s) for s in Status}

    return {
        "overall": _pct(scored),
        "by_pillar": by_pillar,
        "by_workspace": by_workspace,
        "counts": counts,
        "total_scored": len(scored),
    }
