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

# 10.1.2 (Spark application logs retained) has no group check by design. Spark
# *application* logs — driver/executor logs, the Spark history server — are a
# different artefact from a notebook's run history, and Fabric exposes no
# read-only surface for Spark log retention: ``spark/settings`` carries the
# runtime version and default environment, nothing more. A group check here could
# only ever return a hardcoded N/A on every group in every tenant, which reads as
# a crawl failure rather than the honest "a human must confirm this". The ref is
# covered instead by the ``OPS-SPARK-LOGS`` questionnaire check in
# ``questionnaire.py``, where the reviewer answers it directly.


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


def _quote_list(names: list[str], cap: int = 6) -> str:
    """``['a', 'b'] -> "'a', 'b'"`` with a ``(+N more)`` overflow suffix."""
    shown = ", ".join(f"'{name}'" for name in names[:cap])
    if len(names) > cap:
        shown += f" (+{len(names) - cap} more)"
    return shown


def _quote_and(names: list[str], cap: int = 6) -> str:
    """``['a', 'b', 'c'] -> "'a', 'b' and 'c'"`` for a readable sentence tail."""
    quoted = [f"'{name}'" for name in names[:cap]]
    if len(names) > cap:
        quoted.append(f"{len(names) - cap} more")
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _runhistory_bullet(tier: str, with_history: list[str], without_history: list[str]) -> str:
    """One environment's run-history sentence, naming the jobs that lack history."""
    total = len(with_history) + len(without_history)
    if total == 0:
        return f"**{tier}** has no identified Warehouse-load job."
    have = len(with_history)
    if have == total:
        return f"**{tier}** records all {total} load job(s)."
    if have == 0:
        verb = "has" if len(without_history) == 1 else "have"
        return (f"**{tier}** records **0 of {total}** — {_quote_and(without_history)} "
                f"{verb} no run history, so a slow or failed load in {tier} would be "
                "invisible.")
    only = "only " if have == 1 else ""
    return (f"**{tier}** records {have} of {total} load job(s) ({only}"
            f"{_quote_list(with_history)}; no history for {_quote_list(without_history)}).")


def _rowcount_detail(rowcount_by_tier: list[tuple[str, list[str]]]) -> str:
    """``Dev via `a` and `b`; UAT and Prod via `a``` — tiers grouped by shared tables."""
    groups: dict[tuple[str, ...], list[str]] = {}
    order: list[tuple[str, ...]] = []
    for tier, tables in rowcount_by_tier:
        key = tuple(tables)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(tier)
    parts = [
        f"{_xw.and_list(groups[key])} via " + " and ".join(f"`{name}`" for name in key)
        for key in order
    ]
    return "; ".join(parts)


@group_check(
    id="XW-WH-LOAD-MON", ref="10.1.5",
    title="Warehouse load jobs monitored (duration, failures, row counts)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY, Resource.TABLE_COLUMNS],
    required=False,
)
def warehouse_load_monitored(ctx: GroupContext) -> Verdict:
    """Warehouse load jobs monitored on all three axes: duration, failures, rows.

    Scored as a composite across the group's environments on two independent
    axes. **Row counts** come from a source/target-count audit table, so a load
    that succeeds while moving zero rows is still caught. **Duration and
    failures** come from the load jobs' run history — measured *per load job*, not
    per environment, so an environment that has run history for some other item
    but none for its actual load jobs is correctly reported as unmonitored rather
    than passing. N/A when fewer than two members have a Warehouse to monitor.
    """
    applicable: list[str] = []
    rowcount_present: list[str] = []
    rowcount_by_tier: list[tuple[str, list[str]]] = []
    runhistory_bullets: list[str] = []
    total_jobs = 0
    jobs_with_history = 0
    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.ITEMS) and _xw.has_item_type(ws, _xw.DATA_STORE_TYPES)):
            continue
        tier = _xw.env_tier(member)
        applicable.append(tier)
        tables = _rowcount_audit_tables(ws)
        if tables:
            rowcount_present.append(tier)
            rowcount_by_tier.append((tier, tables))
        loaders = _warehouse_load_jobs(ws)
        ran = {item.display_name or item.id for item in ws.items if item.last_run_utc}
        with_history = sorted(name for name in loaders if name in ran)
        without_history = sorted(name for name in loaders if name not in ran)
        total_jobs += len(loaders)
        jobs_with_history += len(with_history)
        runhistory_bullets.append(_runhistory_bullet(tier, with_history, without_history))

    if len(applicable) < 2:
        return not_applicable(
            "fewer than two environments in this group hold a Warehouse whose load "
            "jobs could be monitored"
        )

    total = len(applicable)
    rows_n = len(rowcount_present)
    if rows_n == total:
        rowcount_line = (
            f"- **Row counts:** monitored in all {total} environments — each writes "
            f"source-vs-target row counts to an audit table ({_rowcount_detail(rowcount_by_tier)})."
        )
    elif rows_n:
        without = [tier for tier in applicable if tier not in rowcount_present]
        rowcount_line = (
            f"- **Row counts:** monitored in {rows_n} of {total} environments "
            f"({_rowcount_detail(rowcount_by_tier)}); not recorded in {_xw.and_list(without)}."
        )
    else:
        rowcount_line = (
            f"- **Row counts:** not recorded in any of the {total} environments, so a "
            "load that moves zero rows would look successful."
        )
    runhistory_line = (
        f"- **Duration & failures (run history):** {jobs_with_history} of {total_jobs} "
        f"Warehouse-load job(s) record run history. " + " ".join(runhistory_bullets)
    )
    fully = rows_n == total and total_jobs > 0 and jobs_with_history == total_jobs
    evidence = (
        "Warehouse loads are " + ("fully monitored" if fully else "only partly monitored")
        + f":\n{rowcount_line}\n{runhistory_line}"
    )
    if fully:
        return covered(total, total, evidence)
    run_ratio = (jobs_with_history / total_jobs) if total_jobs else 0.0
    score01 = 0.5 * (rows_n / total) + 0.5 * run_ratio
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
