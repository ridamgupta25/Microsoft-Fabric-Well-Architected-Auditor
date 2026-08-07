"""Operations & Reliability · Data Logs — the observability layer.

Four points about the logging workspace, in two pairs.

**What is captured, and who hears about it.** An audit table that records row
counts, null counts and exceptions is what makes a load reviewable after the
fact; a failure path that reaches a notification activity is what makes a failure
noticed at the time.

**Where telemetry lands, and how it is queried.** Telemetry belongs in a store
built for it (Eventhouse / KQL database) rather than only in a batch store, and
the saved KQL operators actually run should have a home and a version history.

No API exposes a KQL queryset's *text*, so the query checks judge the presence
and source-control posture of the assets, never the content of a query.
"""
from __future__ import annotations

import re

from auditfast.core.check._notebook import executable_code
from auditfast.core.check._pipeline import walk_activities
from auditfast.core.check.helpers import Verdict, binary, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

# ---------------------------------------------------------------------------
# Audit logging and failure alerting
# ---------------------------------------------------------------------------

_AUDIT_LOG_SIGNAL = re.compile(
    r"audit[_ -]?(?:table|log)|quality[_ -]?(?:table|log)|dq[_ -]?(?:table|log)|"
    r"row[_ -]?count|null[_ -]?count|exception[_ -]?count|error[_ -]?count|"
    r"batch[_ -]?id|run[_ -]?id|failure[_ -]?reason",
    re.IGNORECASE,
)
_WRITE_SIGNAL = re.compile(
    r"\.write\b|saveAsTable|INSERT\s+INTO|MERGE\s+INTO|audit[_ -]?(?:table|log)|"
    r"quality[_ -]?(?:table|log)|dq[_ -]?(?:table|log)",
    re.IGNORECASE,
)
_NOTIFY_TYPES = frozenset({"Teams", "Office365Outlook", "Outlook365", "SendEmail", "WebHook"})
_NOTIFY_CALL_TYPES = frozenset({"Web", "WebActivity", "AzureFunctionActivity", "Function"})
_NOTIFY_NAME = re.compile(r"notif|alert|email|teams|activator", re.IGNORECASE)


