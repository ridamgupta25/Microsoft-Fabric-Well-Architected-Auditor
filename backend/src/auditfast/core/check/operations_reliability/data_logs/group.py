"""Operations & Reliability - Data Logs — cross-workspace (group) checks.

These compare the members of a project group (Dev -> UAT -> Prod) for logging,
monitoring and audit-table practices that should hold in *every* environment,
not only production. They register into the separate ``GROUP_REGISTRY`` via
:func:`group_check`, run once per group, and obey N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

import json

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code
from auditfast.core.check.helpers import Verdict, covered, graded, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext

from .automated import _LOAD_ACTION, _WAREHOUSE_TARGET


@group_check(
    id="XW-SPARK-LOGS", ref="10.1.2",
    title="Spark application logs captured for historical analysis",
    pillar=Pillar.RELIABILITY, severity=Severity.LOW,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def spark_logs_consistent(ctx: GroupContext) -> Verdict:
    """Spark application/driver logs retained for historical analysis.

    Spark *application* logs (driver/executor logs, the Spark history server) are
    a different artefact from a notebook's *run history*: run history records that
    a notebook ran and when, not the Spark logs themselves. Fabric exposes no
    read-only REST surface for Spark log **retention** — ``spark/settings`` carries
    only the runtime version and default environment, not a log-retention policy —
    so whether Spark logs are separately retained cannot be determined from the
    crawl for *any* environment. This is therefore a single, consistent N/A across
    the group (never a per-environment pass keyed off run history, which would be
    misleading), decoupled from notebook run history entirely.
    """
    labels = [_xw.env_label(member) for member in ctx.members]
    settings_seen = any(member.workspace.spark_settings for member in ctx.members)
    detail = (
        "spark/settings carries only runtime version and default environment"
        if settings_seen else "spark/settings was not readable"
    )
    return not_applicable(
        "Spark application-log retention is not retrievable via the Fabric REST "
        f"surface for any of the {len(labels)} environment(s) "
        f"({', '.join(labels)}): notebook run history is not a substitute for "
        f"Spark driver/executor logs, and {detail} — no log-retention setting to "
        "read. Verify Spark log retention in the capacity / Monitoring hub "
        "directly."
    )


def _rowcount_audit_tables(ws: WorkspaceContext) -> list[str]:
    """Names of tables that record per-load source vs target row counts.

    The row-count dimension of load monitoring is captured when an audit table
    carries both a source-row-count and a target-row-count column (e.g.
    ``audit_detail.source_count`` / ``target_count``), so a load that succeeds
    while moving zero rows is still caught.
    """
    found: list[str] = []
    for name, table in ws.tables.items():
        cols = {
            str(col.get("name", "")).lower()
            for col in (table.get("columns") or [])
            if isinstance(col, dict) and col.get("name")
        }
        has_source = any("source" in c and "count" in c for c in cols) or "source_count" in cols
        has_target = any("target" in c and "count" in c for c in cols) or "target_count" in cols
        if has_source and has_target:
            found.append(str(name).split(".")[-1])
    return sorted(set(found))


def _warehouse_load_jobs(ws: WorkspaceContext) -> dict[str, str]:
    """``name -> definition text`` for every job that loads a Warehouse.

    A job qualifies when its definition both names a Warehouse target and performs
    a load action (Copy/INSERT/MERGE/write). Mirrors the per-workspace
    ``OPS-WH-LOAD-MONITORED`` detector.
    """
    found: dict[str, str] = {}
    for name, definition in (ws.pipelines or {}).items():
        text = json.dumps(definition)
        if _WAREHOUSE_TARGET.search(text) and _LOAD_ACTION.search(text):
            found[name] = text
    for name, definition in (ws.notebooks or {}).items():
        code = executable_code(definition)
        if _WAREHOUSE_TARGET.search(code) and _LOAD_ACTION.search(code):
            found[name] = code
    return found


def _load_job_breakdown(ws: WorkspaceContext) -> str:
    """Name every Warehouse-load job in the workspace, split by run-history presence."""
    loaders = _warehouse_load_jobs(ws)
    if not loaders:
        return "no Warehouse-load job identified in the definitions"
    ran = {item.display_name or item.id for item in ws.items if item.last_run_utc}
    with_history = sorted(name for name in loaders if name in ran)
    without_history = sorted(name for name in loaders if name not in ran)
    parts = [f"{len(with_history)} of {len(loaders)} load job(s) with run history"]
    if without_history:
        parts.append(f"no run history: {_names(without_history)}")
    if with_history:
        parts.append(f"with run history: {_names(with_history)}")
    return "; ".join(parts)


def _names(names: list[str], cap: int = 5) -> str:
    """A comma-joined, capped list of names with a ``+N more`` suffix."""
    shown = ", ".join(names[:cap])
    if len(names) > cap:
        shown += f" (+{len(names) - cap} more)"
    return shown


@group_check(
    id="XW-WH-LOAD-MON", ref="10.1.5",
    title="Warehouse load jobs monitored (duration, failures, row counts)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY, Resource.TABLE_COLUMNS],
    required=False,
)
def warehouse_load_monitored(ctx: GroupContext) -> Verdict:
    """Warehouse load jobs monitored on all three axes: duration, failures, rows.

    Scored as a composite across the group's environments, each axis
    independently: **row counts** from a source/target-count audit table, and
    **duration / failures** from the load jobs' run history. Reporting them
    separately avoids the previous behaviour of keying the whole check off generic
    run history and missing the row-count monitoring that is actually present. N/A
    when fewer than two members have a Warehouse to monitor.
    """
    applicable: list[str] = []
    rowcount_envs: list[str] = []
    runhistory_envs: list[str] = []
    rowcount_tables: set[str] = set()
    job_details: list[str] = []
    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.ITEMS) and _xw.has_item_type(ws, _xw.DATA_STORE_TYPES)):
            continue
        label = _xw.env_label(member)
        applicable.append(label)
        tables = _rowcount_audit_tables(ws)
        if tables:
            rowcount_envs.append(label)
            rowcount_tables.update(tables)
        if ws.has(Resource.ITEM_RUN_HISTORY) and \
                _xw.has_typed_run_history(ws, {"DataPipeline", "Notebook"}):
            runhistory_envs.append(label)
        job_details.append(f"{label}: {_load_job_breakdown(ws)}")

    if len(applicable) < 2:
        return not_applicable(
            "fewer than two environments in this group hold a Warehouse whose load "
            "jobs could be monitored"
        )

    total = len(applicable)
    rows_n, runs_n = len(rowcount_envs), len(runhistory_envs)
    table_hint = (f" (via {', '.join(sorted(rowcount_tables)[:3])})"
                  if rowcount_tables else "")
    evidence = (
        f"Row-count monitoring{table_hint} present in {rows_n} of {total} "
        f"environment(s) ({', '.join(rowcount_envs) or 'none'}); load-job run "
        f"history (duration/failure) present in {runs_n} of {total} "
        f"({', '.join(runhistory_envs) or 'none'}). "
        + " | ".join(job_details)
    )
    # Composite of two independent axes, each worth up to full marks per env.
    score01 = (rows_n + runs_n) / (2 * total)
    if rows_n == total and runs_n == total:
        return covered(total, total, evidence)
    return graded(max(0, min(3, round(score01 * 3))), evidence)


@group_check(
    id="XW-AUDIT-SCHEMA", ref="10.2.1",
    title="Audit Tables schema designed for queryability (structured, not free-text)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.TABLE_COLUMNS], required=False,
)
def audit_schema_consistent(ctx: GroupContext) -> Verdict:
    """A structured, queryable audit table exists in every environment.

    An environment "implements" a queryable audit schema when it has an
    audit-named table with typed columns for *when* (event timestamp / start-end
    time), *what* (pipeline/activity/notebook or operation/action), *the outcome*
    (status/severity/state) and *an identifier* — structured, not free-text. An
    acting-user column is **not** required: these are ETL run audit tables, not
    data-access audits, and requiring a user column wrongly failed every one.
    N/A when fewer than two members' table columns could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.TABLE_COLUMNS),
        implements=_xw.has_queryable_audit_table,
        practice="has a structured, queryable audit-table schema",
        data_name="audit-table columns",
    )


@group_check(
    id="XW-AUDIT-QUERYABLE", ref="10.2.5",
    title="Audit Tables and Metadata DB are queryable by operations (not just developers)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ROLE_ASSIGNMENTS], required=False,
)
def audit_queryable_consistent(ctx: GroupContext) -> Verdict:
    """Operations can query the audit/metadata stores in every environment.

    An environment "implements" this when it holds an audit/metadata-named store
    and grants an operations reader role. N/A when fewer than two members' items
    and role assignments could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEMS) and ws.has(Resource.ROLE_ASSIGNMENTS),
        implements=lambda ws: bool(_xw.audit_store_items(ws)) and _xw.grants_operations_read(ws),
        practice="exposes audit tables to an operations reader",
        data_name="audit stores and role assignments",
    )
