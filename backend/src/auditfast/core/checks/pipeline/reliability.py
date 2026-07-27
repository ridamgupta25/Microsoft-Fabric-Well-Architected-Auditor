"""Pipeline checks — Reliability.

Does the implemented pipeline survive a bad day: retries, failure paths, alerts,
and bounded runtimes. These never inspect where data comes from or goes to.
"""
from __future__ import annotations

import re

from ...enums import Layer, Pillar, Resource, Scope, Severity
from ...models import CheckContext
from ..helpers import Verdict, binary, covered
from ..registry import check

#: Layers whose workspaces are expected to hold orchestration.
PIPELINE_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)

#: Timeout values Fabric/ADF apply by default — i.e. "nobody set this".
DEFAULT_TIMEOUTS = frozenset({"7.00:00:00", "0.12:00:00", "7.00:00", ""})

NOTIFY_TYPES = frozenset({"Teams", "Office365Outlook", "SendEmail", "WebHook"})
NOTIFY_NAME_RE = re.compile(r"notif|alert|email|teams", re.IGNORECASE)


def activities(definition: dict) -> list[dict]:
    """The activity list of a pipeline definition, tolerating a missing shape."""
    return (definition.get("properties") or {}).get("activities") or []


@check(
    id="PL-RETRY", ref="2.4.1", title="Retry policy configured on activities",
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
)
def retry_policy(ctx: CheckContext) -> Verdict:
    """Activities that call external systems retry at least once before failing."""
    acts = activities(ctx.obj)
    with_retry = [a for a in acts if (a.get("policy") or {}).get("retry", 0) >= 1]
    return covered(len(with_retry), len(acts),
                   f"{len(with_retry)} of {len(acts)} activities have a retry policy")


@check(
    id="PL-FAILPATH", ref="2.4.3", title="On-failure path defined",
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
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
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
)
def failure_notification(ctx: CheckContext) -> Verdict:
    """Someone is told when the pipeline fails — Teams, email, or Data Activator."""
    has_notify = any(
        a.get("type") in NOTIFY_TYPES or NOTIFY_NAME_RE.search(a.get("name", ""))
        for a in activities(ctx.obj)
    )
    return binary(has_notify, "A notification activity is present" if has_notify
                  else "No notification activity found")


@check(
    id="PL-TIMEOUT", ref="2.4", title="Explicit activity timeouts set",
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
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
