"""Read a reader's labels and turn them into scored results.

This closes the loop: a job file goes out with every object a check must judge,
a reader labels them, and this reads those labels back, scores them **in code**,
and rebuilds the advisory verdicts.

The rules that matter here are the ones that stop a label file doing damage:

* A label outside the guide's vocabulary is **rejected**, not coerced. A reader
  writing "probably a dimension" must fail loudly rather than be silently read
  as ``neither``.
* An object the job never mentioned is **rejected**. It means the labels were
  produced against a different export, and applying them would score a workspace
  from another workspace's judgment.
* A blank label is **skipped**, not treated as a verdict. Template rows a reader
  did not get to are normal.
* Nothing here can reach a deterministic check: jobs are only ever written for
  advisory refs, and the ref is re-checked anyway.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from ..core.advisory import is_advisory
from ..core.enums import Layer, Pillar, Scope, Severity, Status
from ..core.judging import guide_for
from ..core.models import CheckResult
from ..core.scoring import status_from_score
from .classify import UNDETERMINED, score

#: Marks a result whose verdict came from a reader rather than the rule.
JUDGED_SOURCE = "advisory-judged"

REQUIRED_COLUMNS = {"check_id", "object", "label"}


class LabelError(ValueError):
    """A label file could not be read, or does not match its job."""


def read_labels(path: str | Path) -> dict[str, dict[str, dict]]:
    """``{check_id: {object: {label, reason, confidence}}}`` from a file or dir."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*labels*.csv"))
        if not files:
            raise LabelError(f"No *labels*.csv files in {path}")
    else:
        if not path.exists():
            raise LabelError(f"No label file at {path}")
        files = [path]

    out: dict[str, dict[str, dict]] = {}
    for file in files:
        with file.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise LabelError(f"{file.name} is empty")
            missing = REQUIRED_COLUMNS - {(n or "").strip() for n in reader.fieldnames}
            if missing:
                raise LabelError(
                    f"{file.name} is missing column(s): {', '.join(sorted(missing))}"
                )
            for line_no, row in enumerate(reader, start=2):
                check_id = (row.get("check_id") or "").strip()
                # NOT stripped: a Fabric item may genuinely be named with a
                # trailing space, and one on this estate is ("For Agent "). The
                # job carries the real name, so stripping here turns a valid
                # label into "not an object in this job" and the whole check
                # fails to score. Only a blank-vs-present test needs the strip.
                obj = row.get("object") or ""
                label = (row.get("label") or "").strip().lower()
                if not label:
                    # A template row the reader did not judge. Normal.
                    continue
                if not check_id or not obj.strip():
                    raise LabelError(
                        f"{file.name} row {line_no}: a label of {label!r} with no "
                        f"check_id or object - the file has been mangled, re-export it"
                    )
                bucket = out.setdefault(check_id, {})
                if obj in bucket:
                    raise LabelError(
                        f"{file.name} row {line_no}: {obj} already has a label for "
                        f"{check_id}. Two labels for one object cannot be resolved."
                    )
                bucket[obj] = {
                    "label": label,
                    "reason": (row.get("reason") or "").strip(),
                    "confidence": (row.get("confidence") or "medium").strip().lower(),
                }
    return out


def load_jobs(path: str | Path) -> dict[str, dict]:
    """``{check_id: job}`` from a jobs directory or a single job file."""
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.glob("*.json"))
        if not files:
            raise LabelError(f"No job files in {path}")
    else:
        if not path.exists():
            raise LabelError(f"No job file at {path}")
        files = [path]

    jobs: dict[str, dict] = {}
    for file in files:
        try:
            job = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise LabelError(f"{file.name} is not valid JSON: {exc}") from exc
        if "check_id" in job:
            jobs[job["check_id"]] = job
    return jobs


def result_from_job(job: dict, meta: dict, band: int | None, evidence: str,
                    judged_by: str) -> CheckResult:
    """Rebuild one finding's result carrying the reader's verdict.

    Built from the job rather than from a live audit so the report can be
    produced later, elsewhere, without re-crawling.
    """
    if band is None:
        # Nothing could be judged. Keep the rule's verdict rather than invent
        # one - the N/A-not-FAIL rule, applied to the whole finding.
        return _deterministic_result(job, meta)

    return CheckResult(
        check_id=job["check_id"],
        ref=job["ref"],
        title=job["title"],
        pillar=Pillar(meta.get("pillar", Pillar.FOUNDATION.value)),
        status=status_from_score(band),
        score=band,
        evidence=f"[judged - {judged_by}] {evidence}",
        recommendation=meta.get("recommendation", ""),
        severity=Severity(meta.get("severity", Severity.MEDIUM.value)),
        workspace=meta.get("workspace") or job.get("workspace", ""),
        layer=Layer(meta.get("layer", Layer.MIXED.value)),
        obj=meta.get("obj", ""),
        scope=Scope(meta.get("scope", Scope.WORKSPACE.value)),
        weight=meta.get("weight", 1.0),
        scored=meta.get("scored", True),
        source=JUDGED_SOURCE,
    )


