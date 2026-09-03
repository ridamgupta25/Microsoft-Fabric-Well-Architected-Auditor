"""Turn a reader's labels into a score, in code.

The advisory reader is asked one thing: *what is this object?* It answers with a
label from a fixed vocabulary. **It is never asked for a score**, and this module
is why - scoring is arithmetic over those labels, and arithmetic belongs in code.

Two things follow, and both matter:

* **A count cannot be invented.** Asking a model "how many of these 537 tables
  are dimensions?" invites a plausible-looking number from a model that saw 40.
  Asking it to label each table it is shown, and counting here, cannot.
* **Scoring is reproducible.** The same labels always produce the same score, so
  two runs can be compared - diff the labels and you can see exactly where the
  reader wobbled, which is impossible when it hands back a number.

The bands mirror :func:`auditfast.core.scoring.band_from_coverage` exactly, so an
advisory score means the same thing as a deterministic one.
"""
from __future__ import annotations

from collections import Counter

from ..core.judging import JudgingGuide
from ..core.scoring import band_from_coverage

#: The shapes A guide may name. Each says how labels become a 0-3 score.
SHAPES: frozenset[str] = frozenset(
    {"ratio", "binary", "pair", "worst", "graded", "best"}
)

#: A reader may say this about any object when the evidence does not support a
#: call. Such objects are excluded from the denominator rather than counted as
#: failures - the N/A-not-FAIL rule, applied per object.
UNDETERMINED = "undetermined"


class GuideError(ValueError):
    """A guide is internally inconsistent - caught at import, not at runtime."""


def validate(check_id: str, guide: JudgingGuide) -> None:
    """Raise when A guide could not produce a score. Called by the tests.

    A guide that names a label it did not declare, or a shape that does not
    exist, would fail silently at judging time - after a reader had done the
    work. Failing here costs nothing.
    """
    if guide.shape not in SHAPES:
        raise GuideError(f"{check_id}: shape {guide.shape!r} is not one of {sorted(SHAPES)}")
    if not guide.labels:
        raise GuideError(f"{check_id}: no labels declared")
    if UNDETERMINED in guide.labels:
        raise GuideError(f"{check_id}: {UNDETERMINED!r} is always allowed and must not be declared")

    if guide.shape in {"ratio", "binary"}:
        if not guide.compliant:
            raise GuideError(f"{check_id}: shape {guide.shape!r} needs a 'compliant' label")
        if guide.compliant not in guide.labels:
            raise GuideError(
                f"{check_id}: compliant label {guide.compliant!r} is not in {guide.labels}"
            )
    if guide.shape == "pair":
        if len(guide.pair) != 2:
            raise GuideError(f"{check_id}: shape 'pair' needs exactly two labels")
        missing = [label for label in guide.pair if label not in guide.labels]
        if missing:
            raise GuideError(f"{check_id}: pair label(s) {missing} not in {guide.labels}")
    if guide.shape in {"graded", "best"}:
        if len(guide.bands) != len(guide.labels):
            raise GuideError(
                f"{check_id}: shape {guide.shape!r} needs one band per label, got "
                f"{len(guide.bands)} for {len(guide.labels)} labels"
            )
        bad = [b for b in guide.bands if b not in (0, 1, 2, 3)]
        if bad:
            raise GuideError(f"{check_id}: band(s) {bad} are outside 0-3")

    missing = [label for label in guide.out_of_scope if label not in guide.labels]
    if missing:
        raise GuideError(
            f"{check_id}: out_of_scope label(s) {missing} are not in {guide.labels}"
        )
    if guide.compliant and guide.compliant in guide.out_of_scope:
        raise GuideError(
            f"{check_id}: {guide.compliant!r} is both the compliant label and "
            f"out of scope, so nothing could ever score"
        )


