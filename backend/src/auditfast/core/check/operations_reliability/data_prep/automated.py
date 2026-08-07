"""Operations & Reliability · Data Prep — does the pipeline survive a bad day.

Retries, failure paths, alerts, and bounded runtimes. These never inspect where
data comes from or goes to.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, notebook_code
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
    id="PL-TIMEOUT", ref="IMPL-23", title="Pipeline activities set an explicit timeout (not Fabric's multi-day default) [PL-TIMEOUT]",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def explicit_timeouts(ctx: CheckContext) -> Verdict:
    """Activities set a real timeout instead of inheriting the multi-day default."""
    acts = activities(ctx.obj)

    def bounded(activity: dict) -> bool:
        timeout = (activity.get("policy") or {}).get("timeout")
        return bool(timeout) and str(timeout) not in DEFAULT_TIMEOUTS

    with_timeout = [a for a in acts if bounded(a)]
    return covered(len(with_timeout), len(acts),
                   f"{len(with_timeout)} of {len(acts)} activities set a non-default timeout")


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
