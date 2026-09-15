"""Node: grade one manual check from uploaded-document excerpts only.

The evaluator asks the model for a 0-3 score, a matched rung, citations, gaps and
a confidence — but the model only *proposes*. This module then **disproves**
fabricated quotes in code: a citation whose text is not actually present in the
retrieved chunks is dropped, and a score with no surviving citation is forced to
``NEEDS_EVIDENCE``. The model can never make an ungrounded score stick.

AI is optional: with no key the evaluator returns ``AI_REQUIRED`` rather than a
guess, matching the deterministic-first posture of the rest of the pipeline.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from ..evidence_intake.catalog import EvidenceRequirement
from ..evidence_intake.ingestion import Chunk
from ..evidence_intake.state import Citation, EvidenceCheck, EvidenceStatus
from ..orchestrator import complete, is_enabled
from ..orchestrator.ai_config import AiConfig

#: A quote shorter than this is too generic to prove anything, so it is ignored.
_MIN_QUOTE_CHARS = 12
_WS = re.compile(r"\s+")

Generator = Callable[[str, str], str | None]

_SYSTEM = (
    "You grade ONE Microsoft Fabric audit checklist point using only the supplied "
    "document excerpts. Cite ONLY text that appears verbatim in the excerpts — never "
    "invent a quote. Map the evidence to the given 0-3 rubric and name the rung. "
    "If the excerpts do not support the point, return score null. "
    'Reply as strict JSON: {"score": 0-3 or null, "rung_matched": "0".."3" or null, '
    '"citations": [{"quote": "...", "source": "...", "page": 1}], '
    '"gaps": ["..."], "recommendations": ["..."], "confidence": 0.0-1.0}. '
    "Recommendations are short, concrete steps to reach the next rung."
)


def _build_user(req: EvidenceRequirement, chunks: list[Chunk]) -> str:
    rubric = "\n".join(f"  {rung}: {text}" for rung, text in sorted(req.rubric.items()))
    excerpts = "\n\n".join(
        f"[source: {c.source} | page: {c.page}]\n{c.text}" for c in chunks
    )
    return (
        f"CHECK {req.ref}: {req.title}\n\n"
        f"RUBRIC (0-3):\n{rubric}\n\n"
        f"DOCUMENT EXCERPTS:\n{excerpts}"
    )


def _normalise(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _parse(raw: str) -> dict | None:
    """Pull the JSON object out of a model reply, tolerating stray prose/fences."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _grounded_citations(data: dict, chunks: list[Chunk]) -> list[Citation]:
    """Keep only citations whose quote really appears in the retrieved chunks."""
    haystack = _normalise("\n".join(c.text for c in chunks))
    by_source = {(c.source, c.page): c for c in chunks}
    out: list[Citation] = []
    for item in data.get("citations", []) or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "")).strip()
        if len(quote) < _MIN_QUOTE_CHARS or _normalise(quote) not in haystack:
            continue
        source = str(item.get("source", "")).strip()
        try:
            page = int(item.get("page", 0))
        except (TypeError, ValueError):
            page = 0
        if (source, page) not in by_source:  # snap to a real chunk location
            snapped = next((c for c in chunks if _normalise(quote) in _normalise(c.text)), chunks[0])
            source, page = snapped.source, snapped.page
        out.append(Citation(quote=quote, source=source, page=page))
    return out


def evaluate(
    req: EvidenceRequirement,
    chunks: list[Chunk],
    *,
    ai: AiConfig | None = None,
    generator: Generator | None = None,
) -> EvidenceCheck:
    """Draft a grounded 0-3 result for one check, or withhold the score."""
    if not chunks:
        return EvidenceCheck(
            ref=req.ref, title=req.title, status=EvidenceStatus.NEEDS_EVIDENCE,
            gaps=[f"No document provided for {req.ask_for}."],
        )
    gen = generator or (lambda system, user: complete(system, user, ai=ai, max_tokens=700))
    if generator is None and not is_enabled(ai):
        return EvidenceCheck(
            ref=req.ref, title=req.title, status=EvidenceStatus.AI_REQUIRED,
            gaps=["Provide an AI key to draft this check."],
        )

    raw = gen(_SYSTEM, _build_user(req, chunks))
    data = _parse(raw) if raw else None
    if data is None:
        return EvidenceCheck(
            ref=req.ref, title=req.title, status=EvidenceStatus.NEEDS_EVIDENCE,
            gaps=["The evaluator could not produce a grounded result."],
        )

    grounded = _grounded_citations(data, chunks)
    score = data.get("score")
    gaps = [str(g) for g in (data.get("gaps") or []) if str(g).strip()]
    recommendations = [str(r) for r in (data.get("recommendations") or []) if str(r).strip()]

    if not isinstance(score, (int, float)) or int(score) <= 0 or not grounded:
        return EvidenceCheck(
            ref=req.ref, title=req.title, status=EvidenceStatus.NEEDS_EVIDENCE,
            gaps=gaps or ["No grounded evidence found in the uploaded document."],
            recommendations=recommendations,
        )

    score = max(0, min(3, int(score)))
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    rung = data.get("rung_matched")
    return EvidenceCheck(
        ref=req.ref, title=req.title, status=EvidenceStatus.DRAFTED,
        score=score, rung_matched=str(rung) if rung is not None else None,
        citations=grounded, gaps=gaps, recommendations=recommendations,
        confidence=confidence, requires_human=True,
    )


__all__ = ["evaluate", "Generator"]
