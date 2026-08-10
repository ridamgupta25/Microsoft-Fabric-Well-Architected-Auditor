"""Operations & Reliability · Data Prep — does the pipeline survive a bad day.

Retries, failure paths, alerts, and bounded runtimes. These never inspect where
data comes from or goes to.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, executable_code, notebook_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities, walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Timeout values Fabric/ADF apply by default — i.e. "nobody set this".
DEFAULT_TIMEOUTS = frozenset({"7.00:00:00", "0.12:00:00", "7.00:00", ""})

#: Copy-activity properties that make a bad row or unreadable file survivable
#: instead of fatal. Any one of them means the activity was configured to keep
#: going: skip the row, redirect it somewhere, or skip the offending file.
FAULT_TOLERANCE_KEYS = (
    "enableSkipIncompatibleRow",
    "redirectIncompatibleRowSettings",
    "skipErrorFile",
)
#: Where the skipped rows are recorded, so "skipped" does not mean "lost".
COPY_LOG_KEYS = ("logSettings", "logStorageSettings")

#: An activity that parks bad data rather than failing the run. Matched on the
#: activity's own name because the destination is a dataset reference we cannot
#: resolve — a Copy named "Write_Rejects_To_Quarantine" is the signal.
QUARANTINE_NAME_RE = re.compile(
    r"quarantin|reject|dead.?letter|bad.?record|error.?(?:row|record|table)|invalid",
    re.IGNORECASE,
)

#: A notebook only meets malformed input if it reads raw files in the first place.
FILE_READ_RE = re.compile(
    r"spark\s*\.\s*read\b|\.\s*read\s*\.\s*(?:json|csv|text|parquet|format)\s*\(|\.\s*load\s*\(",
    re.IGNORECASE,
)
#: Bad rows are kept: written aside, or isolated as a corrupt-record column.
BAD_RECORD_KEPT_RE = re.compile(
    r"badRecordsPath|_corrupt_record|columnNameOfCorruptRecord", re.IGNORECASE,
)
#: Bad rows are dropped — the run survives, but the records are gone silently.
BAD_RECORD_DROPPED_RE = re.compile(r"DROPMALFORMED", re.IGNORECASE)
#: The opposite of graceful handling: one bad row aborts the read.
FAILFAST_RE = re.compile(r"FAILFAST", re.IGNORECASE)
#: A hand-rolled quarantine: caught exception plus a write to a reject location.
EXCEPT_RE = re.compile(r"\bexcept\b", re.IGNORECASE)
QUARANTINE_WRITE_RE = re.compile(
    r"quarantin|reject|dead.?letter|bad.?record|error.?(?:table|record|row)|invalid.?record",
    re.IGNORECASE,
)

#: Activity types that ARE a notification (Teams / email / webhook / Data Activator).
NOTIFY_TYPES = frozenset({"Teams", "Office365Outlook", "Outlook365", "SendEmail", "WebHook"})
#: Generic call activities that only count as a notifier when their *name* says so —
#: a Web/Function activity named "Send_Teams_Alert" posts to a webhook, but a Copy or
#: Lookup that merely contains "email" in its name does not.
NOTIFY_CALL_TYPES = frozenset({"Web", "WebActivity", "AzureFunctionActivity", "Function"})
NOTIFY_NAME_RE = re.compile(r"notif|alert|email|teams", re.IGNORECASE)
_DATA_MOVE_TYPES = frozenset({"Copy", "Script", "TridentNotebook", "SqlServerStoredProcedure", "Lookup"})
_RECORD_ERROR = re.compile(
    r"reject|invalid|quarantine|dead[_ -]?letter|error[_ -]?output|bad[_ -]?records?|"
    r"failed[_ -]?records?|_corrupt|_rejected|_quarantine",
    re.IGNORECASE,
)
_IDEMPOTENT_PATTERN = re.compile(
    r"\bmerge\b|\bupsert\b|overwrite|replace|delete[_ -]?then[_ -]?insert|"
    r"dedup|dropduplicates|drop_duplicates|idempotent|batch[_ -]?id|run[_ -]?id|"
    r"watermark|checkpoint|load[_ -]?date",
    re.IGNORECASE,
)
_CLEANUP_ACTIVITY = re.compile(
    r"delete|truncate|purge|cleanup|clean[_ -]?up|remove|clear",
    re.IGNORECASE,
)
_WRITE_SIGNAL = re.compile(
    r"\.write\b|saveastable\b|insert\s+into|merge\s+into|create\s+or\s+replace|overwrite",
    re.IGNORECASE,
)
_CLEANUP_BEFORE_WRITE = re.compile(
    r"(?:delete\s+from|truncate\s+table|drop\s+table|"
    r"mssparkutils\.fs\.rm|notebookutils\.fs\.rm|fs\.rm|rm\s*\().*?"
    r"(?:\.write\b|saveastable\b|insert\s+into|merge\s+into|overwrite)",
    re.IGNORECASE | re.DOTALL,
)
_RESTART_BOUNDARY = re.compile(
    r"restart|resume|checkpoint|watermark|batch[_ -]?id|"
    r"from[_ -]?(?:activity|failure|checkpoint)|start[_ -]?from|control[_ -]?(?:table|store)",
    re.IGNORECASE,
)


@check(
    id="PL-RETRY", ref="2.4.1", title="All pipeline activities have appropriate retry policies configured (copy, notebook, lookup, web, ForEach)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def retry_policy(ctx: CheckContext) -> Verdict:
    """Activities that call external systems retry at least once before failing."""
    acts = activities(ctx.obj)
    with_retry = [a for a in acts if (a.get("policy") or {}).get("retry", 0) >= 1]
    return covered(len(with_retry), len(acts),
                   f"{len(with_retry)} of {len(acts)} activities have a retry policy")


@check(
    id="PL-FAILPATH", ref="2.4.3", title="On-failure paths defined for critical activities",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def failure_path(ctx: CheckContext) -> Verdict:
    """At least one activity has a dependency edge handling the Failed condition."""
    has_failure_edge = any(
        "Failed" in (dep.get("dependencyConditions") or [])
        for activity in activities(ctx.obj)
        for dep in (activity.get("dependsOn") or [])
    )
    return binary(has_failure_edge, "A Failed dependency path exists" if has_failure_edge
                  else "No on-failure path found")


@check(
    id="PL-NOTIFY", ref="2.4.5", title="Pipeline failure triggers notification (Data Activator, email, Teams)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def failure_notification(ctx: CheckContext) -> Verdict:
    """Someone is told when the pipeline fails — Teams, email, or Data Activator.

    A notifier is a Teams/Outlook/email/webhook activity, or a generic Web/Function
    call whose own name marks it as one. Container activities (If Condition,
    ForEach, Switch, Until) are searched too, so a notifier nested inside an
    ``If Condition`` still counts. A Copy/Lookup that merely has "email" in its
    name is not treated as a notifier.
    """
    def is_notifier(activity: dict) -> bool:
        activity_type = activity.get("type")
        if activity_type in NOTIFY_TYPES:
            return True
        return (activity_type in NOTIFY_CALL_TYPES
                and bool(NOTIFY_NAME_RE.search(activity.get("name", ""))))

    has_notify = any(is_notifier(a) for a in walk_activities(ctx.obj))
    return binary(has_notify, "A notification activity is present" if has_notify
                  else "No notification activity found")


@check(
    id="PL-RESTART", ref="9.1.1",
    title="Failed pipelines can be restarted from point of failure (not full re-run)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def restart_from_failure(ctx: CheckContext) -> Verdict:
    """Pipeline definitions expose a restart boundary or durable progress marker."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("Pipeline has no data-movement activity to restart")
    blob = json.dumps(ctx.obj)
    ok = bool(_RESTART_BOUNDARY.search(blob))
    return binary(ok, "Restart boundary/checkpoint or durable progress marker detected" if ok
                  else "No restart-from-failure boundary; a retry may re-run the full pipeline")