def score(guide: JudgingGuide, labels: list[str]) -> tuple[int | None, str]:
    """``(score, evidence)`` from one finding's labels.

    Returns ``(None, reason)`` when nothing could be judged, which the caller
    turns into N/A - keeping the deterministic verdict rather than inventing a
    zero.
    """
    counts = Counter(label.strip().lower() for label in labels if label and label.strip())
    undetermined = counts.pop(UNDETERMINED, 0)
    # Out-of-scope labels are judgments, not gaps, but they leave the
    # denominator all the same - the practice never applied to those objects.
    out_of_scope = sum(counts.pop(label, 0) for label in guide.out_of_scope)
    judged = sum(counts.values())

    if not judged:
        reason = (
            f"No object was in scope for this check ({out_of_scope} out of "
            f"scope, {undetermined} undetermined), so the deterministic verdict "
            f"stands"
            if out_of_scope else
            f"No object could be judged ({undetermined} undetermined), so the "
            f"deterministic verdict stands"
        )
        return None, reason

    # The share matters, not just the count. A check where 30 of 56 objects
    # could not be assessed scores the same as one where all 56 failed, so
    # without saying so the evidence cannot distinguish "this estate is bad"
    # from "we could only see half of it". Called out once it passes a third.
    total = judged + undetermined
    if not undetermined:
        tail = ""
    elif undetermined * 3 >= total:
        tail = (f". NOTE: {undetermined} of {total} object(s) could not be "
                f"judged ({undetermined / total:.0%}), so this score rests on "
                f"{judged} object(s)")
    else:
        tail = f". {undetermined} object(s) could not be judged"
    if out_of_scope:
        tail += f". {out_of_scope} object(s) were out of scope"

    if guide.shape == "ratio":
        compliant = counts.get(guide.compliant, 0)
        ratio = compliant / judged
        return band_from_coverage(ratio), (
            f"{compliant} of {judged} object(s) are {guide.compliant} "
            f"({ratio:.0%}){tail}"
        )

    if guide.shape == "binary":
        compliant = counts.get(guide.compliant, 0)
        return (3 if compliant else 0), (
            f"{compliant} of {judged} object(s) are {guide.compliant}{tail}"
        )

    if guide.shape == "pair":
        first, second = guide.pair
        have_first, have_second = counts.get(first, 0), counts.get(second, 0)
        present = bool(have_first) + bool(have_second)
        band = {2: 3, 1: 1, 0: 0}[present]
        return band, (
            f"{have_first} {first}(s) and {have_second} {second}(s) among "
            f"{judged} judged object(s){tail}"
        )

    if guide.shape in {"graded", "best"}:
        # Each label carries its own band, so a 3/2/0 check - "stops hard",
        # "exits softly", "carries on" - keeps the gap the deterministic
        # `graded()` helper gives it.
        #
        # `graded` takes the weakest, because one unhandled case defeats the
        # practice however many handled ones surround it. `best` takes the
        # strongest, for a workspace-level question - "does anything here
        # persist run history?" - where one good implementation is the answer
        # and the other objects are not failures, just silent.
        bands = dict(zip(guide.labels, guide.bands, strict=True))
        scored = {label: bands[label] for label in counts if label in bands}
        if not scored:
            return None, (
                f"No label mapped to a band ({judged} judged), so the "
                f"deterministic verdict stands"
            )
        pick = min if guide.shape == "graded" else max
        chosen = pick(scored, key=lambda label: scored[label])
        which = "weakest" if guide.shape == "graded" else "strongest"
        return scored[chosen], (
            f"{judged} object(s) judged; the {which} is '{chosen}'{tail}"
        )

    # "worst": the weakest object sets the score, for checks where one bad
    # object defeats the practice however many good ones surround it.
    order = list(guide.labels)
    worst = max((order.index(label) for label in counts if label in order), default=0)
    band = max(0, 3 - worst)
    return band, (
        f"{judged} object(s) judged; the weakest is '{order[worst]}'{tail}"
    )
