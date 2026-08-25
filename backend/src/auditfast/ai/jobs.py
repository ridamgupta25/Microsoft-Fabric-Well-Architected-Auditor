"""One judging job per advisory check: the rule, the instruction sheet, and
**every** object it must look at.

The unit of work is the *check*, not the finding. A theme mixes five questions
across hundreds of findings, and switching between them is where a reader starts
to drift; one check at a time means one question held in mind throughout.

Objects are split into chunks that fit a prompt, but **nothing is sampled** - a
537-table estate becomes eight chunks, a 5,000-table estate becomes seventy-five,
and in both cases every object is judged. That is the difference between "we
looked at 40" and "we looked at all of them", and it is the reason a check can be
trusted to catch what the rule missed as well as what it wrongly flagged.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from ..core.advisory import (
    question_for,
    reason_for,
    theme_of,
)
from ..core.enums import Scope
from ..core.judging import LABELLING_RULES, guide_for
from ..core.models import CheckResult, WorkspaceContext
from . import evidence as evidence_builders

#: Characters per chunk. Sized so a chunk plus the instruction sheet sits well
#: inside a prompt, leaving room for the reader's own working.
CHUNK_CHARS = 18000

#: The finding name a workspace-scoped check reports under when it names no
#: object. Matches what the engine puts in the report's object column.
WORKSPACE_FINDING = "(workspace)"

JOBS_DIRNAME = "jobs"
MANIFEST_NAME = "advisory-manifest.json"


def _chunk(records: list[dict]) -> list[list[dict]]:
    """Split records into prompt-sized groups, keeping every one."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for record in records:
        cost = len(record.get("id", "")) + len(record.get("facts", "")) + 40
        if current and size + cost > CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(record)
        size += cost
    if current:
        chunks.append(current)
    return chunks


def build_job(
    check_id: str,
    results: list[CheckResult],
    workspace: WorkspaceContext,
) -> dict | None:
    """The judging job for one check, or ``None`` when it has no guide yet.

    A check without a guide is still advisory - it reaches the report with its
    deterministic verdict - so guides can be added one at a time without
    stranding anything.
    """
    guide = guide_for(check_id)
    if guide is None or not results:
        return None

    from ..core.check.registry import REGISTRY

    first = results[0]
    ref = first.ref
    spec = REGISTRY.get(check_id)
    objects = evidence_builders.build(guide.evidence, workspace)
    if not objects:
        return None

    # Every object belongs to the finding whose score it contributes to. For a
    # workspace-scoped check that is one finding fed by all of them; for an
    # object-scoped check each object is its own finding. Pinning them all to
    # the first result would collapse 40 notebooks into one verdict and leave
    # 39 findings unjudged, so the mapping is built from the scope.
    if first.scope is Scope.WORKSPACE:
        by_finding = {(first.obj or WORKSPACE_FINDING): first}
        for record in objects:
            record["finding"] = first.obj or WORKSPACE_FINDING
    else:
        by_finding = {r.obj: r for r in results if r.obj}
        objects = [r for r in objects if r["id"] in by_finding]
        if not objects:
            return None
        for record in objects:
            record["finding"] = record["id"]
            # The reader is told to treat `rule_says` as the rule's claim and
            # check it. For an object-scoped check the rule reaches a verdict
            # per object, and that verdict - not the descriptive fact the
            # builder happened to derive - is the claim worth checking. Without
            # this the field read "5 activities", which is nothing to agree or
            # disagree with, and a reader had to dig into `findings` to find
            # what the rule actually concluded.
            verdict = by_finding[record["id"]]
            record["rule_says"] = (
                f"{verdict.status.value}: {verdict.evidence}"
                if verdict.evidence else verdict.status.value
            )

    chunks = _chunk(objects)
    return {
        "check_id": check_id,
        "ref": ref,
        "title": first.title,
        "theme": theme_of(ref),
        "question": question_for(theme_of(ref)),
        "workspace": first.workspace,
        "rule": (spec.description or (spec.fn.__doc__ or "")).strip() if spec else "",
        "why_advisory": reason_for(ref),
        "instruction": guide.classify,
        "labels": list(guide.labels),
        "undetermined_label": "undetermined",
        "how_it_is_scored": _scoring_sentence(guide),
        # What the rule concluded overall. For an object-scoped check every
        # object also carries its own `rule_says`; for a workspace-scoped one
        # this is the single verdict the reader is being asked to re-examine,
        # and without it the instruction's "where you disagree with the rule"
        # has nothing to point at.
        "rule_verdict": _verdict_summary(by_finding),
        # One entry per finding the judged run must reproduce, carried so the
        # report can be rebuilt from the job alone, on another machine, after
        # the audit has left memory - the same reason the bundle carried them.
        "findings": {name: _finding_meta(r) for name, r in sorted(by_finding.items())},
        "objects": len(objects),
        "chunks": [
            {"chunk": i, "of": len(chunks), "objects": group}
            for i, group in enumerate(chunks, start=1)
        ],
    }


