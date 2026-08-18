"""Operations & Reliability · Data Logs — the observability layer.

Four points about the logging workspace, in two pairs, plus two promoted from
self-assessment (see below).

**What is captured, and who hears about it.** An audit table that records row
counts, null counts and exceptions is what makes a load reviewable after the
fact; a failure path that reaches a notification activity is what makes a failure
noticed at the time.

**Where telemetry lands, and how it is queried.** Telemetry belongs in a store
built for it (Eventhouse / KQL database) rather than only in a batch store, and
the saved KQL operators actually run should have a home and a version history.

No API exposes a KQL queryset's *text*, so the query checks judge the presence
and source-control posture of the assets, never the content of a query.

**Run history (10.1.1)** is answered the same way: Fabric's own run history sits
behind the Activity/monitoring admin API, so what is verifiable from the
definitions is whether the solution *exports* run outcomes to a durable table.

**Two points promoted from self-assessment.** 10.1.5 (are the Warehouse loads
monitored at all) and 10.4.2 (does the monitoring data refresh often enough) were
once assumed unreadable. Both are answered from the per-item job-run history the
crawl already reads — 10.4.2 from the *observed* interval between runs, since the
scheduler's configured schedule is never fetched.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from statistics import median
from typing import Any

from auditfast.core.check._dax import time_intelligence_calls, uses_time_intelligence
from auditfast.core.check._notebook import executable_code
from auditfast.core.check._pipeline import walk_activities
from auditfast.core.check._tables import (
    columns,
    has_timestamp_column,
    is_audit_table,
    name_words,
    normalise_column,
)
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    pillar=Pillar.RELIABILITY, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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
    """Distinct display names of the items whose type is in ``types``, sorted.

    Deduplicated because Fabric surfaces one logical store under several item
    types that share a display name — an Eventhouse and the KQLDatabase inside it
    both report ``Telemetry``. Counting the name once keeps the evidence honest:
    "2 stores" must mean two stores, not one store seen twice.
    """
    return sorted({i.display_name or i.id for i in items if i.type in types})


@check(
    id="WS-EVENTHOUSE-TELEMETRY", ref="10.3.1",
    title="Eventhouse/KQL DB used for high-volume or real-time telemetry where appropriate",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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


# ---------------------------------------------------------------------------
# 10.3.4 — ingestion volume monitored
# ---------------------------------------------------------------------------

#: A column that records *how many rows moved*. Matched against the normalised
#: (lower-cased, separator-stripped) column name, so ``row_count``, ``RowCount``
#: and ``ROWCOUNT`` are one thing.
_ROW_COUNT_COLUMN = re.compile(
    # rows_read / record_count / numrows / rowcnt / recordswritten
    r"^(?:num|total|n|source|target|src|tgt|delta|expected|actual)?"
    r"(?:row|rec|record)s?"
    r"(?:read|written|processed|ingested|loaded|inserted|updated|deleted|rejected|"
    r"failed|discarded|skipped|count|cnt)?$|"
    # inserted_rows / rejected / skippedrecords / writtenrowcount
    r"^(?:inserted|updated|deleted|rejected|discarded|skipped|read|written|"
    r"processed|ingested|loaded)(?:(?:row|rec|record)s?)?(?:count|cnt)?$|"
    # volume / ingestionvolume / bytesingested
    r"^(?:ingestion|ingest|load|batch)?volume$",
    re.IGNORECASE,
)


def _has_row_count_column(table: dict) -> bool:
    return any(_ROW_COUNT_COLUMN.match(normalise_column(c.get("name") or ""))
               for c in columns(table))


@check(
    id="WS-INGEST-VOLUME", ref="10.3.4",
    title="Ingestion volume monitored (no silent drop or over-ingestion)",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def ingestion_volume_monitored(ctx: CheckContext) -> Verdict:
    """An audit/log table records rows moved per run, stamped with when the run happened.

    **What it can determine.** Whether an audit-shaped table carries a
    row-count-shaped column (``row_count``, ``records_read``,
    ``rows_inserted``, ``rejected``…) together with a run timestamp. Both are
    needed: a count with no timestamp is one reading, and a timestamp with no
    count monitors nothing about volume.

    **What it cannot.** Whether rows are written, whether anyone compares one
    run's volume against the last, or whether a threshold/alert exists — none of
    that is in table metadata, and no row data is fetched to find out. A
    workspace with no audit table at all is N/A, not a failure: this check
    judges the shape of the monitoring store, not whether one should exist.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable("No lakehouse/warehouse tables were read for this workspace")

    audit = {n: t for n, t in tables.items() if is_audit_table(n, t) and columns(t)}
    if not audit:
        return not_applicable(
            "No audit/log-shaped table with readable column metadata was found, so "
            "ingestion volume has nowhere to be recorded"
        )

    monitored = sorted(n for n, t in audit.items()
                       if _has_row_count_column(t) and has_timestamp_column(t))
    counted_only = sorted(n for n, t in audit.items() if _has_row_count_column(t))
    if monitored:
        return binary(
            True,
            f"{len(monitored)} of {len(audit)} audit table(s) record row volume per run "
            f"with a timestamp: {', '.join(monitored[:4])}",
        )
    if counted_only:
        return graded(
            1,
            f"{len(counted_only)} audit table(s) record a row count "
            f"({', '.join(counted_only[:4])}) but carry no run timestamp — a single "
            f"reading, so a silent drop between runs is not visible",
        )
    return binary(
        False,
        f"None of the {len(audit)} audit table(s) records rows read/written/rejected, so "
        f"a silent drop or over-ingestion would leave no trace",
    )


