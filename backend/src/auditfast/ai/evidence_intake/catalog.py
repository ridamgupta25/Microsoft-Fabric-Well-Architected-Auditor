"""The evidence catalog: what to upload for each manual check and how to grade it.

One entry per checklist point tells the intake form which document to ask for
(``ask_for`` + ``accepted_types``) and gives the evaluator a fixed 0-3 ``rubric``
so scoring is reproducible. The catalog is plain data shipped with the package
(``data/evidence_catalog.json``); it carries no code and no secrets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ...core.enums import Pillar

_RUNGS = frozenset({"0", "1", "2", "3"})
_CATALOG_PATH = Path(__file__).with_name("data") / "evidence_catalog.json"

# The checklist ref's leading section number -> the tool's pillar. Sections 1-12
# follow the shared taxonomy; 13 is Documentation and 14 (reporting/semantic)
# belongs to Data Modeling & Storage, so the surface shows the real 13 pillars.
_SECTION_PILLARS: dict[str, Pillar] = {
    "1": Pillar.ARCHITECTURE,
    "2": Pillar.DATA_INTEGRATION,
    "3": Pillar.DATA_PROCESSING,
    "4": Pillar.DATA_MODELING,
    "5": Pillar.DATA_QUALITY,
    "6": Pillar.SECURITY_ACCESS,
    "7": Pillar.COMPLIANCE,
    "8": Pillar.DATA_GOVERNANCE,
    "9": Pillar.RELIABILITY,
    "10": Pillar.MONITORING,
    "11": Pillar.DEVOPS,
    "12": Pillar.COST_MANAGEMENT,
    "13": Pillar.DOCUMENTATION,
    "14": Pillar.DATA_MODELING,
}


def _pillar_for(ref: str) -> str:
    section = ref.split(".", 1)[0]
    return str(_SECTION_PILLARS.get(section, Pillar.FOUNDATION))


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """What one manual check needs from the user and how it is scored."""

    ref: str
    title: str
    tier: str  # "A" | "B" | "C" | "D"
    ask_for: str
    accepted_types: tuple[str, ...]
    rubric: dict[str, str]  # keys "0".."3"
    pillar: str = ""  # the tool's pillar name, derived from the ref section
    cross_check: str | None = None  # Tier B: the KB field to compare against
    requires_attestation: bool = False  # Tier C: block a 3 until a human attests

    def intake_prompt(self) -> str:
        """The one-line 'upload this' instruction shown to the user."""
        return f"{self.ref} — {self.title}: upload {self.ask_for}"


def _build(entry: dict) -> EvidenceRequirement:
    ref = entry["ref"]
    rubric = entry["rubric"]
    if set(rubric) != _RUNGS:
        raise ValueError(f"{ref}: rubric must have exactly rungs 0-3")
    accepted = tuple(entry["accepted_types"])
    if not accepted:
        raise ValueError(f"{ref}: accepted_types must not be empty")
    return EvidenceRequirement(
        ref=ref,
        title=entry["title"],
        tier=entry["tier"],
        ask_for=entry["ask_for"],
        accepted_types=accepted,
        rubric=rubric,
        pillar=_pillar_for(ref),
        cross_check=entry.get("cross_check"),
        requires_attestation=entry.get("requires_attestation", False),
    )


@lru_cache(maxsize=1)
def load() -> dict[str, EvidenceRequirement]:
    """Load the catalog keyed by ref. Raises on a duplicate or malformed entry."""
    raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    out: dict[str, EvidenceRequirement] = {}
    for entry in raw:
        req = _build(entry)
        if req.ref in out:
            raise ValueError(f"duplicate ref {req.ref} in evidence catalog")
        out[req.ref] = req
    return out


def get(ref: str) -> EvidenceRequirement:
    """Look up one requirement by ref, or raise ``KeyError``."""
    return load()[ref]


__all__ = ["EvidenceRequirement", "load", "get"]