def _verdict_summary(by_finding: dict) -> str:
    """What the rule concluded, in one line the reader cannot miss."""
    if len(by_finding) == 1:
        only = next(iter(by_finding.values()))
        return (f"{only.status.value} - {only.evidence}" if only.evidence
                else only.status.value)
    tally = Counter(r.status.value for r in by_finding.values())
    return ", ".join(f"{count} {status}" for status, count in sorted(tally.items()))


def _finding_meta(result: CheckResult) -> dict:
    """Everything needed to rebuild one finding's row, judged or not."""
    return {
        "pillar": result.pillar.value,
        "severity": result.severity.value,
        "layer": result.layer.value,
        "scope": result.scope.value,
        "weight": result.weight,
        "scored": result.scored,
        "obj": result.obj,
        "recommendation": result.recommendation,
        "deterministic": {
            "status": result.status.value,
            "score": result.score,
            "evidence": result.evidence,
        },
    }


def _scoring_sentence(guide) -> str:
    """How this check's labels become a score - stated, not left to be guessed.

    The reader does not compute this; it is told so it can see that its labels
    are the whole contribution and there is no number for it to supply.

    The denominator rule is stated explicitly because leaving it implicit
    changed an answer: on one check 515 of 537 objects were ``undetermined``,
    and whether they counted was the difference between 16/537 = score 0 and
    16/22 = score 1. The same labels, the opposite verdict.
    """
    excluded = (
        "Objects you label 'undetermined' are excluded from BOTH the numerator "
        "and the denominator - they do not count against the estate."
    )
    if guide.out_of_scope:
        excluded += (
            f" So are objects you label "
            f"{' or '.join(repr(o) for o in guide.out_of_scope)}, which is a "
            f"judgment that the practice does not apply rather than a gap in "
            f"the evidence."
        )

    if guide.shape == "ratio":
        return (f"Code counts how many objects you label '{guide.compliant}', "
                f"divides by the number that remain, and bands the ratio "
                f"(1.00 -> 3, >=0.80 -> 2, >=0.50 -> 1, below -> 0). {excluded} "
                f"You do not provide a score.")
    if guide.shape == "binary":
        return (f"Code scores 3 if any object is labelled '{guide.compliant}', "
                f"otherwise 0. {excluded} You do not provide a score.")
    if guide.shape == "pair":
        first, second = guide.pair
        return (f"Code scores 3 if both '{first}' and '{second}' appear among "
                f"your labels, 1 if only one does, 0 if neither. {excluded} "
                f"You do not provide a score.")
    if guide.shape in {"graded", "best"}:
        pairs = ", ".join(
            f"'{label}' = {band}"
            for label, band in zip(guide.labels, guide.bands, strict=True)
        )
        which = "weakest" if guide.shape == "graded" else "strongest"
        return (f"Each label carries a fixed band ({pairs}), and code takes the "
                f"{which} one you assign. {excluded} You do not provide a score.")
    return (f"Code takes the weakest label you assign as the score. {excluded} "
            f"You do not provide a score.")