# ---------------------------------------------------------------------------
# 10.1.1 — run history kept past Fabric's own retention window
# ---------------------------------------------------------------------------
#
# Fabric's monitoring hub keeps pipeline run history for a limited window, and
# the Activity/monitoring admin API this tool does not call is the only way to
# read that history. What *is* readable from the definitions is whether the
# solution copies the run outcome somewhere durable — which is the practice the
# point is actually asking for. The gated roadmap entry ``R-10-1-1`` carries the
# same ref and records what admin access would add.

#: The run's *identity* — which execution this row describes. A pipeline
#: expression (``@pipeline().RunId``) or a variable/column named for it.
_RUN_IDENTITY = re.compile(
    r"pipeline\s*\(\s*\)\s*\.\s*(?:RunId|Pipeline|TriggerId|TriggerName)|"
    r"\brun[_\s-]?id\b|\bpipeline[_\s-]?(?:run|name)\b|\bexecution[_\s-]?id\b|"
    r"\btrigger[_\s-]?(?:id|name|time)\b",
    re.IGNORECASE,
)
#: The run's *outcome and timing* — status plus when it ran or how long it took.
_RUN_STATUS = re.compile(
    r"\bstatus\b|\bsucceeded\b|\bfailed\b|\boutcome\b|\brun[_\s-]?state\b|\berror[_\s-]?message\b",
    re.IGNORECASE,
)
_RUN_TIMING = re.compile(
    r"\bduration\b|\belapsed\b|\bstart[_\s-]?(?:time|date|utc)\b|"
    r"\bend[_\s-]?(?:time|date|utc)\b|\bfinish[_\s-]?time\b|\brun[_\s-]?(?:date|timestamp)\b",
    re.IGNORECASE,
)
#: The row is *persisted* — written to a table, not printed. The target has to
#: be an audit/log/history-shaped name: writing run metadata into a business
#: table is not a run-history archive.
_RUN_HISTORY_WRITE = re.compile(
    r"(?:INSERT\s+INTO|MERGE\s+INTO|COPY\s+INTO)\s+[\w.\[\]\"`]*"
    r"(?:run[_\s-]?(?:history|log|audit|stat|metric)|pipeline[_\s-]?(?:run|log|history)|"
    r"execution[_\s-]?(?:log|history)|job[_\s-]?(?:log|history)|"
    r"audit[_\s-]?(?:log|table|history)|monitor\w*)|"
    r"(?:saveAsTable|\.write)[\s\S]{0,120}?"
    r"(?:run_?history|run_?log|pipeline_?run\w*|execution_?log|job_?log|"
    r"audit_?log|monitoring\w*)",
    re.IGNORECASE,
)


