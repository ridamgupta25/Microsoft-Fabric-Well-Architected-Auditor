#!/usr/bin/env python3
"""Deterministic pre-labeler for mechanical advisory checks.

The Advisory Judge normally labels every object with an LLM. For checks whose
verdict is a plain lookup - "is a notifier wired to a Failed edge?", "does the
Copy sink upsert?" - that judgement can be made in code from the job evidence,
with no model and no context cost. This tool fills those clear-cut labels and
leaves anything ambiguous blank (marked NEEDS-REVIEW) for the agent.

Guarantees:
* It never overwrites a label that is already filled in.
* It never changes ``check_id``, ``finding`` or ``object``.
* It only assigns a label when the evidence is unambiguous; otherwise it leaves
  the row blank for a reviewer. It does not guess.

Usage, from the ``backend`` directory::

    python tools/advisory_prelabel.py PL-NOTIFY            # newest run
    python tools/advisory_prelabel.py PL-NOTIFY --run output/<dir>
    python tools/advisory_prelabel.py PL-NOTIFY --verify   # compare, write nothing
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Optional

# A labeler returns (label, reason, confidence). label is None to defer to the
# agent (the row is left blank and flagged NEEDS-REVIEW).
Verdict = tuple[Optional[str], str, str]


# --------------------------------------------------------------------------- #
# Shared evidence helpers
# --------------------------------------------------------------------------- #
def walk_activities(activities: Any) -> Iterator[dict]:
    """Yield every activity, descending into If/Switch/ForEach containers."""
    for activity in activities or []:
        yield activity
        props = activity.get("typeProperties", {})
        for key in ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities"):
            yield from walk_activities(props.get(key))
        for case in props.get("cases", []) or []:
            yield from walk_activities(case.get("activities"))


def parse_pipeline(obj: dict) -> Any:
    """Return the pipeline JSON, None for an empty pipeline, or 'ERR' if unparsable."""
    facts = (obj.get("facts") or "").strip()
    if facts in ("", "{}") or "no activities" in facts:
        return None
    try:
        return json.loads(facts[facts.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return "ERR"


def copy_activities(pipeline: dict) -> list[dict]:
    return [a for a in walk_activities(pipeline.get("properties", {}).get("activities", []))
            if a.get("type") == "Copy"]


def has_non_copy_load(pipeline: dict) -> bool:
    """True when work happens in a notebook/script/sproc not visible as a Copy."""
    loaders = {"TridentNotebook", "DatabricksNotebook", "SqlServerStoredProcedure", "Script"}
    return any(a.get("type") in loaders
               for a in walk_activities(pipeline.get("properties", {}).get("activities", [])))


# --------------------------------------------------------------------------- #
# Per-check labelers
# --------------------------------------------------------------------------- #
_NOTIFIER_TYPES = {"Office365Email", "Teams"}
_WEBHOOK_HINTS = (
    "webhook", "slack", "pagerduty", "servicenow",
    "office.com", "logic.azure", "teams.microsoft", "outlook",
)


def label_pl_notify(obj: dict) -> Verdict:
    """PL-NOTIFY (2.4.5): does any activity tell a person when the pipeline fails?"""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "Empty pipeline has nothing to notify about", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")

    for activity in walk_activities(pipeline.get("properties", {}).get("activities", [])):
        kind = activity.get("type")
        if kind in _NOTIFIER_TYPES:
            return ("notifies", f"{kind} activity present", "high")
        if kind in ("Web", "WebActivity", "AzureFunctionActivity"):
            blob = json.dumps(activity.get("typeProperties", {})).lower()
            if any(hint in blob for hint in _WEBHOOK_HINTS):
                return ("notifies", f"{kind} posts to a webhook", "high")
            # A web call to an unknown URL might or might not notify - defer.
            return (None, "Web activity with an unrecognized target", "low")
    return ("silent", "No email/Teams/webhook notifier anywhere in the pipeline", "high")


_NOTIFIER_WITH_WEB = _NOTIFIER_TYPES | {"Web", "WebActivity", "AzureFunctionActivity"}


def _runs_on_failure(activity: dict) -> bool:
    for dep in activity.get("dependsOn", []):
        conditions = dep.get("dependencyConditions") or []
        if "Failed" in conditions or "Completed" in conditions:
            return True
    return False


def label_pl_failure_alert(obj: dict) -> Verdict:
    """PL-FAILURE-ALERT (10.1.4): is an enabled notifier wired to a Failed edge?"""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "No activities to evaluate for failure alerting", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")

    notifiers = [a for a in walk_activities(pipeline.get("properties", {}).get("activities", []))
                 if a.get("type") in _NOTIFIER_WITH_WEB]
    enabled = [a for a in notifiers if a.get("state", "Active") != "Inactive"]
    on_failure = [a for a in enabled if _runs_on_failure(a)]
    if on_failure:
        conf = "high" if len(enabled) == len(notifiers) else "medium"
        return ("wired_to_failure", f"Enabled {on_failure[0]['type']} runs on a Failed edge", conf)
    if enabled:
        return ("notifier_not_on_failure_path",
                "A notifier exists but only runs on success, so failures raise no alert", "medium")
    if notifiers:
        return ("no_notifier", "The only notifier is disabled (Inactive), so nothing can fire", "high")
    return ("no_notifier", "No email/Teams/web/Activator notifier anywhere in the pipeline", "high")


_SECRET_PATTERNS = (
    r"AccountKey=[A-Za-z0-9+/]{20,}={0,2}",
    r"SharedAccessSignature=",
    r"password\"?\s*[:=]\s*\"(?!@\{|\*+|<|\{\{)[^\"]{4,}\"",
    r"pwd=(?!@\{)[^;\"]{4,}",
)
_SECRET_SAFE = ("keyvault", "secretname", "@{", "managedidentity", "workspaceidentity")


def label_pl_secrets(obj: dict) -> Verdict:
    """PL-SECRETS (6.4.2): is a real credential value written into the definition?"""
    facts = (obj.get("facts") or "").strip()
    if facts in ("", "{}") or "no activities" in facts:
        return ("no_secret", "Empty/near-empty definition contains no literal credential", "high")
    for pattern in _SECRET_PATTERNS:
        for match in re.findall(pattern, facts, re.IGNORECASE):
            if not any(safe in match.lower() for safe in _SECRET_SAFE):
                return (None, "Possible literal credential - needs review", "low")
    return ("no_secret",
            "No literal credential; connections use linked-service/Key Vault/managed-identity references",
            "high")


def label_pl_deadletter(obj: dict) -> Verdict:
    """PL-DEADLETTER (2.4.4): are rows that cannot load kept for inspection?"""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "Pipeline moves no rows / definition not readable", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")
    copies = copy_activities(pipeline)
    if not copies:
        return ("undetermined",
                "No Copy activity; any load happens in a notebook/script not visible here", "high")
    for copy in copies:
        props = copy.get("typeProperties", {})
        if ("redirectIncompatibleRowSettings" in props or "logSettings" in props
                or props.get("enableSkipIncompatibleRow")):
            return ("routes_bad_rows", "Copy redirects/logs incompatible rows", "high")
    return ("drops_or_halts",
            "Copy has no incompatible-row redirection or error logging, so a bad row is dropped or halts the load",
            "high")


def label_pl_idempotent(obj: dict) -> Verdict:
    """PL-IDEMPOTENT (2.4.6): would running twice duplicate data?"""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "Pipeline moves no rows / not readable", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")
    copies = copy_activities(pipeline)
    if not copies:
        return ("undetermined",
                "No Copy activity; write logic lives in a notebook/script not visible here", "high")
    appends = False
    for copy in copies:
        props = copy.get("typeProperties", {})
        sink = props.get("sink", {})
        behavior = (sink.get("writeBehavior") or "").lower()
        action = (sink.get("tableActionOption") or "").lower()
        pre = str(props.get("preCopyScript") or sink.get("preCopyScript") or "").lower()
        truncates = "truncat" in pre or "delete " in pre or "drop " in pre
        if behavior in ("upsert", "merge") or action == "overwrite" or truncates:
            continue
        if behavior == "insert" or action == "append" or (not behavior and not action):
            appends = True
    if appends:
        return ("appends_duplicates",
                "At least one Copy appends unconditionally (no upsert/overwrite/truncate), so a rerun re-adds rows",
                "medium")
    return ("rerunnable",
            "Every Copy uses upsert or truncate-then-insert/overwrite, so a rerun replaces rather than duplicates",
            "high")


def label_pl_dq_gate(obj: dict) -> Verdict:
    """PL-DQ-GATE (5.1.9): does a validation halt the flow? Auto only the clear no-validation case."""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "No validation activity in the pipeline", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")
    activities = list(walk_activities(pipeline.get("properties", {}).get("activities", [])))
    types = {a.get("type") for a in activities}
    blob = json.dumps(pipeline).lower()
    gate_capable = bool(types & {"IfCondition", "Switch", "Fail", "Validation"}) \
        or "Lookup" in types or "ExecutePipeline" in types \
        or "count(" in blob or "row_count" in blob
    if gate_capable:
        # There is something that could be a gate - the agent judges if it halts.
        return (None, "Has a validation/gate-capable activity", "low")
    return ("undetermined",
            "No validation activity (row-count/schema/null/reconciliation) in the pipeline", "high")


def label_pl_reconcile(obj: dict) -> Verdict:
    """PL-RECONCILE (7.2.6): proves target matches source? Auto only the clear no-reconciliation case."""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "Pipeline moves no data", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")
    # If the rule engaged with this pipeline (PASS/FAIL), reconciliation is in play -
    # leave the halt/decorative judgement to the agent rather than auto-marking N/A.
    rule = (obj.get("rule_says") or "").upper()
    if rule.startswith("PASS") or rule.startswith("FAIL"):
        return (None, "Rule scored a reconciliation verdict - needs review", "low")
    types = {a.get("type") for a in walk_activities(pipeline.get("properties", {}).get("activities", []))}
    blob = json.dumps(pipeline).lower()
    recon_shape = ("count(" in blob or "row_count" in blob or "except" in blob
                   or "subtract" in blob or "reconcil" in blob or bool(types & {"IfCondition", "Fail"}))
    if recon_shape:
        return (None, "Has a count/compare or reconciliation reference", "low")
    return ("undetermined", "No source-to-target reconciliation activity in the pipeline", "high")


_AUDIT_SINK = re.compile(r"(audit|_log|log_|journal|watermark|_ctl)", re.IGNORECASE)
_DESTRUCTIVE = re.compile(
    r'(truncate|delete\s+from|drop\s+table|insert\s+overwrite|"tableActionOption"\s*:\s*"Overwrite")',
    re.IGNORECASE,
)


def label_pl_audit_immutable(obj: dict) -> Verdict:
    """PL-AUDIT-IMMUTABLE (4.6.5): is the audit/log record append-only? Auto only the clear no-audit-write case."""
    pipeline = parse_pipeline(obj)
    if pipeline is None:
        return ("undetermined", "No audit/log table written here", "high")
    if pipeline == "ERR":
        return (None, "Definition could not be parsed", "low")
    for copy in copy_activities(pipeline):
        blob = json.dumps(copy)
        if _AUDIT_SINK.search(blob) and _DESTRUCTIVE.search(blob):
            return (None, "Possible destructive write to an audit/log table", "low")
    return ("undetermined",
            "Pipeline writes no audit/log table with a destructive operation", "high")


_BACKFILL = re.compile(
    r"(backfill|one[_-]?off|initial[_-]?load|full[_-]?history|reload[_-]?all|historic[_-]?load)",
    re.IGNORECASE,
)


def label_pl_hist_separation(obj: dict) -> Verdict:
    """PL-HIST-SEPARATION (2.2.3): is a historical load kept off the daily path? Auto only the clear no-backfill case."""
    name = obj.get("id", "")
    if _BACKFILL.search(name):
        return (None, "Name suggests a historical/backfill load", "low")
    return ("undetermined", "No historical/backfill load in this pipeline", "high")


LABELERS: dict[str, Callable[[dict], Verdict]] = {
    "PL-NOTIFY": label_pl_notify,
    "PL-FAILURE-ALERT": label_pl_failure_alert,
    "PL-SECRETS": label_pl_secrets,
    "PL-DEADLETTER": label_pl_deadletter,
    "PL-IDEMPOTENT": label_pl_idempotent,
    "PL-DQ-GATE": label_pl_dq_gate,
    "PL-RECONCILE": label_pl_reconcile,
    "PL-AUDIT-IMMUTABLE": label_pl_audit_immutable,
    "PL-HIST-SEPARATION": label_pl_hist_separation,
}

# Signals that an object has no readable evidence at all. Marking these
# 'undetermined' is correct for *every* check - there is nothing to judge - so it
# is the one label that is always safe to assign without a check-specific rule.
_UNREADABLE_HINTS = (
    "definition not readable", "no activities", "could not be read",
    "not readable", "definition could not be parsed",
)


def is_unreadable(obj: dict) -> bool:
    facts = (obj.get("facts") or "").strip()
    return facts in ("", "{}") or any(hint in facts.lower() for hint in _UNREADABLE_HINTS)


def label_generic(obj: dict) -> Verdict:
    """Fallback for any check: only the no-evidence case is safe to auto-label."""
    if is_unreadable(obj):
        return ("undetermined", "Definition not readable / no evidence to judge", "high")
    return (None, "Needs review", "low")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _objects_by_id(job: dict) -> dict[str, dict]:
    return {o["id"]: o for chunk in job["chunks"] for o in chunk["objects"]}


def prelabel(check_id: str, job_path: Path, labels_path: Path, *, verify: bool) -> int:
    labeler = LABELERS.get(check_id, label_generic)
    specific = check_id in LABELERS

    job = json.loads(job_path.read_text(encoding="utf-8"))
    objects = _objects_by_id(job)

    # utf-8-sig tolerates a byte-order mark some editors add to the template.
    with labels_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = ["check_id", "finding", "object", "label", "reason", "confidence"]

    auto = review = kept = mismatch = 0
    for row in rows:
        obj = objects.get(row["object"])
        computed = labeler(obj) if obj is not None else (None, "Object not in job", "low")
        label, reason, confidence = computed

        if verify:
            existing = (row.get("label") or "").strip()
            if existing and label and existing != label:
                mismatch += 1
                print(f"  MISMATCH {row['object']}: script={label} file={existing}")
            continue

        if (row.get("label") or "").strip():
            kept += 1
            continue
        if label:
            row["label"], row["reason"], row["confidence"] = label, reason, confidence
            auto += 1
        else:
            row["reason"] = reason or "NEEDS-REVIEW"
            review += 1

    if verify:
        agree = sum(1 for r in rows if (r.get("label") or "").strip()
                    and labeler(objects.get(r["object"], {}))[0] == r["label"])
        print(f"{check_id}: verify - {agree} agree, {mismatch} mismatch")
        return 1 if mismatch else 0

    tmp = labels_path.with_name(labels_path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(labels_path)
    mode = "specific" if specific else "generic (unreadable-only)"
    print(f"{check_id}: auto={auto} review={review} kept={kept} [{mode}]")
    return 0


def _newest_run(output_dir: Path) -> Optional[Path]:
    runs = [p for p in output_dir.iterdir() if p.is_dir() and (p / "advisory-manifest.json").exists()]
    return max(runs, key=lambda p: p.name) if runs else None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministically pre-label mechanical advisory checks.")
    parser.add_argument("check_id", help="Check id to pre-label, e.g. PL-NOTIFY.")
    parser.add_argument("--run", type=Path, help="Run directory. Defaults to the newest under output/.")
    parser.add_argument("--verify", action="store_true", help="Compare against existing labels, write nothing.")
    args = parser.parse_args(argv)

    run = args.run or _newest_run(Path("output"))
    if run is None or not run.exists():
        print("No run directory found under output/.", file=sys.stderr)
        return 1

    job_path = run / "jobs" / f"{args.check_id}.json"
    labels_path = run / "jobs" / f"{args.check_id}-labels.csv"
    if not job_path.exists() or not labels_path.exists():
        print(f"Missing job or labels file for {args.check_id} in {run}.", file=sys.stderr)
        return 1

    return prelabel(args.check_id, job_path, labels_path, verify=args.verify)


if __name__ == "__main__":
    raise SystemExit(main())
