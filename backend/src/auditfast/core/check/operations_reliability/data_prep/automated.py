"""Operations & Reliability · Data Prep — does the pipeline survive a bad day.

Retries, failure paths, alerts, and bounded runtimes. These never inspect where
data comes from or goes to.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, notebook_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities, walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Timeout values Fabric/ADF apply by default — i.e. "nobody set this".
DEFAULT_TIMEOUTS = frozenset({"7.00:00:00", "0.12:00:00", "7.00:00", ""})

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
    id="PL-RETRY", ref="2.4.1", title="Retry policy configured on activities",
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
    id="PL-FAILPATH", ref="2.4.3", title="On-failure path defined",
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
    id="PL-NOTIFY", ref="2.4.5", title="Failure notification present",
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
    id="PL-TIMEOUT", ref="2.4", title="Explicit activity timeouts set",
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
    id="PL-RETRY-VALUES", ref="2.4.2", title="Retry counts and intervals are sane",
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
    title="Pipeline reruns are idempotent",
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
    id="NB-IDEMPOTENT", ref="3.1.11",
    title="Notebook reruns are idempotent",
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
    title="Failed records captured to dead-letter or quarantine output",
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
    id="NB-DEADLETTER", ref="3.1.9",
    title="Failed records captured to dead-letter or quarantine output",
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