def _clear_stale(jobs_dir: Path) -> None:
    """Remove job files from a previous export; move its labels aside.

    The output directory is reused between runs, and ``load_jobs`` reads *every*
    ``*.json`` in it. A job left behind by an earlier run - a different estate,
    or a check whose guide has since been removed - would therefore be judged
    and reported as part of this audit.

    **Labels are archived, not deleted.** They represent hours of judging, and
    a re-run for an unrelated reason silently destroyed twenty checks' worth on
    the first day this existed. They cannot simply be left in place - labels
    produced against a different export are rejected downstream, correctly -
    so they move to a timestamped folder where they can be recovered.
    """
    stale_labels = sorted(jobs_dir.glob("*-labels.csv"))
    if any(_has_labels(p) for p in stale_labels):
        archive = jobs_dir / f"previous-labels-{time.strftime('%Y%m%d-%H%M%S')}"
        archive.mkdir(parents=True, exist_ok=True)
        for path in stale_labels:
            try:
                path.replace(archive / path.name)
            except OSError:
                continue
    else:
        for path in stale_labels:
            try:
                path.unlink()
            except OSError:
                continue

    for path in jobs_dir.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            # A file held open elsewhere is not worth failing an audit over;
            # it will simply be overwritten if this run writes the same name.
            continue


def _has_labels(path: Path) -> bool:
    """True when a label file carries at least one judged row."""
    import csv

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return any((row.get("label") or "").strip() for row in csv.DictReader(handle))
    except (OSError, ValueError):
        return False


def write_jobs(
    results_by_check: dict[str, list[CheckResult]],
    contexts: dict[str, WorkspaceContext],
    out_dir: Path,
) -> dict[str, str]:
    """Write one job file per guide-backed check, plus a manifest.

    Keyed and named by **check id**. A ref is not unique - ``5.1.9`` is two
    checks in different scopes - so ref-named files would collide and one would
    silently overwrite the other.
    """
    out_dir = Path(out_dir)
    jobs_dir = out_dir / JOBS_DIRNAME
    jobs_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale(jobs_dir)

    written: dict[str, str] = {}
    summary: list[dict] = []
    for check_id, results in sorted(results_by_check.items()):
        workspace = contexts.get(results[0].workspace) if results else None
        if workspace is None:
            continue
        job = build_job(check_id, results, workspace)
        if job is None:
            continue
        path = jobs_dir / f"{check_id}.json"
        path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
        written[f"advisory_job_{check_id}"] = str(path)

        template = jobs_dir / f"{check_id}-labels.csv"
        _write_label_template(template, job)
        written[f"advisory_labels_{check_id}"] = str(template)

        summary.append({
            "check_id": check_id,
            "ref": job["ref"],
            "title": job["title"],
            "theme": job["theme"],
            "objects": job["objects"],
            "chunks": len(job["chunks"]),
            "job": str(path),
            "labels_file": str(template),
        })

    manifest = {
        "workspaces": sorted({r[0].workspace for r in results_by_check.values() if r}),
        "checks": len(summary),
        "total_objects": sum(job["objects"] for job in summary),
        "labelling_rules": LABELLING_RULES,
        "jobs": sorted(summary, key=lambda job: -job["objects"]),
    }
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    written["advisory_manifest"] = str(manifest_path)
    return written


def _write_label_template(path: Path, job: dict) -> None:
    """A CSV pre-filled with every object id, ready for its label.

    Without it a reader retypes hundreds of identifiers, which is the likeliest
    way to produce labels that then match nothing. ``finding`` is carried so a
    reader can see which row each object's label will score.
    """
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["check_id", "finding", "object", "label", "reason", "confidence"]
        )
        for chunk in job["chunks"]:
            for record in chunk["objects"]:
                writer.writerow(
                    [job["check_id"], record.get("finding", ""), record["id"], "", "", ""]
                )