@check(
    id="WS-RUN-HISTORY-EXPORT", ref="10.1.1",
    title="Pipeline run history monitored beyond Fabric's default retention",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,),
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS],
    required=True,
)
def run_history_is_persisted(ctx: CheckContext) -> Verdict:
    """Run outcomes are written to a durable table, not left to Fabric's retention window.

    **What it can determine.** Whether any pipeline or notebook writes a *run
    history* row to a run/pipeline/execution/audit-log-shaped table, and whether
    that row carries the three things it needs to be useful later: which run it
    was (``@pipeline().RunId``, a run/execution id, the pipeline name), whether
    it succeeded, and when/how long it ran. All three must be present for a full
    pass — a status with no run identity cannot be joined to anything, and a
    timestamp with no status is not an outcome.

    **What it cannot.** Read Fabric's own run history (that is the Activity /
    monitoring admin API, which this tool does not call), verify the retention of
    the target table, or confirm rows are actually being written. The gated
    ``R-10-1-1`` carries the same ref for the admin-API view.

    **Distinct from ``WS-RUNCONTROL`` (ref 2.5.3).** That check is about the ETL
    *control* row for a data load — batch id, status, row counts, timestamps —
    and passes on any batch/load-log write. This one requires run-level identity
    and outcome persisted to a history-shaped target; a control table that
    records row counts per batch but never a run status or duration passes there
    and fails here, which is the difference the retention point turns on.
    """
    pipelines = ctx.workspace.pipelines or {}
    notebooks = ctx.workspace.notebooks or {}
    if not (ctx.workspace.has(Resource.PIPELINE_DEFINITIONS)
            or ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS)):
        return not_applicable(
            "Neither pipeline nor notebook definitions could be read from Fabric"
        )
    if not pipelines and not notebooks:
        return not_applicable(
            "Workspace has no pipeline or notebook definitions, so there is no run "
            "history here to persist"
        )

    writers: list[str] = []
    corpus: list[str] = []
    for name, definition in pipelines.items():
        text = json.dumps(definition)
        if _RUN_HISTORY_WRITE.search(text):
            writers.append(name)
            corpus.append(text)
    for name, definition in notebooks.items():
        code = executable_code(definition)
        if _RUN_HISTORY_WRITE.search(code):
            writers.append(name)
            corpus.append(code)

    total = len(pipelines) + len(notebooks)
    if not writers:
        return binary(
            False,
            f"None of {len(pipelines)} pipeline(s) and {len(notebooks)} notebook(s) writes "
            f"run outcomes to a run-history/audit table — once Fabric's retention window "
            f"passes, this workspace's run history is gone",
        )

    blob = "\n".join(corpus)
    elements = {
        "run identity": bool(_RUN_IDENTITY.search(blob)),
        "status": bool(_RUN_STATUS.search(blob)),
        "timing": bool(_RUN_TIMING.search(blob)),
    }
    present = sorted(k for k, ok in elements.items() if ok)
    missing = sorted(k for k, ok in elements.items() if not ok)
    where = ", ".join(sorted(writers)[:3])
    # Coverage, not presence: one notebook archiving its own run beside 186 that do
    # not is a proof of concept, not a practice. The completeness of the archived
    # row (identity / status / timing) caps the score, and the share of runnables
    # that archive at all sets it.
    share = len(writers) / total if total else 0.0
    ceiling = 3 if not missing else max(1, len(present))
    if share >= 0.5:
        score = ceiling
    elif share >= 0.2:
        score = min(ceiling, 2)
    else:
        score = 1
    detail = (f"{len(writers)} of {total} pipeline(s)/notebook(s) persist run history "
              f"({where})")
    if missing:
        detail += (f" carrying {', '.join(present) or 'no run metadata'}, but missing "
                   f"{', '.join(missing)} — the archived rows cannot answer what "
                   f"happened on a given run")
    else:
        detail += " with run identity, status and timing"
    if share < 0.5:
        detail += (f"; only {share:.0%} of runnable items archive anything, so most of "
                   f"this workspace's history still expires with Fabric's retention")
    return graded(score, detail)


