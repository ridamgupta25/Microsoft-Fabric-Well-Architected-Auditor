"""Deterministic checklist-point -> existing-check matcher.

The first question the intake pipeline asks about a user-supplied checklist point
is *"do we already assess this?"*. That answer must be the same every time — no
model, no network — so this matcher is **pure Python over the registered
``CheckSpec`` metadata**. It is always available, even with the ``ai`` extra
uninstalled and ``settings.ai_enabled = False``.

This is intentionally *not* in :mod:`auditfast.core`: it reads the registry but is
part of the additive AI/intake layer, so the determinism boundary (core never
imports ai) is preserved. Nothing here influences a score — matching only decides
whether to route a point to the existing catalog or to the authoring path.

Scoring is a weighted token overlap between the user's phrase and each check's
searchable text (id, ref, title, description, pillar, scope). It favours
precision-friendly signals — an exact ``ref`` hit, a title-phrase hit — over
loose single-token overlap, so "git integration" lands on ``WS-GIT`` rather than
on every check that merely mentions "integration".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.check.registry import REGISTRY, CheckRegistry
from ..core.models import CheckSpec

#: Confidence at or above which a point is considered already covered by an
#: existing check. Tuned so a close paraphrase of a real check title clears it
#: while a genuinely new point does not.
DEFAULT_MATCH_THRESHOLD = 0.45

_TOKEN = re.compile(r"[a-z0-9]+")
_REF = re.compile(r"\b\d+(?:\.\d+){1,4}\b")

#: Words carrying no discriminating signal for a Fabric best-practice phrase.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "have", "in", "into", "is", "it", "its", "must", "of", "on", "or", "should",
        "that", "the", "their", "them", "then", "there", "these", "this", "to",
        "use", "used", "using", "via", "when", "which", "with", "within", "all",
        "any", "each", "ensure", "ensures", "enabled", "enable", "make", "sure",
        "set", "check", "checks", "verify", "verified", "every", "must-have",
        "workspace", "workspaces",  # too common across checks to discriminate
    }
)


@dataclass(frozen=True, slots=True)
class CheckMatch:
    """One existing check that resembles a submitted checklist point."""

    spec: CheckSpec
    confidence: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "check_id": self.spec.id,
            "ref": self.spec.ref,
            "title": self.spec.title,
            "pillar": self.spec.pillar.value,
            "scope": self.spec.scope.value,
            "severity": self.spec.severity.value,
            "automation": self.spec.automation.value,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS}


def _ref_in(text: str) -> str | None:
    match = _REF.search(text)
    return match.group(0) if match else None


def _haystack(spec: CheckSpec) -> str:
    return " ".join(
        [spec.id, spec.ref, spec.title, spec.description or "", spec.pillar.value, spec.scope.value]
    )


def match_point(
    point: str,
    registry: CheckRegistry = REGISTRY,
    *,
    limit: int = 5,
) -> list[CheckMatch]:
    """Rank existing checks by how closely they cover ``point``.

    Returns at most ``limit`` matches, highest confidence first, with a stable
    tie-break on check id so the same input always yields the same ordering.
    """
    query = _tokens(point)
    query_ref = _ref_in(point)
    if not query and not query_ref:
        return []

    matches: list[CheckMatch] = []
    for spec in registry:
        hay = _tokens(_haystack(spec))
        overlap = query & hay
        ref_hit = bool(query_ref) and spec.ref == query_ref
        if not overlap and not ref_hit:
            continue

        coverage = len(overlap) / len(query) if query else 0.0
        title_tokens = _tokens(spec.title)
        title_hits = len(query & title_tokens)
        title_bonus = 0.2 * (title_hits / len(query)) if query else 0.0
        ref_bonus = 0.5 if ref_hit else 0.0
        confidence = min(1.0, 0.7 * coverage + title_bonus + ref_bonus)

        bits = []
        if ref_hit:
            bits.append(f"ref {spec.ref} matches exactly")
        if overlap:
            bits.append("shared terms: " + ", ".join(sorted(overlap)))
        matches.append(CheckMatch(spec, confidence, "; ".join(bits) or "related"))

    matches.sort(key=lambda m: (-m.confidence, m.spec.id))
    return matches[:limit]


def best_match(point: str, registry: CheckRegistry = REGISTRY) -> CheckMatch | None:
    """The single closest existing check, or ``None`` when nothing overlaps."""
    matches = match_point(point, registry, limit=1)
    return matches[0] if matches else None


def is_covered(
    matches: list[CheckMatch],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> bool:
    """True when the top match is confident enough to call the point covered."""
    return bool(matches) and matches[0].confidence >= threshold
