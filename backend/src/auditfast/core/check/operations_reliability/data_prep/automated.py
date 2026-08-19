"""Operations & Reliability · Data Prep — does the pipeline survive a bad day.

Retries, failure paths, alerts, and bounded runtimes. These never inspect where
data comes from or goes to.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import (
    NOTEBOOK_LAYERS,
    executable_code,
    notebook_code,
    strip_sql_comments,
)
from auditfast.core.check._pipeline import (
    PIPELINE_LAYERS,
    activities,
    script_sql,
    walk_activities,
)
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable, note
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
_ERROR_ROUTE_NAME = re.compile(
    r"quarantin|reject|dead.?letter|bad.?record|invalid|"
    r"(?:log|capture|write|route).*(?:fail|error)|(?:fail|error).*(?:log|capture|write|route)",
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def restart_from_failure(ctx: CheckContext) -> Verdict:
    """Restart-from-failure is a Fabric platform capability, so this is reported, not scored.

    **Why this is unscored.** Fabric Data Factory provides rerun-from-failure for
    *every* pipeline, with nothing to configure: the run-history view offers
    "rerun the entire pipeline, or rerun only from the failed activity"
    (``learn.microsoft.com/fabric/data-factory/monitor-pipeline-runs``). The
    capability the point asks about is therefore always present, and searching a
    pipeline definition for a "restart boundary" failed pipelines for not
    declaring something Fabric never asked them to declare.

    **What is still worth reporting.** A rerun only helps if re-running an
    activity is *safe*. That is idempotency, which ``PL-IDEMPOTENT`` (ref 2.4.6)
    scores, and checkpointing, which shows up as watermark/incremental state. So
    this emits an unscored note naming what the pipeline does have, and points at
    the check that does the judging.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("Pipeline has no data-movement activity to restart")
    blob = json.dumps(ctx.obj)
    marker = bool(_RESTART_BOUNDARY.search(blob))
    return note(
        "Fabric supports rerun-from-failed-activity for every pipeline with no "
        "configuration, so restart capability is not scored here"
        + (". This pipeline also keeps a durable progress marker (watermark or "
           "checkpoint), so a rerun can resume rather than repeat" if marker else
           ". No durable progress marker was found, so a rerun repeats the "
           "activity - whether that is safe is scored by ref 2.4.6 (idempotency)")
    )