def _deterministic_result(job: dict, meta: dict) -> CheckResult:
    """The rule's own verdict for one finding, rebuilt from the job."""
    deterministic = meta.get("deterministic") or {}
    return CheckResult(
        check_id=job["check_id"],
        ref=job["ref"],
        title=job["title"],
        pillar=Pillar(meta.get("pillar", Pillar.FOUNDATION.value)),
        status=Status(deterministic.get("status", Status.NA.value)),
        score=deterministic.get("score"),
        evidence=deterministic.get("evidence", ""),
        recommendation=meta.get("recommendation", ""),
        severity=Severity(meta.get("severity", Severity.MEDIUM.value)),
        workspace=meta.get("workspace") or job.get("workspace", ""),
        layer=Layer(meta.get("layer", Layer.MIXED.value)),
        obj=meta.get("obj", ""),
        scope=Scope(meta.get("scope", Scope.WORKSPACE.value)),
        weight=meta.get("weight", 1.0),
        scored=meta.get("scored", True),
    )


def _findings_of(job: dict) -> dict[str, dict]:
    """``{finding: meta}`` for a job, rejecting a file from the old export."""
    findings = job.get("findings")
    if not isinstance(findings, dict) or not findings:
        raise LabelError(
            f"{job.get('check_id', '?')}: the job file has no 'findings' block. It "
            f"was written by an older export - re-run the audit to regenerate it."
        )
    return findings


def apply_labels(
    jobs: dict[str, dict],
    labels: dict[str, dict[str, dict]],
    *,
    judged_by: str = "agent",
) -> tuple[list[CheckResult], dict]:
    """Score every finding in every job from its labels. Returns (results, summary).

    One result per **finding**, not per check: an object-scoped check reports a
    row per notebook or pipeline, and the judged run has to reproduce those rows
    or the advisory report loses them against its deterministic counterpart.
    """
    results: list[CheckResult] = []
    summary = {
        "judged": 0, "unjudged": 0, "objects_labelled": 0,
        "objects_undetermined": 0, "rejected_non_advisory": 0,
        "changed": [], "agreed": [],
    }

    for check_id, job in sorted(jobs.items()):
        if not is_advisory(job.get("ref", "")):
            # Defence in depth: a job should only ever exist for an advisory
            # check, but a hand-made file must not reach a scored one.
            summary["rejected_non_advisory"] += 1
            continue

        findings = _findings_of(job)
        guide = guide_for(check_id)
        given = labels.get(check_id, {})
        if guide is None or not given:
            summary["unjudged"] += len(findings)
            results.extend(_deterministic_result(job, m) for m in findings.values())
            continue

        # Which finding each object's label scores. An object-scoped check has
        # one finding per object, so a label must be attributed to its own
        # finding rather than pooled across the whole check.
        finding_of = {
            o["id"]: o.get("finding", "")
            for chunk in job.get("chunks", [])
            for o in chunk["objects"]
        }
        allowed = set(guide.labels) | {UNDETERMINED}

        assigned: dict[str, list[str]] = {name: [] for name in findings}
        for obj, entry in given.items():
            if obj not in finding_of:
                near = [k for k in finding_of if k.strip() == obj.strip()]
                hint = (
                    f" The job has {near[0]!r} - the names differ only in "
                    f"whitespace, which a spreadsheet may have trimmed."
                    if near else
                    " The labels were produced against a different export - "
                    "re-export and judge again."
                )
                raise LabelError(f"{check_id}: {obj!r} is not an object in this job.{hint}")
            if entry["label"] not in allowed:
                raise LabelError(
                    f"{check_id}: '{obj}' is labelled {entry['label']!r}, which is "
                    f"not one of {sorted(allowed)}"
                )
            assigned.setdefault(finding_of[obj], []).append(entry["label"])

        summary["objects_labelled"] += sum(len(v) for v in assigned.values())
        summary["objects_undetermined"] += sum(
            v.count(UNDETERMINED) for v in assigned.values()
        )

        population = Counter(finding_of.values())

        for name, meta in sorted(findings.items()):
            given_here = assigned.get(name, [])
            was = (meta.get("deterministic") or {}).get("score")
            band, evidence = score(guide, given_here) if given_here else (None, "")

            if band is None:
                # Either nothing was labelled for this finding, or everything
                # was undetermined. Both keep the rule's verdict, so neither is
                # a judgment and neither belongs in changed/agreed.
                summary["unjudged"] += 1
                results.append(_deterministic_result(job, meta))
                continue

            unlabelled = population.get(name, 0) - len(given_here)
            if unlabelled > 0:
                evidence += f". {unlabelled} object(s) were not labelled"

            results.append(result_from_job(job, meta, band, evidence, judged_by))
            summary["judged"] += 1
            moved = {
                "check_id": check_id, "ref": job.get("ref", ""),
                "finding": name, "was": was, "now": band,
            }
            summary["changed" if band != was else "agreed"].append(moved)

    return results, summary