# ---------------------------------------------------------------------------
# 10.4.4 — the monitoring model can express a trend, not only a current state
# ---------------------------------------------------------------------------
#
# Whether a *report* actually plots a trend is not readable: no API this tool
# calls returns a report's visual layout. What the semantic model does state is
# whether the trend *mechanics* exist — a date/calendar table to put on an axis,
# and measures that shift the date filter to compare one period against another.
# A model with neither can only ever show the current state.

#: Words that mark a model table as the date/calendar dimension a trend axis
#: needs. ``time`` is excluded on purpose: "processing time" and "run time" are
#: measures of duration, not a calendar.
_DATE_TABLE_WORDS: frozenset[str] = frozenset({"date", "dates", "calendar", "calender"})

#: How many time-intelligence measure names the evidence spells out per model.
_MAX_NAMED_TREND_MEASURES = 4


def _has_date_table(model: dict) -> bool:
    """True when the model carries a date/calendar table to trend along."""
    return any(name_words(str(table)) & _DATE_TABLE_WORDS
               for table in (model.get("tables") or []))


@check(
    id="WS-MONITOR-TREND", ref="10.4.4",
    title="Historical trend analysis enabled (not just current-state)",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def monitoring_models_support_trend_analysis(ctx: CheckContext) -> Verdict:
    """The monitoring semantic models carry the mechanics a trend needs: a date table and time intelligence.

    **What it can determine.** For each semantic model in this (monitoring)
    workspace, read from its TMSL definition: whether it has a date/calendar
    table — the axis a trend is plotted along — and whether any measure *calls* a
    DAX time-intelligence function (``DATEADD``, ``SAMEPERIODLASTYEAR``,
    ``TOTALYTD``, ``DATESINPERIOD``, ``PARALLELPERIOD`` and the rest of the
    family), which is how a period is compared against another period. A model
    with neither can only ever answer "what is true now".

    **What it cannot.** Whether any report actually *shows* a trend: report
    definitions and visual layouts are not fetched, so a beautiful trend page
    over a model with no time intelligence is invisible here, and so is a model
    whose measures exist but are never placed on a page. It also cannot see a
    trend produced outside the model — a KQL time-series query, a Spark
    aggregation, a paginated report — nor judge how far back the history goes.
    A model that has a date table but no time-intelligence measure is named in
    the evidence rather than silently failed, because a report *can* trend a
    plain measure along a date axis without any time intelligence at all.

    **How it scores.** The monitoring estate can express a historical trend as
    soon as *one* model carries both mechanics, so the verdict grades the estate,
    not every model: at least one model with a date table **and** a
    time-intelligence measure passes fully (3); no such model but at least one
    with a date table scores partial (1); no date table anywhere scores 0. A
    multi-model workspace is not failed because a second, unrelated model happens
    to lack the mechanics.

    **Sibling.** ``WS-RUN-HISTORY-EXPORT`` (ref 10.1.1) asks whether run history
    is *retained* long enough to have a trend; this asks whether the monitoring
    model can *express* one. Retention without mechanics, and mechanics without
    retention, are different gaps.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable(
            "No semantic model definition was read for this workspace, so no trend "
            "mechanics can be judged"
        )

    full: list[str] = []
    axis_only: list[str] = []
    for name, model in sorted(models.items()):
        trend_measures = [
            measure.get("name") or "?"
            for measure in (model.get("measures") or [])
            if uses_time_intelligence(str(measure.get("expression") or ""))
        ]
        functions = sorted({
            fn
            for measure in (model.get("measures") or [])
            for fn in time_intelligence_calls(str(measure.get("expression") or ""))
        })
        dated = _has_date_table(model)
        if dated and trend_measures:
            named = ", ".join(sorted(trend_measures)[:_MAX_NAMED_TREND_MEASURES])
            full.append(f"{name} ({len(trend_measures)} measure(s): {named}; "
                        f"{', '.join(functions[:4])})")
        elif dated:
            axis_only.append(name)

    tail = (
        ". Whether a report actually plots a trend is not readable: report visuals are "
        "not fetched."
    )
    # The estate can express a historical trend as soon as *one* monitoring model
    # carries the mechanics; demanding every model in a multi-model workspace have
    # them would fail a workspace whose one dashboard model is perfectly capable.
    if full:
        return graded(
            3,
            f"{len(full)} of {len(models)} semantic model(s) carry both trend mechanics — a "
            f"date/calendar table and a time-intelligence measure, so the monitoring estate "
            f"can express a historical trend: {'; '.join(full[:2])}" + tail,
        )
    if axis_only:
        return graded(
            1,
            f"No model carries a time-intelligence measure, but {len(axis_only)} of "
            f"{len(models)} model(s) have a date/calendar table ({', '.join(axis_only[:3])}) — "
            f"a report can still trend a plain measure along that axis, which is not readable "
            f"here" + tail,
        )
    return graded(
        0,
        f"None of the {len(models)} semantic model(s) has a date/calendar table or a "
        f"time-intelligence measure, so the monitoring estate can only show the current "
        f"state, never a historical trend" + tail,
    )


# ---------------------------------------------------------------------------
# 10.1.5 — the Warehouse load jobs are observable
# ---------------------------------------------------------------------------
#
# Promoted from the interactive point of the same ref. The earlier triage assumed
# Warehouse job history needed a monitoring API this tool does not call; it does
# not. Every runnable Fabric item exposes ``…/items/{id}/jobs/instances``, which
# is already read for ``Resource.ITEM_RUN_HISTORY``, and the pipeline/notebook
# definitions already say which jobs load a Warehouse.

#: A Warehouse is the target of this job. ``synapsesql`` is the Spark connector
#: notebooks use to write to a Warehouse; ``DataWarehouse`` is the pipeline
#: linked-service / sink type.
_WAREHOUSE_TARGET = re.compile(
    r"\bDataWarehouse\b|\bwarehouse\b|synapsesql|sqlanalytics|"
    r"\bfabric[_\s-]?warehouse\b",
    re.IGNORECASE,
)
#: The job *loads* something — it is not merely reading the Warehouse.
_LOAD_ACTION = re.compile(
    r"\bCOPY\s+INTO\b|\bINSERT\s+INTO\b|\bMERGE\s+INTO\b|\bUPDATE\s+\w|\bDELETE\s+FROM\b|"
    r"\bCTAS\b|\bCREATE\s+TABLE\s+AS\b|\.write\b|saveAsTable|\bbulk[_\s-]?insert\b|"
    r'"type"\s*:\s*"Copy"|"type"\s*:\s*"Script"',
    re.IGNORECASE,
)
#: The job (or its audit table) records how many rows the load moved. ``rowsCopied``
#: is what a Copy activity's own output reports; the rest are audit-column spellings.
_ROWS_LOGGED = re.compile(
    r"\browsCopied\b|\browsRead\b|\brows[_\s-]?(?:loaded|written|inserted|affected|processed)\b|"
    r"\brow[_\s-]?count\b|\brecord[_\s-]?count\b|@@ROWCOUNT",
    re.IGNORECASE,
)


def _warehouse_loaders(ctx: CheckContext) -> dict[str, str]:
    """Name -> definition text, for every job that loads a Warehouse.

    A job qualifies only when its definition both names a Warehouse target *and*
    performs a load action; reading from a Warehouse is not a Warehouse load.
    """
    found: dict[str, str] = {}
    for name, definition in (ctx.workspace.pipelines or {}).items():
        text = json.dumps(definition)
        if _WAREHOUSE_TARGET.search(text) and _LOAD_ACTION.search(text):
            found[name] = text
    for name, definition in (ctx.workspace.notebooks or {}).items():
        code = executable_code(definition)
        if _WAREHOUSE_TARGET.search(code) and _LOAD_ACTION.search(code):
            found[name] = code
    return found


@check(
    id="OPS-WH-LOAD-MONITORED", ref="10.1.5",
    title="Warehouse load jobs monitored (duration, failures, row counts)",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,),
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY, Resource.PIPELINE_DEFINITIONS,
              Resource.NOTEBOOK_DEFINITIONS, Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def warehouse_loads_monitored(ctx: CheckContext) -> Verdict:
    """The jobs that load a Warehouse leave a run record, and something records the rows they moved.

    **What it can determine.** Which pipelines and notebooks load a Warehouse
    (their definitions name a Warehouse target *and* perform a load action), then
    two readable facts about them: whether each such job has a job-run history at
    all — the source of duration and failure status in Fabric's monitoring hub —
    and whether the row volume of a load is recorded anywhere, either by the job
    writing a row count or by an audit-shaped table carrying a row-count column.
    A load that succeeds but moves no rows is only visible on that third axis.

    **What it cannot.** It does not read the run rows themselves, so it cannot say
    a load *did* fail, how long it took, or that anyone looked. It cannot see a
    Warehouse loaded from outside this workspace, or one loaded by a stored
    procedure whose body Fabric does not expose. Unresolvable is N/A, never FAIL.

    **Retention is a different point.** Fabric's own run history is kept for a
    limited window, so "observable today" is not "observable next quarter". That
    gap is ``WS-RUN-HISTORY-EXPORT`` (ref 10.1.1), which asks whether run outcomes
    are *exported* to a durable table. This check asks the prior question —
    whether the Warehouse loads are monitored **at all** — and a workspace can
    pass here and fail there.
    """
    if not (ctx.workspace.has(Resource.PIPELINE_DEFINITIONS)
            or ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS)):
        return not_applicable(
            "Neither pipeline nor notebook definitions could be read from Fabric, so "
            "the Warehouse load jobs cannot be identified"
        )
    loaders = _warehouse_loaders(ctx)
    if not loaders:
        return not_applicable(
            f"No pipeline or notebook in this workspace loads a Warehouse "
            f"({len(ctx.workspace.pipelines or {})} pipeline(s), "
            f"{len(ctx.workspace.notebooks or {})} notebook(s) read), so there is no "
            f"Warehouse load here to monitor"
        )
    if not ctx.workspace.has(Resource.ITEM_RUN_HISTORY):
        return not_applicable(
            f"{len(loaders)} Warehouse load job(s) found, but the per-item run history "
            f"(jobs/instances) could not be read, so whether their duration and failures "
            f"are observable cannot be determined"
        )

    ran = {i.display_name or i.id for i in ctx.workspace.items if i.last_run_utc}
    observed = sorted(name for name in loaders if name in ran)
    share = len(observed) / len(loaders)

    counting_jobs = sorted(name for name, text in loaders.items() if _ROWS_LOGGED.search(text))
    counting_tables = sorted(
        name for name, table in (ctx.workspace.tables or {}).items()
        if is_audit_table(name, table) and _has_row_count_column(table)
    )
    counts_rows = bool(counting_jobs or counting_tables)

    where_rows = ", ".join((counting_jobs + counting_tables)[:3])
    detail = (f"{len(observed)} of {len(loaders)} Warehouse load job(s) have a job-run "
              f"history, so their duration and failure status are observable")
    if observed:
        detail += f" ({', '.join(observed[:3])})"
    detail += (f"; row volume per load is recorded by {where_rows}" if counts_rows
               else "; nothing records the rows a load moved, so a load that succeeds "
                    "but moves no rows leaves no trace")

    if share >= 0.8 and counts_rows:
        score = 3
    elif share >= 0.8 or (share >= 0.5 and counts_rows):
        score = 2
    elif observed or counts_rows:
        score = 1
    else:
        score = 0
    return graded(score, detail)


# ---------------------------------------------------------------------------
# 10.4.2 — the monitoring data refreshes often enough to act on
# ---------------------------------------------------------------------------
#
# Promoted from the interactive point of the same ref. The cadence is *observed*,
# from the run timestamps the job-scheduler history already returns — the same
# single call that yields ``Item.last_run_utc``. No schedule API is called, so
# this measures what actually happened, not what was configured.

#: An item whose job feeds the monitoring picture. Matched on the display name,
#: which is the only description of purpose the item list carries. ``alert``,
#: ``refresh`` and ``dashboard`` are deliberately excluded: an alert/notification
#: item (e.g. a Reflex or an email step) is not itself monitoring *data*, and
#: those words match too much to identify the monitoring estate reliably.
_MONITORING_NAME = re.compile(
    r"monitor|observab|telemetr|audit|\blog\b|logs|metric|health|sla|heartbeat",
    re.IGNORECASE,
)

#: Interval bands, in hours. Deliberately loose at the edges: a job scheduled
#: hourly does not run exactly 3600s apart, and a nightly job drifts either side
#: of 24h. Tightening these would score jitter rather than cadence.
_HOURLY_HOURS = 1.25
_INTRADAY_HOURS = 6.0
_DAILY_HOURS = 26.0


def _parse_stamp(stamp: str) -> datetime | None:
    """Parse an ISO-8601 UTC job timestamp, or ``None`` when it is unreadable."""
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _median_interval_hours(stamps: list[str]) -> float | None:
    """The median gap, in hours, between consecutive runs — ``None`` if underivable.

    The *median* rather than the mean so one backfill or one long outage does not
    redefine the cadence of an otherwise regular job. Needs at least two readable
    timestamps; anything less is "we cannot tell", which the caller reports as N/A.
    """
    parsed = sorted(p for p in (_parse_stamp(s) for s in stamps) if p is not None)
    if len(parsed) < 2:
        return None
    gaps = [
        (later - earlier).total_seconds() / 3600.0
        for earlier, later in zip(parsed, parsed[1:], strict=False)
        if (later - earlier).total_seconds() > 0
    ]
    return median(gaps) if gaps else None


@check(
    id="OPS-MONITOR-REFRESH", ref="10.4.2",
    title="Refresh frequency of monitoring data is adequate (near-real-time or hourly)",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY],
    required=True,
)
def monitoring_refresh_cadence(ctx: CheckContext) -> Verdict:
    """The jobs behind the monitoring picture actually run hourly or better.

    **What it can determine.** The *observed* cadence: the median interval
    between consecutive runs, taken from the job-run timestamps the scheduler
    history already returns for each item. Hourly or better passes; several times
    a day is partial; daily is weak; weekly or less scores nothing. Items are
    selected by name (monitor / telemetry / audit / log / metric / SLA /
    heartbeat …); an alert or notification item is not itself monitoring data, so
    it is not selected. In a Data Logs workspace, where every runnable item is
    part of the monitoring estate, all of them are used when no name matches.

    **What it cannot.** It does not read the *configured* schedule — the job
    scheduler's schedule API is not called — so a job configured hourly but
    failing to fire reads here as its real, slower cadence, which is the honest
    answer for this point. Semantic models are excluded: their refresh history is
    read one row at a time from the Power BI API, so no interval is derivable for
    them. Fewer than two runs, or unreadable run history, is N/A — never a FAIL.

    **Sibling.** ``WS-MONITOR-TREND`` (ref 10.4.4) asks whether the monitoring
    model can *express* a trend; this asks how fresh the data behind it is.
    """
    if not ctx.workspace.has(Resource.ITEM_RUN_HISTORY):
        return not_applicable(
            "Per-item run history (jobs/instances) could not be read from Fabric, so "
            "the observed refresh cadence cannot be derived"
        )
    history = ctx.workspace.run_history or {}
    if not history:
        return not_applicable(
            "No item in this workspace has two or more recorded runs, so no interval "
            "between runs can be derived"
        )

    by_id = {i.id: i for i in ctx.workspace.items if i.id}

    def _name(item_id: str) -> str:
        item = by_id.get(item_id)
        return (item.display_name or item.id) if item else item_id

    named = {
        item_id: stamps for item_id, stamps in history.items()
        if _MONITORING_NAME.search(_name(item_id))
    }
    candidates = named or (history if ctx.workspace.layer is Layer.LOGS else {})
    if not candidates:
        return not_applicable(
            f"None of the {len(history)} item(s) with a run history is identifiable as "
            f"monitoring-oriented by name, and this workspace is not tagged Data Logs, "
            f"so there is no monitoring cadence to judge"
        )

    intervals: dict[str, float] = {}
    for item_id, stamps in candidates.items():
        gap = _median_interval_hours(stamps)
        if gap is not None:
            intervals[_name(item_id)] = gap
    if not intervals:
        return not_applicable(
            f"{len(candidates)} monitoring item(s) have a run history, but fewer than two "
            f"readable timestamps each, so no interval between runs can be derived"
        )

    overall = median(intervals.values())
    fastest = min(intervals.items(), key=lambda kv: kv[1])
    slowest = max(intervals.items(), key=lambda kv: kv[1])
    detail = (
        f"observed refresh cadence across {len(intervals)} monitoring item(s): median "
        f"{overall:.1f}h between runs (fastest {fastest[0]} at {fastest[1]:.1f}h, "
        f"slowest {slowest[0]} at {slowest[1]:.1f}h). This is the cadence actually "
        f"observed in the run history, not the configured schedule"
    )
    if overall <= _HOURLY_HOURS:
        return graded(3, detail + " — near-real-time or hourly")
    if overall <= _INTRADAY_HOURS:
        return graded(2, detail + " — several times a day, short of hourly")
    if overall <= _DAILY_HOURS:
        return graded(1, detail + " — roughly daily, so a problem is seen a day late")
    return graded(0, detail + " — weekly or less, too slow to act on")


# ---------------------------------------------------------------------------
# 10.3.3 — Eventhouse retention configured
# ---------------------------------------------------------------------------


def _retention_policy(eventhouse: Any) -> Any:
    """Read retention metadata from either a normalized dict or provider object."""
    if isinstance(eventhouse, dict):
        return eventhouse.get("retention_policy", eventhouse.get("retentionPolicy"))
    return getattr(eventhouse, "retention_policy", None)


@check(
    id="EVENTHOUSE-RETENTION",
    ref="10.3.3",
    title="Eventhouse retention configured",
    pillar=Pillar.RELIABILITY,
    scope=Scope.EVENTHOUSE,
    layers=[Layer.LOGS],
    severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
    required=True,
)
def eventhouse_retention_configured(ctx: CheckContext) -> Verdict:
    """Verify that each readable Eventhouse has explicit retention metadata.

    The current Fabric item inventory does not expose retention policy details.
    In that case this check is intentionally N/A rather than treating an
    unreadable policy as a misconfiguration.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Eventhouse inventory could not be read from Fabric")

    eventhouses = [
        item for item in ctx.workspace.items
        if item.type == "Eventhouse"
    ]
    if not eventhouses:
        return not_applicable("No Eventhouse artifacts were found in the workspace")

    unreadable = [
        item.display_name or item.id
        for item in eventhouses
        if _retention_policy(item) is None
    ]
    if unreadable:
        return not_applicable(
            "Retention policy metadata is unavailable for Eventhouse(s): "
            + ", ".join(unreadable)
        )

    names = ", ".join(item.display_name or item.id for item in eventhouses)
    return binary(True, f"Retention policy is configured for Eventhouse(s): {names}")