@check(
    id="PL-TIMEOUT", ref="13.4.1", title="Pipeline activities set an explicit timeout (not Fabric's multi-day default) [PL-TIMEOUT]",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.LOW,
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
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
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
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def retry_values(ctx: CheckContext) -> Verdict:
    """Where retries are configured, the count is bounded and an interval is set.

    A retry with no interval hammers a failing dependency. Only activities that
    already declare a retry are judged - whether to retry at all is PL-RETRY's job.

    **On the upper bound.** Fabric's documented range is 1-1000 with no
    infinite option (``learn.microsoft.com/fabric/data-factory/activity-overview``),
    so an unbounded retry is not something a pipeline *can* configure. What a high
    count does mean is a long tail of failure: the threshold below is a
    house opinion, not a platform limit, and is settable per project.

    **On the interval.** Fabric itself rejects a Copy-activity interval outside
    30-86400 ("retryIntervalInSeconds cannot be '0'"), so for Copy activities the
    positive-interval test is a floor the platform already enforces and will
    essentially never fail. It is kept because it is not free everywhere: the
    property can be absent entirely, other activity types are not known to share
    the same validation, and a definition can reach the tenant through the REST
    API, Git sync or a deployment pipeline without passing portal validation.
    Treat a real failure here as a signal about *how the pipeline was authored*.
    In practice the discriminating half of this check is the retry-count bound.

    **What this cannot determine.** A retry count or interval supplied as a
    pipeline expression (``@pipeline().parameters.retries``) resolves only at run
    time, so its value is not readable from the definition. Those activities are
    excluded from the ratio and reported, rather than guessed at or scored 0.
    (The Fabric portal exposes retry only as a numeric spinner, so this shape
    arrives through the API/Git rather than the UI.)

    An activity with an explicit ``retry: 0`` but a parameterised interval is
    reported separately as inert configuration: the back-off was made dynamic and
    the retry never enabled, so the interval can never take effect. It is not
    scored here - whether an activity should retry at all is PL-RETRY's question.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    # walk_activities, not activities: a Copy inside a ForEach/If/Switch/Until is
    # the commonest Fabric shape, and judging only the top level both misses bad
    # nested retries and reports N/A for a pipeline that does configure them.
    acts = walk_activities(ctx.obj)

    # Partition in one pass. An activity whose retry *or* interval is a run-time
    # expression cannot be judged either way, so it leaves the scored population
    # rather than failing it.
    with_retry: list[dict] = []
    dynamic: list[dict] = []
    inert: list[dict] = []
    for activity in acts:
        count = _retry_count(activity)
        if _retry_is_dynamic(activity):
            if count == 0:
                inert.append(activity)      # a dynamic back-off that can never run
            else:
                dynamic.append(activity)
        elif count is not None and count >= 1:
            with_retry.append(activity)

    inert_note = (
        f". A further {len(inert)} activity(ies) parameterise a retry interval but set "
        f"retry to 0, so the interval can never take effect"
    ) if inert else ""

    if not with_retry:
        if dynamic:
            return not_applicable(
                f"{len(dynamic)} activity(ies) set retry behaviour through a pipeline "
                f"expression, which resolves only at run time and cannot be read from "
                f"the definition" + inert_note
            )
        if inert:
            return not_applicable(
                f"No activity declares a retry policy. {len(inert)} activity(ies) "
                f"parameterise a retry interval but set retry to 0, so the interval "
                f"can never take effect"
            )
        return not_applicable(
            f"Pipeline '{ctx.obj_name}' has no activity with a positive retry count; "
            f"whether activities should retry at all is PL-RETRY's question (ref 2.4.1)"
        )

    limit = ctx.setting("max_retry_count", _DEFAULT_MAX_RETRY)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = _DEFAULT_MAX_RETRY

    def faults(activity: dict) -> list[str]:
        """Why this activity's retry settings are unsound, named so they can be fixed."""
        problems: list[str] = []
        count = _retry_count(activity)
        interval = _retry_interval(activity)
        if count is not None and count > limit:
            problems.append(f"count {count:g} exceeds maximum {limit}")
        if interval is None or interval <= 0:
            problems.append("missing or non-positive interval")
        return problems

    assessed = [(a, faults(a)) for a in with_retry]
    good = [a for a, problems in assessed if not problems]
    offenders = [
        f"{a.get('name') or '?'}: {', '.join(problems)}"
        for a, problems in assessed if problems
    ]
    evidence = (
        f"{len(good)} of {len(with_retry)} retrying activities set a bounded count "
        f"(1-{limit}) and a positive interval. Fabric allows 1-1000 and has no "
        f"infinite option, so the upper bound here is a project convention"
    )
    if offenders:
        evidence += f". Unsound: {'; '.join(sorted(offenders)[:_MAX_NAMED_OFFENDERS])}"
        if len(offenders) > _MAX_NAMED_OFFENDERS:
            evidence += f" (+{len(offenders) - _MAX_NAMED_OFFENDERS} more)"
    if dynamic:
        evidence += (
            f". A further {len(dynamic)} activity(ies) set retry behaviour through a "
            f"run-time expression and are not judged here"
        )
    evidence += inert_note
    return covered(len(good), len(with_retry), evidence)


#: Offending activities named before the evidence turns into a wall of text. The
#: count is always reported, so nothing is hidden - only the naming is capped.
_MAX_NAMED_OFFENDERS = 5


#: Retry attempts above which a failure takes long enough to look like a hang.
#: Fabric permits up to 1000; this is a house convention, overridable per project.
_DEFAULT_MAX_RETRY = 10


def _policy_number(activity: dict, key: str) -> float | None:
    """A numeric activity-policy value, or ``None`` when it is not statically known.

    Fabric accepts either a literal or an expression object
    (``{"value": "@pipeline().parameters.retries", "type": "Expression"}``) for
    ``retry`` and ``retryIntervalInSeconds``. Comparing the latter with ``<=``
    raises, so the two cases are separated here: a real number, or "not readable
    from the definition". Booleans are rejected because ``True`` is an ``int``.
    """
    value = (activity.get("policy") or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _retry_count(activity: dict) -> float | None:
    return _policy_number(activity, "retry")


def _retry_interval(activity: dict) -> float | None:
    return _policy_number(activity, "retryIntervalInSeconds")


def _retry_is_dynamic(activity: dict) -> bool:
    """True when retry behaviour is set, but only as a run-time expression."""
    policy = activity.get("policy") or {}
    return any(
        key in policy and _policy_number(activity, key) is None
        for key in ("retry", "retryIntervalInSeconds")
    )


@check(
    id="PL-IDEMPOTENT", ref="2.4.6",
    title="Idempotency ensured — re-running a failed pipeline does not produce duplicates",
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
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
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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


#: Copy-activity settings that *are* a dead-letter route. Fabric writes skipped
#: rows to the location named in ``redirectIncompatibleRowSettings`` /
#: ``logSettings``, so their presence is the feature being configured, not a
#: word that happens to appear in the JSON.
_SKIP_SETTINGS = (
    "redirectincompatiblerowsettings", "logsettings", "skiperrorfile",
)

#: Activity types that can serve as a quarantine sink on a ``[Failed]`` branch.
_QUARANTINE_SINK_TYPES = frozenset({
    "Copy", "SqlServerStoredProcedure", "Script", "TridentNotebook",
    "Lookup", "AzureFunctionActivity", "WebActivity", "Web",
})


def _redirects_bad_rows(activity: dict) -> bool:
    """True when a Copy activity is configured to *retain* incompatible rows.

    ``enableSkipIncompatibleRow`` on its own silently drops them, which is the
    very failure this point is about; it counts only when paired with a redirect
    or log destination that keeps them.
    """
    props = activity.get("typeProperties") or {}
    keys = {str(k).lower() for k in props}
    if not (keys & set(_SKIP_SETTINGS)):
        return False
    return bool(props.get("enableSkipIncompatibleRow", True))


def _failure_branch_targets(acts: list[dict]) -> list[str]:
    """Names of activities that run *because* another activity failed."""
    targets = []
    for activity in acts:
        for dep in activity.get("dependsOn") or []:
            conditions = {str(c).lower() for c in (dep.get("dependencyConditions") or [])}
            if conditions & {"failed", "skipped"}:
                name = str(activity.get("name") or "").strip()
                if name and (activity.get("type") or "") in _QUARANTINE_SINK_TYPES:
                    targets.append(name)
    return targets


@check(
    id="PL-DEADLETTER", ref="2.4.4",
    title="Failed records captured to dead-letter / quarantine area (not silently dropped or halting good records)",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_deadletter(ctx: CheckContext) -> Verdict:
    """Invalid records are routed to a retained dead-letter or quarantine output.

    **Judged structurally, never by keyword.** An earlier version searched the
    whole pipeline JSON for words like ``error`` and ``reject``. On a real estate
    that matched *column names* streaming from the source - ``REJECT_CODE``,
    ``ERROR_DESC``, ``AUTHORIZATION_REJECTED`` - inside ``source.name`` /
    ``sink.name`` column mappings, and scored a PASS on pipelines that had no
    error handling whatsoever. Business data that happens to describe rejections
    is not a rejection route.

    What counts, all of them things Fabric actually configures:

    * a Copy activity that redirects incompatible rows to a retained location
      (``redirectIncompatibleRowSettings`` / ``logSettings`` / ``skipErrorFile``);
    * an activity that runs on a ``[Failed]`` dependency - the quarantine branch;
    * an activity whose own *name* marks it as the reject/quarantine step.

    **What it cannot determine.** Whether the redirected rows are ever reviewed,
    or whether a downstream notebook quarantines records the pipeline handed it.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for failed records")

    redirecting = [
        str(a.get("name") or "?") for a in data_acts if _redirects_bad_rows(a)
    ]
    failure_targets = _failure_branch_targets(acts)
    named_sinks = [
        str(a.get("name") or "?") for a in acts
        if _RECORD_ERROR.search(str(a.get("name") or ""))
    ]

    if redirecting:
        return binary(True, f"Incompatible rows are redirected to a retained "
                            f"location by: {', '.join(redirecting[:5])}")
    if named_sinks:
        return binary(True, f"A quarantine/reject step is present: {', '.join(named_sinks[:5])}")
    if failure_targets:
        return graded(2, f"{len(failure_targets)} activity(ies) run on a [Failed] "
                         f"dependency ({', '.join(failure_targets[:5])}), so a failure path "
                         f"exists - but nothing names a record-level quarantine sink, so "
                         f"whether the bad *rows* are retained could not be confirmed")
    return binary(
        False,
        f"Pipeline '{ctx.obj_name}' moves data but has no structural failed-record route: "
        f"none of the {len(data_acts)} data-movement activity(ies) redirects incompatible "
        f"rows, no activity runs on a [Failed] dependency, and no activity names a reject "
        f"sink - so a bad record either halts the run or is dropped. Column names "
        f"containing 'error'/'reject' are source data, not an error route, and do not count",
    )


#: A *genuine* dead-letter route is evidenced by either the DataFrame being
#: written (``rejected_df.write``) or the literal sink receiving it
#: (``saveAsTable("dq.quarantine")`` / SQL ``INSERT INTO dq.quarantine``).
_ERR_SINK = (
    r"(?:reject|invalid|quarantin|dead[_ -]?letter|error|bad[_ -]?rec|"
    r"fail(?:ed|ure)|corrupt|discard|exclus|violation|exception)"
)
_DATAFRAME_WRITE = re.compile(
    r"\b(?P<dataframe>[A-Za-z_]\w*)\s*\.\s*write\b",
    re.IGNORECASE,
)
_DATAFRAME_ASSIGNMENT = re.compile(
    r"(?m)^\s*(?P<dataframe>[A-Za-z_]\w*)\s*=",
)
_FAILED_ROW_OPERATION = re.compile(
    r"(?:filter|where)\s*\([^)]{0,240}(?:is_valid\s*(?:=|==)\s*false|"
    + _ERR_SINK + r")|"
    r"dropmalformed|badrecordspath|columnnameofcorruptrecord",
    re.IGNORECASE | re.DOTALL,
)
_ERROR_TARGET_WRITE = re.compile(
    r"(?:saveastable|insertinto|\.save)\s*\(\s*[fr]?[\"'`]"
    r"(?P<target>[^\"'`)]*" + _ERR_SINK + r"[^\"'`]*)[\"'`]"
    r"|insert\s+(?:into|overwrite)\s+(?:table\s+)?"
    r"(?P<sql_target>[\w.`\"']*" + _ERR_SINK + r"[\w.`\"']*)",
    re.IGNORECASE,
)
#: Any persist — a table write, insert, or file write. Distinguishes "wrote the
#: failed rows *somewhere*" (unverifiable sink → PARTIAL) from "detected errors
#: and wrote nothing" (dropped → FAIL).
_ANY_WRITE = re.compile(
    r"\.write\b|saveastable|insertinto|insert\s+(?:into|overwrite)|"
    r"create\s+or\s+replace|\.save\s*\(",
    re.IGNORECASE,
)


@check(
    id="NB-DEADLETTER", ref="5.1.10",
    title="DQ quarantine pattern: failed records routed to error tables with failure reason",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_deadletter(ctx: CheckContext) -> Verdict:
    """Failed records are retained in a distinctly-named dead-letter / quarantine table.

    Tiered so keyword co-occurrence can no longer score a pass — the old check
    matched any ``write``/``reject``/``invalid`` token, including ones sitting in a
    comment, so a notebook that merely *mentioned* "invalid" passed:

      * **PASS** — the *executable* code writes to a distinctly error / quarantine /
        reject / exclusion-named sink (an error-named ``saveAsTable`` / ``INSERT
        INTO`` target, or an error-named DataFrame being written).
      * **PARTIAL** — either the quarantine intent is only in **comments** (documented,
        not implemented), or the notebook flags record errors and writes to *some*
        table but no recognisably error-named sink could be confirmed (the target is
        often a runtime variable) — flagged for a human to verify.
      * **FAIL** — the executable code flags record errors but performs no write at
        all: the failed rows are detected and then dropped, nothing retained.
      * **N/A** — no record-error vocabulary anywhere (not a DQ notebook).
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    raw = notebook_code(ctx.obj)
    if not _RECORD_ERROR.search(raw):
        return not_applicable("Notebook has no record-validation or error-routing pattern")
    # Judge only uncommented code (strip Python ``#`` and SQL ``--`` / ``/* */``);
    # string literals are kept so a table name or embedded SQL still counts.
    code = strip_sql_comments(executable_code(ctx.obj))

    dataframe_writes = [match.group("dataframe") for match in _DATAFRAME_WRITE.finditer(code)]
    error_dataframe_writes = [
        name for name in dataframe_writes if re.search(_ERR_SINK, name, re.IGNORECASE)
    ]
    target_hit = _ERROR_TARGET_WRITE.search(code)
    if error_dataframe_writes or target_hit:
        evidence_parts: list[str] = []
        if error_dataframe_writes:
            evidence_parts.append(
                "failed-record DataFrame(s) written: "
                + ", ".join(sorted(set(error_dataframe_writes)))
            )
        if target_hit:
            target = target_hit.group("target") or target_hit.group("sql_target") or "?"
            evidence_parts.append(f"error/quarantine sink written: {target}")
        return binary(
            True,
            "Executable code retains failed/invalid records — " + "; ".join(evidence_parts),
        )

    if not _RECORD_ERROR.search(code):
        found = ", ".join(sorted({m.lower() for m in _RECORD_ERROR.findall(raw)}))
        return graded(
            1,
            f"Dead-letter / quarantine references ({found}) were found only in comments, "
            "not in the executable code. Looked for an actual write to a distinct error / "
            "quarantine / reject table (saveAsTable / INSERT INTO an error-named target) in "
            "the uncommented code and SQL queries but found none — the quarantine pattern "
            "is documented but not implemented",
        )

    assigned_error_dataframes = sorted({
        match.group("dataframe")
        for match in _DATAFRAME_ASSIGNMENT.finditer(code)
        if re.search(_ERR_SINK, match.group("dataframe"), re.IGNORECASE)
    })
    if not (
        assigned_error_dataframes
        or error_dataframe_writes
        or target_hit
        or _FAILED_ROW_OPERATION.search(code)
    ):
        return not_applicable(
            "Notebook mentions error/reject vocabulary only in table schemas, column "
            "names, or ordinary data writes; no structural failed-record detection or "
            "routing operation was found"
        )

    abandoned = sorted(set(assigned_error_dataframes) - set(dataframe_writes))
    if abandoned:
        return binary(
            False,
            "Executable code creates failed/rejected DataFrame(s) that are not written: "
            f"{', '.join(abandoned)}. Writes of clean/main DataFrames do not "
            "prove that dropped records were retained",
        )

    if _ANY_WRITE.search(code):
        return graded(
            1,
            "Executable code flags record errors (validation / reject) and writes to a "
            "table, but no distinctly error / quarantine / reject-named sink could be "
            "confirmed — the write target is a runtime variable, so it is not provable "
            "from the code that the failed records are retained with a failure reason "
            "rather than merged into the main output; verify manually",
        )

    return binary(
        False,
        "Executable code flags record errors (validation / reject) but performs no write "
        "at all — the failed/invalid records are detected and then dropped, not retained",
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
#: An explicit transaction requires both an opener and a completion operation.
#: A stray ``commit()``/``rollback()`` may belong to unrelated setup code.
_TXN_BEGIN = re.compile(
    r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bSET\s+IMPLICIT_TRANSACTIONS\s+ON\b|"
    r"\bautocommit\s*=\s*False\b",
    re.IGNORECASE,
)
_TXN_END = re.compile(r"\bCOMMIT(?:\s+TRAN(?:SACTION)?)?\b|\bROLLBACK\b|\.commit\s*\(|\.rollback\s*\(", re.IGNORECASE)
_TXN_CONTEXT = re.compile(r"\bwith\s+[^\n:]{0,120}?\.begin\s*\([^\n:]*\)\s*:", re.IGNORECASE)
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
#: A caught failure that undoes or cleans up partial work. The operation must be
#: inside the indented ``except`` suite; a later DROP/DELETE is normal notebook
#: logic and does not compensate for the failed sequence.
_TXN_COMPENSATION = re.compile(
    r"\brollback\b|compensat|\bRESTORE\s+TABLE\b|VERSION\s+AS\s+OF|"
    r"restoreToVersion|\bDROP\s+TABLE\b|\bDELETE\s+FROM\b|"
    r"\bTRUNCATE\s+TABLE\b|fs\.rm\s*\(",
    re.IGNORECASE,
)
_EXCEPT_SUITE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)except\b[^\n]*:\s*\n"
    r"(?P<body>(?:(?P=indent)[ \t]+[^\n]*(?:\n|$))+)",
)


def _transaction_compensation(code: str) -> str:
    """Return the compensating operation found inside an exception suite."""
    for suite in _EXCEPT_SUITE.finditer(code):
        match = _TXN_COMPENSATION.search(suite.group("body"))
        if match:
            return " ".join(match.group(0).split())
    return ""


def _explicit_transaction(code: str) -> bool:
    """Whether code proves a transaction lifecycle, not just a stray commit."""
    return bool(_TXN_CONTEXT.search(code) or (_TXN_BEGIN.search(code) and _TXN_END.search(code)))


@check(
    id="NB-TXN-BOUNDARY", ref="9.3.3",
    title="Transaction boundaries defined for multi-step operations (incl. Warehouse loads)",
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
            f"Notebook '{ctx.obj_name}' performs {len(writes)} terminal write "
            f"operation(s); at least 2 are required for a multi-step transaction "
            f"boundary assessment"
        )

    if _explicit_transaction(code):
        return graded(3, f"Notebook '{ctx.obj_name}' has {len(writes)} writes bounded "
                         f"by an explicit transaction (BEGIN/COMMIT/ROLLBACK or a "
                         f"managed connection commit)")
    if _TXN_STAGING_SWAP.search(code):
        return graded(3, f"Notebook '{ctx.obj_name}' has {len(writes)} writes staged "
                         f"and swapped in atomically, so a mid-sequence failure leaves "
                         f"the published tables untouched")
    compensation = _transaction_compensation(code)
    if compensation:
        return graded(3, f"Notebook '{ctx.obj_name}' has {len(writes)} writes bounded "
                         f"by failure compensation inside an exception handler "
                         f"(matched '{compensation}')")

    atomic = len(_TXN_ATOMIC_WRITE.findall(code))
    if atomic >= len(writes):
        return graded(
            1,
            f"Notebook '{ctx.obj_name}': all {len(writes)} writes are individually "
            f"atomic (merge/overwrite/replace), but the sequence is unbounded: it has "
            f"no transaction, staging swap, or exception-handler compensation; a "
            f"failure part-way leaves the target set inconsistent",
        )
    return graded(
        0,
        f"Notebook '{ctx.obj_name}': {len(writes)} dependent writes have no explicit "
        f"transaction, staging swap, or compensation inside an exception handler; "
        f"a failure part-way through leaves the load half-applied",
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
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
#: The notebook is written to run after something went wrong. Patterns allow
#: Python/SQL identifier separators, such as ``backfill_mode`` and ``failed_run``.
_RECOVERY_CONTEXTS = (
    ("recovery", re.compile(r"(?:^|\W|_)recover\w*", re.I)),
    ("restart", re.compile(r"(?:^|\W|_)restart\w*", re.I)),
    ("reprocessing", re.compile(r"(?:^|\W|_)reprocess\w*", re.I)),
    ("replay", re.compile(r"(?:^|\W|_)replay(?:\W|_|$)", re.I)),
    ("backfill", re.compile(r"(?:^|\W|_)backfill(?:\W|_|$)", re.I)),
    ("resume", re.compile(r"(?:^|\W|_)resume(?:\W|_|$)", re.I)),
    ("failed run", re.compile(r"failed[_ -]?run", re.I)),
    ("after failure", re.compile(r"after[_ -]?failure", re.I)),
    ("repair", re.compile(r"(?:^|\W|_)repair(?:\W|_|$)", re.I)),
    ("post-failure reconciliation", re.compile(r"reconcile[_ -]?after", re.I)),
)
#: Common names for adjacent or explicitly compared data layers. Each pair is
#: bounded so unrelated mentions elsewhere in a large notebook do not qualify.
_CROSS_LAYER_PAIRS = (
    ("Bronze", "Silver", re.compile(r"bronze[\s\S]{0,400}?silver|silver[\s\S]{0,400}?bronze", re.I)),
    ("Silver", "Gold", re.compile(r"silver[\s\S]{0,400}?gold|gold[\s\S]{0,400}?silver", re.I)),
    ("Source", "Target", re.compile(r"source[\s\S]{0,400}?target|target[\s\S]{0,400}?source", re.I)),
    ("Staging", "Final", re.compile(r"staging[\s\S]{0,400}?final|final[\s\S]{0,400}?staging", re.I)),
    ("Raw", "Curated", re.compile(
        r"(?:^|\W|_)raw(?:\W|_)[\s\S]{0,400}?curated|"
        r"curated[\s\S]{0,400}?(?:^|\W|_)raw(?:\W|_|$)",
        re.I,
    )),
    ("Landing", "Curated", re.compile(r"landing[\s\S]{0,400}?curated|curated[\s\S]{0,400}?landing", re.I)),
)
_COUNT_COMPARISON = re.compile(
    r"\.count\s*\(\s*\)\s*(?:==|!=|<|>|<=|>=)|"
    r"\b\w*count\w*\s*(?:==|!=|<|>|<=|>=)\s*\w*count\w*",
    re.IGNORECASE,
)
_KEY_SET_COMPARISON = re.compile(
    r"left[_ -]?anti|exceptAll\s*\(|\.subtract\s*\(|mismatch|out[_ -]?of[_ -]?sync",
    re.IGNORECASE,
)
_VALUE_COMPARISON = re.compile(
    r"checksum|hash[_ -]?(?:diff|compare|match)|compare[_ -]?(?:values|columns)|"
    r"validate[_ -]?(?:layer|integrity)",
    re.IGNORECASE,
)
_INTEGRITY_ENFORCEMENT = re.compile(
    r"\bassert\b|\braise\s+\w*(?:Error|Exception)",
    re.IGNORECASE,
)


def _cross_layer_pair(code: str) -> tuple[str, str] | None:
    for first, second, pattern in _CROSS_LAYER_PAIRS:
        if pattern.search(code):
            return first, second
    return None


def _recovery_contexts(code: str) -> list[str]:
    return [label for label, pattern in _RECOVERY_CONTEXTS if pattern.search(code)]


def _integrity_methods(code: str) -> list[str]:
    methods: list[str] = []
    if _COUNT_COMPARISON.search(code):
        methods.append("row-count comparison")
    if _KEY_SET_COMPARISON.search(code):
        methods.append("key/set mismatch comparison")
    if _VALUE_COMPARISON.search(code):
        methods.append("value/checksum validation")
    return methods


@check(
    id="NB-POST-FAILURE-INTEGRITY", ref="9.3.4",
    title="Data integrity validated across layers after failures",
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    code = executable_code(ctx.obj)
    recovery_contexts = _recovery_contexts(code)
    if not recovery_contexts:
        return not_applicable(
            f"Notebook '{ctx.obj_name}' contains no executable recovery, restart, replay, "
            "reprocessing, backfill, or failed-run path"
        )
    layer_pair = _cross_layer_pair(code)
    if not layer_pair:
        return not_applicable(
            f"Notebook '{ctx.obj_name}' has a {', '.join(recovery_contexts)} path but no recognized "
            "cross-layer pair (Bronze/Silver, Silver/Gold, Source/Target, Staging/Final, "
            "Raw/Curated, or Landing/Curated) to compare"
        )

    methods = _integrity_methods(code)
    enforced = bool(_INTEGRITY_ENFORCEMENT.search(code))
    first_layer, second_layer = layer_pair
    subject = (
        f"Notebook '{ctx.obj_name}' declares post-failure context(s) "
        f"[{', '.join(recovery_contexts)}] and compares the {first_layer} and "
        f"{second_layer} layers"
    )
    if not methods:
        return binary(
            False,
            f"{subject}, but no row-count, key/set, value, or checksum comparison is "
            "implemented. The recovery can trust a partially written layer as complete",
        )
    method_detail = ", ".join(methods)
    if not enforced:
        return binary(
            False,
            f"{subject} using {method_detail}, but no assertion or raised exception blocks "
            "continuation on a mismatch. Differences can therefore be detected and ignored",
        )
    return binary(
        True,
        f"{subject} using {method_detail}. An assertion or raised exception blocks "
        "continuation when the layers disagree. This verdict verifies saved notebook logic, "
        "not the current data values or the pipeline's failure dependency",
    )


# -- 9.3.2 — the write itself is a keyed upsert --------------------------------
#
# This is deliberately NARROWER than its two siblings, and the distinction is the
# whole reason it exists as a separate check:
#
#   * ``PL-IDEMPOTENT`` (ref 2.4.6) and ``NB-IDEMPOTENT`` (ref 9.3.1) ask whether
#     a rerun is safe *by any mechanism at all* — their ``_IDEMPOTENT_PATTERN``
#     is satisfied by the word ``overwrite``, by a ``batch_id`` variable, by a
#     ``watermark``, or by a cleanup activity ordered before a write.
#   * This check asks the specific question the point asks: does the **write**
#     use a MERGE/upsert **keyed on a business key**, rather than a blind append?
#
# A notebook containing ``batch_id = run_id`` and ``df.write.mode("append")``
# passes 9.3.1 and must score 0 here. That gap is the defect this check exists to
# find, and ``tests/test_mlc_batch_checks.py`` pins it in
# ``test_9_3_2_is_strictly_narrower_than_9_3_1``.

#: A keyed upsert in SQL: ``MERGE INTO t USING s ON <predicate>``. The ``ON`` is
#: required — a MERGE without a match predicate is not keyed on anything.
_MERGE_SQL_KEYED = re.compile(
    r"\bMERGE\s+INTO\b[\s\S]{0,600}?\bUSING\b[\s\S]{0,600}?\bON\b",
    re.IGNORECASE,
)
#: A keyed upsert in the Delta Python/Scala API: ``.merge(source, condition)``
#: followed by a matched/not-matched clause. The clause is what makes it an
#: upsert rather than a bare join.
_MERGE_DELTA_KEYED = re.compile(
    r"\.merge\s*\([\s\S]{0,600}?\)[\s\S]{0,600}?\.when(?:Matched|NotMatched)",
    re.IGNORECASE,
)
#: Spark's keyed overwrite: rewrite exactly the partition/predicate this run
#: owns. Keyed on a value, so a rerun replaces rather than appends.
_KEYED_REPLACE = re.compile(
    r"replaceWhere|partitionOverwriteMode|\bINSERT\s+OVERWRITE\b|"
    r"\bON\s+CONFLICT\b|\bON\s+DUPLICATE\s+KEY\b",
    re.IGNORECASE,
)
#: A write that appends rows with no key handling — the defect.
_BLIND_APPEND = re.compile(
    r"""\.mode\s*\(\s*["']append["']\s*\)|"""
    r"""\.option\s*\(\s*["']mode["']\s*,\s*["']append["']\s*\)|"""
    r"\.insertInto\s*\(|\bINSERT\s+INTO\b",
    re.IGNORECASE,
)
#: A full-table overwrite. It does prevent duplicates on a rerun, but it is not
#: a keyed upsert and it cannot express an incremental load.
_FULL_OVERWRITE = re.compile(
    r"""\.mode\s*\(\s*["']overwrite["']\s*\)|"""
    r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b|\bTRUNCATE\s+TABLE\b",
    re.IGNORECASE,
)
#: Deduplication applied on named key columns. Weaker than an upsert — it cleans
#: up after the duplicate rather than never creating it — but it is key-aware.
_KEYED_DEDUP = re.compile(
    r"dropDuplicates\s*\(\s*\[|drop_duplicates\s*\(\s*(?:subset\s*=\s*)?\[|"
    r"\bROW_NUMBER\s*\(\s*\)\s*OVER\s*\(\s*PARTITION\s+BY\b|"
    r"\bGROUP\s+BY\b[\s\S]{0,200}?\bHAVING\s+COUNT\b",
    re.IGNORECASE,
)

#: Does this artifact write to a table at all? Deliberately wider than
#: ``_WRITE_SIGNAL`` (which 9.3.1 uses): the Delta ``.merge(...).execute()`` API
#: writes without ever touching ``.write`` or ``saveAsTable``, and gating on
#: ``_WRITE_SIGNAL`` would send exactly the compliant notebooks to N/A.
_ANY_TABLE_WRITE = re.compile(
    r"\.write\b|\.writeStream\b|saveAsTable\s*\(|\.save\s*\(|\.insertInto\s*\(|"
    r"\.merge\s*\(|\bINSERT\s+(?:INTO|OVERWRITE)\b|\bMERGE\s+INTO\b|"
    r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b|\bTRUNCATE\s+TABLE\b|\bCOPY\s+INTO\b",
    re.IGNORECASE,
)

#: Copy-activity sinks are judged structurally (``writeBehavior`` +
#: ``upsertSettings.keys``) rather than by pattern, so they need no regex.


def _merge_grade(text: str, subject: str) -> Verdict:
    """Grade one artifact's write pattern against the keyed-upsert claim.

    Shared by the pipeline and the notebook check so the two cannot drift apart
    while both carry ref 9.3.2. ``subject`` names the artifact for the evidence.
    """
    keyed = bool(_MERGE_SQL_KEYED.search(text)) or bool(_MERGE_DELTA_KEYED.search(text))
    replace = bool(_KEYED_REPLACE.search(text))
    appends = bool(_BLIND_APPEND.search(text))
    overwrites = bool(_FULL_OVERWRITE.search(text))
    dedups = bool(_KEYED_DEDUP.search(text))

    if keyed:
        return binary(True, f"{subject} writes through a keyed MERGE/upsert (a match "
                            f"predicate binds source to target), so a re-execution updates "
                            f"the matched rows instead of appending duplicates")
    if replace:
        return graded(3, f"{subject} writes with a keyed replace (replaceWhere / dynamic "
                         f"partition overwrite / INSERT OVERWRITE / ON CONFLICT) — a "
                         f"re-execution rewrites exactly the rows this run owns rather "
                         f"than appending them again")
    if overwrites and not appends:
        return graded(2, f"{subject} fully overwrites its target instead of merging. That "
                         f"does prevent duplicates on a re-run, but it is not a keyed "
                         f"upsert: it cannot load incrementally, and it rewrites rows the "
                         f"run did not touch")
    if appends and dedups:
        return graded(1, f"{subject} appends and then deduplicates on named key columns. "
                         f"The duplicates are created and cleaned up rather than never "
                         f"written, so a run that fails between the two leaves them behind")
    if appends:
        return graded(0, f"{subject} appends rows with no key handling — no MERGE/upsert, "
                         f"no keyed replace, no deduplication on a key. Re-executing it "
                         f"writes the same rows again")
    return not_applicable(f"{subject} performs no recognisable table write, so there is no "
                          f"write pattern to judge for duplicate-on-rerun safety")


@check(
    id="NB-MERGE-KEYED", ref="9.3.2",
    title="Merge/upsert patterns prevent duplicates on re-execution",
    pillar=Pillar.RELIABILITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_merge_keyed(ctx: CheckContext) -> Verdict:
    """The notebook's write is a keyed MERGE/upsert, not a blind append.

    **How this differs from its siblings — read this before calling it a
    duplicate.** ``NB-IDEMPOTENT`` (ref 9.3.1) and ``PL-IDEMPOTENT`` (ref 2.4.6)
    ask the general question: is *any* rerun-safety mechanism present? Their
    detector is satisfied by the word ``overwrite``, a ``batch_id`` variable, a
    ``watermark``, or a cleanup step ordered before a write. This check asks the
    specific one this point asks: is the **write itself** keyed? A notebook doing
    ``batch_id = run_id`` and ``df.write.mode("append").saveAsTable(...)``
    satisfies 9.3.1 and scores **0** here, which is the correct reading of both
    points.

    **What it can determine.** Which of five write shapes the notebook's code
    uses: a keyed MERGE (SQL ``MERGE INTO … USING … ON``, or the Delta
    ``.merge(...).whenMatched…`` API); a keyed replace (``replaceWhere``,
    dynamic partition overwrite, ``INSERT OVERWRITE``, ``ON CONFLICT``); a full
    overwrite; append-then-deduplicate on named key columns; or a bare append.
    Read through :func:`executable_code`, so a comment describing a MERGE — or a
    commented-out one — cannot satisfy it.

    **What it cannot.** It cannot tell whether the merge predicate names the
    *right* business key, only that a predicate exists; a MERGE keyed on a
    surrogate that changes every load would read as compliant here. It cannot
    follow a write performed inside an imported helper module, since only this
    notebook's own cells are fetched. A notebook that performs no recognisable
    write is N/A, as is an unreadable definition — never FAIL.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    # executable_code, not notebook_code: this detects a *technique*, so a
    # comment mentioning MERGE must not satisfy it.
    code = executable_code(ctx.obj)
    if not _ANY_TABLE_WRITE.search(code):
        return not_applicable("Notebook has no write operation, so it cannot create "
                              "duplicates on re-execution")
    return _merge_grade(code, "Notebook")


@check(
    id="PL-MERGE-KEYED", ref="9.3.2",
    title="Merge/upsert patterns prevent duplicates on re-execution",
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_merge_keyed(ctx: CheckContext) -> Verdict:
    """The pipeline's load is a keyed upsert, not a blind insert.

    **How this differs from its sibling.** ``PL-IDEMPOTENT`` (ref 2.4.6) asks
    whether *any* rerun-safety mechanism is present anywhere in the pipeline
    JSON, and a stray ``batch_id`` parameter name satisfies it. This check reads
    only what the pipeline actually *writes with*: the T-SQL its Script
    activities carry, and its Copy activities' sink settings. A Copy whose sink
    has no ``upsertSettings`` and a Script that only ``INSERT``s score 0 here
    while passing 2.4.6.

    **What it can determine.** Whether a Copy sink is configured with
    ``writeBehavior: upsert`` plus ``upsertSettings.keys`` (a keyed upsert), and
    which write shape the inline Script SQL uses — the same five shapes the
    notebook check grades, over :func:`script_sql` rather than notebook cells.

    **What it cannot.** It cannot see inside a stored procedure a
    ``SqlServerStoredProcedure`` activity calls: Fabric does not expose the
    procedure body, so a pipeline whose upsert lives there is judged only on what
    is visible and may be understated — which is why an unrecognisable write is
    N/A rather than a failure. It cannot see a write performed by a notebook the
    pipeline invokes; that notebook is judged separately by ``NB-MERGE-KEYED``.
    A pipeline with no Copy sink and no Script SQL is N/A.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    acts = walk_activities(ctx.obj)
    copy_sinks = [
        (a.get("name") or "Copy", (a.get("typeProperties") or {}).get("sink") or {})
        for a in acts if (a.get("type") or "") == "Copy"
    ]
    upserting = [
        name for name, sink in copy_sinks
        if isinstance(sink, dict)
        and str(sink.get("writeBehavior") or "").lower() == "upsert"
        and (sink.get("upsertSettings") or {}).get("keys")
    ]
    sql = script_sql(ctx.obj)

    if not copy_sinks and not sql.strip():
        return not_applicable("Pipeline has no Copy activity sink and no inline Script SQL, "
                              "so it performs no write this check can judge")

    if upserting and len(upserting) == len(copy_sinks) and not sql.strip():
        return binary(True, f"All {len(copy_sinks)} Copy activit(y/ies) write with "
                            f"writeBehavior 'upsert' keyed on explicit upsertSettings.keys "
                            f"({', '.join(sorted(upserting))}), so a re-execution updates "
                            f"matched rows instead of inserting duplicates")

    if sql.strip():
        verdict = _merge_grade(sql, "Pipeline Script SQL")
        if verdict.score is not None:
            suffix = (f" ({len(upserting)} of {len(copy_sinks)} Copy sink(s) also upsert on "
                      f"explicit keys)") if copy_sinks else ""
            if upserting and verdict.score < 3:
                # Some of the load is keyed even though the SQL is not; say so
                # rather than reporting the pipeline as uniformly unkeyed.
                return graded(max(verdict.score, 1), verdict.evidence + suffix)
            return graded(verdict.score, verdict.evidence + suffix)

    if copy_sinks:
        return covered(
            len(upserting), len(copy_sinks),
            f"{len(upserting)} of {len(copy_sinks)} Copy activit(y/ies) write with a keyed "
            f"upsert (writeBehavior 'upsert' plus upsertSettings.keys); the rest insert "
            f"rows, so a re-execution appends them again. Writes inside a stored procedure "
            f"are not visible to this check",
        )
    return not_applicable("Pipeline performs no write this check can judge")