@check(
    id="NB-AUDIT-LOG", ref="4.6.4",
    title="Audit Tables capture data quality logs, row counts, null checks, and exceptions",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.LOGS,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def audit_tables_capture_quality_logs(ctx: CheckContext) -> Verdict:
    """Workspace notebooks write a repeatable audit log with quality metrics."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    if not ctx.workspace.notebooks:
        return not_applicable("No notebook definitions are available for Data Logs")
    candidates = []
    for name, definition in ctx.workspace.notebooks.items():
        code = executable_code(definition)
        if _WRITE_SIGNAL.search(code) and _AUDIT_LOG_SIGNAL.search(code):
            candidates.append(name)
    return binary(bool(candidates),
                  f"Quality audit-log writer found in: {', '.join(sorted(candidates))}"
                  if candidates else
                  "No notebook writes an audit table with row/null/exception quality metrics")


@check(
    id="PL-FAILURE-ALERT", ref="10.1.4",
    title="Alerting on pipeline failure (Data Activator or equivalent)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_failure_alert(ctx: CheckContext) -> Verdict:
    """A pipeline failure dependency leads to a recognizable notification activity."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    if not acts:
        return not_applicable("Pipeline has no activities to evaluate for failure alerting")
    failure_names = {
        a.get("name", "") for a in acts
        if any("Failed" in (dep.get("dependencyConditions") or [])
               for dep in (a.get("dependsOn") or []))
    }
    notifiers = {
        a.get("name", "") for a in acts
        if a.get("type") in _NOTIFY_TYPES
        or (a.get("type") in _NOTIFY_CALL_TYPES and _NOTIFY_NAME.search(a.get("name", "")))
    }
    linked = sorted(failure_names & notifiers)
    return binary(bool(linked),
                  f"Failure path is linked to notification activity: {', '.join(linked)}"
                  if linked else
                  "No failure-linked Data Activator, email, Teams, or webhook notification found")


# ---------------------------------------------------------------------------
# The telemetry store and its saved queries
# ---------------------------------------------------------------------------

#: A store designed for high-volume, high-ingest telemetry. An Eventhouse is the
#: container; a KQLDatabase is what queries actually run against. Either one
#: present means the real-time store exists.
EVENTHOUSE_TYPES: frozenset[str] = frozenset({"Eventhouse", "KQLDatabase"})

#: Items that *stream* data into the workspace. Their presence is the readable
#: signal that real-time telemetry is arriving here — the "where appropriate"
#: half of the point — without guessing at volume, which no API reports.
STREAMING_SOURCE_TYPES: frozenset[str] = frozenset({"Eventstream"})

#: Batch stores. Telemetry landing only here is the defect the point describes:
#: a high-volume/real-time feed forced through a batch-oriented store.
BATCH_STORE_TYPES: frozenset[str] = frozenset({"Lakehouse", "Warehouse", "SQLDatabase"})

#: Saved KQL an operator opens during an investigation. A queryset is the
#: canonical form; a real-time dashboard carries its queries inline and counts
#: as the same practice.
KQL_QUERY_ASSET_TYPES: frozenset[str] = frozenset({"KQLQueryset", "KQLDashboard"})


def _named(items, types: frozenset[str]) -> list[str]:
    """Display names of the items whose type is in ``types``, sorted."""
    return sorted(i.display_name or i.id for i in items if i.type in types)


@check(
    id="WS-EVENTHOUSE-TELEMETRY", ref="10.3.1",
    title="Eventhouse/KQL DB used for high-volume or real-time telemetry where appropriate",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.ITEMS], required=True,
)
def eventhouse_for_telemetry(ctx: CheckContext) -> Verdict:
    """Telemetry in the logging workspace lands in an Eventhouse / KQL database.

    "High-volume" is not readable from any Fabric API, so the *appropriateness*
    half is judged from what the workspace holds: an Eventstream means real-time
    telemetry is genuinely arriving, and an Eventhouse or KQL database means
    there is a store built to absorb and query it.

    A workspace whose only stores are batch (Lakehouse / Warehouse) scores in the
    middle rather than failing — a log workspace may legitimately be batch-only —
    and a workspace with neither a store nor a streaming source is N/A, because
    there is no telemetry here to place.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    items = ctx.workspace.items
    eventhouses = _named(items, EVENTHOUSE_TYPES)
    streams = _named(items, STREAMING_SOURCE_TYPES)
    batch = _named(items, BATCH_STORE_TYPES)

    if eventhouses:
        detail = f"Eventhouse/KQL store present: {', '.join(eventhouses)}"
        if streams:
            detail += f"; fed by {len(streams)} streaming source(s)"
        return binary(True, detail)

    if streams:
        return binary(
            False,
            f"{len(streams)} streaming source(s) ({', '.join(streams)}) but no Eventhouse "
            f"or KQL database — real-time telemetry has no store built for it",
        )

    if batch:
        return graded(
            1,
            f"No Eventhouse or KQL database; telemetry is held only in {len(batch)} batch "
            f"store(s) ({', '.join(batch)}) — adequate for low volume, not for high-volume "
            f"or real-time feeds",
        )

    return not_applicable(
        "Workspace holds no telemetry store and no streaming source, so there is "
        "no telemetry here to place"
    )


@check(
    id="WS-KQL-QUERIES", ref="10.3.2",
    title="KQL queries exist for common operational investigations and are version-controlled",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.ITEMS, Resource.GIT], required=True,
)
def kql_queries_version_controlled(ctx: CheckContext) -> Verdict:
    """Saved KQL for investigations exists as a workspace item and is under Git.

    Two halves, both readable. *Exist*: a KQL queryset or a real-time dashboard
    is a saved, shareable query — an investigation run from someone's browser tab
    leaves nothing behind. *Version-controlled*: the workspace is Git-connected,
    so those querysets have a history and a review path.

    Judged only where there is something to query: a workspace with no Eventhouse
    or KQL database is N/A rather than a failure. The *content* of a queryset is
    never inspected — no API returns it — so "covers the common investigations"
    is out of reach and deliberately not guessed at.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    items = ctx.workspace.items
    stores = _named(items, EVENTHOUSE_TYPES)
    if not stores:
        return not_applicable(
            "Workspace holds no Eventhouse or KQL database, so there is nothing "
            "for a saved KQL query to investigate"
        )
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable(
            f"{len(stores)} Eventhouse/KQL store(s) present, but the Git connection state "
            f"could not be read, so version control cannot be judged"
        )

    assets = _named(items, KQL_QUERY_ASSET_TYPES)
    versioned = ctx.workspace.git_connected
    version_note = "Git-connected" if versioned else "not Git-connected"

    if assets and versioned:
        return graded(
            3,
            f"{len(assets)} saved KQL asset(s) ({', '.join(assets)}) against "
            f"{len(stores)} store(s); workspace is {version_note}",
        )
    if assets:
        return graded(
            1,
            f"{len(assets)} saved KQL asset(s) ({', '.join(assets)}) exist, but the "
            f"workspace is {version_note} — the queries have no history or review path",
        )
    if versioned:
        return graded(
            0,
            f"{len(stores)} Eventhouse/KQL store(s) but no saved KQL queryset or real-time "
            f"dashboard — investigations leave nothing reusable behind (workspace is "
            f"{version_note})",
        )
    return graded(
        0,
        f"{len(stores)} Eventhouse/KQL store(s) with no saved KQL queryset or real-time "
        f"dashboard, and the workspace is {version_note}",
    )
