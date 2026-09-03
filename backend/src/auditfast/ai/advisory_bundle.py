"""Offline advisory judging: export a bundle, judge it elsewhere, import verdicts.

The API path in :mod:`auditfast.ai.advisory` needs a server-side key and calls a
gateway mid-audit. That is not always available or wanted: a per-seat assistant
like GitHub Copilot cannot be called from a server, and some engagements will not
send workspace code to a configured endpoint at all.

This provides the same judging step in a **pull** shape instead of a **push** one:

1. :func:`write_bundle` serialises each advisory finding - the check, the
   deterministic verdict, and the same bounded knowledge-base slice the API path
   would have sent - to a JSONL file that stays on disk.
2. A human (or an assistant with repository access) judges those findings and
   produces a CSV.
3. :func:`apply_verdicts` reads that CSV back and rewrites *only* the advisory
   results.

**The deterministic score is unreachable from here.** Verdicts whose ``ref`` is
not in :data:`~auditfast.core.advisory.ADVISORY_CHECKLIST` are rejected outright,
so a hand-edited CSV cannot alter a scored check - the one real risk this route
introduces that the API route does not have.

Every imported row is marked ``source="advisory-offline"`` and carries its
provenance, so no reader can mistake a human/assistant judgment for a rule.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ..core.advisory import (
    ADVISORY_CHECKLIST,
    SCORING_GUIDE,
    is_advisory,
    question_for,
    reason_for,
    theme_of,
)
from ..core.advisory import THEMES as _THEMES
from ..core.models import CheckResult
from ..core.scoring import status_from_score

logger = logging.getLogger(__name__)


def _now() -> str:
    """An ISO-8601 UTC stamp.

    ``timezone.utc`` rather than ``datetime.UTC``: the package supports Python
    3.10 (``requires-python = ">=3.10"``) and ``datetime.UTC`` is 3.11+. Every
    other module here uses this spelling.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

#: The bundle filename written next to the reports.
BUNDLE_NAME = "advisory-bundle.jsonl"

#: Per-theme bundles live here; the manifest describes them.
BUNDLES_DIRNAME = "advisory-bundles"

#: Named distinctly from the jobs manifest in :mod:`auditfast.ai.jobs`. Both
#: stages write into the same output directory and the bundle runs second, so
#: sharing one filename meant the bundle silently clobbered the jobs manifest -
#: leaving 50 job files on disk that the judging agent could not discover, and
#: a manifest describing one leftover finding as if it were the whole run.
MANIFEST_NAME = "advisory-bundle-manifest.json"

#: Columns a verdict CSV must carry. ``finding_id`` ties a verdict back to the
#: exact bundle line, so a verdict cannot be applied to the wrong object.
REQUIRED_COLUMNS = {"finding_id", "score"}

#: Marks a result as judged offline rather than by a rule or the API path.
OFFLINE_SOURCE = "advisory-offline"

#: Judging themes and the ref -> theme mapping both live in
#: :mod:`auditfast.core.advisory`, so a ref cannot be advisory-but-unthemed.
#: Re-exported here because callers of this module expect them.
THEMES = _THEMES
THEME_OF_REF: dict[str, str] = {ref: entry[0]
                                for ref, entry in ADVISORY_CHECKLIST.items()}


#: A verdict this uncertain is not applied: the deterministic verdict is kept.
#: The API path ignores ``confidence`` entirely, which lets a model say "low"
#: and still overwrite a rule-based finding at full weight. With a human in the
#: loop that signal is worth acting on.
_LOW_CONFIDENCE = "low"

#: The accepted confidence vocabulary. Anything outside it is rejected rather
#: than treated as confident: a typo must not silently carry full weight.
_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


