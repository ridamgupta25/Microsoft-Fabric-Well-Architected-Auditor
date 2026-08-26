"""Advisory judging as an explicit, user-triggered step.

The audit writes the judging jobs and stops. This runs afterwards, on demand,
against the files the audit left in ``output/jobs`` - the same files the offline
Copilot agent reads. Both routes therefore consume identical input and produce
identical artefacts, which is what makes a keyed run comparable with an agent
run rather than merely similar.

Three deliberate choices:

**It reads and writes files, not memory.** Holding the audit's context alive
between two API calls would tie a judging run to the process that produced it -
no retry after a restart, no judging a report someone else generated. The jobs
are already on disk; using them costs nothing and decouples the two steps.

**It writes the label CSVs.** They are the evidence for *why* each object was
judged as it was, and the input to the contradiction and scorecard gates. A run
that scored without leaving them would be unauditable, which defeats the point
of an advisory report.

**The user's key is never stored.** It arrives on the request, lives on the
stack for the run, and is gone. It is not written to the job store, not logged,
and not echoed back.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from ..ai.advisory import label_job
from ..ai.labels import LabelError, apply_labels, load_jobs, read_labels
from ..ai.orchestrator import Credentials
from ..core.scoring import aggregate

logger = logging.getLogger(__name__)

#: Where the audit leaves its jobs, relative to the output directory.
JOBS_DIRNAME = "jobs"

#: Kept separate from ``advisory-report.*`` so a judged run never overwrites the
#: audit's own advisory report - a reader needs both to see what judging changed.
JUDGED_DIRNAME = "advisory-judged"


class AdvisoryError(RuntimeError):
    """Advisory judging could not run at all."""


def run_advisory(
    out_dir: str | Path,
    *,
    credentials: Credentials | None = None,
    project_name: str = "Advisory",
    judged_by: str = "ai",
) -> dict[str, Any]:
    """Label every exported job, write the labels, and score them into a report.

    Returns a summary plus the files written. Raises :class:`AdvisoryError` only
    when there is nothing to judge; a model that fails on some checks is not an
    error - those findings keep their deterministic verdict.
    """
    out_dir = Path(out_dir)
    jobs_dir = out_dir / JOBS_DIRNAME

    try:
        jobs = load_jobs(jobs_dir)
    except LabelError as exc:
        raise AdvisoryError(
            f"No judging jobs in {jobs_dir}. Run an audit first."
        ) from exc

    labelled_checks = 0
    for check_id, job in sorted(jobs.items()):
        given = label_job(job, credentials=credentials)
        if not given:
            # The model returned nothing usable for this check. Leave the
            # template untouched so the deterministic verdict stands and a
            # reader can still judge it by hand.
            continue
        _write_labels(jobs_dir / f"{check_id}-labels.csv", job, given)
        labelled_checks += 1

    if not labelled_checks:
        raise AdvisoryError(
            "The model returned no usable labels for any check. Check the key, "
            "the deployment name, and that the provider is reachable."
        )

    labels = read_labels(jobs_dir)
    results, summary = apply_labels(jobs, labels, judged_by=judged_by)

    from .audit_service import AuditRun, write_advisory_reports

    run = AuditRun(project_name=project_name)
    run.advisory_results = results
    run.advisory_aggregate = aggregate(results)
    files = write_advisory_reports(run, out_dir / JUDGED_DIRNAME)

    judged_checks = {e["check_id"] for e in summary["changed"] + summary["agreed"]}
    return {
        "checks_total": len(jobs),
        "checks_labelled": labelled_checks,
        "checks_judged": len(judged_checks),
        "findings_judged": summary["judged"],
        "findings_left_to_rules": summary["unjudged"],
        "objects_labelled": summary["objects_labelled"],
        "objects_undetermined": summary["objects_undetermined"],
        "findings_changed": len(summary["changed"]),
        "findings_agreed": len(summary["agreed"]),
        "files": files,
    }


def _write_labels(path: Path, job: dict, given: dict[str, dict]) -> None:
    """The job's label CSV, filled in with what the model decided.

    Every object gets a row whether or not it was labelled, so the file is also
    the record of what the model declined to judge. Column order matches the
    template the agent path fills by hand, so the same tooling reads both.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["check_id", "finding", "object", "label", "reason", "confidence"])
        seen: set[str] = set()
        for chunk in job.get("chunks", []):
            for record in chunk.get("objects", []):
                obj = record["id"]
                # `read_labels` keys by object name and rejects a repeat, but an
                # export can emit one display name twice for different items.
                # They pool into the same finding anyway, so keep the first.
                if obj in seen:
                    continue
                seen.add(obj)
                entry = given.get(obj) or {}
                writer.writerow([
                    job["check_id"],
                    record.get("finding", ""),
                    obj,
                    entry.get("label", ""),
                    entry.get("reason", ""),
                    entry.get("confidence", ""),
                ])