@check(
    id="PL-TIMEOUT", ref="IMPL-23", title="Pipeline activities set an explicit timeout (not Fabric's multi-day default) [PL-TIMEOUT]",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def explicit_timeouts(ctx: CheckContext) -> Verdict:
    """Activities set a *custom* timeout instead of Fabric's multi-day default.

    Fabric always writes a timeout, so a value being present is not enough: an
    activity that keeps the platform default (0.12:00:00 / 7.00:00:00) is a
    *partial* — a deliberate timeout was not defined — not a failure. Only
    activities that declare a timeout at all are assessed.
    """
    def timeout_of(activity: dict) -> str:
        return str((activity.get("policy") or {}).get("timeout") or "").strip()

    timed = [t for a in activities(ctx.obj) if (t := timeout_of(a))]
    if not timed:
        return not_applicable("No activity declares a timeout to assess")
    custom = [t for t in timed if t not in DEFAULT_TIMEOUTS]
    if len(custom) == len(timed):
        return graded(3, f"All {len(timed)} activities with a timeout set a custom (non-default) value")
    defaults = ", ".join(sorted({t for t in timed if t in DEFAULT_TIMEOUTS}))
    return graded(
        1,
        f"{len(timed) - len(custom)} of {len(timed)} activities keep Fabric's default timeout "
        f"({defaults}) — a custom timeout is not defined",
    )


@check(
    id="PL-POISON", ref="9.1.3",
    title="Poison message / corrupt file handling (quarantine, not crash)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def poison_message_handling(ctx: CheckContext) -> Verdict:
    """One bad row or unreadable file is parked, not allowed to kill the run.

    Judged on the Copy activities, because they are what meets untrusted source
    data. Fabric's fault-tolerance settings are the mechanism: skip the
    incompatible row, redirect it to a log, or skip the unreadable file. A
    pipeline that instead routes failures to a quarantine activity counts too,
    which is why an on-failure edge into a reject/dead-letter activity is
    accepted as evidence.

    This reads the pipeline's *configuration* — it shows the safeguard is wired
    up, not that bad data has actually been caught.
    """
    copies = [a for a in walk_activities(ctx.obj) if a.get("type") == "Copy"]
    if not copies:
        return not_applicable("Pipeline has no Copy activity to ingest untrusted data")

    def tolerant(activity: dict) -> bool:
        props = activity.get("typeProperties") or {}
        return any(props.get(key) for key in FAULT_TOLERANCE_KEYS)

    def logged(activity: dict) -> bool:
        props = activity.get("typeProperties") or {}
        return any(props.get(key) for key in COPY_LOG_KEYS)

    safe = [a for a in copies if tolerant(a)]
    # A quarantine route reached on failure protects the whole pipeline, so it
    # counts for every Copy rather than for one activity.
    quarantine = [a for a in walk_activities(ctx.obj)
                  if QUARANTINE_NAME_RE.search(a.get("name", ""))
                  and any("Failed" in (dep.get("dependencyConditions") or [])
                          for dep in (a.get("dependsOn") or []))]
    if quarantine and not safe:
        names = ", ".join(sorted(a.get("name", "?") for a in quarantine))
        return covered(
            len(copies), len(copies),
            f"No Copy sets fault tolerance, but failures route to a quarantine "
            f"activity: {names}",
        )

    detail = (f"{len(safe)} of {len(copies)} Copy activities skip/redirect bad rows "
              f"or files")
    if safe and not any(logged(a) for a in safe):
        detail += " — none records the skipped rows, so they are dropped silently"
    return covered(len(safe), len(copies), detail)


@check(
    id="NB-BADRECORDS", ref="9.1.3",
    title="Poison message / corrupt file handling (quarantine, not crash)",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_bad_records(ctx: CheckContext) -> Verdict:
    """A notebook reading raw files keeps bad rows instead of dying or dropping them.

    Graded rather than binary, because the options differ in how much they cost
    you. ``badRecordsPath`` or a ``_corrupt_record`` column keeps the rejects for
    inspection; ``DROPMALFORMED`` keeps the run alive but discards the records
    with no trace; ``FAILFAST`` aborts the whole read on the first bad row.

    ``PERMISSIVE`` is deliberately not credited on its own — it is Spark's
    default, so its presence proves nothing was decided.
    """
    code = notebook_code(ctx.obj)
    if not FILE_READ_RE.search(code):
        return not_applicable("Notebook does not read raw files")

    if BAD_RECORD_KEPT_RE.search(code):
        return graded(3, "Bad records are captured (badRecordsPath / corrupt-record column)")
    if EXCEPT_RE.search(code) and QUARANTINE_WRITE_RE.search(code):
        return graded(3, "Read errors are caught and routed to a quarantine/reject location")
    if BAD_RECORD_DROPPED_RE.search(code):
        return graded(1, "Uses DROPMALFORMED — the run survives but bad records are "
                         "discarded with no record of them")
    if FAILFAST_RE.search(code):
        return graded(0, "Uses FAILFAST — a single malformed record aborts the read")
    return graded(0, "Reads raw files with no bad-record handling — one malformed "
                     "record fails the notebook")


@check(
    id="PL-RETRY-VALUES", ref="2.4.2", title="Retry count and interval follow reasonable patterns (not infinite retries)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def retry_values(ctx: CheckContext) -> Verdict:
    """Where retries are configured, the count is bounded and an interval is set.

    A retry with no interval hammers a failing dependency; an unbounded count
    turns a transient error into an hours-long hang. Only activities that already
    declare a retry are judged — whether to retry at all is PL-RETRY's job.
    """
    with_retry = [a for a in activities(ctx.obj)
                  if (a.get("policy") or {}).get("retry", 0) >= 1]
    if not with_retry:
        return not_applicable("No activity declares a retry policy")

    def sane(activity: dict) -> bool:
        policy = activity.get("policy") or {}
        count = policy.get("retry", 0)
        interval = policy.get("retryIntervalInSeconds", 0)
        return 1 <= count <= 10 and bool(interval) and interval > 0

    good = [a for a in with_retry if sane(a)]
    return covered(
        len(good), len(with_retry),
        f"{len(good)} of {len(with_retry)} retrying activities set a bounded count "
        f"(1-10) and a positive interval",
    )


@check(
    id="PL-IDEMPOTENT", ref="2.4.6",
    title="Idempotency ensured — re-running a failed pipeline does not produce duplicates",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_idempotent(ctx: CheckContext) -> Verdict:
    """A failed pipeline can be rerun without appending duplicate records."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for rerun idempotency")
    if all((a.get("type") or "") == "TridentNotebook" for a in data_acts):
        return not_applicable(
            "Rerun-safety runs inside a notebook — idempotency is not assessable from "
            "the pipeline definition (assess it in the notebook checks)"
        )
    blob = json.dumps(ctx.obj)
    explicit_pattern = bool(_IDEMPOTENT_PATTERN.search(blob))
    cleanup_before_write = any(
        _CLEANUP_ACTIVITY.search(activity.get("name", ""))
        and any(
            dependency.get("activity") == activity.get("name")
            and "Succeeded" in (dependency.get("dependencyConditions") or [])
            for data_activity in data_acts
            for dependency in (data_activity.get("dependsOn") or [])
        )
        for activity in acts
    )
    ok = explicit_pattern or cleanup_before_write
    return binary(
        ok,
        "Rerun-safety pattern detected (atomic upsert, overwrite, deduplication, run key, or ordered cleanup)"
        if ok else
        "No rerun-safety pattern detected; a failed rerun may append duplicate records",
    )


@check(
    id="NB-IDEMPOTENT", ref="9.3.1",
    title="All pipelines and notebooks are idempotent (safe to re-run)",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_idempotent(ctx: CheckContext) -> Verdict:
    """Notebook writes are rerun-safe through merge/upsert, dedup, or ordered cleanup."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _WRITE_SIGNAL.search(code):
        return not_applicable("Notebook has no write operation to assess for rerun idempotency")
    explicit_pattern = bool(_IDEMPOTENT_PATTERN.search(code))
    cleanup_before_write = bool(_CLEANUP_BEFORE_WRITE.search(code))
    ok = explicit_pattern or cleanup_before_write
    return binary(
        ok,
        "Rerun-safety pattern detected (merge/upsert, overwrite, deduplication, run key, or ordered cleanup)"
        if ok else
        "No rerun-safety pattern detected; a failed rerun may append duplicate records",
    )


@check(
    id="PL-DEADLETTER", ref="2.4.4",
    title="Failed records captured to dead-letter / quarantine area (not silently dropped or halting good records)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_deadletter(ctx: CheckContext) -> Verdict:
    """Invalid records are routed to a retained dead-letter or quarantine output."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for failed records")
    blob = json.dumps(ctx.obj)
    if not _RECORD_ERROR.search(blob):
        return not_applicable("No record-validation or error-routing pattern found")
    ok = any(_RECORD_ERROR.search(json.dumps(a)) for a in acts)
    return binary(
        ok,
        "Failed/invalid records have an explicit error or quarantine route" if ok
        else "Record errors are mentioned but no activity-level quarantine route was found",
    )


@check(
    id="NB-DEADLETTER", ref="5.1.10",
    title="DQ quarantine pattern: failed records routed to error tables with failure reason",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_deadletter(ctx: CheckContext) -> Verdict:
    """Invalid records are retained in a dead-letter or quarantine output."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _RECORD_ERROR.search(code):
        return not_applicable("Notebook has no record-validation or error-routing pattern")
    ok = bool(re.search(
        r"(?:write|save|insert|append|quarantine|dead[_ -]?letter|reject|invalid)",
        code,
        re.IGNORECASE,
    ))
    return binary(
        ok,
        "Notebook routes failed/invalid records to a retained output" if ok
        else "Notebook detects record errors but does not retain a failed-record output",
    )


# =============================================================================
# 9.3.3 — transaction boundaries around a multi-step operation
# =============================================================================
#
# Which reading of "transaction boundary" this check uses, and why.
#
# The checklist point names two surfaces (Notebook; Warehouse) but a boundary
# means something different on each, and only one of them is readable per object:
#
#   * Fabric Warehouse T-SQL genuinely supports BEGIN TRANSACTION / COMMIT /
#     ROLLBACK — but that T-SQL lives inside stored procedures the API does not
#     return, or in a pipeline Script activity, which is a *pipeline* object.
#   * Spark has no multi-statement transaction at all. Delta gives atomicity per
#     *statement*, so a notebook that writes three times has three independent
#     commits and can fail half-done.
#
# So the reading implemented here is the notebook one, judged on the notebook the
# check is scoped to: **when a notebook performs more than one write, that
# sequence must be bounded** — by an explicit T-SQL transaction where it drives
# a Warehouse, by staging-then-atomic-swap, or by failure compensation that
# undoes the partial work. A notebook whose writes are each individually atomic
# but unbounded as a sequence sits in the middle: no single write leaves a torn
# table, but a mid-sequence failure still leaves the *set* of tables
# inconsistent. A single-write notebook has no multi-step operation to bound and
# is N/A, never a failure.

#: A *terminal* write — the call or statement that actually commits. Chosen so
#: one write expression counts once: ``df.write.mode(...).saveAsTable(t)`` matches
#: only at ``saveAsTable``, and a Delta merge builder only at its ``execute()``.
_TXN_WRITE_OP = re.compile(
    r"\.saveAsTable\s*\(|\.insertInto\s*\(|\.toTable\s*\(|\.save\s*\(|"
    r"\bINSERT\s+(?:INTO|OVERWRITE)\b|\bMERGE\s+INTO\b|\.execute\s*\(\s*\)|"
    r"\bUPDATE\s+[`\"\[]?\w[\w.`\"\]]*\s+SET\b|\bDELETE\s+FROM\b|"
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b|\bTRUNCATE\s+TABLE\b",
    re.IGNORECASE,
)
#: A write whose *own* effect is all-or-nothing. Counted, not paired, so the
#: comparison against the write count is an approximation — it only decides the
#: middle band (1 vs 0) and never the N/A gate.
_TXN_ATOMIC_WRITE = re.compile(
    r"\bMERGE\s+INTO\b|\.merge\s*\(|\bINSERT\s+OVERWRITE\b|"
    r"mode\s*\(\s*[\"']overwrite[\"']\s*\)|replaceWhere|"
    r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b",
    re.IGNORECASE,
)
#: An explicit T-SQL transaction — the real thing, available when the notebook
#: drives a Warehouse or SQL database over JDBC/pyodbc.
_TXN_EXPLICIT = re.compile(
    r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bSET\s+IMPLICIT_TRANSACTIONS\b|"
    r"\bconn(?:ection)?\.commit\s*\(|\.rollback\s*\(|\bautocommit\s*=\s*False\b",
    re.IGNORECASE,
)
#: Stage everything aside, then swap it in with one atomic rename/replace.
#: ``_tmp``/``_temp`` are deliberately absent — they are ordinary variable
#: suffixes, so including them would let any notebook holding a ``df_tmp`` pass
#: on an unrelated ``CREATE OR REPLACE TABLE``.
_TXN_STAGING_SWAP = re.compile(
    r"(?:_stg\b|_stage\b|_staging\b|staging[_.]|\bstage_)"
    r"[\s\S]{0,2000}?"
    r"(?:\bALTER\s+TABLE\b[\s\S]{0,200}?\bRENAME\b|\bRENAME\s+TO\b|"
    r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b|\bREPLACE\s+TABLE\b|\bSWAP\b)",
    re.IGNORECASE,
)
#: A caught failure that undoes or cleans up the partial work — the hand-rolled
#: compensating transaction. Bare words like "restore" or "revert" are excluded:
#: a log message mentioning one is not a compensation, so only an operation that
#: actually reverses or removes the partial write counts.
_TXN_COMPENSATION = re.compile(
    r"\bexcept\b[\s\S]{0,800}?"
    r"(?:\brollback\b|compensat|"
    r"\bRESTORE\s+TABLE\b|VERSION\s+AS\s+OF|restoreToVersion|"
    r"\bDROP\s+TABLE\b|\bDELETE\s+FROM\b|\bTRUNCATE\s+TABLE\b|fs\.rm\s*\()",
    re.IGNORECASE,
)


@check(
    id="NB-TXN-BOUNDARY", ref="9.3.3",
    title="Transaction boundaries defined for multi-step operations (incl. Warehouse loads)",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_transaction_boundary(ctx: CheckContext) -> Verdict:
    """A notebook writing more than once bounds the sequence so it cannot half-apply.

    Any one of three boundaries satisfies the point: an explicit T-SQL
    transaction (the Warehouse case), staging the work and swapping it in
    atomically, or catching the failure and compensating for the partial writes.
    Individually atomic writes with no boundary across them score in the middle —
    each table survives, the set of them does not. Read from *executable* code
    only, so a commented-out rollback proves nothing.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")

    code = executable_code(ctx.obj)
    writes = _TXN_WRITE_OP.findall(code)
    if len(writes) < 2:
        return not_applicable(
            f"Notebook performs {len(writes)} write operation(s) — no multi-step "
            f"operation to bound"
        )

    if _TXN_EXPLICIT.search(code):
        return graded(3, f"{len(writes)} writes bounded by an explicit transaction "
                         f"(BEGIN/COMMIT/ROLLBACK or a managed connection commit)")
    if _TXN_STAGING_SWAP.search(code):
        return graded(3, f"{len(writes)} writes staged and swapped in atomically, so a "
                         f"mid-sequence failure leaves the published tables untouched")
    if _TXN_COMPENSATION.search(code):
        return graded(3, f"{len(writes)} writes bounded by failure compensation — the "
                         f"partial work is rolled back, restored, or cleaned up on error")

    atomic = len(_TXN_ATOMIC_WRITE.findall(code))
    if atomic >= len(writes):
        return graded(
            1,
            f"All {len(writes)} writes are individually atomic (merge/overwrite/replace) "
            f"but the sequence is unbounded — a failure part-way leaves the set of "
            f"targets inconsistent",
        )
    return graded(
        0,
        f"{len(writes)} dependent writes with no transaction, staging swap, or "
        f"compensation — a failure part-way through leaves the load half-applied",
    )


# =============================================================================
# MLC Cat-1 · resilience (9.1.2 transient retry, 9.3.4 post-failure integrity)
# =============================================================================

# -- 9.1.2 transient failure handling: retries with backoff --------------------
#: A retry loop written in notebook code. The pipeline side of this point is
#: already covered by ``PL-RETRY`` (2.4.1) and ``PL-RETRY-VALUES`` (2.4.2), which
#: read the activity ``policy`` block; a notebook that calls an API or a flaky
#: source has to implement its own, and nothing else in the registry looks for it.
_NB_RETRY = re.compile(
    r"@retry\b|\bretry\s*\(|tenacity|backoff|"
    r"for\s+attempt\s+in\s+range|for\s+_?\s*retry\s+in\s+range|"
    r"while\s+attempt\s*<|max_retries|max_attempts|retry_count",
    re.IGNORECASE,
)
#: The wait between attempts grows instead of hammering the source immediately.
_NB_BACKOFF = re.compile(
    r"exponential|backoff|2\s*\*\*\s*\w+|\w+\s*\*\*\s*attempt|"
    r"sleep\s*\(\s*\w*\s*\*|sleep\s*\(\s*\d+\s*\*\*|"
    r"wait_exponential|wait_fixed|retry_delay|jitter",
    re.IGNORECASE,
)
#: The notebook reaches something that can fail transiently in the first place.
_NB_REMOTE_CALL = re.compile(
    r"requests\.(?:get|post|put)|http[s]?://|urllib|"
    r"\.load\s*\(|spark\.read\b|jdbc|\bapi[_ -]?call\b|client\.\w+\(",
    re.IGNORECASE,
)


@check(
    id="NB-RETRY-BACKOFF", ref="9.1.2",
    title="Transient failure handling: notebook retries with backoff",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_retry_backoff(ctx: CheckContext) -> Verdict:
    """A notebook calling a remote source retries, and waits longer each time.

    Deliberately notebook-only. ``PL-RETRY`` (2.4.1) and ``PL-RETRY-VALUES``
    (2.4.2) already judge the pipeline half of this checklist point from the
    activity ``policy`` block; repeating it here would double-count. Spark's own
    task-level retries do not help when the notebook itself calls an API.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _NB_REMOTE_CALL.search(code):
        return not_applicable("Notebook makes no remote/source call that could fail "
                              "transiently")
    if not _NB_RETRY.search(code):
        return binary(False, "Notebook reads a remote source with no retry — a single "
                             "transient error fails the whole run")
    if not _NB_BACKOFF.search(code):
        return graded(1, "Notebook retries but with no backoff or delay between attempts — "
                         "it retries into the same overload it just hit")
    return graded(3, "Notebook retries transient failures with a backoff/delay")


# -- 9.3.4 data integrity validated across layers after failures --------------
#: The notebook is written to run after something went wrong.
_RECOVERY_CONTEXT = re.compile(
    r"\brecover\w*|\brestart\w*|\breprocess\w*|\breplay\b|\bbackfill\b|"
    r"\bresume\b|failed[_ -]?run|after[_ -]?failure|repair\b|reconcile[_ -]?after",
    re.IGNORECASE,
)
#: It proves the layers agree before letting the run continue.
_INTEGRITY_ASSERT = re.compile(
    r"assert\b|\braise\s+\w*(?:Error|Exception)|"
    r"\.count\s*\(\s*\)\s*(?:==|!=|<|>)|"
    r"if\s+\w*count\w*\s*(?:==|!=|<|>)|"
    r"mismatch|out[_ -]?of[_ -]?sync|integrity[_ -]?check|validate[_ -]?(?:layer|integrity)",
    re.IGNORECASE,
)
#: …and it looks at more than one layer while doing so.
_CROSS_LAYER = re.compile(
    r"bronze[\s\S]{0,400}?silver|silver[\s\S]{0,400}?gold|"
    r"source[\s\S]{0,400}?target|staging[\s\S]{0,400}?final",
    re.IGNORECASE,
)


@check(
    id="NB-POST-FAILURE-INTEGRITY", ref="9.3.4",
    title="Data integrity validated across layers after failures",
    pillar=Pillar.OPERATIONS, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_post_failure_integrity(ctx: CheckContext) -> Verdict:
    """A recovery/rerun path proves the layers still agree before continuing.

    Narrower than ``NB-LAYER-RECON`` (5.4.6), which asks whether the routine
    Silver-to-Gold hop reconciles. This gates on *recovery* wording — the notebook
    is doing a restart, replay or backfill — and asks whether that path re-checks
    integrity rather than trusting a partially-written layer.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _RECOVERY_CONTEXT.search(code):
        return not_applicable("Notebook implements no recovery / restart / replay path")
    if not _CROSS_LAYER.search(code):
        return not_applicable("Recovery path touches only one layer — nothing to "
                              "cross-check against")
    ok = bool(_INTEGRITY_ASSERT.search(code))
    return binary(ok, "Recovery path validates integrity across layers before continuing"
                  if ok else
                  "Recovery/replay path reprocesses across layers but never asserts the "
                  "layers agree — a partially-written layer is trusted as complete")