def finding_id(result: CheckResult, context: str, workspace_id: str = "") -> str:
    """Stable id for one advisory finding, derived from its content.

    Content-hashed rather than positional so re-exporting an unchanged workspace
    yields the same ids (verdicts stay valid), while a re-crawl that changed the
    evidence yields different ones - making a stale verdict detectable instead of
    silently applied to data it was not judged against.

    ``CheckResult`` carries the workspace's *display name*, not its id, so two
    workspaces in one project sharing a name (``Dev`` is the obvious case) would
    collide for the same check and object. The caller passes ``workspace_id``
    from the ``WorkspaceContext`` it already holds, which removes the ambiguity
    at its source; it defaults to empty so a caller without one still produces
    the name-based id rather than failing.
    """
    payload = "|".join((
        result.check_id, result.ref, workspace_id, result.workspace, result.obj,
        hashlib.sha256((context or "").encode("utf-8")).hexdigest()[:16],
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_bundle(
    results: list[CheckResult],
    contexts: dict,
) -> list[dict]:
    """One record per advisory finding, carrying everything needed to judge it."""
    from ..ai.advisory import _kb_context  # noqa: PLC0415 - avoids a cycle
    from ..core.check.registry import REGISTRY

    records: list[dict] = []
    for result in results:
        if not is_advisory(result.ref):
            continue
        spec = REGISTRY.get(result.check_id)
        workspace = contexts.get(result.workspace)
        context = _kb_context(result, workspace)
        workspace_id = getattr(workspace, "id", "") if workspace else ""
        records.append({
            "finding_id": finding_id(result, context, workspace_id),
            "ref": result.ref,
            "check_id": result.check_id,
            "title": result.title,
            "why_advisory": reason_for(result.ref),
            "rule": (spec.description or (spec.fn.__doc__ or "")).strip() if spec else "",
            "workspace": result.workspace,
            "object": result.obj,
            "scope": result.scope.value,
            # Carried so the verdicts can be applied and the advisory report
            # rebuilt from the bundle alone, without re-running the audit.
            "pillar": result.pillar.value,
            "severity": result.severity.value,
            "layer": result.layer.value,
            "weight": result.weight,
            "scored": result.scored,
            "recommendation": result.recommendation,
            "deterministic": {
                "status": result.status.value,
                "score": result.score,
                "evidence": result.evidence,
            },
            "evidence": context,
        })
    return records


def results_from_bundle(path: str | Path) -> list[CheckResult]:
    """Rebuild the advisory ``CheckResult`` list from a bundle file.

    The bundle is self-contained by design: a reviewer can judge it and apply the
    verdicts later, on another machine, without the audit still being in memory
    or the workspaces being re-crawled.

    Accepts a directory of themed bundles, read as one.
    """
    from ..core.enums import Layer, Pillar, Scope, Severity, Status

    results: list[CheckResult] = []
    for index, record in enumerate(_bundle_records(path), 1):
        try:
            deterministic = record["deterministic"]
            results.append(CheckResult(
                check_id=record["check_id"],
                ref=record["ref"],
                title=record["title"],
                pillar=Pillar(record["pillar"]),
                status=Status(deterministic["status"]),
                score=deterministic["score"],
                evidence=deterministic["evidence"],
                recommendation=record.get("recommendation", ""),
                severity=Severity(record["severity"]),
                workspace=record["workspace"],
                layer=Layer(record["layer"]),
                obj=record["object"],
                scope=Scope(record["scope"]),
                weight=record.get("weight", 1.0),
                scored=record.get("scored", True),
            ))
        except (KeyError, ValueError) as exc:
            raise AdvisoryVerdictError(
                f"Bundle record {index} is malformed: {exc}"
            ) from exc
    return results


def _result_key(result: CheckResult) -> tuple:
    """What identifies a finding across an export/judge/import round trip."""
    return (result.check_id, result.ref, result.workspace, result.obj)


def bundle_ids(path: str | Path) -> dict[tuple, str]:
    """``{(check_id, ref, workspace, obj): finding_id}`` recorded by a bundle.

    The bundle already stores each id, so matching a verdict is a dict lookup
    rather than a re-derivation. Accepts a directory of themed bundles.
    """
    out: dict[tuple, str] = {}
    for record in _bundle_records(path):
        out[(record["check_id"], record["ref"],
             record["workspace"], record["object"])] = record["finding_id"]
    return out


def bundle_contexts(path: str | Path) -> dict[str, str]:
    """``{finding_id: evidence}`` from a bundle. Accepts a directory."""
    return {r["finding_id"]: r.get("evidence", "") for r in _bundle_records(path)}


def _bundle_records(path: str | Path) -> list[dict]:
    """Every record in a bundle file or a directory of them.

    Raises :class:`AdvisoryVerdictError` for a missing path, an empty directory
    or a malformed line, rather than letting a raw ``FileNotFoundError`` or
    ``JSONDecodeError`` escape - the CLI catches only the former, so anything
    else surfaces to the user as a traceback.
    """
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.jsonl"))
        if not files:
            raise AdvisoryVerdictError(f"No .jsonl bundles in {path}")
    else:
        if not path.exists():
            raise AdvisoryVerdictError(f"No bundle at {path}")
        files = [path]

    records: list[dict] = []
    for file in files:
        for line_no, line in enumerate(
            file.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError as exc:
                raise AdvisoryVerdictError(
                    f"{file.name} line {line_no} is not valid JSON: {exc}"
                ) from exc
    return records


def write_bundle(
    results: list[CheckResult],
    contexts: dict,
    out_dir: Path,
    *,
    records: list[dict] | None = None,
) -> Path:
    """Write the flat judging bundle as JSONL and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / BUNDLE_NAME
    _write_jsonl(path, build_bundle(results, contexts) if records is None else records)
    return path


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_verdict_template(path: Path, records: list[dict]) -> None:
    """A verdict CSV pre-filled with the ids, ready for a reviewer to score.

    Without this the reviewer copies several hundred content-hashed ids by hand,
    which is the single likeliest way to produce verdicts that then land as
    ``unmatched``. The ``ref`` and ``object`` columns are there to make the file
    readable; only ``finding_id`` and ``score`` are consumed.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["finding_id", "score", "evidence", "recommendation",
                         "confidence", "judged_by", "ref", "object"])
        for record in records:
            writer.writerow([record["finding_id"], "", "", "", "", "",
                             record["ref"], record["object"]])


def write_themed_bundles(
    results: list[CheckResult],
    contexts: dict,
    out_dir: Path,
    *,
    records: list[dict] | None = None,
) -> dict[str, str]:
    """Split the bundle by judging theme and write a manifest describing the work.

    One flat file of 1,940 findings cannot be judged well in a single session.
    Split by theme it becomes a handful of focused jobs that can be run
    independently - and, because each is self-contained, in parallel.

    ``records`` lets a caller that has already built them pass them in: each
    record embeds up to 16 KB of evidence, so re-deriving them costs a second
    full pass over every finding and a second copy of the same payload.

    Returns ``{name: path}`` for every file written, including the manifest an
    agent reads to plan the run.
    """
    out_dir = Path(out_dir)
    bundles_dir = out_dir / BUNDLES_DIRNAME
    bundles_dir.mkdir(parents=True, exist_ok=True)

    if records is None:
        records = build_bundle(results, contexts)
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(theme_of(record["ref"]), []).append(record)

    written: dict[str, str] = {}
    jobs: list[dict] = []
    for theme, items in sorted(grouped.items()):
        path = bundles_dir / f"{theme}.jsonl"
        _write_jsonl(path, items)
        written[f"advisory_bundle_{theme}"] = str(path)

        template = bundles_dir / f"{theme}-verdicts.csv"
        _write_verdict_template(template, items)
        written[f"advisory_template_{theme}"] = str(template)

        by_ref: dict[str, int] = {}
        for record in items:
            by_ref[record["ref"]] = by_ref.get(record["ref"], 0) + 1
        jobs.append({
            "theme": theme,
            "question": question_for(theme),
            "bundle": str(path),
            "findings": len(items),
            "refs": dict(sorted(by_ref.items())),
            "verdicts": str(template),
        })

    manifest = {
        "generated": _now(),
        # Named so a reviewer asked to "judge the NOIDA audit" can confirm the
        # bundle on disk is that audit, rather than one left by an earlier run
        # over a different estate. The output directory is reused between runs,
        # so without this the only clue is the timestamp.
        "workspaces": sorted({r["workspace"] for r in records if r.get("workspace")}),
        "total_findings": len(records),
        # Carried in the manifest so the rubric travels with the work. A reader
        # judging on another machine gets the same banding as the engine rather
        # than whatever it remembers, and the guide cannot drift from the code.
        "scoring_guide": SCORING_GUIDE,
        "jobs": sorted(jobs, key=lambda job: -job["findings"]),
    }
    manifest_path = out_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    written["advisory_bundle_manifest"] = str(manifest_path)
    return written


class AdvisoryVerdictError(ValueError):
    """A verdict CSV could not be read, or tried to reach a scored check."""


def read_verdicts(path: str | Path) -> dict[str, dict]:
    """``{finding_id: verdict}`` from a judged CSV, validated.

    Accepts a directory, in which case every ``*-verdicts.csv`` inside it is
    merged - so the themed jobs can be judged separately, in parallel, and
    applied in one step.
    """
    path = Path(path)
    if path.is_dir():
        merged: dict[str, dict] = {}
        files = sorted(path.glob("*verdicts*.csv"))
        if not files:
            raise AdvisoryVerdictError(f"No *verdicts*.csv files in {path}")
        for file in files:
            merged.update(read_verdicts(file))
        return merged
    if not path.exists():
        raise AdvisoryVerdictError(f"No verdict file at {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise AdvisoryVerdictError("Verdict CSV is empty")
        missing = REQUIRED_COLUMNS - {(n or "").strip() for n in reader.fieldnames}
        if missing:
            raise AdvisoryVerdictError(
                f"Verdict CSV missing required column(s): {', '.join(sorted(missing))}"
            )
        verdicts: dict[str, dict] = {}
        for line_no, row in enumerate(reader, start=2):
            key = (row.get("finding_id") or "").strip()
            raw = (row.get("score") or "").strip()
            if not key:
                # A template row the reviewer did not judge is fine and common.
                # A row carrying a score but no id is not: the id column was
                # mangled (Excel is the usual culprit) and the judgment would be
                # silently discarded.
                if raw:
                    raise AdvisoryVerdictError(
                        f"Row {line_no}: a score of {raw!r} with no finding_id. "
                        f"The id column has been lost - re-export the template."
                    )
                continue
            if key in verdicts:
                # Last-wins would resolve two contradictory judgments by file
                # sort order, silently. Likely when themed CSVs are merged.
                raise AdvisoryVerdictError(
                    f"Row {line_no}: finding_id {key} already has a verdict. "
                    f"Two judgments of one finding cannot be resolved automatically."
                )
            if not raw:
                continue
            try:
                score = int(raw)
            except ValueError as exc:
                # Not int(float(...)): that turns 2.7 into 2 and 0.9 into 0. The
                # rubric is integers, so a decimal is a mistake, not a rounding
                # question.
                raise AdvisoryVerdictError(
                    f"Row {line_no}: score {raw!r} is not a whole number 0-3"
                ) from exc
            if not 0 <= score <= 3:
                raise AdvisoryVerdictError(f"Row {line_no}: score {score} is outside 0-3")
            confidence = (row.get("confidence") or "medium").strip().lower()
            if confidence not in _CONFIDENCE_LEVELS:
                # An unrecognised value must not read as "confident enough to
                # overwrite a rule": a typo would silently carry full weight.
                raise AdvisoryVerdictError(
                    f"Row {line_no}: confidence {confidence!r} is not one of "
                    f"{', '.join(sorted(_CONFIDENCE_LEVELS))}"
                )
            verdicts[key] = {
                "score": score,
                "evidence": (row.get("evidence") or "").strip(),
                "recommendation": (row.get("recommendation") or "").strip(),
                "confidence": confidence,
                "judged_by": (row.get("judged_by") or "offline").strip(),
            }
    return verdicts


def apply_verdicts(
    results: list[CheckResult],
    contexts: dict,
    verdict_path: str | Path,
    *,
    evidence_by_id: dict[str, str] | None = None,
    id_by_key: dict[tuple, str] | None = None,
) -> tuple[list[CheckResult], dict]:
    """Rewrite advisory results from a judged CSV. Returns (results, summary).

    A finding is left untouched when it has no verdict, when its verdict is
    low-confidence, or when its ``ref`` is not advisory. Nothing here can reach a
    deterministic result: the caller passes only the advisory partition, and the
    ref is re-checked anyway.

    ``id_by_key`` maps ``(check_id, ref, workspace, obj)`` to the id the bundle
    recorded, so a verdict is matched with one dict lookup. ``evidence_by_id``
    is accepted for callers that hold only the evidence; it is turned into the
    same map rather than compared finding-by-finding, which was quadratic (two
    SHA-256 digests per pair, ~7.5M hashes on a 1,940-finding bundle).
    """
    verdicts = read_verdicts(verdict_path)
    stamp = _now()
    from ..ai.advisory import _kb_context  # noqa: PLC0415 - avoids a cycle

    lookup = dict(id_by_key or {})
    if evidence_by_id and not lookup:
        for result in results:
            for key, evidence in evidence_by_id.items():
                if finding_id(result, evidence) == key:
                    lookup[_result_key(result)] = key
                    break

    applied = skipped_low = unmatched = rejected = 0
    matched_ids: set[str] = set()
    out: list[CheckResult] = []
    for result in results:
        key = lookup.get(_result_key(result))
        if key is None:
            workspace = contexts.get(result.workspace)
            key = finding_id(
                result,
                _kb_context(result, workspace),
                getattr(workspace, "id", "") if workspace else "",
            )
        verdict = verdicts.get(key)

        if verdict is None:
            unmatched += 1
            out.append(result)
            continue
        matched_ids.add(key)

        if not is_advisory(result.ref):
            # Defence in depth: a hand-edited CSV must never reach a scored
            # check. Counted only when a verdict actually targeted this finding,
            # so the number means "verdicts refused" rather than "results that
            # happened not to be advisory".
            rejected += 1
            logger.warning(
                "advisory: refused a verdict for non-advisory ref %s (%s)",
                result.ref, result.check_id,
            )
            out.append(result)
            continue
        if verdict["confidence"] == _LOW_CONFIDENCE:
            skipped_low += 1
            out.append(result)
            continue
        score = verdict["score"]
        label = f"[offline - {verdict['judged_by']} - {verdict['confidence']} confidence]"
        out.append(replace(
            result,
            score=score,
            status=status_from_score(score),
            evidence=f"{label} {verdict['evidence']}".strip(),
            recommendation=verdict["recommendation"] or result.recommendation,
            source=OFFLINE_SOURCE,
        ))
        applied += 1

    # Verdict rows that matched no finding. The mirror of ``unmatched``, and the
    # half that actually detects a stale bundle: judging 500 findings against the
    # wrong export yields applied=0 and 500 ignored rows, which otherwise goes
    # unreported.
    orphaned = sorted(set(verdicts) - matched_ids)

    return out, {
        "applied": applied,
        "skipped_low_confidence": skipped_low,
        "unmatched": unmatched,
        "rejected_non_advisory": rejected,
        "verdicts_in_file": len(verdicts),
        "orphaned_verdicts": len(orphaned),
        "orphaned_ids": orphaned[:10],
        "applied_at": stamp,
    }
