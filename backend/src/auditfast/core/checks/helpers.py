"""What a check returns, and the helpers for building it.

A check does not construct a :class:`~auditfast.domain.models.CheckResult`. It
returns a small :class:`Verdict` — a score plus the evidence for it — and the
engine assembles the full result by combining that with the check's registered
:class:`~auditfast.domain.models.CheckSpec`.

That split is why a check body is now three lines instead of ten: the id, ref,
title, pillar, severity, workspace name, layer, and object name are all known
from the spec and the context, so no check has to repeat them.

    @check(id="WS-GIT", ref="11.1.2", ...)
    def git_enabled(ctx):
        connected = ctx.workspace.git_connected
        return binary(connected, "Connected to Git" if connected else "Not connected to Git")
"""
from __future__ import annotations

from dataclasses import dataclass

from ..enums import Status
from ..models import MAX_SCORE
from ..scoring import band_from_coverage


@dataclass(frozen=True, slots=True)
class Verdict:
    """A check's answer: a score, the evidence, and how to interpret them."""

    evidence: str
    score: int | None = None
    coverage: float | None = None
    scored: bool = True
    status: Status | None = None
    #: Overrides the object name in the result — only needed when one check
    #: emits several results for different objects.
    obj: str | None = None


# -- builders -----------------------------------------------------------------

def binary(ok: bool, evidence: str, *, obj: str | None = None) -> Verdict:
    """Done or not done. Scores 3 or 0 with no middle ground."""
    return Verdict(evidence=evidence, score=MAX_SCORE if ok else 0,
                   coverage=1.0 if ok else 0.0, obj=obj)


def covered(compliant: int, total: int, evidence: str, *, obj: str | None = None) -> Verdict:
    """*N of M* objects comply. An empty population is vacuously compliant.

    Guarding the empty case here — rather than in each check — is what removes
    the ``x / len(items) if items else 1.0`` idiom that every coverage check used
    to repeat, along with its zero-division risk.
    """
    ratio = 1.0 if total <= 0 else compliant / total
    ratio = max(0.0, min(1.0, ratio))
    return Verdict(evidence=evidence, score=band_from_coverage(ratio), coverage=ratio, obj=obj)


def graded(score: int, evidence: str, *, obj: str | None = None) -> Verdict:
    """Explicit 0-3 judgement, for rules with genuine middle ground."""
    return Verdict(evidence=evidence, score=max(0, min(MAX_SCORE, int(score))), obj=obj)


def note(evidence: str, *, obj: str | None = None) -> Verdict:
    """Report a fact without judging it. Never counts toward any score."""
    return Verdict(evidence=evidence, score=None, scored=False, status=Status.INFO, obj=obj)


def not_applicable(evidence: str, *, obj: str | None = None) -> Verdict:
    """The check could not be evaluated, so it is excluded from scoring.

    Use this when the data needed to judge was unavailable — which is different
    from the practice being absent, and must not score as a failure.
    """
    return Verdict(evidence=evidence, score=None, scored=False, status=Status.NA, obj=obj)


class RemediationBook:
    """Pre-written remediation text, keyed by checklist ``ref``.

    Replaces the previous module-level ``_REMEDIATION`` dict and its
    ``set_remediation()`` mutator, so two audits can run concurrently without
    trampling each other's guidance text.
    """

    __slots__ = ("_by_ref",)

    def __init__(self, mapping: dict | None = None) -> None:
        self._by_ref = dict(mapping or {})

    def get(self, ref: str) -> str:
        return self._by_ref.get(ref, "")

    def __len__(self) -> int:
        return len(self._by_ref)


EMPTY_REMEDIATION = RemediationBook()
