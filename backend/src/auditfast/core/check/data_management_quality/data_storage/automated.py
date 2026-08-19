"""Data Management & Quality Â· Data Storage â€” table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, executable_code
from auditfast.core.check._pipeline import activities as pipeline_activities
from auditfast.core.check._pipeline import script_sql
from auditfast.core.check._recency import parse_stamp
from auditfast.core.check._tables import (
    TABLE_LAYERS,
    col_names,
    column_type,
    columns,
    dimensions_in,
    facts_in,
    has_audit_column,
    has_surrogate_key,
    in_warehouse,
    is_audit_column,
    is_audit_table,
    is_key_column,
    is_low_cardinality_shape,
    is_platform_table,
    is_snake_case,
    is_text_column,
    is_timestamp_column,
    key_referent,
    name_words,
    normalise_column,
    normalise_table_name,
    purpose_tokens,
    related_columns,
    store_of,
    table_roles,
    tables_by_store,
)
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext, Item

_NO_TABLES = "No lakehouse/warehouse tables were read for this workspace"

#: N/A reason when lakehouse/warehouse tables exist but none are dimensions with
#: readable columns. Named to make the scope explicit â€” semantic-model tables
#: (e.g. a Power BI ``DateDimension``) are not lakehouse tables and are judged
#: separately by the 5.4.x semantic-model checks.
_NO_DIMS = "No lakehouse/warehouse dimension tables with column metadata"

#: N/A reason when lakehouse/warehouse tables exist but none carry readable column
#: schemas. Scope is made explicit so it is not mistaken for "no columns anywhere"
#: â€” semantic-model column metadata is not read by these table checks.
_NO_COLS = "No lakehouse/warehouse table column metadata available"

#: Column names implying a date/time value, for the data-type check.
_DATE_NAME = re.compile(r"(date|timestamp|_dt$|_time$)", re.IGNORECASE)


#: Row-by-row processing, in the two languages a Fabric notebook actually uses.
#:
#: **T-SQL cursors** belong to Warehouse routines, not notebooks - a Fabric
#: notebook is Spark - so those patterns are applied to ``sql_routines`` by the
#: Warehouse checks and are deliberately absent here.
#:
#: **The Spark anti-patterns** are what a notebook can genuinely do wrong:
#: pulling a distributed DataFrame onto the driver and then looping over it.
#: ``collect()`` and ``toPandas()`` alone are not enough - both are legitimate
#: for a small result - so each is paired with the iteration that follows it,
#: which is what turns a materialisation into row-by-row processing.
_ROW_BY_ROW = (
    ("pandas .iterrows()", re.compile(r"\.iterrows\s*\(\s*\)")),
    ("pandas .itertuples()", re.compile(r"\.itertuples\s*\(\s*\)")),
    # ``for row in df.collect():`` - the classic driver-side loop. The receiver
    # is matched loosely because a real call is chained
    # (``spark.table('x').collect()``), not a bare name.
    ("loop over collect()", re.compile(
        r"for\s+\w+\s+in\s+[^\n:]*?\.collect\s*\(\s*\)", re.IGNORECASE)),
    ("loop over toPandas()", re.compile(
        r"for\s+\w+\s+in\s+[^\n:]*?\.toPandas\s*\(\s*\)", re.IGNORECASE)),
    # ``for i in range(df.count()):`` then indexing - iteration by row number.
    ("loop over row count", re.compile(
        r"for\s+\w+\s+in\s+range\s*\(\s*[^\n:]*?\.count\s*\(\s*\)", re.IGNORECASE)),
    # ``.rdd.map(...)`` with a Python lambda is per-row Python, not set-based.
    ("rdd row map", re.compile(r"\.rdd\s*\.\s*(?:map|foreach)\s*\(", re.IGNORECASE)),
)

#: A ``collect()``/``toPandas()`` whose result is *assigned* and then iterated a
#: few lines later - the same anti-pattern spread over two statements, which a
#: single-line pattern misses. The right-hand side is matched loosely because a
#: real call is chained (``spark.table('silver.sales').collect()``), not a bare
#: name: an earlier ``[\w.]*`` version matched only the simplest possible form
#: and silently missed every realistic one.
_COLLECT_ASSIGNED = re.compile(
    r"(\w+)\s*=\s*[^\n=]*?\.(?:collect|toPandas)\s*\(\s*\)", re.IGNORECASE)

#: Table/schema name patterns that indicate a staging area.
#: Keys in ctx.workspace.tables are either plain lakehouse names ("StagingTemp")
#: or "WarehouseName.schema.table" for warehouse tables.  The pattern must
#: match the schema segment that follows the first dot, so it correctly detects
#: "DataflowsStagingWarehouse.stg.sales" (schema = stg) and
#: "AnyWarehouse.staging.orders" (schema = staging), but NOT
#: "DataflowsStagingWarehouse.dbo.customers" where only the warehouse name
#: contains "Staging" while the actual schema is dbo.
_STAGING_NAME = re.compile(
    # Lakehouse: plain name contains staging (e.g. StagingTemp)
    r"(?:^|[_.\-])staging(?:[_.\-]|$)"
    r"|^stg[_.]|[_.]stg$"
    r"|^stage[_.]|[_.]stage$"
    # Warehouse: schema segment (after the first dot) is stg/staging/stage
    r"|\.[_]?(?:stg|staging|stage)[_.]",
    re.IGNORECASE,
)

_WAREHOUSE_SQL_LOAD = re.compile(
    r"\bMERGE\s+INTO\b|\bINSERT\s+INTO\b|\bCOPY\s+INTO\b|"
    r"\bCREATE\s+TABLE\b|\bCTAS\b|\bTRUNCATE\s+TABLE\b|\bDELETE\s+FROM\b",
    re.IGNORECASE,
)
_INCREMENTAL_SQL = re.compile(
    r"\bMERGE\s+INTO\b|\bupsert\b|\bwatermark\b|\bcdc\b|"
    r"change[_\s]?tracking|change[_\s]?data|last_?modified|high_?water|"
    r"incremental",
    re.IGNORECASE,
)
_TRY_CATCH_SQL = re.compile(
    r"\bBEGIN\s+TRY\b.*?\bEND\s+TRY\b.*?\bBEGIN\s+CATCH\b.*?\bEND\s+CATCH\b",
    re.IGNORECASE | re.DOTALL,
)
_TXN_SQL = re.compile(
    r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bCOMMIT\s+TRAN(?:SACTION)?\b|\bROLLBACK\s+TRAN(?:SACTION)?\b",
    re.IGNORECASE,
)
_STATS_UPDATE_SQL = re.compile(
    r"\bUPDATE\s+STATISTICS\b|\bsp_updatestats\b|\bANALYZE\s+TABLE\b.*\bCOMPUTE\s+STATISTICS\b",
    re.IGNORECASE | re.DOTALL,
)
_SHORTCUTS_PATH = re.compile(r"(?:^|/)shortcuts(?:/|$)", re.IGNORECASE)
_BRONZE_TOKEN = re.compile(r"(?:^|[/_\-.])bronze(?:[/_\-.]|$)", re.IGNORECASE)
_SILVER_TOKEN = re.compile(r"(?:^|[/_\-.])silver(?:[/_\-.]|$)", re.IGNORECASE)

_STRATEGY_METADATA_KEYS = (
    "partitionBy", "partitionColumns", "partition_keys",
    "clusterBy", "clusteredBy", "clusteringColumns", "zOrderBy",
)
_DECIMAL_TYPE = re.compile(r"^decimal\s*\((\d+)\s*,\s*(\d+)\)$", re.IGNORECASE)
_OVERSIZED_VARCHAR = re.compile(r"^varchar\s*\((\d+)\)$", re.IGNORECASE)
_SURROGATE_KEY_NAME = re.compile(r"(?:^|_)(?:sk|surrogate|hash(?:_?key)?)(?:$|_)", re.IGNORECASE)
_PK_FK_NAME_HINT = re.compile(r"(?:^|_)(?:pk|fk|primary|foreign)(?:$|_)", re.IGNORECASE)
_VIEW_PROC_HINT = re.compile(r"(?:^|_)(?:vw|view|sp|proc|procedure)(?:$|_)", re.IGNORECASE)
_LOGIC_HINT = re.compile(r"(?:merge|join|window|row_number|dedup|rule|calc|business|transform)", re.IGNORECASE)

_DECLARED_WIDTH = re.compile(r"^n?(?:varchar|char)\s*\(\s*(max|\d+)\s*\)", re.IGNORECASE)

#: Widths above this are treated as oversized â€” they defeat statistics and inflate row size.
_MAX_TEXT_WIDTH = 4000
_DECIMAL_PRECISION = re.compile(r"^(?:decimal|numeric)\((\d+)\s*,\s*(\d+)\)$", re.IGNORECASE)


_GENERATED_KEY_HINT = re.compile(
    r"(?:hash|row_?number|row_?num|sequence|seq|surrogate)",
    re.IGNORECASE,
)
#: Column count above which a fact table is worth reviewing for denormalised
#: attributes. Reported by ``TB-STARSCHEMA`` as context, never scored: how wide is
#: "too wide" is a modelling judgement, and ``TB-FACT-PURITY`` (4.5.3) is what
#: scores the underlying defect. On the reference estate every fact sat between 6
#: and 25 columns, so 30 flags the genuinely unusual rather than the merely broad.
_WIDE_FACT_COLUMNS = 30

_SAMPLE_LIMIT = 8
_BUSINESS_KEY_HINT = re.compile(
    r"(?:business|natural|code|number|_bk$)",
    re.IGNORECASE,
)

_SQL_PERMISSION_HINT = (
    "Request workspace Viewer role (CONNECT + ReadData on Warehouse/SQL analytics endpoint and Metadata/Audit DBs) "
    "plus client approval for schema/catalog and row-level verification queries; this consumes capacity CU"
)
_ONELAKE_PERMISSION_HINT = (
    "Request Workspace.Read.All + OneLake.Read.All (delegated, read-only). "
    "Reads lakehouse table/column structure, Files hierarchy and shortcuts (structure only, never row data)"
)


def _to_text(value: object) -> str:
    """Flatten a nested activity/script payload into plain text for regex checks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "query", "commandText", "sqlText", "value"):
            item = value.get(key)
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (dict, list)):
                parts.append(_to_text(item))
        return " ".join(part for part in parts if part) or str(value)
    if isinstance(value, list):
        return " ".join(_to_text(item) for item in value)
    return str(value)


def _shortcut_path_tokens(path: str) -> list[str]:
    norm = (path or "").replace("\\", "/").strip("/").lower()
    return [part for part in norm.split("/") if part]


def _domain_from_path(path: str) -> tuple[str | None, str | None]:
    """Infer (layer, domain) from a shortcut path when present."""
    tokens = _shortcut_path_tokens(path)
    for idx, token in enumerate(tokens):
        if token in {"bronze", "silver"}:
            if idx + 1 < len(tokens):
                return token, tokens[idx + 1]
            return token, None
    return None, None

@check(
    id="WS-WH-LOAD", ref="3.6.1",
    title="Gold Warehouse load pattern is defined and consistent",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_load_pattern(ctx: CheckContext) -> Verdict:
    """Warehouse tables are populated via a defined pattern: COPY INTO, CTAS, Copy activity, or stored procedure."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    warehouses = [i for i in ctx.workspace.items if i.type == "Warehouse"]
    if not warehouses:
        return not_applicable("No Warehouse items found in this workspace")

    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    pipelines = ctx.workspace.pipelines
    if not pipelines:
        return graded(
            1,
            f"{len(warehouses)} Warehouse item(s) found but no pipelines defined â€” "
            "a load pattern (COPY INTO / CTAS / Copy activity / stored procedure) should be established",
        )

    load_acts: list[str] = []

    for pl_name, pl_def in pipelines.items():
        for act in pipeline_activities(pl_def):
            act_type = str(act.get("type", "") or "")
            props = act.get("typeProperties") or {}
            act_name = act.get("name", act_type) or act_type

            # Copy activity
            if act_type == "Copy":
                load_acts.append(f"{pl_name}/{act_name}")
                continue

            if act_type == "Script":
                scripts = props.get("scripts") or []
                text = _to_text(scripts).upper()
                if any(kw in text for kw in ("COPY INTO", "CREATE TABLE", "INSERT INTO", "CTAS")):
                    load_acts.append(f"{pl_name}/{act_name}")
                    continue

            if act_type in ("SqlServerStoredProcedure", "StoredProcedure"):
                load_acts.append(f"{pl_name}/{act_name}")
                continue

    if not load_acts:
        return graded(
            1,
            f"{len(warehouses)} Warehouse item(s) found across {len(pipelines)} pipeline(s) "
            "but no activity uses a defined load pattern (COPY INTO / CTAS / Copy / stored procedure)",
        )

    return binary(
        True,
        f"{len(load_acts)} load-pattern activity/activities across {len(pipelines)} pipeline(s): "
        + ", ".join(load_acts[:5]),
    )

@check(
    id="NB-NO-CURSOR", ref="3.6.2",
    title="Silver-to-Gold transformations are set-based (no row-by-row cursors)",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_cursor(ctx: CheckContext) -> Verdict:
    """Transformations run set-based rather than looping over rows on the driver.

    **Scoped to what a notebook can actually do.** An earlier version searched
    notebook code for T-SQL cursor syntax (``DECLARE ... CURSOR``,
    ``FETCH NEXT``, ``WHILE @@FETCH_STATUS``). A Fabric notebook is Spark, so
    that syntax cannot appear in one and those patterns could never fire; the
    cursor question belongs to Warehouse routines and is asked there. What is
    checked here is the Spark equivalent: materialising a distributed DataFrame
    on the driver and then iterating it.

    ``collect()`` and ``toPandas()`` on their own are **not** flagged - both are
    legitimate for a small result, and ``NB-COLLECT`` already judges unbounded
    materialisation. The signal is the *iteration*: a loop over the collected
    result, whether written inline or one statement later.

    **What it cannot determine.** Whether a small driver-side loop is
    justified - iterating 12 month names is not the anti-pattern this describes.
    The evidence names the construct found so a reviewer can judge the scale.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not code.strip():
        return not_applicable("Notebook has no executable code to assess")

    found: list[str] = []
    for label, pattern in _ROW_BY_ROW:
        hits = pattern.findall(code)
        if hits:
            found.append(f"{label} x{len(hits)}")

    # A collect()/toPandas() assigned to a name, then looped over separately.
    for match in _COLLECT_ASSIGNED.finditer(code):
        variable = re.escape(match.group(1))
        if re.search(rf"for\s+\w+\s+in\s+{variable}\b", code):
            found.append("loop over a collected DataFrame")
            break

    if not found:
        return binary(True, "No row-by-row iteration found - transformations run "
                            "set-based on the Spark engine")
    return binary(
        False,
        f"Row-by-row processing found ({', '.join(sorted(set(found)))}). Each pulls "
        f"rows onto the driver and loops over them, so the work runs single-threaded "
        f"instead of across the cluster; confirm the volume justifies it",
    )


@check(
    id="WS-STAGING", ref="3.6.3",
    title="Staging tables/schema used for Warehouse loads before merge into final tables",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def wh_staging_pattern(ctx: CheckContext) -> Verdict:
    """A staging layer (stg_*/staging_* tables or schema) buffers loads before the final merge."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    staging = [n for n in tables if _STAGING_NAME.search(n)]
    if staging:
        return binary(
            True,
            f"{len(staging)} staging table(s) found: {', '.join(staging[:5])}",
        )
    return graded(
        1,
        f"{len(tables)} table(s) found but none follow a staging naming pattern "
        "(stg_* / staging_* / stage_*) â€” a staging schema buffers loads before the final merge",
    )

@check(
    id="WS-WH-TRYCATCH", ref="3.6.5",
    title="Warehouse/lakehouse load SQL uses TRY...CATCH with transaction handling",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_try_catch_transactions(ctx: CheckContext) -> list[Verdict]:
    """Load SQL wraps transactional changes in TRY...CATCH.

    **Only readable SQL is judged.** Two sources carry it: stored procedures and
    functions declared in the Warehouse (read from ``INFORMATION_SCHEMA.ROUTINES``,
    which is where a ``SqlServerStoredProcedure`` activity's logic actually lives)
    and the inline T-SQL a pipeline runs through a Script activity.

    **A Copy activity is out of scope, not un-judged.** It runs no SQL of its own -
    Fabric generates the load internally and the pipeline definition contains
    nothing to read - so there is no error handling for this check to find, ever.
    Counting Copy activities as unreadable loads made the check look blind on
    estates that simply do not use SQL for loading: on one workspace 109 of 114
    "loads" were Copy activities, and the verdict read as though 96% of the estate
    were hidden. They are now excluded from the population entirely and reported
    separately as context.

    **The failing loads are named.** An earlier version listed only the
    *compliant* ones, so a reviewer saw "5 of 12" and the names of the 5 already
    fine - nothing to act on. Each load missing the handling now gets its own
    named row, unscored so that one checklist point does not vote once per load.

    **When nothing readable exists the answer is N/A, never FAIL.** A workspace
    that loads exclusively through Copy activities has no SQL error-handling
    practice to assess - that is not the same finding as having a bad one.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return [not_applicable("Workspace items could not be read from Fabric")]

    storage_items = [
        item for item in ctx.workspace.items
        if item.type in {"Warehouse", "Lakehouse"}
    ]
    if not storage_items:
        return [not_applicable("No Warehouse/Lakehouse items found in this workspace")]

    inspected: list[tuple[str, bool]] = []
    copy_loads: list[str] = []
    unread_routines: list[str] = []

    # Stored procedures and functions declared in the Warehouse itself.
    for routine in ctx.workspace.sql_routines:
        text = str(routine.get("definition") or "")
        if not _WAREHOUSE_SQL_LOAD.search(text):
            continue
        marker = f"{routine.get('store', '')}/{routine.get('name', '')}"
        inspected.append((
            marker,
            bool(_TRY_CATCH_SQL.search(text)) and bool(_TXN_SQL.search(text)),
        ))

    declared_routines = {
        str(r.get("name") or "").lower() for r in ctx.workspace.sql_routines
    }

    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        for pipeline_name, pipeline_def in ctx.workspace.pipelines.items():
            for activity in pipeline_activities(pipeline_def):
                activity_type = str(activity.get("type", "") or "")
                activity_name = activity.get("name", activity_type) or activity_type
                marker = f"{pipeline_name}/{activity_name}"

                if activity_type == "Script":
                    text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
                    if not _WAREHOUSE_SQL_LOAD.search(text):
                        continue
                    has_try_catch = bool(_TRY_CATCH_SQL.search(text))
                    has_transaction = bool(_TXN_SQL.search(text))
                    inspected.append((marker, has_try_catch and has_transaction))
                    continue

                if activity_type == "Copy":
                    # No SQL exists to read, by design - not a gap in the crawl.
                    copy_loads.append(marker)
                    continue

                if activity_type in ("SqlServerStoredProcedure", "StoredProcedure"):
                    # The body lives in the Warehouse. If the routine list did not
                    # carry it, that *is* a gap worth reporting - unlike a Copy.
                    called = str((activity.get("typeProperties") or {}).get(
                        "storedProcedureName") or "").split(".")[-1].strip("[]").lower()
                    if not called or called not in declared_routines:
                        unread_routines.append(marker)

    if not inspected:
        if unread_routines:
            return [not_applicable(
                f"{len(unread_routines)} pipeline activity/activities call a stored "
                f"procedure whose body is not in this snapshot, so TRY...CATCH "
                f"handling cannot be verified: {_named(unread_routines)}. "
                + _SQL_PERMISSION_HINT
            )]
        if copy_loads:
            return [not_applicable(
                f"This workspace loads through {len(copy_loads)} Copy activity/activities "
                f"and declares no SQL load routine. A Copy activity runs no SQL of its "
                f"own - Fabric generates the load internally - so there is no error "
                f"handling to assess, which is not the same as having none"
            )]
        return [not_applicable("No SQL load routine or Script activity was found to assess")]

    compliant = [name for name, ok in inspected if ok]
    offenders = [name for name, ok in inspected if not ok]
    evidence = (
        f"{len(compliant)} of {len(inspected)} readable SQL load(s) use TRY...CATCH "
        "and BEGIN/COMMIT/ROLLBACK transaction handling"
    )
    # Name the loads that are *missing* the handling. The previous version listed
    # only the compliant ones, so a reviewer could see the size of the gap but not
    # which store or pipeline it was in - the finding said "5 of 12" and named the
    # 5 that were already fine.
    if offenders:
        evidence += (f". No TRY...CATCH with transaction handling in: "
                     f"{_named(offenders)} - a failure part-way through leaves the "
                     f"target in a partially loaded state")
    if compliant:
        evidence += f". Compliant: {_named(compliant)}"
    if copy_loads:
        evidence += (f". A further {len(copy_loads)} Copy activity/activities load without "
                     f"SQL and are out of scope for this check")
    if unread_routines:
        evidence += (f". {len(unread_routines)} activity/activities call a stored procedure "
                     f"whose body could not be read: {_named(unread_routines)}")

    verdicts: list[Verdict] = [covered(len(compliant), len(inspected), evidence)]
    # Unscored per-object rows so the report's Object column names each load,
    # without one checklist point voting once per load on a large estate.
    for name in offenders:
        verdicts.append(note(
            "SQL load with no TRY...CATCH and transaction handling - a failure part-way "
            "through leaves the target partially loaded, with no rollback",
            obj=name,
        ))
    return verdicts

@check(
    id="WS-WH-INCREMENTAL", ref="3.6.6",
    title="Warehouse loads avoid unnecessary full reloads",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_incremental_loads(ctx: CheckContext) -> Verdict:
    """Warehouse loads favour incremental patterns over full reloads.

    Reads both sources of load SQL: stored procedures and functions declared in
    the Warehouse (``INFORMATION_SCHEMA.ROUTINES``), and the inline T-SQL a
    pipeline Script activity runs. A ``SqlServerStoredProcedure`` activity keeps
    its logic in the routine, not the pipeline, so before those bodies were
    fetched such a load could not be judged at all.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [item for item in ctx.workspace.items if item.type == "Warehouse"]
    if not warehouses:
        return not_applicable("No Warehouse items found in this workspace")

    inspected: list[tuple[str, bool]] = []

    for routine in ctx.workspace.sql_routines:
        text = str(routine.get("definition") or "")
        if not _WAREHOUSE_SQL_LOAD.search(text):
            continue
        inspected.append((
            f"{routine.get('store', '')}/{routine.get('name', '')}",
            bool(_INCREMENTAL_SQL.search(text)),
        ))

    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        for pipeline_name, pipeline_def in ctx.workspace.pipelines.items():
            for activity in pipeline_activities(pipeline_def):
                if str(activity.get("type", "") or "") != "Script":
                    continue
                text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
                if not _WAREHOUSE_SQL_LOAD.search(text):
                    continue
                inspected.append((
                    f"{pipeline_name}/{activity.get('name', 'Script')}",
                    bool(_INCREMENTAL_SQL.search(text)),
                ))

    if not inspected:
        return not_applicable(
            "No readable warehouse load SQL was found - the Warehouse declared no "
            "stored procedure, and no pipeline runs an inline Script load, so "
            "incremental versus full reload cannot be judged"
        )

    incremental = [name for name, is_incremental in inspected if is_incremental]
    return covered(
        len(incremental), len(inspected),
        f"{len(incremental)} of {len(inspected)} readable warehouse load(s) "
        f"use incremental signals (MERGE / watermark / CDC)"
        + (f"; incremental: {', '.join(incremental[:5])}" if incremental else ""),
    )


@check(
    id="WS-WH-STATS", ref="3.6.7",
    title="Statistics are updated after significant Warehouse loads",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS, Resource.TABLE_SCHEMAS],
    required=True,
)
def wh_stats_updated_after_loads(ctx: CheckContext) -> Verdict:
    """Statistics keep pace with Warehouse loads - which Fabric now handles itself.

    **The engine refreshes statistics automatically, so a load with no explicit
    ``UPDATE STATISTICS`` is not a finding.** Microsoft documents that *"if the
    query engine determines that existing statistics relevant to query no longer
    accurately reflect the data, those statistics are automatically refreshed"*,
    using the same recompilation threshold as SQL Server 2016, and that proactive
    refresh - which front-loads updates after data changes - is *"enabled by
    default"* (``learn.microsoft.com/fabric/data-warehouse/statistics``). It
    applies to the SQL analytics endpoint as well as the Warehouse.

    An earlier version scored every readable load that did not run statistics
    maintenance as non-compliant. On Fabric that is the normal configuration, so
    the check was penalising estates for not doing work the platform performs -
    and asking them to add maintenance that buys nothing.

    **What is still worth reporting.** Microsoft documents one residual use:
    pre-warming statistics *"if there's a large enough window between your table
    transformations and your query workload"*, so the first production query
    does not pay for the refresh. A load that does this is doing something
    genuinely useful, and gets credit; a load that does not is fine.

    **What it cannot determine.** Whether statistics are accurate or fresh - the
    engine owns that - or whether any particular table would benefit from
    pre-warming. Sibling ``WS-STATS-STRATEGY`` (4.4.6) asks the same question
    from the store's side rather than the load's.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    storage_items = [
        item for item in ctx.workspace.items
        if item.type in {"Warehouse", "Lakehouse"}
    ]
    if not storage_items:
        return not_applicable("No Warehouse/Lakehouse items found in this workspace")

    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    loads: set[str] = set()
    prewarming: set[str] = set()

    for pipeline_name, pipeline_def in ctx.workspace.pipelines.items():
        for activity in pipeline_activities(pipeline_def):
            if str(activity.get("type", "") or "") != "Script":
                continue
            text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
            if _WAREHOUSE_SQL_LOAD.search(text):
                loads.add(pipeline_name)
            if _STATS_UPDATE_SQL.search(text):
                prewarming.add(pipeline_name)

    for routine in ctx.workspace.sql_routines:
        body = str(routine.get("definition") or "")
        name = str(routine.get("name") or "stored procedure")
        if _WAREHOUSE_SQL_LOAD.search(body):
            loads.add(name)
        if _STATS_UPDATE_SQL.search(body):
            prewarming.add(name)

    engine = ("Fabric refreshes statistics automatically when the optimizer finds them "
              "stale, and proactive refresh front-loads updates after data changes "
              "(on by default), so a load that runs no UPDATE STATISTICS is correctly "
              "configured")

    # Verified, not assumed: a store with AUTO_CREATE/UPDATE_STATISTICS switched
    # off does not get the automatic maintenance these loads otherwise rely on.
    auto_off = sorted(
        store for store, opts in (ctx.workspace.warehouse_options or {}).items()
        if opts.get("auto_create_stats") is False or opts.get("auto_update_stats") is False
    )
    if auto_off and loads:
        return graded(
            1,
            f"{len(loads)} readable load path(s) feed store(s) where automatic statistics "
            f"are switched OFF ({_named(auto_off)}). Those loads run no UPDATE STATISTICS "
            f"and the engine will not refresh for them either, so plans are built from "
            f"stale or missing histograms",
        )

    if prewarming:
        return graded(
            3,
            f"{engine}. {len(prewarming)} load path(s) additionally pre-warm statistics "
            f"({_named(sorted(prewarming))}) - the one case Microsoft still documents for "
            f"manual maintenance, removing first-query latency after a batch load",
        )
    if loads:
        return graded(
            3,
            f"{engine}, and automatic statistics are confirmed ON for every readable "
            f"store. {len(loads)} readable load path(s) rely on that automatic "
            f"maintenance. Pre-warming statistics in a maintenance window would remove "
            f"first-query latency, but is an optimisation rather than a requirement",
        )
    return not_applicable(
        "No readable SQL warehouse/lakehouse load was found, so there is no load path "
        "to assess for statistics handling"
    )


@check(
    id="TB-NAMING", ref="4.2.1", title="Tables use meaningful, consistent naming conventions (agreed standard)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_naming(ctx: CheckContext) -> Verdict:
    """Table names are snake_case (the agreed, tool-friendly default)."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    ok = [n for n in tables if is_snake_case(n)]
    return covered(len(ok), len(tables), f"{len(ok)} of {len(tables)} tables use snake_case names")


@check(
    id="TB-MANAGED-DELTA", ref="4.1.1", title="Lakehouse Tables (managed) used for structured data; Files section for raw/unstructured",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_managed_delta(ctx: CheckContext) -> Verdict:
    """Analytical tables are managed and in Delta format, not external/Parquet."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    ok = [n for n, t in tables.items()
          if (t.get("type") or "").lower() == "managed"
          and (t.get("format") or "").lower() == "delta"]
    return covered(len(ok), len(tables), f"{len(ok)} of {len(tables)} are managed Delta tables")


@check(
    id="WS-SHORTCUT-GOVERNANCE", ref="4.1.3",
    title="Shortcuts avoid circular and ungoverned access paths",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS], required=True,
)
def shortcut_governance(ctx: CheckContext) -> Verdict:
    """Shortcut paths are structurally governed and avoid loop-prone patterns.

    A shortcut is flagged only for a genuine structural smell: a missing target
    type (ungoverned), a ``..`` traversal segment, a nested ``Shortcuts`` path
    (loop-prone), or a true duplicate — the *same* shortcut (parent path **and**
    name) listed twice.

    A shortcut rooted under ``Tables`` is **not** flagged. The Fabric List
    Shortcuts API returns ``path`` as the parent folder a shortcut sits in
    (``Tables`` / ``Files`` / a subpath), so a OneLake table shortcut naturally
    reports ``path = Tables`` — and that is the supported, recommended way to
    surface a Delta table across lakehouses without copying data. It is neither
    circular nor ungoverned. Because ``path`` is the *parent* folder rather than
    a per-shortcut path, two distinct shortcuts in one folder share it, so
    duplicate detection keys on the folder **plus the shortcut name** — otherwise
    every second table shortcut reads as a false duplicate. Whether an *external*
    target is allowed is the neighbouring ``WS-SHORTCUT-SCOPE`` (4.1.2)
    question, not this one.
    """
    if not ctx.workspace.has(Resource.SHORTCUTS):
        return not_applicable(
            "Shortcut metadata could not be read. Request Workspace.Read.All + OneLake.Read.All "
            "(delegated, read-only) to inspect lakehouse files hierarchy and shortcuts"
        )

    if not ctx.workspace.shortcuts:
        return not_applicable("No lakehouse shortcuts found in this workspace")

    total = 0
    risky: list[str] = []

    for lakehouse_name, shortcuts in (ctx.workspace.shortcuts or {}).items():
        seen_identities: set[str] = set()
        for row in (shortcuts or []):
            total += 1
            name = str((row or {}).get("name") or "")
            path = str((row or {}).get("path") or "")
            target_type = str((row or {}).get("target_type") or "")
            normalized = path.replace("\\", "/").strip().strip("/")
            normalized_low = normalized.lower()
            # ``path`` is the parent folder (``Tables`` / ``Files`` / a subpath),
            # not a per-shortcut path, so a shortcut's identity is that folder
            # plus its own name. Keying duplicate detection on the folder alone
            # made every second table shortcut a false duplicate.
            identity = f"{normalized_low}/{name.strip().lower()}"

            issues: list[str] = []
            if not target_type.strip():
                issues.append("missing target type")
            if ".." in normalized_low:
                issues.append("path traversal segment '..'")
            if _SHORTCUTS_PATH.search(normalized_low):
                issues.append("nested Shortcut path (loop-prone)")
            if identity != "/" and identity in seen_identities:
                issues.append("duplicate shortcut path")
            seen_identities.add(identity)

            if issues:
                risky.append(
                    f"{lakehouse_name}/{name or '<unnamed>'}: " + ", ".join(issues)
                )

    if total == 0:
        return not_applicable("No shortcut entries were returned from the lakehouse metadata")

    safe = total - len(risky)
    return covered(
        safe,
        total,
        f"{safe} of {total} shortcut(s) passed governance path checks"
        + (f"; flagged: {', '.join(risky[:5])}" if risky else ""),
    )

@check(
    id="WS-LH-BRONZE-SILVER-SEP", ref="4.1.4",
    title="Bronze and Silver lakehouse responsibilities are clearly separated per domain",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS, Resource.TABLE_SCHEMAS], required=True,
)
def bronze_silver_separation(ctx: CheckContext) -> Verdict:
    """Domain paths should map cleanly to either Bronze or Silver, not both in one route."""
    domain_layers: dict[str, set[str]] = {}
    unknown: list[str] = []

    if ctx.workspace.has(Resource.SHORTCUTS):
        for lakehouse_name, shortcuts in (ctx.workspace.shortcuts or {}).items():
            for row in (shortcuts or []):
                path = str((row or {}).get("path") or "")
                shortcut_name = str((row or {}).get("name") or "<unnamed>")
                layer, domain = _domain_from_path(path)
                if not layer or not domain:
                    unknown.append(f"{lakehouse_name}/{shortcut_name}")
                    continue
                domain_layers.setdefault(domain, set()).add(layer)

    # Fallback: infer from table naming when shortcuts are absent/empty.
    if not domain_layers and ctx.workspace.has(Resource.TABLE_SCHEMAS):
        for table_name in (ctx.workspace.tables or {}):
            tokens = [t for t in re.split(r"[._-]+", str(table_name).lower()) if t]
            for idx, token in enumerate(tokens):
                if token in {"bronze", "silver"} and idx + 1 < len(tokens):
                    domain_layers.setdefault(tokens[idx + 1], set()).add(token)
                    break

    if not domain_layers:
        if not ctx.workspace.has(Resource.SHORTCUTS) and not ctx.workspace.has(Resource.TABLE_SCHEMAS):
            return not_applicable("Shortcut/table structure metadata could not be read. " + _ONELAKE_PERMISSION_HINT)
        return not_applicable("No Bronze/Silver domain pattern was found in shortcuts or table names to assess separation")

    mixed_domains = sorted([d for d, layers in domain_layers.items() if len(layers) > 1])
    separated = len(domain_layers) - len(mixed_domains)
    return covered(
        separated,
        len(domain_layers),
        f"{separated} of {len(domain_layers)} domain(s) map to a single layer responsibility"
        + (f"; mixed domains: {', '.join(mixed_domains[:5])}" if mixed_domains else "")
        + (f"; {len(unknown)} shortcut(s) had no parseable Bronze/Silver domain path" if unknown else ""),
    )


@check(
    id="WS-LH-TAXONOMY", ref="4.1.5",
    title="Bronze/Silver domain folders follow a consistent workspace taxonomy",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS, Resource.TABLE_SCHEMAS], required=True,
)
def bronze_silver_taxonomy(ctx: CheckContext) -> Verdict:
    """Paths should consistently follow bronze/<domain>/... or silver/<domain>/..."""
    assessed = 0
    good = 0
    bad_examples: list[str] = []

    if ctx.workspace.has(Resource.SHORTCUTS):
        for lakehouse_name, shortcuts in (ctx.workspace.shortcuts or {}).items():
            for row in (shortcuts or []):
                path = str((row or {}).get("path") or "")
                name = str((row or {}).get("name") or "<unnamed>")
                path_low = path.lower()
                if not (_BRONZE_TOKEN.search(path_low) or _SILVER_TOKEN.search(path_low)):
                    continue
                assessed += 1
                layer, domain = _domain_from_path(path)
                if layer and domain and re.fullmatch(r"[a-z0-9_\-]+", domain):
                    good += 1
                else:
                    bad_examples.append(f"{lakehouse_name}/{name}")

    # Fallback: use table naming taxonomy when shortcuts are absent/empty.
    if assessed == 0 and ctx.workspace.has(Resource.TABLE_SCHEMAS):
        for table_name in (ctx.workspace.tables or {}):
            name = str(table_name)
            low = name.lower()
            if not (_BRONZE_TOKEN.search(low) or _SILVER_TOKEN.search(low)):
                continue
            assessed += 1
            tokens = [t for t in re.split(r"[._-]+", low) if t]
            ok = False
            for idx, token in enumerate(tokens):
                if token in {"bronze", "silver"} and idx + 1 < len(tokens):
                    domain = tokens[idx + 1]
                    ok = bool(re.fullmatch(r"[a-z0-9_\-]+", domain))
                    break
            if ok:
                good += 1
            else:
                bad_examples.append(name)

    if assessed == 0:
        if not ctx.workspace.has(Resource.SHORTCUTS) and not ctx.workspace.has(Resource.TABLE_SCHEMAS):
            return not_applicable("Shortcut/table structure metadata could not be read. " + _ONELAKE_PERMISSION_HINT)
        return not_applicable("No Bronze/Silver folder/table naming pattern was found to validate taxonomy")

    return covered(
        good,
        assessed,
        f"{good} of {assessed} Bronze/Silver shortcut/table path(s) match the expected taxonomy"
        + (f"; inconsistent: {', '.join(bad_examples[:5])}" if bad_examples else ""),
    )

@check(
    id="TB-PARTITION-STRATEGY", ref="4.2.2",
    title="Partitioning or clustering strategy is defined for large tables",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.LAKEHOUSE_FILES],
    required=True,
)
def table_partition_strategy(ctx: CheckContext) -> Verdict:
    """Large-table candidates should expose explicit partition/clustering metadata.

    **What it can determine.** Whether a table *declares* a partitioning or
    clustering strategy - ``partitionBy``, ``clusterBy``, ``zOrderBy`` and the
    like, read from the table listing.

    **What it cannot.** Whether a table without that metadata is partitioned.
    Fabric's table listing carries no partition keys - verified against a real
    1,845-table crawl, where ``partitionBy``/``partitionColumns`` appear only
    inside notebook source, never on a table. A date-ish column name says a table
    *could* be partitioned, never that it *is*, so such tables are excluded from
    the denominator rather than scored on a guess. That keeps this a statement
    about what was read, not about what was inferred.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    large_candidates = {
        name: meta
        for name, meta in tables.items()
        if any(token in name.lower() for token in ("fact", "fct", "event", "txn", "sales", "orders"))
    }
    if not large_candidates:
        return not_applicable("No large-table naming candidates found to assess partition/clustering strategy")

    with_strategy: list[str] = []
    without_strategy: list[str] = []

    for name, meta in large_candidates.items():
        table_meta = meta or {}
        if any(table_meta.get(key) for key in _STRATEGY_METADATA_KEYS):
            partition_columns = table_meta.get("partitionColumns") or []
            with_strategy.append(
                f"{name} ({', '.join(partition_columns)})" if partition_columns else name
            )
        elif table_meta.get("partitions_listed"):
            # OneLake listing succeeded and showed no partitioning: a readable
            # absence, so this one is a genuine finding rather than a blind spot.
            without_strategy.append(name)

    inspectable = len(with_strategy) + len(without_strategy)
    if inspectable == 0:
        return not_applicable(
            "Large-table candidates were found but no partition/clustering metadata was available to verify strategy. "
            + _ONELAKE_PERMISSION_HINT
        )

    return covered(
        len(with_strategy),
        inspectable,
        f"{len(with_strategy)} of {inspectable} large-table candidate(s) expose a partition/clustering strategy"
        + (f"; declared: {', '.join(with_strategy[:5])}" if with_strategy else "")
        + (f"; no strategy found: {', '.join(without_strategy[:5])}" if without_strategy else ""),
    )

@check(
    id="TB-AUDITCOLS", ref="4.2.5", title="Audit columns present (created_date, modified_date, source_system, batch_id)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_audit_columns(ctx: CheckContext) -> Verdict:
    """Each *solution* table records lineage via audit columns (created/modified/batch id).

    Matched on the *normalised* column name, so ``CreatedDate``, ``created_date``
    and ``load_dt`` all count. Business dates (``order_date``, ``birth_date``) do
    not - the event vocabulary is deliberately narrow.

    **Platform and scratch tables are excluded from the population.** A workspace
    carries hundreds of tables nobody designed: Fabric's own
    ``managed_delta_table_*`` bookkeeping, SQL ``dm_*`` dynamic-management views,
    Dynamics ``msdyn_*`` system tables, and Power Query staging tables named with
    a GUID whose columns are ``Column1..Column8``. Scoring them turned a question
    about a *deliberate* lineage practice into a headcount of platform noise -
    on a real estate they were the majority of the "failing" tables, which is why
    the ratio looked wrong to anyone who checked it by hand.

    **What it cannot determine.** Whether the audit columns are *populated* or
    maintained on each load - only that the column exists.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {
        n: t for n, t in ctx.workspace.tables.items()
        if columns(t) and not is_platform_table(n) and not _is_scratch_table(n, t)
    }
    if not tables:
        return not_applicable(
            "No solution-owned table with readable columns - every table read is "
            "platform bookkeeping or an unnamed staging table, which carries no "
            "deliberate audit practice to judge"
        )
    ok = [n for n, t in tables.items() if has_audit_column(t)]
    missing = sorted(set(tables) - set(ok))
    evidence = f"{len(ok)} of {len(tables)} solution tables have audit columns"
    if missing:
        shown = ", ".join(missing[:_SAMPLE_LIMIT])
        if len(missing) > _SAMPLE_LIMIT:
            shown += f", \u2026(+{len(missing) - _SAMPLE_LIMIT} more)"
        evidence += f". Without one: {shown}"
    return covered(len(ok), len(tables), evidence)


#: A Power Query / dataflow staging table: the name is a GUID fragment and the
#: columns were never named. Nobody designed these, so they cannot evidence - or
#: fail - an audit-column practice.
_GUID_TABLE_NAME = re.compile(r"^[0-9a-f]{8,}[_-]?[0-9a-f]*$", re.IGNORECASE)
_UNNAMED_COLUMN = re.compile(r"^column\d+$", re.IGNORECASE)


def _is_scratch_table(name: str, table: dict) -> bool:
    """True for a machine-generated staging table with machine-generated columns."""
    leaf = (name or "").split(".")[-1].strip()
    if not _GUID_TABLE_NAME.match(leaf.replace("_", "")):
        return False
    cols = [str(c.get("name") or "") for c in columns(table)]
    return bool(cols) and all(_UNNAMED_COLUMN.match(c) for c in cols)

def _models(ctx: CheckContext) -> dict[str, dict]:
    """Semantic models to resolve declared table roles from, when readable.

    Passed to :func:`facts_in` / :func:`dimensions_in` so a role the modeller
    *declared* through a relationship outranks one inferred from column shape.
    Empty when the definitions were not read - the classifier then falls back to
    its own evidence exactly as before.
    """
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return {}
    return ctx.workspace.semantic_models or {}


def _table_stores(ctx: CheckContext) -> str:
    """Name the lakehouse/warehouse(s) whose tables a workspace check inspected.

    Workspace-scoped table checks aggregate over every store's tables, so the
    engine leaves their object blank. Naming the store(s) here points the finding
    at what was judged â€” the analogue of a pipeline check naming its pipeline.
    Falls back to a generic label when the item list was not read.
    """
    names = sorted({
        i.display_name or i.id
        for i in ctx.workspace.items
        if (i.type or "") in ("Lakehouse", "Warehouse")
    })
    if not names:
        return "lakehouse/warehouse tables"
    joined = ", ".join(names[:_SAMPLE_LIMIT])
    if len(names) > _SAMPLE_LIMIT:
        joined += f", \u2026(+{len(names) - _SAMPLE_LIMIT} more)"
    return joined


@check(
    id="TB-STARSCHEMA", ref="4.5.1", title="Star schema design implemented (fact + dimension tables, not flat wide tables)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ITEMS], required=True,
)
def table_star_schema(ctx: CheckContext) -> Verdict:
    """The model separates fact tables from dimension tables (not flat wide tables).

    **What it can determine.** Whether the workspace holds both fact-named
    (``fact*``/``fct*``) and dimension-named (``dim*``) tables â€” the readable
    signature of a dimensional model. The evidence also reports how wide the fact
    tables are, because the point contrasts a star schema with "flat wide
    tables": a fact carrying dozens of columns is the shape that warrants a look.

    **What it cannot determine, and deliberately does not score.** Whether the
    model is *correctly* star-shaped. Column width is reported but **not scored**
    here, for two reasons: how wide is "too wide" is a modelling judgement rather
    than a fact, and the underlying defect â€” descriptive attributes sitting on a
    fact instead of a dimension â€” is already scored by ``TB-FACT-PURITY``
    (ref 4.5.3). Scoring it twice would penalise one mistake under two refs.
    Grain is judged by ``TB-FACT-GRAIN`` (4.5.2), relationships by
    ``TB-REL-DECLARED`` (4.4.5), and the Warehouse-scoped version of this same
    fact+dim question by ``TB-WH-MODELED`` (1.2.6).

    Naming is the only signal Fabric exposes: a correctly modelled schema whose
    tables are named in some other vocabulary is reported as *not detected*,
    with the tables listed, rather than as a defect.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    stores = _table_stores(ctx)
    facts = facts_in(tables, _models(ctx))
    dims = dimensions_in(tables, _models(ctx))

    if facts and dims:
        return binary(
            True,
            f"Both fact ({len(facts)}) and dimension ({len(dims)}) tables are present"
            + _fact_width_note(facts),
            obj=stores,
        )

    reasons = []
    if not facts:
        reasons.append("no fact tables (named fact*/fct*)")
    if not dims:
        reasons.append("no dimension tables (named dim*)")
    names = sorted(tables)
    sample = ", ".join(names[:_SAMPLE_LIMIT])
    if len(names) > _SAMPLE_LIMIT:
        sample += f", \u2026(+{len(names) - _SAMPLE_LIMIT} more)"
    return binary(
        False,
        f"Star-schema naming not detected across {len(names)} table(s): "
        f"{' and '.join(reasons)}. Rename modelled tables with fact_/dim_ prefixes "
        f"(e.g. fact_sales, dim_customer). Tables seen: {sample}",
        obj=stores,
    )


def _fact_width_note(facts: dict[str, dict]) -> str:
    """Report fact-table width and size - context for "not flat wide tables", unscored.

    A star-schema fact holds keys and measures; a flat wide table has descriptive
    attributes denormalised onto it. Width is the readable hint, and the row
    count (read from partition metadata, never by scanning rows) says whether the
    table is big enough for the shape to matter. Both are only ever *reported*
    here - ``TB-FACT-PURITY`` (4.5.3) is what scores attributes that belong on a
    dimension.
    """
    widths = {name: len(columns(table)) for name, table in facts.items() if columns(table)}
    if not widths:
        return ". No fact table had readable column metadata, so table width is not reported"
    widest = sorted(widths.items(), key=lambda kv: -kv[1])
    wide = [f"{n} ({c} cols)" for n, c in widest if c >= _WIDE_FACT_COLUMNS]
    detail = (f". Widest fact: {widest[0][0]} ({widest[0][1]} columns) across "
              f"{len(widths)} fact table(s) with readable columns")

    sized = {n: t.get("row_count") for n, t in facts.items()
             if isinstance(t.get("row_count"), int)}
    if sized:
        largest = max(sized.items(), key=lambda kv: kv[1])
        detail += (f"; largest holds ~{largest[1]:,} rows ({largest[0]}) by partition "
                   f"metadata, so no row was read")
    if wide:
        detail += (f"; {len(wide)} carry {_WIDE_FACT_COLUMNS}+ columns and are worth "
                   f"reviewing for denormalised attributes - {', '.join(wide[:3])} "
                   f"(reported, not scored: ref 4.5.3 judges fact-table purity)")
    return detail


@check(
    id="TB-DATATYPE-SIZING", ref="4.4.3",
    title="Data types are appropriate and sized correctly",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def table_type_sizing(ctx: CheckContext) -> Verdict:
    """Declared text widths and decimal precision/scale are within sane bounds."""
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)

    assessed = compliant = oversized_text = imprecise_numeric = 0
    lakehouse_defaults = 0

    for table in tables.values():
        for col in columns(table):
            ctype = (col.get("type", "") or "").lower()
            if not ctype:
                continue

            if _is_lakehouse_default_text(col, ctype):
                lakehouse_defaults += 1
                continue

            text_sizing = _too_wide(ctype)
            if text_sizing is not None:
                assessed += 1
                if text_sizing:
                    oversized_text += 1
                else:
                    compliant += 1
                continue

            numeric_sizing = _decimal_is_reasonable(ctype)
            if numeric_sizing is not None:
                assessed += 1
                if numeric_sizing:
                    compliant += 1
                else:
                    imprecise_numeric += 1

    if not assessed:
        return not_applicable(
            "No assessable declared text widths or decimal/numeric precision "
            "metadata"
            + (f"; {lakehouse_defaults} Lakehouse default varchar("
               f"{_LAKEHOUSE_TEXT_WIDTH}) column(s) excluded" if lakehouse_defaults else "")
        )

    return covered(
        compliant, assessed,
        f"{compliant} of {assessed} assessable columns have appropriate sizing â€” "
        f"{oversized_text} oversized text column(s), {imprecise_numeric} "
        "decimal/numeric column(s) with invalid precision/scale"
        + (f"; {lakehouse_defaults} Lakehouse default varchar({_LAKEHOUSE_TEXT_WIDTH}) "
           "column(s) excluded" if lakehouse_defaults else ""),
    )


#: How many offending object names one evidence string carries. Enough to act
#: on, few enough to stay readable in a report cell - the same limit the other
#: naming checks in this module use.
_MAX_NAMED_TABLES = 5


def _named(names: list[str]) -> str:
    """``"a, b, c (+4 more)"`` - a bounded, sorted list of offenders.

    A finding that reports only a ratio ("43 of 80 dimensions") tells a reviewer
    the size of the problem and nothing about where it is. Naming the objects is
    what makes the row actionable.
    """
    shown = ", ".join(names[:_MAX_NAMED_TABLES])
    extra = len(names) - _MAX_NAMED_TABLES
    return f"{shown} (+{extra} more)" if extra > 0 else shown


@check(
    id="TB-SURROGATE-GEN", ref="4.4.4",
    title="Surrogate keys are implemented for dimensions with a generated-key pattern",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def table_surrogate_generated(ctx: CheckContext) -> list[Verdict]:
    """Dimension schemas include surrogate keys with generation-oriented naming hints.

    **Declared evidence first.** Fabric Warehouse supports ``IDENTITY`` columns,
    and Microsoft names them the way to build a surrogate key: *"IDENTITY columns
    enable automatic generation of these surrogate keys when inserting new rows
    into a table"* (``learn.microsoft.com/fabric/data-warehouse/identity``). The
    crawl reads ``sys.identity_columns``, so where a table declares one this
    check reports a fact rather than an inference.

    **Names are the fallback, not the primary signal.** Nothing in Fabric flags
    a column as "a surrogate key" - it is a design convention - and a Lakehouse
    Delta table has no ``IDENTITY`` concept at all, so on Lakehouse tables the
    naming proxy is all there is. It cannot inspect ETL code paths
    (hash/window/key-table logic) either. The evidence says which basis was used.

    **Each offending dimension is its own scored row.** A single workspace-level
    ratio ("43 of 80") named no object, so a reviewer could not act on it: the
    report's Object column was empty and the affected tables were invisible.
    Following ``R-MODEL-HIDDEN-KEYS`` (14.1.8), the summary verdict is followed
    by one row per offending table carrying its name.

    **The two ways a dimension fails are different problems**, so they read
    differently. A dimension with *no surrogate key at all* is keyed on its
    business key - and Microsoft is explicit that "a surrogate key is required
    because there will be duplicate natural keys when multiple versions are
    stored", so this blocks SCD Type 2 outright. A dimension that *has* a
    surrogate key but keeps no natural/business key beside it breaks the ETL
    lookup Kimball describes, where an incoming row is matched by natural key to
    find its surrogate.
    """
    if not ctx.workspace.tables:
        return [not_applicable(_NO_TABLES)]
    dims = {n: t for n, t in dimensions_in(ctx.workspace.tables, _models(ctx)).items()
            if columns(t)}
    if not dims:
        return [not_applicable(_NO_DIMS)]

    compliant: list[str] = []
    declared: list[str] = []
    no_surrogate: list[str] = []
    no_hint: list[str] = []
    for name, table in sorted(dims.items()):
        names = col_names(table)
        identity = [str(c).lower() for c in (table.get("identity_columns") or [])]
        has_surrogate = bool(identity) or any(
            n.endswith(("_sk", "_key")) or n in {"surrogate_key", "surrogate_id"}
            for n in names
        )
        has_generated_hint = bool(identity) or any(
            _GENERATED_KEY_HINT.search(n) for n in names)
        has_business_hint = any(_BUSINESS_KEY_HINT.search(n) for n in names)
        if has_surrogate and (has_generated_hint or has_business_hint):
            compliant.append(name)
            if identity:
                declared.append(f"{name}.{identity[0]}")
        elif not has_surrogate:
            no_surrogate.append(name)
        else:
            no_hint.append(name)

    basis = (f". {len(declared)} confirmed by a declared IDENTITY column "
             f"({_named(declared)}) rather than inferred from naming"
             if declared else
             ". No dimension declares an IDENTITY column, so this rests on column "
             "naming - Lakehouse Delta tables have no IDENTITY concept, and Fabric "
             "exposes no flag marking a column as a surrogate key")

    verdicts: list[Verdict] = [covered(
        len(compliant), len(dims),
        f"{len(compliant)} of {len(dims)} dimension table(s) implement a surrogate key "
        f"with a generated-key pattern{basis}",
    )]
    # The per-object rows are UNSCORED. They exist so the report names the
    # affected tables - the reviewer's "object name not captured" - not to vote
    # again on a verdict the summary already cast.
    #
    # Scoring them would let one checklist point dominate the roll-up on a large
    # estate: a workspace with 120 dimensions emitted 121 scored rows out of ~375
    # in total, so this single point carried a third of the score, while an
    # unrelated critical finding still carried one row. It would also punish size
    # rather than quality - two estates equally bad at surrogate keys would score
    # very differently purely because one has more tables. The summary's ratio
    # already reflects the proportion, which is the fair measure.
    for name in no_surrogate:
        verdicts.append(note(
            "No surrogate key column - the dimension is keyed on its business key, so "
            "no second version of a member can be stored (SCD Type 2 is not possible)",
            obj=name,
        ))
    for name in no_hint:
        verdicts.append(note(
            "Surrogate key present but no natural/business key beside it - an "
            "incremental load cannot match an incoming row back to its dimension row",
            obj=name,
        ))
    return verdicts


@check(
    id="TB-REL-DECLARED", ref="4.4.5",
    title="Primary/foreign key relationships are declared where supported",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.TABLE_SCHEMAS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def table_relationships_declared(ctx: CheckContext) -> list[Verdict]:
    """Fact tables declare their key relationships somewhere machine-readable.

    **Two sources, strongest first.** A Warehouse can declare ``NOT ENFORCED``
    PK/FK constraints, and the crawl now reads them from ``sys.foreign_keys`` -
    that is the point stated literally, so a table carrying one satisfies it
    outright. Where no constraint is declared (a Lakehouse table, or a Warehouse
    that never declared any), semantic-model relationships are the fallback:
    they are the same structure expressed in the model rather than the database.

    **Each undeclared fact is its own scored row.** A single ratio named no
    object, leaving the report's Object column empty and the affected tables
    invisible. Following ``R-MODEL-HIDDEN-KEYS`` (14.1.8), a summary verdict is
    followed by one row per fact table that declares nothing.

    **What it cannot determine.** Whether a declared relationship is *correct* -
    Fabric does not enforce these constraints, so a declaration is a statement of
    intent, not a guarantee that the data honours it. ``NB-FK-INTEGRITY`` (5.3.2)
    is what looks for code that actually validates the values.
    """
    if not ctx.workspace.tables:
        return [not_applicable(_NO_TABLES)]

    facts = list(facts_in(ctx.workspace.tables, _models(ctx)))
    if not facts:
        return [not_applicable(
            "No fact-like tables found to assess for declared FK relationships")]

    # Declared database constraints - the direct answer where they exist.
    with_constraint = {
        name for name in facts
        if (ctx.workspace.tables.get(name) or {}).get("references")
    }

    linked_tables: set[str] = set()
    models = ctx.workspace.semantic_models if ctx.workspace.has(
        Resource.SEMANTIC_MODEL_DEFINITIONS) else {}
    if models:
        table_names = {_norm_name(name) for name in ctx.workspace.tables}
        for model in models.values():
            for rel in model.get("relationships") or []:
                from_table = _norm_name(str(rel.get("from_table") or rel.get("fromTable") or ""))
                to_table = _norm_name(str(rel.get("to_table") or rel.get("toTable") or ""))
                if from_table in table_names:
                    linked_tables.add(from_table)
                if to_table in table_names:
                    linked_tables.add(to_table)

    declared = with_constraint | {
        name for name in facts if _norm_name(name) in linked_tables
    }
    if not declared and not models and not with_constraint:
        return [not_applicable(
            "Neither declared database constraints nor semantic-model "
            "relationships could be read, so key relationships cannot be assessed"
        )]

    evidence = (f"{len(declared)} of {len(facts)} fact-like table(s) declare their key "
                f"relationships")
    if with_constraint:
        evidence += (f" ({len(with_constraint)} through a Warehouse FK constraint, the "
                     f"rest through semantic-model relationships)")
    else:
        evidence += (" through semantic-model relationships; no Warehouse FK constraint "
                     "is declared, which Fabric supports as NOT ENFORCED metadata")

    verdicts: list[Verdict] = [covered(len(declared), len(facts), evidence)]
    # Unscored, for the reason given on TB-SURROGATE-GEN above: these rows name
    # the affected tables, they do not re-cast the summary's verdict. On a
    # 133-fact estate, scoring them would give this one point 126 votes out of
    # ~375 rows and would penalise a large estate over a small one with the same
    # proportion of gaps.
    for name in sorted(set(facts) - declared):
        verdicts.append(note(
            "No declared relationship - neither a Warehouse FK constraint nor a "
            "semantic-model relationship, so nothing machine-readable states how this "
            "table joins to its dimensions",
            obj=name,
        ))
    return verdicts


@check(
    id="WS-STATS-STRATEGY", ref="4.4.6",
    title="Statistics maintenance strategy is defined and automated",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS, Resource.TABLE_SCHEMAS],
    required=True,
)
def stats_strategy_defined(ctx: CheckContext) -> list[Verdict]:
    """Statistics exist, and nothing has disabled their automatic maintenance.

    **Fabric maintains statistics itself, so "no manual strategy" is not a
    finding.** The engine *"automatically creates those statistics if they don't
    already exist"* whenever the optimizer needs them, and *"if the query engine
    determines that existing statistics relevant to query no longer accurately
    reflect the data, those statistics are automatically refreshed"*
    (``learn.microsoft.com/fabric/data-warehouse/statistics``). Proactive refresh
    is *"enabled by default"*. This applies to the SQL analytics endpoint as well
    as the Warehouse.

    An earlier version scored a workspace down for having no pipeline or stored
    procedure running ``UPDATE STATISTICS``. On Fabric that is the normal,
    correct configuration, so the check reported a gap that no longer exists.

    **But passing on the strength of that alone would be an assumption, not an
    audit.** ``sys.stats.no_recompute`` is the one readable setting that turns the
    automatic refresh *off* for a statistics object - a deliberate act, and the
    only way an estate genuinely ends up with stale statistics on Fabric. The
    crawl now reads it, so this check verifies that nothing has been disabled
    rather than trusting that the platform is doing its job.

    **Pre-warming earns credit, never a requirement.** Microsoft documents one
    residual use for manual statistics: running them *"if there's a large enough
    window between your table transformations and your query workload"*, which
    removes first-query latency after a batch load.

    **What it cannot determine.** Whether a given statistic is *accurate*, or
    whether a heavily-queried table would benefit from pre-warming. A table with
    no statistics is expected rather than a gap - they are created on first
    query.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return [not_applicable("Workspace items could not be read from Fabric")]

    storage_items = [item for item in ctx.workspace.items
                     if item.type in {"Warehouse", "Lakehouse"}]
    if not storage_items:
        return [not_applicable("No Warehouse/Lakehouse items found in this workspace")]

    tables = ctx.workspace.tables or {}
    options = ctx.workspace.warehouse_options or {}
    if not tables and not options:
        return [not_applicable(
            "Neither the tables a store holds nor its database options could be read, "
            "so statistics handling is not assessable. " + _SQL_PERMISSION_HINT
        )]

    with_stats = sum(1 for table in tables.values() if table.get("statistics"))

    # The genuinely auditable setting. Fabric maintains statistics itself, so no
    # manual schedule is required - but a user can switch that automatic
    # behaviour off, and Microsoft says OFF "can cause suboptimal query plans and
    # degraded query performance". `None` means unreadable, never "off".
    auto_off = sorted(
        store for store, opts in options.items()
        if opts.get("auto_create_stats") is False or opts.get("auto_update_stats") is False
    )
    checked = sorted(
        store for store, opts in options.items()
        if opts.get("auto_create_stats") is not None or opts.get("auto_update_stats") is not None
    )

    prewarming: list[str] = []
    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        for name, pipeline_def in (ctx.workspace.pipelines or {}).items():
            for activity in pipeline_activities(pipeline_def):
                if str(activity.get("type", "") or "") != "Script":
                    continue
                text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
                if _STATS_UPDATE_SQL.search(text):
                    prewarming.append(name)
                    break
    for routine in ctx.workspace.sql_routines:
        if _STATS_UPDATE_SQL.search(str(routine.get("definition") or "")):
            prewarming.append(str(routine.get("name") or "stored procedure"))

    engine = ("Fabric creates and refreshes statistics automatically at query time "
              "(incremental and proactive refresh are on by default), so no manual "
              "maintenance schedule is required")

    if auto_off:
        verdicts: list[Verdict] = [covered(
            len(checked) - len(auto_off), max(len(checked), 1),
            f"{len(auto_off)} store(s) have AUTO_CREATE_STATISTICS or "
            f"AUTO_UPDATE_STATISTICS switched OFF ({_named(auto_off)}). That disables the "
            f"automatic maintenance Fabric otherwise performs, and Microsoft documents the "
            f"OFF state as causing suboptimal query plans and degraded query performance",
        )]
        for store in auto_off:
            verdicts.append(note(
                "Automatic statistics creation or update is switched off for this store "
                "(ALTER DATABASE ... SET AUTO_CREATE_STATISTICS / AUTO_UPDATE_STATISTICS "
                "OFF), so query plans will be built from missing or stale histograms",
                obj=store,
            ))
        return verdicts

    verified = (f", and automatic statistics are confirmed ON for all {len(checked)} "
                f"readable store(s)" if checked else
                ". The database options that would disable it could not be read for any "
                "store, so that half is unverified")

    if prewarming:
        return [graded(
            3,
            f"{engine}{verified}. {len(prewarming)} load step(s) additionally pre-warm "
            f"statistics ({_named(sorted(set(prewarming)))}), removing first-query latency "
            f"after a batch load - the one case Microsoft still documents for manual "
            f"CREATE/UPDATE STATISTICS"
            + (f". {with_stats} table(s) already carry statistics objects"
               if with_stats else ""),
        )]

    return [graded(
        3,
        f"{engine}{verified}. {with_stats} of {len(tables)} readable table(s) already "
        f"carry statistics objects; the rest acquire them on first query. No load step "
        f"pre-warms statistics, which is optional - it only removes first-query latency "
        f"after a batch load",
    )]
@check(
    id="TB-DATEDIM", ref="4.5.7", title="Date/Time dimension exists with all required attributes (fiscal periods, quarter, holidays)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ITEMS], required=False,
)
def table_date_dimension(ctx: CheckContext) -> Verdict:
    """Each assessable data store has its own dedicated date/calendar dimension."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    def is_date_dimension_name(name: str) -> bool:
        leaf = name.rsplit(".", 1)[-1].lower()
        compact = re.sub(r"[^a-z0-9]", "", leaf)
        return bool(re.search(
            r"dim(?:ension)?(?:date|calendar)|(?:date|calendar)dim(?:ension)?|"
            r"datedimension|calendar",
            compact,
        ))

    grouped = {
        store: {
            name: table for name, table in store_tables.items()
            if not is_platform_table(name)
        }
        for store, store_tables in tables_by_store(tables).items()
    }
    grouped = {store: store_tables for store, store_tables in grouped.items() if store_tables}

    if grouped:
        found_by_store = {
            store: sorted(name for name in store_tables if is_date_dimension_name(name))
            for store, store_tables in grouped.items()
        }
        compliant = {store: names for store, names in found_by_store.items() if names}
        missing = sorted(set(grouped) - set(compliant))
        found_detail = "; ".join(
            f"{store}: {', '.join(names[:3])}"
            for store, names in sorted(compliant.items())
        ) or "none"
        missing_detail = ", ".join(missing) or "none"
        return covered(
            len(compliant),
            len(grouped),
            f"{len(compliant)} of {len(grouped)} data store(s) have a date/time "
            f"dimension. Found — {found_detail}. Missing — {missing_detail}",
            obj=", ".join(sorted(grouped)),
        )

    stores = _table_stores(ctx)
    found = sorted(name for name in tables if is_date_dimension_name(name))
    if found:
        return binary(
            True,
            f"A date/time dimension table exists: {', '.join(found[:3])}",
            obj=stores,
        )
    names = sorted(tables)
    sample = ", ".join(names[:_SAMPLE_LIMIT])
    if len(names) > _SAMPLE_LIMIT:
        sample += f", \u2026(+{len(names) - _SAMPLE_LIMIT} more)"
    return binary(
        False,
        f"No date/time dimension detected across {len(names)} table(s): expected a "
        f"table named like dim_date, date_dim or calendar. Add a dedicated date "
        f"dimension (e.g. dim_date) with fiscal periods, quarter and holiday "
        f"attributes. Tables seen: {sample}",
        obj=stores,
    )


@check(
    id="TB-SURROGATE", ref="4.5.6", title="Surrogate keys used for dimension tables (not business keys as PKs in facts)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_surrogate_keys(ctx: CheckContext) -> Verdict:
    """Dimensions declare a surrogate key column, not only a business key.

    **Declared constraints first.** A Warehouse can declare ``NOT ENFORCED``
    primary keys, and the crawl now reads them: a dimension with a declared key
    is telling us so outright, which beats any name inference. The naming rule
    below is the fallback for a Lakehouse table or a Warehouse that declares no
    constraints - both common - and it accepts the spellings Microsoft's own
    material uses: ``customer_sk`` (the Fabric dimensional-modelling guidance)
    and ``CustomerKey`` (AdventureWorksDW, where it is an ``IDENTITY(1,1)``
    column). Matching only the underscored form reported ``0 of 19`` on an estate
    where most dimensions were correctly modelled.

    An ``â€¦AlternateKey`` never counts: AdventureWorks uses that for the natural
    key, which is the distinction this point is about.

    **What it cannot determine.** Whether the column is genuinely system
    generated - a load-time property, not visible in a schema.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in dimensions_in(ctx.workspace.tables, _models(ctx)).items()
            if columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)

    declared = [n for n, t in dims.items() if t.get("has_declared_key")]
    by_name = [n for n, t in dims.items()
               if n not in declared and has_surrogate_key(t)]
    ok = declared + by_name
    missing = sorted(set(dims) - set(ok))

    evidence = f"{len(ok)} of {len(dims)} dimensions have a surrogate key"
    if declared:
        evidence += (f" ({len(declared)} declare a primary/unique key constraint, "
                     f"{len(by_name)} inferred from the column name)")
    if missing:
        shown = ", ".join(missing[:_SAMPLE_LIMIT])
        if len(missing) > _SAMPLE_LIMIT:
            shown += f", \u2026(+{len(missing) - _SAMPLE_LIMIT} more)"
        evidence += f". Without one: {shown}"
    return covered(len(ok), len(dims), evidence)


@check(
    id="TB-COL-NAMING", ref="4.2.3", title="Column naming is consistent and self-documenting",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_column_naming(ctx: CheckContext) -> Verdict:
    """Column names follow *one* convention across the workspace â€” whichever one.

    **Consistency is what is scored, not a particular house style.** Each name is
    classified as ``snake_case``, ``UPPER_CASE``, ``PascalCase``, ``camelCase`` or
    ``mixed``; the dominant convention is found, and the score is the share of
    columns that follow it. Requiring ``snake_case`` specifically marked down any
    estate that had standardised on something else â€” Microsoft's own AdventureWorks
    sample is PascalCase throughout â€” which measured style preference rather than
    quality. A ``mixed`` name (a space, ``Customer_ID`` blending Pascal with
    underscores) can never be dominant: it follows no convention at all.

    **Deliberately different from ``TB-WH-NAME-CONSISTENCY`` (ref 4.4.2)**, which
    asks the same question but only of tables known to live in a **Warehouse**,
    and judges table names as well as column names. This one covers **every**
    table in the workspace, Lakehouse included, and only its columns â€” so a
    Lakehouse-only estate (where 4.4.2 is N/A) is still assessed here.

    **What it cannot determine.** Whether a consistently-named column is
    *self-documenting*: ``col1``, ``x`` and ``value`` are all valid snake_case.
    Only the convention half of the point is machine-readable.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)
    styles = [
        naming_style(str(column.get("name") or ""))
        for table in tables.values()
        for column in columns(table)
    ]
    if not styles:
        return not_applicable(_NO_COLS)

    convention, following = _dominant(styles)
    if convention == "none":
        return covered(
            0, len(styles),
            f"None of {len(styles)} column name(s) across {len(tables)} table(s) follows "
            f"a single naming convention â€” every name mixes styles (a space, or "
            f"capitals joined by underscores)",
        )
    mixed = sum(1 for style in styles if style == "mixed")
    return covered(
        following, len(styles),
        f"{following} of {len(styles)} column name(s) across {len(tables)} table(s) "
        f"follow the dominant convention ({convention})"
        + (f"; {mixed} name(s) follow no convention at all" if mixed else "")
        + ". Consistency is what is scored â€” any one convention counts, provided "
          "the estate sticks to it.",
    )


@check(
    id="TB-DATATYPES", ref="4.2.4", title="Data types are appropriate (no stringly-typed dates, no oversized varchars)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_data_types(ctx: CheckContext) -> Verdict:
    """Date-named columns are typed temporally, and declared text widths are sane.

    Only columns that can actually be judged are counted: a date-named column, or a
    text column that declares a width. A bare ``string`` has no width to assess.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)

    assessed = compliant = stringly_dates = oversized = 0
    lakehouse_defaults = 0
    for table in tables.values():
        for col in columns(table):
            name = col.get("name", "")
            ctype = (col.get("type", "") or "").lower()
            if not ctype:
                continue
            if _DATE_NAME.search(name):
                assessed += 1
                if ctype.startswith(("string", "varchar", "char", "nvarchar", "nchar")):
                    stringly_dates += 1
                else:
                    compliant += 1
                continue
            if _is_lakehouse_default_text(col, ctype):
                # A Lakehouse SQL endpoint renders every Delta ``string`` as
                # varchar(8000) regardless of intent, so the width is the
                # platform's, not the author's. Judging it would fail every
                # string column in every lakehouse - a tool artefact, not a
                # finding. Only a Warehouse width is a real design choice.
                lakehouse_defaults += 1
                continue
            if _too_wide(ctype) is not None:
                assessed += 1
                if _too_wide(ctype):
                    oversized += 1
                else:
                    compliant += 1

    if not assessed:
        return not_applicable(
            "No date/time-named columns and no text columns whose width the author "
            "chose" + (f"; {lakehouse_defaults} Lakehouse column(s) carry the platform's "
                       f"default varchar(8000) and cannot be judged"
                       if lakehouse_defaults else "")
        )
    return covered(
        compliant, assessed,
        f"{compliant} of {assessed} assessable columns are appropriately typed â€” "
        f"{stringly_dates} date column(s) typed as text, "
        f"{oversized} text column(s) wider than {_MAX_TEXT_WIDTH}"
        + (f"; {lakehouse_defaults} Lakehouse column(s) excluded â€” a Lakehouse SQL "
           f"endpoint forces every Delta string to varchar({_LAKEHOUSE_TEXT_WIDTH}), "
           f"so that width is the platform's choice, not the model's"
           if lakehouse_defaults else ""),
    )


#: A Lakehouse SQL analytics endpoint maps every Delta ``string`` column to this
#: width, whatever the author intended. See
#: ``fabric-skills/common/SQLDW-CONSUMPTION-CORE.md``: "Lakehouse SQLEP maps Delta
#: string -> varchar(8000)".
_LAKEHOUSE_TEXT_WIDTH = 8000


def _is_lakehouse_default_text(col: dict, column_type: str) -> bool:
    """True when a width was imposed by a Lakehouse endpoint rather than chosen.

    Only applies to ``varchar(8000)`` read from a Lakehouse: a Warehouse author
    picks their own widths, and any other Lakehouse width had to be declared
    deliberately, so both remain assessable.
    """
    if (col.get("source_kind") or "").strip().lower() != "lakehouse":
        return False
    match = _DECLARED_WIDTH.match(column_type)
    if not match:
        return False
    width = match.group(1).lower()
    return width != "max" and int(width) == _LAKEHOUSE_TEXT_WIDTH


def _too_wide(column_type: str) -> bool | None:
    """True/False for a text type with a declared width, None when not assessable."""
    match = _DECLARED_WIDTH.match(column_type)
    if not match:
        return None
    width = match.group(1).lower()
    return True if width == "max" else int(width) > _MAX_TEXT_WIDTH


def _decimal_is_reasonable(column_type: str) -> bool | None:
    """True/False for decimal precision/scale sanity, None when not a decimal type."""
    match = _DECIMAL_PRECISION.match(column_type)
    if not match:
        return None
    precision = int(match.group(1))
    scale = int(match.group(2))
    return 1 <= precision <= 38 and 0 <= scale <= precision


def _norm_name(text: str) -> str:
    """Lowercased identifier with separators normalized for name matching."""
    return re.sub(r"[\s\-.]+", "_", (text or "").strip().lower())


#: Connection types that stay inside OneLake's governance boundary.
_ONELAKE_NATIVE = {
    "lakehouse", "warehouse", "onelake", "datamart", "kustodatabase",
    "kqldatabase", "eventhouse", "powerbisemanticmodel", "fabricsql",
}

#: A local or UNC filesystem path â€” ``C:\Users\...`` or ``\\server\share``.
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

#: A personal cloud drive rather than a governed team site.
_PERSONAL_CLOUD = re.compile(r"-my\.sharepoint\.com|/personal/|onedrive", re.IGNORECASE)

#: An endpoint that is a single data file, not a managed store.
_FILE_ENDPOINT = re.compile(r"\.(?:csv|tsv|xlsx?|xlsb|json|txt|parquet|xml)(?:\?|$)", re.IGNORECASE)


def _shadow_reason(conn: dict) -> str | None:
    """Why this connection is ungoverned shadow storage, or None when it is governed.

    Deliberately conservative: an enterprise object store reached through a
    shareable cloud connection (ADLS, S3, GCS, a SQL source) is *not* shadow
    storage â€” it is a governed external source, which the point allows. What it
    flags is data living outside any shared, managed store: a file on a person's
    machine, a personal cloud drive, or an ad-hoc file pulled over HTTP.
    """
    connectivity = (conn.get("connectivity_type") or "").strip().lower()
    conn_type = (conn.get("connection_type") or "").strip().lower()
    endpoint = (conn.get("endpoint") or "").strip()

    if "personal" in connectivity:
        return "personal gateway"
    if conn_type == "file" or _LOCAL_PATH.search(endpoint):
        return "local file path"
    if _PERSONAL_CLOUD.search(endpoint):
        return "personal cloud drive"
    if conn_type in ("web", "httpserver") and _FILE_ENDPOINT.search(endpoint):
        return "ad-hoc file over HTTP"
    return None


@check(
    id="WS-SHORTCUT-SCOPE", ref="4.1.2",
    title="OneLake used as the single data lake â€” no ungoverned shadow storage",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS, Resource.CONNECTIONS], required=False,
)
def shortcut_scope(ctx: CheckContext) -> Verdict:
    """Data reaches the workspace through OneLake or a governed source, not a side door.

    Two populations answer this. **Shortcuts** show where OneLake itself points;
    a shortcut to Dataverse or ADLS is a legitimate governed pattern, so it is
    reported for review rather than failed. **Connections** are where shadow
    storage actually shows up â€” a spreadsheet on someone's laptop reached through
    a personal gateway is exactly the "ungoverned shadow storage" the point
    forbids, and that is what this check scores.
    """
    has_shortcuts = ctx.workspace.has(Resource.SHORTCUTS)
    has_connections = ctx.workspace.has(Resource.CONNECTIONS)
    if not has_shortcuts and not has_connections:
        return not_applicable("Neither shortcuts nor connections could be read from Fabric")

    shortcuts = [s for entries in (ctx.workspace.shortcuts or {}).values() for s in entries]
    onelake = sum(1 for s in shortcuts
                  if (s.get("target_type") or "").strip().lower() == "onelake")
    external_types = sorted({
        (s.get("target_type") or "unknown")
        for s in shortcuts
        if (s.get("target_type") or "").strip().lower() != "onelake"
    })
    if shortcuts:
        shortcut_note = (
            f"{len(shortcuts)} shortcut(s): {onelake} target OneLake, "
            f"{len(shortcuts) - onelake} target external sources "
            f"({', '.join(external_types) or 'none'})"
        )
    elif has_shortcuts:
        shortcut_note = "no OneLake shortcuts"
    else:
        shortcut_note = "shortcuts could not be read"

    if not has_connections:
        return not_applicable(
            f"Connections could not be read from Fabric, so shadow storage cannot be "
            f"judged â€” {shortcut_note}"
        )
    connections = ctx.workspace.connections or []
    if not connections:
        return not_applicable(f"No Fabric source connections were returned â€” {shortcut_note}")

    reasons: dict[str, int] = {}
    native = governed = 0
    for conn in connections:
        reason = _shadow_reason(conn)
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        elif (conn.get("connection_type") or "").strip().lower() in _ONELAKE_NATIVE:
            native += 1
        else:
            governed += 1

    shadow = sum(reasons.values())
    breakdown = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
    return covered(
        len(connections) - shadow, len(connections),
        f"{len(connections) - shadow} of {len(connections)} source connection(s) are "
        f"governed ({native} OneLake-native, {governed} governed external); "
        f"{shadow} are ungoverned shadow storage"
        f"{' â€” ' + breakdown if breakdown else ''}. {shortcut_note}. "
        f"External shortcuts and external sources are legitimate when governed â€” "
        f"confirm each is intended.",
    )



#: The three roles an SCD Type 2 row needs, each with the spellings seen in the
#: wild. Matching by *role* rather than by one canonical name is the point: an
#: estate using ``effective_date``/``end_date``/``active_flag`` is doing SCD2 and
#: must be judged as such, then told its names deviate from the standard.
_SCD2_START = frozenset({
    "valid_from", "validfrom", "effective_date", "effectivedate", "effective_from",
    "effectivefrom", "start_date", "startdate", "row_effective_date",
    "record_start_date", "effective_start_date", "valid_start_date", "dw_valid_from",
})
_SCD2_END = frozenset({
    "valid_to", "validto", "valid_until", "validuntil", "end_date", "enddate",
    "expiration_date", "expirationdate", "expiry_date", "expirydate",
    "effective_to", "effectiveto", "row_expiry_date", "record_end_date",
    "effective_end_date", "valid_end_date", "dw_valid_to",
})
_SCD2_FLAG = frozenset({
    "is_current", "iscurrent", "current_flag", "currentflag", "current_ind",
    "active_flag", "activeflag", "is_active", "isactive", "current_record",
    "is_latest", "islatest", "latest_flag", "dw_is_current",
})

#: The spelling the checklist point names. Anything else is a deviation worth
#: reporting - it still works, but it costs every consumer a lookup.
_SCD2_STANDARD = ("valid_from", "valid_to", "is_current")


def _scd2_roles(table: dict) -> dict[str, str]:
    """Which SCD2 role each column fills: ``{"start": "effective_date", ...}``."""
    found: dict[str, str] = {}
    for name in col_names(table):
        key = str(name or "").strip().lower()
        if "start" not in found and key in _SCD2_START:
            found["start"] = key
        elif "end" not in found and key in _SCD2_END:
            found["end"] = key
        elif "flag" not in found and key in _SCD2_FLAG:
            found["flag"] = key
    return found


@check(
    id="TB-SCD2", ref="4.5.9", title="SCD Type 2 includes valid_from, valid_to, and is_current flag correctly maintained (where used)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_scd2(ctx: CheckContext) -> Verdict:
    """A slowly-changing dimension carries a start date, an end date, and a current flag.

    **Judged by column-role pattern, not by name — across every table.** The
    point names ``valid_from``/``valid_to``/``is_current``, but an estate spelling
    them ``effective_date``/``end_date``/``active_flag`` is implementing the same
    pattern and must be scored on whether the *trio is complete*, not on whether
    it picked the same words. And SCD2 history tables are not always the ones a
    model or a name calls a "dimension" - on a real estate they are Silver tables
    the role classifier reads as unknown/fact - so restricting the scan to
    dimension-role tables reported "no SCD2" on an estate full of it. This scans
    every table that has column metadata.

    A table is an SCD2 candidate once it carries a **current-flag** role plus at
    least one validity date: a bare start/end date pair with no current indicator
    is an ordinary validity period (a price, a rate), not row versioning.

    **What it cannot determine.** Whether ``valid_to`` is actually maintained on
    supersede, or whether exactly one row per key is flagged current - that needs
    the data, not the schema. Naming deviations are reported alongside the score.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tabled = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tabled:
        return not_applicable(_NO_COLS)

    candidates = {n: _scd2_roles(t) for n, t in tabled.items()}
    candidates = {n: r for n, r in candidates.items() if r.get("flag") and len(r) >= 2}
    if not candidates:
        # The columns *were* read (so this is not a permission gap) - there is
        # simply no SCD2 pattern. The reason string is the whole value of an N/A.
        return not_applicable(
            f"Column metadata was read for {len(tabled)} table(s), but none carries an "
            f"SCD2 start-date / end-date / current-flag pattern, so no table is "
            f"versioned as SCD Type 2"
        )

    complete = {n: r for n, r in candidates.items() if len(r) == 3}
    incomplete = {n: r for n, r in candidates.items() if len(r) < 3}
    nonstandard = sorted({
        col for roles in candidates.values() for col in roles.values()
        if col not in _SCD2_STANDARD
    })

    evidence = (
        f"{len(complete)} of {len(candidates)} SCD2 table(s) carry the full "
        f"start-date / end-date / current-flag trio"
    )
    if incomplete:
        missing = "; ".join(
            f"{n} lacks {', '.join(sorted({'start', 'end', 'flag'} - set(r)))}"
            for n, r in sorted(incomplete.items())[:5]
        )
        evidence += f". Incomplete: {missing}"
    if nonstandard:
        evidence += (
            f". Non-standard column names in use ({', '.join(nonstandard[:8])}) - the "
            f"point names valid_from / valid_to / is_current, so a consumer cannot "
            f"rely on one spelling across the estate"
        )
    return covered(len(complete), len(candidates), evidence)


# =============================================================================
# Store-aware table checks and dimensional purity checks.
# (4.5.3, 4.5.4, 4.5.8).
#
# The store-aware ones read ``store``/``store_kind``, which the crawler fills in
# from the SQL analytics endpoint a table's columns were read through. An empty
# store means the endpoint could not be read â€” *unknown*, never a mismatch â€” so
# every one of them excludes those tables and reports N/A when nothing is left.
# =============================================================================

#: N/A reason when no table could be attributed to an owning store.
_NO_STORE = (
    "No table could be attributed to an owning Lakehouse/Warehouse â€” the SQL "
    "analytics endpoints were not readable, so store membership is unknown"
)


@check(
    id="TB-WH-MODELED", ref="1.2.6",
    title="Gold Warehouse is consumption-ready and modeled (star schema) for the semantic layer",
    pillar=Pillar.ARCHITECTURE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def warehouse_is_modeled(ctx: CheckContext) -> Verdict:
    """Each Warehouse holds both fact and dimension tables, so it is modeled, not a dump.

    **What it can determine.** Which tables are *known* to live in a Warehouse
    (from the SQL endpoint they were read through), and whether each such
    Warehouse carries both fact-named and dimension-named tables â€” the readable
    signature of a star schema.

    **What it cannot.** Whether the semantic layer in a *different* workspace
    actually binds to this Warehouse: the point's cross-workspace half is not
    readable from a per-workspace crawl, and is not guessed at here. Nor does it
    judge relationships or grain.

    Narrower than ``TB-STARSCHEMA`` (ref 4.5.1), which asks the same fact+dim
    question across *all* tables in the workspace regardless of where they live.
    This one is Warehouse-scoped: a Lakehouse-only estate makes it N/A, and a
    star that exists only because facts sit in a Lakehouse and dimensions in a
    Warehouse does not satisfy it.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    warehouse_tables = {n: t for n, t in tables.items() if in_warehouse(t)}
    if not warehouse_tables:
        unknown = sum(1 for t in tables.values() if not store_of(t))
        return not_applicable(
            f"No table is known to live in a Warehouse ({len(tables)} table(s) read; "
            f"{unknown} with no readable owning store)"
        )

    by_store = tables_by_store(warehouse_tables)
    if not by_store:
        return not_applicable(_NO_STORE)

    modeled, detail = [], []
    roles = table_roles(tables, _models(ctx))
    for store, store_tables in sorted(by_store.items()):
        facts = [n for n in store_tables if roles.get(n) == "fact"]
        dims = [n for n in store_tables if roles.get(n) == "dimension"]
        if facts and dims:
            modeled.append(store)
            detail.append(f"{store}: {len(facts)} fact / {len(dims)} dimension table(s)")
        else:
            detail.append(
                f"{store}: {len(facts)} fact / {len(dims)} dimension table(s) â€” not modeled"
            )
    return covered(
        len(modeled), len(by_store),
        f"{len(modeled)} of {len(by_store)} Warehouse(s) hold both fact and dimension "
        f"tables â€” {'; '.join(detail)}. Whether a semantic model in another workspace "
        f"consumes them is not readable here.",
    )


@check(
    id="TB-AUDIT-SEPARATED", ref="1.2.8",
    title="Audit Tables role clearly defined and separated from business data",
    pillar=Pillar.ARCHITECTURE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def audit_tables_separated(ctx: CheckContext) -> Verdict:
    """Audit/DQ tables live in a store of their own rather than beside business tables.

    **What it can determine.** Which tables are audit-shaped â€” either their name
    says so (audit / log / quality / exception / reject) or their columns are
    dominated by lineage columns â€” and which store holds each, so it can say
    whether audit data is concentrated in a dedicated store or scattered through
    the business stores.

    **What it cannot.** Whether the audit tables are *populated*, or whether the
    role is documented anywhere. Tables whose owning store could not be read are
    excluded, never counted as a mismatch.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    by_store = tables_by_store(tables)
    if not by_store:
        return not_applicable(_NO_STORE)

    audit_by_store = {
        store: [n for n, t in store_tables.items() if is_audit_table(n, t)]
        for store, store_tables in by_store.items()
    }
    total_audit = sum(len(names) for names in audit_by_store.values())
    if not total_audit:
        return not_applicable(
            f"No audit/log/quality-shaped table found in {len(by_store)} store(s), so "
            f"there is no audit role to separate"
        )

    separated, detail = 0, []
    for store, audit_names in sorted(audit_by_store.items()):
        if not audit_names:
            continue
        # Every Fabric SQL endpoint exposes queryinsights and Delta-metadata
        # views. Counting those as business data made a store holding nothing
        # but audit tables read as mixed.
        business_names = [
            name for name in by_store[store]
            if name not in set(audit_names) and not is_platform_table(name)
        ]
        business = len(business_names)
        # A store is an audit store when audit tables dominate it â€” a couple of
        # reference tables alongside the logs is "few", not a mixed store.
        dedicated = business <= len(audit_names) // 4
        if dedicated:
            separated += len(audit_names)
        detail.append(
            f"{store}: {len(audit_names)} audit ({_sample(audit_names)}) / "
            f"{business} business"
            + (f" ({_sample(business_names)})" if business else "")
            + f" â€” {'dedicated' if dedicated else 'mixed'}"
        )
    return covered(
        separated, total_audit,
        f"{separated} of {total_audit} audit table(s) sit in a store dedicated to audit "
        f"data â€” {'; '.join(detail)}",
    )


def _sample(names: list[str], limit: int = 3) -> str:
    """Up to ``limit`` table names, with the remainder summarised as a count."""
    shown = sorted(names)[:limit]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f", +{rest} more" if rest > 0 else "")


@check(
    id="TB-CONFORMED-DIM", ref="4.4.9",
    title="Cross-domain conformed dimensions shared, not duplicated per domain",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def conformed_dimensions(ctx: CheckContext) -> Verdict:
    """One dimension per business concept, shared across stores instead of copied into each.

    **What it can determine.** Dimension names that reduce to the same *purpose*
    once container, tier and version words are stripped (``DimCustomer``,
    ``dim_customer_v2``, ``GOLD_dim_customer``). The same purpose materialised in
    two or more stores is a per-domain copy, not a conformed dimension.

    **What it cannot.** Whether two same-named dimensions actually hold the same
    rows, or whether a domain copy was a deliberate, documented exception. Names
    that reduce to nothing but noise are excluded rather than guessed at, and
    tables with no readable owning store never count as a duplicate.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    by_store = tables_by_store(tables)
    if not by_store:
        return not_applicable(_NO_STORE)
    if len(by_store) < 2:
        return not_applicable(
            f"Only {len(by_store)} store could be attributed, so no dimension can be "
            f"duplicated across stores"
        )

    stores_by_purpose: dict[tuple[str, ...], set[str]] = {}
    judged = 0
    roles = table_roles(tables, _models(ctx))
    for store, store_tables in by_store.items():
        for name in store_tables:
            if roles.get(name) != "dimension":
                continue
            purpose = purpose_tokens(name)
            if not purpose:
                continue
            judged += 1
            stores_by_purpose.setdefault(purpose, set()).add(store)

    if not judged:
        return not_applicable(
            "No dimension table with a comparable name was attributed to a store"
        )

    duplicated = {p: s for p, s in stores_by_purpose.items() if len(s) > 1}
    shared = len(stores_by_purpose) - len(duplicated)
    if not duplicated:
        return covered(
            len(stores_by_purpose), len(stores_by_purpose),
            f"{len(stores_by_purpose)} dimension concept(s) across {len(by_store)} store(s) "
            f"each exist in exactly one store",
        )
    detail = "; ".join(
        f"'{' '.join(purpose)}' in " + ", ".join(sorted(stores))
        for purpose, stores in sorted(duplicated.items())[:3]
    )
    return covered(
        shared, len(stores_by_purpose),
        f"{len(duplicated)} of {len(stores_by_purpose)} dimension concept(s) are "
        f"materialised in more than one store: {detail}",
    )


@check(
    id="TB-FACT-PURITY", ref="4.5.3",
    title="Fact tables contain only foreign keys and measures (no descriptive attributes)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def fact_tables_have_no_descriptive_attributes(ctx: CheckContext) -> Verdict:
    """Fact tables carry keys, measures and lineage columns â€” descriptions belong in dimensions.

    **What it can determine.** Text-typed columns on a fact table that are
    neither a key/identifier nor an audit/lineage column. Those are descriptive
    attributes that should live on a dimension.

    **What it cannot.** Whether a numeric column is a genuine measure, or whether
    a denormalised attribute was a deliberate, documented performance choice.
    Key detection is deliberately generous, so the count under-reports rather
    than accuses. Facts whose columns could not be read are excluded.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    facts = {n: t for n, t in facts_in(tables, _models(ctx)).items()
             if any(c.get("type") for c in columns(t))}
    if not facts:
        return not_applicable(
            "No fact table with readable column types â€” nothing to judge for "
            "descriptive attributes"
        )

    clean, offenders = [], []
    for name, table in facts.items():
        descriptive = [
            c.get("name") or ""
            for c in columns(table)
            if c.get("type")
            and is_text_column(c)
            and not is_key_column(c.get("name") or "")
            and not is_audit_column(c.get("name") or "")
        ]
        if descriptive:
            offenders.append(f"{name} ({', '.join(sorted(descriptive)[:4])})")
        else:
            clean.append(name)
    return covered(
        len(clean), len(facts),
        f"{len(clean)} of {len(facts)} fact table(s) carry only keys, measures and audit "
        f"columns" + (f"; descriptive attributes on {'; '.join(sorted(offenders)[:3])}"
                      if offenders else ""),
    )


@check(
    id="TB-DIM-DENORM", ref="4.5.4",
    title="Dimension tables are denormalized appropriately (star over snowflake unless justified)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def dimensions_are_denormalized(ctx: CheckContext) -> Verdict:
    """A dimension is flat: it does not key out to another dimension table.

    **What it can determine.** A key column on one dimension whose name resolves
    to *another* dimension table present in the workspace (``dim_product`` with
    ``category_sk`` beside a ``dim_category``). That is the snowflake shape the
    point warns about.

    **What it cannot.** Whether a snowflake was justified â€” a genuinely large,
    slowly-changing outrigger is a legitimate exception â€” so the evidence names
    the links for review rather than asserting they are wrong. A key pointing at
    a dimension that is not in this workspace is not counted, because it cannot
    be confirmed.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in dimensions_in(tables, _models(ctx)).items() if columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)

    purposes: dict[tuple[str, ...], str] = {}
    for name in dims:
        purpose = purpose_tokens(name)
        if purpose:
            purposes.setdefault(purpose, name)

    flat, snowflaked = [], []
    for name, table in dims.items():
        own = purpose_tokens(name)
        links = sorted({
            purposes[referent]
            for referent in (key_referent(c.get("name") or "") for c in columns(table))
            if referent and referent != own and referent in purposes
        })
        if links:
            snowflaked.append(f"{name} -> {', '.join(links)}")
        else:
            flat.append(name)
    return covered(
        len(flat), len(dims),
        f"{len(flat)} of {len(dims)} dimension(s) are flat"
        + (f"; snowflake links: {'; '.join(sorted(snowflaked)[:3])} â€” confirm each is a "
           f"justified outrigger" if snowflaked else ""),
    )


#: Column-name markers of a declared SCD strategy. Any one of them means the
#: dimension's change handling was designed rather than defaulted.
#:
#: ``start_date``/``end_date``/``version``/``expiry_date`` are deliberately
#: **excluded**: they are ordinary business columns (a contract's term, a
#: promotion window, a product revision), and excluding them keeps this
#: vocabulary consistent with :func:`is_audit_column`, which rejects the same
#: words for the same reason. An SCD marker must name *row validity* or *row
#: change*, never an arbitrary date range.
#:
#: ``is_active``/``is_deleted`` are excluded for the same reason â€” they are
#: business state flags. A row being active says nothing about whether its
#: history is versioned.
#: Columns that mark the *start* of a row version. Vocabulary drawn from the
#: frameworks that actually generate these columns: dbt snapshots
#: (``dbt_valid_from``), Data Vault 2.0 (``load_date``), SQL Server temporal
#: tables (``SysStartTime``), and Microsoft's own ADF/Synapse SCD-2 data flow,
#: which uses ``StartDate``/``EndDate``/``IsActive``
#: (``learn.microsoft.com/azure/data-factory/data-flow-scd-type-2``).
_SCD_START_MARKERS: frozenset[str] = frozenset({
    "valid_from", "validfrom", "valid_start_date", "dbt_valid_from",
    "effective_from", "effectivefrom", "effective_start_date",
    "row_effective_date", "record_start_date", "dw_valid_from", "dw_start_date",
    "load_date", "load_dts", "sysstarttime", "sys_start_time",
    "meta_valid_from", "_scd_start_date", "__start_at",
})

#: Names that *may* be row-versioning columns but are far more often ordinary
#: business attributes: a contract term, a promotion window, an employee's
#: tenure, a product revision. ``is_audit_column`` already refuses to read
#: ``start_date`` as lineage metadata for exactly this reason, and the
#: vocabularies must agree - a dimension holding a business date range is not
#: thereby a Type 2 dimension.
#:
#: They count only alongside a current-row flag. That combination is Microsoft's
#: own ADF/Synapse SCD-2 output (``StartDate``/``EndDate``/``IsActive``) and is
#: not a shape a business date range takes: a contract term does not carry
#: ``is_current``.
_SCD_START_AMBIGUOUS: frozenset[str] = frozenset({
    "start_date", "startdate", "start_dt", "begin_date", "begin_dt",
    "effective_date", "effectivedate", "eff_date", "eff_dt",
})

#: Columns that mark the *end* of a row version - the half most often missing.
_SCD_END_MARKERS: frozenset[str] = frozenset({
    "valid_to", "validto", "valid_until", "validuntil", "dbt_valid_to",
    "expiry_date", "expiration_date",
    "row_expiry_date", "row_expiration_date", "record_end_date",
    "effective_to", "effectiveto", "effective_end_date",
    "thru_date", "through_date", "dw_valid_to", "dw_end_date",
    "load_end_date", "load_end_dts", "sysendtime", "sys_end_time",
    "meta_valid_to", "_scd_end_date", "__end_at",
})

#: The end-date counterparts to ``_SCD_START_AMBIGUOUS`` - same reasoning.
_SCD_END_AMBIGUOUS: frozenset[str] = frozenset({
    "end_date", "enddate", "end_dt", "finish_date", "finish_dt",
})

#: A convenience flag. On its own it proves nothing - ``is_active`` is just as
#: likely to be a soft-delete marker - so it never establishes SCD 2 alone.
_SCD_FLAG_MARKERS: frozenset[str] = frozenset({
    "is_current", "iscurrent", "current_flag", "currentflag", "curr_flag",
    "current_ind", "current_indicator", "current_record_flag", "is_current_record",
    "active_flag", "activeflag", "is_active", "isactive", "active_ind",
    "is_latest", "islatest", "latest_flag", "dw_is_current", "row_is_current",
})

#: A monotonic version is the documented alternative to an end date: paired with
#: a start date it identifies which row supersedes which.
_SCD_VERSION_MARKERS: frozenset[str] = frozenset({
    "version", "version_number", "row_version", "rowversion",
    "record_version", "scd_version", "revision",
})

#: Change-detection hashes. Evidence of *how* changes are spotted, never of row
#: versioning itself - a hash column alone is as likely to be deduplication.
_SCD_HASH_MARKERS: frozenset[str] = frozenset({
    "row_hash", "rowhash", "record_hash", "hash_diff", "hashdiff",
    "dbt_scd_id", "checksum", "change_hash", "delta_hash",
})

#: Type 3 keeps the prior value in a second column beside the current one.
_SCD3_PREVIOUS = re.compile(
    r"^(?:prev|previous|prior|old|former|original)[_-]\w+"
    r"|\w+[_-](?:prev|previous|prior|old|former|original)$",
    re.IGNORECASE,
)


def _scd_shape(table: dict) -> str:
    """Classify one dimension's SCD implementation from its column names.

    Returns ``"complete"``, ``"incomplete"``, ``"type3"`` or ``"none"``.

    **The rule follows Kimball.** Design Tip #107 names three metadata columns
    for Type 2 - row effective date, row expiry date, current row indicator -
    and the validity *pair* is what makes row versioning work: a start date says
    when a version began, an end date says when it was superseded. A version
    number or a change hash is the documented alternative machinery.

    A lone current flag is deliberately **not** enough. ``is_active`` is just as
    likely to be a soft-delete marker, and a flag with no validity window cannot
    say which row was current *when* - which is the entire point of Type 2.

    Ambiguous business names (``start_date``, ``end_date``) count only when a
    current-row flag sits beside them: that combination is Microsoft's own
    ADF/Synapse SCD-2 output, and is not a shape a contract term ever takes.

    Validity columns must also carry a temporal *type*. ``valid_from int`` is
    not a date whatever it is called. Columns with no readable type stay
    eligible - an absent type is not evidence against.
    """
    names = {c.replace(" ", "").replace("-", "_").lower() for c in col_names(table)}
    # A validity column must be able to hold a point in time. `valid_from int`
    # or `start_date varchar` is not a date, whatever it is called - a free
    # filter against a false positive, using type data already in the snapshot.
    # Columns whose type is unreadable stay eligible: an absent type is not
    # evidence against, and this check must not degrade on a partial crawl.
    dated = {
        (c.get("name") or "").replace(" ", "").replace("-", "_").lower()
        for c in columns(table)
        if not column_type(c) or is_timestamp_column(c)
    }
    has_flag = bool(names & _SCD_FLAG_MARKERS)
    has_start = bool(dated & _SCD_START_MARKERS) or (
        has_flag and bool(dated & _SCD_START_AMBIGUOUS))
    has_end = bool(dated & _SCD_END_MARKERS) or (
        has_flag and bool(dated & _SCD_END_AMBIGUOUS))
    has_version = bool(names & _SCD_VERSION_MARKERS)
    has_hash = bool(names & _SCD_HASH_MARKERS)

    if has_start and (has_end or has_version):
        return "complete"
    # A change hash is unambiguous SCD machinery - no business column is called
    # ``hash_diff`` - so it stands on its own as a declared change-handling
    # strategy, even though it versions nothing by itself.
    if has_hash:
        return "complete"
    if has_start or has_end or has_flag or has_version:
        return "incomplete"
    if sum(1 for name in names if _SCD3_PREVIOUS.match(name)) >= 2:
        return "type3"
    return "none"


@check(
    id="TB-SCD-STRATEGY", ref="4.5.8",
    title="SCD strategy defined and implemented per dimension (Type 1 / Type 2 / Hybrid)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def scd_strategy_per_dimension(ctx: CheckContext) -> Verdict:
    """Each dimension's change-handling strategy is evident, and *complete*.

    **What counts as complete.** Kimball's Type 2 needs a validity *pair* - a
    start date plus either an end date or a version number - because that is
    what says which row was current at a given time. Microsoft's own ADF/Synapse
    SCD-2 data flow generates exactly that shape (``StartDate`` / ``EndDate`` /
    ``IsActive``), as do dbt snapshots (``dbt_valid_from`` / ``dbt_valid_to``)
    and SQL Server temporal tables (``SysStartTime`` / ``SysEndTime``).

    **Why a lone flag is not enough.** An earlier version accepted *any single*
    marker, so a dimension carrying only ``is_current`` scored as a declared
    strategy - and an estate where every dimension looked like that scored a
    full pass. That is the broken half-implementation the point warns about:
    ``is_active`` is as often a soft-delete marker, and without dates nothing
    records *when* a row was current. Those now score as incomplete.

    **Type 1 is legitimate, so it is never a hard failure.** A dimension with no
    markers may be a deliberate overwrite-in-place with the decision recorded
    somewhere unreadable. The floor is 1, not 0.

    **What it cannot determine.** Whether the ETL actually maintains the
    columns, whether the flag stays in sync with the dates, or whether Type 1
    was chosen rather than defaulted into. Column names show declared *intent*,
    never operational correctness.

    Broader than ``TB-SCD2`` (ref 4.5.9), which asks whether dimensions already
    identified as Type 2 carry the full trio. This asks which strategy - if
    any - each dimension declares.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in dimensions_in(tables, _models(ctx)).items() if columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)

    shapes = {name: _scd_shape(table) for name, table in dims.items()}
    complete = sorted(n for n, s in shapes.items() if s == "complete")
    incomplete = sorted(n for n, s in shapes.items() if s == "incomplete")
    type3 = sorted(n for n, s in shapes.items() if s == "type3")
    plain = sorted(n for n, s in shapes.items() if s == "none")
    declared = complete + type3

    detail = ""
    if incomplete:
        detail = (
            f". {len(incomplete)} carry a partial marker set "
            f"({', '.join(incomplete[:4])}) - a current flag with no validity dates, "
            f"or a start date with no end date or version, cannot record which row "
            f"was current when, so row versioning is not demonstrated"
        )

    if declared and not incomplete and not plain:
        return graded(
            3,
            f"All {len(dims)} dimension(s) declare a complete change-handling strategy "
            f"({len(complete)} with a validity pair, {len(type3)} keeping previous "
            f"values)",
        )
    if declared:
        return graded(
            2,
            f"{len(declared)} of {len(dims)} dimension(s) declare a complete SCD "
            f"strategy ({', '.join(declared[:4])}); {len(plain)} overwrite in place "
            f"(Type 1 by default), which may be correct but is not evident in the "
            f"schema{detail}",
        )
    if incomplete:
        return graded(
            1,
            f"None of the {len(dims)} dimension(s) declares a complete SCD strategy"
            f"{detail}. Confirm whether row history is genuinely tracked",
        )
    return graded(
        1,
        f"None of the {len(dims)} dimension(s) carry SCD markers - all are Type 1 by "
        f"default. That is a legitimate strategy, but no schema evidence shows it was "
        f"chosen per dimension; confirm and record the decision",
    )


# =============================================================================
# 4.5.2 — fact grain, and 4.5.11 — degenerate / junk dimension candidates
#
# Both points are only *partly* readable, and the limit is named in each
# docstring rather than blurred:
#
# * 4.5.2 asks for a grain that is "clearly defined **and documented**". No
#   Fabric REST call and no SQL analytics endpoint query returns a table
#   description, an extended property or a column comment (verified against the
#   Fabric T-SQL surface area and the Lakehouse List Tables reference), so the
#   documented half is out of reach entirely. Only the "clearly defined" half is
#   scored.
# * 4.5.11 asks for degenerate/junk dimensions "where appropriate". Whether a
#   modelling choice is appropriate is a judgement, and the deciding fact
#   (cardinality) needs row data this tool must not fetch. The *detection* is
#   still factual - a key resolving to no dimension is a readable gap - so the
#   check scores the share of facts free of those shapes and names the rest as
#   candidates for review.
# =============================================================================

#: A grain needs at least this many independent components to be identifiable
#: from the schema. One key alone ("one row per order") is as often a surrogate
#: key on a wide table as a declared grain; two say "one row per X per Y".
_MIN_GRAIN_COMPONENTS = 2

#: The pseudo-referent used for a fact's time component when that component comes
#: from a plain timestamp column rather than a date key.
_TIME_COMPONENT = ("<time>",)


def _grain_components(name: str, table: dict) -> set[tuple[str, ...]]:
    """The distinct things one row of ``table`` is keyed by, as purpose tuples.

    A key column whose referent is the fact's *own* purpose (``sales_sk`` on
    ``fact_sales``) is the row's surrogate identity, not a grain component, so it
    is excluded. A timestamp column that is not itself a key adds the time
    component, because "per day" is part of most fact grains.
    """
    own = purpose_tokens(name)
    components: set[tuple[str, ...]] = set()
    timed = False
    for column in columns(table):
        column_name = column.get("name") or ""
        referent = key_referent(column_name)
        if referent and referent != own:
            components.add(referent)
        elif not is_key_column(column_name) and is_timestamp_column(column):
            timed = True
    if timed:
        components.add(_TIME_COMPONENT)
    return components


@check(
    id="TB-FACT-GRAIN", ref="4.5.2",
    title="Fact table grain clearly defined and documented for each fact table",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def fact_grain_is_identifiable(ctx: CheckContext) -> Verdict:
    """Each fact table's schema shows what one row *is* â€” the keys that define its grain.

    **What it can determine.** For every fact table with readable columns, the
    distinct grain components its schema declares: foreign keys resolving to
    something other than the fact itself (``customer_sk``, ``product_id``,
    ``date_sk``) plus a time component from a non-key timestamp column. Two or
    more such components mean the schema states a grain â€” "one row per customer
    per day" â€” that a reviewer can read off the table. Fewer means the grain is
    not evident from the schema at all.

    **What it cannot â€” half the checklist point.** *Documented* is not readable.
    No Fabric REST endpoint and no SQL analytics endpoint query this tool makes
    returns a table description, an extended property, or a column comment, so
    whether the grain is written down anywhere is out of reach and is **not**
    guessed at: this check scores only the "clearly defined" half, and the
    evidence says so. It also cannot confirm the declared grain is the *intended*
    one, cannot tell a genuine grain key from a coincidentally key-named column,
    and never reads a row to check the grain is actually unique.

    **Siblings.** ``TB-FACT-PURITY`` (ref 4.5.3) asks whether a fact carries
    descriptive text it should not; this asks whether it carries enough keys to
    say what a row means. ``NB-GRAIN-UNIQUE`` (ref 5.4.9) reads notebook code for
    a duplicate-grain assertion â€” code, not schema, and uniqueness, not
    definition.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    facts = {n: t for n, t in facts_in(tables, _models(ctx)).items() if columns(t)}
    if not facts:
        return not_applicable(
            "No fact table with readable column metadata â€” there is no grain to read"
        )

    defined, undefined = [], []
    for name, table in sorted(facts.items()):
        components = _grain_components(name, table)
        if len(components) >= _MIN_GRAIN_COMPONENTS:
            defined.append(f"{name} ({len(components)} grain key(s))")
        else:
            undefined.append(f"{name} ({len(components)} grain key(s))")
    return covered(
        len(defined), len(facts),
        f"{len(defined)} of {len(facts)} fact table(s) declare a readable grain â€” at least "
        f"{_MIN_GRAIN_COMPONENTS} distinct grain key(s) in the schema"
        + (f"; grain not evident on {'; '.join(undefined[:3])}" if undefined else "")
        + ". Whether the grain is *documented* is not readable from any Fabric or SQL "
          "endpoint API, so only the 'clearly defined' half of the point is scored here.",
    )


#: Column names that read as a low-cardinality flag or indicator - the kind of
#: attribute a junk dimension is built to collapse. Matched on the whole
#: (lower-cased) name so ``is_active``, ``paid_flag`` and ``order_status`` count
#: while ``flag_description`` does not.
#:
#: Split into two confidences, because they behave differently against real
#: schemas. See ``_UNAMBIGUOUS_FLAG`` below.
_FLAG_COLUMN = re.compile(
    r"^(?:is|has|can|was|are)_\w+$|"
    r"^\w+_(?:flag|flg|ind|indicator|status|type|code|category|reason)$|"
    r"^(?:flag|status|indicator)$",
    re.IGNORECASE,
)

#: The half of the pattern that is unambiguous on its own. Nobody names a
#: comment field ``is_active`` or a description ``paid_flag`` - the ``is_``/
#: ``has_`` prefix and the ``_flag``/``_ind`` suffix state the column's shape
#: in the name itself.
#:
#: **Why the distinction earns its place.** The junk half also requires the
#: declared type to look narrow, so a ``rejection_reason varchar(500)`` cannot
#: pass on its name alone. But Lakehouse Delta tables usually declare a bare
#: ``string`` with no width, and requiring a width there silenced the check
#: completely: on a real estate every one of 51 findings disappeared, including
#: genuine ones like ``store_and_fwd_flag``. Trading 51 false positives for zero
#: findings is not an improvement.
#:
#: So these names are trusted without a width, and the vaguer suffixes -
#: ``_type``, ``_status``, ``_code``, ``_category``, ``_reason``, which are the
#: ones that actually caused the false positives - still need the type to agree.
_UNAMBIGUOUS_FLAG = re.compile(
    r"^(?:is|has|can|was|are)_\w+$|"
    r"^\w+_(?:flag|flg|ind|indicator)$|"
    r"^(?:flag|indicator)$",
    re.IGNORECASE,
)


def _is_junk_candidate(column: dict) -> bool:
    """True when a column is plausibly a junk-dimension input.

    An unambiguous flag name stands alone. A vaguer suffix needs the declared
    type to be narrow as well - or to be absent, since an unreadable type is
    not evidence against a column.
    """
    name = (column.get("name") or "").strip()
    if not _FLAG_COLUMN.match(name):
        return False
    if _UNAMBIGUOUS_FLAG.match(name):
        return True
    return not column_type(column) or is_low_cardinality_shape(column)


#: Below this many flag columns on one fact, collapsing them into a junk
#: dimension buys nothing - the pattern exists to remove *several* low-value
#: columns, not one.
_MIN_JUNK_CANDIDATES = 3


@check(
    id="TB-DEGENERATE-JUNK-DIM", ref="4.5.11",
    title="Degenerate and junk dimensions used where appropriate",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def degenerate_and_junk_dimension_candidates(ctx: CheckContext) -> Verdict:
    """Fact tables carry no unmodelled key or uncollapsed flag cluster.

    **What it can determine.** *Degenerate candidates*: key-shaped columns on a
    fact table whose referent matches no dimension table in this workspace and is
    not the fact's own identity (``order_number`` on ``fact_sales`` with no
    ``dim_order``) - the exact shape of a degenerate dimension. *Junk
    candidates*: three or more flag/indicator/status-shaped columns on one fact,
    the cluster a junk dimension exists to collapse.

    **Declared relationships outrank the name test.** A column that participates
    in a semantic-model relationship is *stated* by the modeller to resolve to
    another table, so it is never reported as unmodelled - whatever it is called.
    Only columns with no declared relationship fall through to the name-based
    test. That turns the strongest half of this check from an inference into a
    fact the estate itself asserts, and removes the false positives from
    dimensions whose names do not resemble their foreign keys.

    **Declared types constrain the vaguer half of the junk test.** A junk
    dimension collapses *low-cardinality* columns. An unambiguous flag name -
    ``is_returned``, ``store_and_fwd_flag`` - is trusted on its own, because
    nobody names a comment field that way. A vaguer suffix (``_type``,
    ``_status``, ``_code``, ``_category``, ``_reason``) also needs its declared
    type to look narrow, so ``rejection_reason varchar(500)`` no longer counts.
    Columns whose type is unreadable are kept: an unreadable type is not
    evidence against. The split matters because Lakehouse Delta tables usually
    declare a bare ``string`` with no width; requiring a width everywhere
    silenced this half of the check entirely on a real estate.

    **Scored on the share of fact tables that are clean.** An earlier version
    reported the candidates as an unscored ``note``, reasoning that "where
    appropriate" is a modelling judgement. It is - but the *detection* is not:
    a key that resolves to no dimension is a readable, factual gap in the model,
    and reporting it as INFO meant accurate findings never influenced anything.
    The verdict now scores how many facts are free of these shapes; the named
    candidates remain what a reviewer confirms.

    **What it cannot.** Column cardinality (no row data is read - the type is a
    shape proxy), whether the referenced dimension lives in another workspace,
    whether a degenerate column is intentional, or whether an existing junk
    dimension is already in use somewhere it cannot see. Where no semantic model
    is readable there are no declared relationships, and the check falls back
    entirely to names - a Bronze/landing workspace gets the weaker test, and the
    evidence says so. A named table is a *candidate for review*: the check
    reports the shape, not the intent.

    **Sibling.** ``TB-FACT-PURITY`` (ref 4.5.3) scores *text* attributes on a
    fact that belong on a dimension. This looks at key- and flag-shaped columns,
    which that check deliberately ignores.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    facts = {n: t for n, t in facts_in(tables, _models(ctx)).items() if columns(t)}
    if not facts:
        return not_applicable(
            "No fact table with readable column metadata â€” no degenerate or junk "
            "dimension candidate can be identified"
        )

    dimension_purposes = {
        purpose_tokens(n) for n in dimensions_in(tables, _models(ctx)) if purpose_tokens(n)
    }

    degenerate: list[str] = []
    junk: list[str] = []
    # A relationship is the modeller declaring that this column resolves to a
    # dimension. Where one exists the name test is not consulted at all - the
    # estate has already answered the question the name was being used to guess.
    modelled = related_columns(_models(ctx))
    for name, table in sorted(facts.items()):
        own = purpose_tokens(name)
        table_key = normalise_table_name(name)
        orphan_keys = sorted({
            column.get("name") or ""
            for column in columns(table)
            if (referent := key_referent(column.get("name") or ""))
            and referent != own
            and referent not in dimension_purposes
            and (table_key, normalise_column(column.get("name") or "")) not in modelled
        })
        if orphan_keys:
            degenerate.append(f"{name}: {', '.join(orphan_keys[:4])}")
        # A junk dimension collapses *low-cardinality* columns. An unambiguous
        # flag name (`is_*`, `*_flag`) is trusted on its own; a vaguer suffix
        # (`*_type`, `*_reason`) needs the declared type to agree, because that
        # is where the false positives came from.
        flags = sorted({
            (column.get("name") or "")
            for column in columns(table)
            if _is_junk_candidate(column)
        })
        if len(flags) >= _MIN_JUNK_CANDIDATES:
            junk.append(f"{name}: {len(flags)} flag column(s) ({', '.join(flags[:4])})")

    flagged = {entry.split(":")[0] for entry in degenerate} | {entry.split(":")[0] for entry in junk}
    clean = len(facts) - len(flagged)

    if not degenerate and not junk:
        return covered(
            len(facts), len(facts),
            f"All {len(facts)} fact table(s) are free of degenerate/junk dimension "
            f"candidates: every key column resolves to a dimension in this workspace "
            f"and no fact carries {_MIN_JUNK_CANDIDATES}+ flag/status columns"
        )
    parts = []
    if degenerate:
        parts.append(
            f"{len(degenerate)} fact table(s) carry a key with no matching dimension "
            f"(degenerate-dimension candidates) - {'; '.join(degenerate[:3])}"
        )
    if junk:
        parts.append(
            f"{len(junk)} fact table(s) carry {_MIN_JUNK_CANDIDATES}+ flag/status columns "
            f"(junk-dimension candidates) - {'; '.join(junk[:3])}"
        )
    return covered(
        clean, len(facts),
        f"{clean} of {len(facts)} fact table(s) carry no degenerate or junk dimension "
        f"candidate. " + "; ".join(parts)
        + ". Each named table is a candidate for review: cardinality is not readable "
          "without querying rows, and whether the pattern is appropriate here is a "
          "modelling judgement this check does not make."
    )


# =============================================================================
# 4.4.1 â€” Warehouse schema organization
# =============================================================================

#: Words that mark a schema as the *landing / work* area rather than a
#: presentation schema a consumer queries.
_STAGING_SCHEMA_WORDS: frozenset[str] = frozenset({
    "stg", "stage", "staging", "landing", "land", "raw", "bronze", "ingest",
    "ingestion", "tmp", "temp", "work", "wrk", "etl", "elt", "load", "src",
})

#: The default schema every Warehouse ships with. A Warehouse whose tables all
#: sit here has taken no organisational decision at all.
_DEFAULT_SCHEMA = "dbo"

# Fabric catalog schemas are platform metadata, not business-domain organization.
_SYSTEM_SCHEMAS: frozenset[str] = frozenset({
    "sys", "information_schema", "queryinsights", "query_insights",
})


def _schema_qualifier(key: str, table: dict) -> str:
    """The SQL schema a Warehouse table belongs to, or ``""`` when unknown.

    Read from the column metadata (``INFORMATION_SCHEMA.TABLE_SCHEMA``, recorded
    on each column by the SQL-endpoint reader) - the authoritative answer. The
    key is only consulted as a fallback, for snapshots crawled before the reader
    captured the schema: it keys a table by name and prefixes ``"<store>."`` only
    when two stores hold the same table name, so the store is stripped first and
    never mistaken for a schema.
    """
    for column in columns(table):
        declared = str(column.get("schema") or "").strip()
        if declared:
            return declared

    store = store_of(table)
    rest = key
    if store and rest.lower().startswith(f"{store.lower()}."):
        rest = rest[len(store) + 1:]
    head, separator, tail = rest.partition(".")
    return head.strip() if separator and tail.strip() and head.strip() else ""


def _schema_score(schemas: dict[str, int]) -> int:
    """0-3 for one Warehouse's schema layout, given ``{schema: table count}``."""
    named = {s for s in schemas if s and s != _DEFAULT_SCHEMA}
    has_staging = any(s in _STAGING_SCHEMA_WORDS for s in schemas)
    presentation = {s for s in named if s not in _STAGING_SCHEMA_WORDS}
    if not named:
        return 0                                    # everything in dbo
    if len(named) == 1 and not has_staging:
        return 1                                    # one schema, but at least not dbo
    if not (has_staging and presentation):
        return 2                                    # domains split, no separate staging
    return 3


@check(
    id="TB-WH-SCHEMAS", ref="4.4.1",
    title="Warehouse schema organization is logical (by domain schema, plus staging schema)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def warehouse_schema_organization(ctx: CheckContext) -> Verdict:
    """Warehouse tables are split across domain schemas, with staging kept separate.

    Judged per Warehouse, from the schema each table name is qualified with:
    everything in ``dbo`` scores 0 (no organisational decision was taken), a
    single non-default schema 1, two or more domain schemas 2, and two or more
    domain schemas *plus* a distinct staging/landing schema 3. The workspace
    verdict is the floor of the per-Warehouse mean, and the evidence names each
    Warehouse's schemas.

    Only tables *known* to live in a Warehouse are judged (``in_warehouse``), the
    same gating the other store-aware checks use â€” a Lakehouse has no comparable
    schema concept, and a table whose owning store could not be read is unknown,
    never a finding.

    **What it cannot determine.** Whether the schema *names* carry the domain
    meaning their owners intended - ``dbo`` versus ``sales`` versus ``stg`` is
    read as a structural signal, not a semantic one. The schema itself is now
    read from ``INFORMATION_SCHEMA.TABLE_SCHEMA`` and recorded on every column,
    with the schema-qualified table key as a fallback. Older snapshots created
    before either source retained ``TABLE_SCHEMA`` contain bare names; those
    remain N/A rather than being misread as a badly organised Warehouse. A
    fresh crawl upgrades the evidence.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    warehouse_tables = {n: t for n, t in tables.items() if in_warehouse(t)}
    if not warehouse_tables:
        return not_applicable(
            "No table in this workspace is known to live in a Warehouse, so there "
            "is no Warehouse schema layout to judge"
        )

    by_store: dict[str, dict[str, int]] = {}
    qualified = 0
    excluded_system = 0
    for name, table in warehouse_tables.items():
        schema = _schema_qualifier(name, table).lower()
        if schema in _SYSTEM_SCHEMAS:
            excluded_system += 1
            continue
        if schema:
            qualified += 1
        store = store_of(table) or "(unnamed warehouse)"
        counts = by_store.setdefault(store, {})
        counts[schema] = counts.get(schema, 0) + 1

    if not by_store:
        return not_applicable(
            f"All {len(warehouse_tables)} Warehouse table(s) belong to Fabric system "
            "schemas, so there is no business schema layout to judge"
        )

    if not qualified:
        return not_applicable(
            f"None of the {len(warehouse_tables)} Warehouse table(s) read carries a "
            f"schema qualifier â€” the SQL-endpoint reader records the table name "
            f"without its INFORMATION_SCHEMA.TABLE_SCHEMA â€” so schema organisation "
            f"cannot be assessed from this snapshot"
        )

    scores = {store: _schema_score(counts) for store, counts in by_store.items()}
    detail = "; ".join(
        f"'{store}': " + ", ".join(
            f"{schema or '(unqualified)'} ({count} table(s))"
            for schema, count in sorted(by_store[store].items())
        )
        for store in sorted(by_store)
    )
    return graded(
        sum(scores.values()) // len(scores),
        f"{len(by_store)} Warehouse(s) judged on schema layout â€” {detail}. "
        f"{qualified} of {len(warehouse_tables)} Warehouse table(s) carry a schema "
        f"qualifier; a Warehouse holding everything in dbo, or with no staging "
        f"schema separate from its presentation schemas, scores below full. "
        f"Excluded {excluded_system} Fabric system-schema table(s).",
    )


# =============================================================================
# 4.4.2 â€” Warehouse naming *consistency* (one convention, whichever it is)
# =============================================================================

#: A name written entirely in one convention. Order matters when matching:
#: ``customer_id`` is snake before it is anything else, and ``ID`` is UPPER
#: before it is Pascal.
_NAMING_STYLES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("snake_case", re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")),
    ("UPPER_CASE", re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")),
    ("PascalCase", re.compile(r"^(?:[A-Z][a-z0-9]*)+$")),
    ("camelCase", re.compile(r"^[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+$")),
)


def naming_style(name: str) -> str:
    """Which single naming convention ``name`` is written in, or ``"mixed"``.

    ``"mixed"`` covers everything that follows no one convention â€” a space in
    the name, ``Customer_ID`` mixing Pascal with underscores, ``LDP Course
    Name/Domain`` â€” and is what makes an estate's naming *inconsistent*
    regardless of which convention it chose.
    """
    text = (name or "").strip()
    if not text:
        return "mixed"
    for style, pattern in _NAMING_STYLES:
        if pattern.match(text):
            return style
    return "mixed"


def _leaf_name(key: str, table: dict) -> str:
    """The bare table name, with any store and schema qualifier removed."""
    rest = _strip_store(key, table)
    return rest.rpartition(".")[2] if "." in rest else rest


def _strip_store(key: str, table: dict) -> str:
    store = store_of(table)
    if store and key.lower().startswith(f"{store.lower()}."):
        return key[len(store) + 1:]
    return key


def _dominant(styles: list[str]) -> tuple[str, int]:
    """The most common convention in ``styles`` and how many names use it.

    ``mixed`` is never dominant: a name in no convention cannot be the standard
    the estate follows. Ties break alphabetically so the answer is stable.
    """
    counts: dict[str, int] = {}
    for style in styles:
        if style != "mixed":
            counts[style] = counts.get(style, 0) + 1
    if not counts:
        return "none", 0
    best = max(sorted(counts), key=lambda style: counts[style])
    return best, counts[best]


@check(
    id="TB-WH-NAME-CONSISTENCY", ref="4.4.2",
    title="Table and column naming conventions are consistent across the Warehouse",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def warehouse_naming_is_internally_consistent(ctx: CheckContext) -> Verdict:
    """Every Warehouse table and column follows *one* convention â€” whichever one.

    Each name is classified as ``snake_case``, ``UPPER_CASE``, ``PascalCase``,
    ``camelCase``, or ``mixed`` (no single convention â€” a space, or Pascal words
    joined by underscores). The dominant convention is then found separately for
    table names and for column names, and the score is the share of names that
    follow their own group's dominant convention. A Warehouse written entirely
    in PascalCase scores full marks; one that is half snake and half Pascal does
    not.

    **Deliberately different from ``TB-COL-NAMING`` (ref 4.2.3)**, which scores
    the share of columns that are specifically ``snake_case``, across *every*
    table in the workspace. Two differences, both real: this one is scoped to
    tables known to live in a **Warehouse** (``in_warehouse``), and it mandates
    **no particular convention** â€” it measures internal consistency, so an
    all-PascalCase Warehouse passes here and fails there, which is the intended
    distinction between "follow the house style" and "follow snake_case".

    Store and schema qualifiers are stripped before judging, so a key like
    ``Sales.dbo.FactOrders`` is judged on ``FactOrders``. N/A when no table is
    known to live in a Warehouse; column consistency is skipped (and said so)
    when no column metadata was read.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    warehouse_tables = {n: t for n, t in tables.items() if in_warehouse(t)}
    if not warehouse_tables:
        return not_applicable(
            "No table in this workspace is known to live in a Warehouse, so "
            "Warehouse naming consistency cannot be judged"
        )

    table_styles = [naming_style(_leaf_name(n, t)) for n, t in warehouse_tables.items()]
    column_styles = [
        naming_style(str(column.get("name") or ""))
        for table in warehouse_tables.values()
        for column in columns(table)
    ]

    table_convention, table_ok = _dominant(table_styles)
    column_convention, column_ok = _dominant(column_styles)

    compliant = table_ok + column_ok
    total = len(table_styles) + len(column_styles)

    detail = (
        f"{table_ok} of {len(table_styles)} Warehouse table name(s) follow the "
        f"dominant convention ({table_convention})"
    )
    if column_styles:
        detail += (f"; {column_ok} of {len(column_styles)} column name(s) follow "
                   f"theirs ({column_convention})")
    else:
        detail += ("; no column metadata was read, so column naming is not "
                   "included in this score")
    detail += (". Consistency is what is scored â€” any one convention counts, "
               "provided the Warehouse sticks to it.")
    return covered(compliant, total, detail)


# =============================================================================
# View/procedure abstraction between the physical tables and the
# semantic layer
# =============================================================================

#: A view definition in T-SQL or Spark SQL. ``CREATE OR ALTER VIEW`` and
#: ``CREATE OR REPLACE VIEW`` are both spelled by the optional middle group.
_VIEW_DDL = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:MATERIALIZED\s+)?VIEW\b|"
    r"\bALTER\s+VIEW\b",
    re.IGNORECASE,
)

#: A stored procedure or a user-defined function â€” abstraction over the physical
#: tables for *writes* and reusable logic, but not the semantic-facing read
#: surface a view provides.
_PROC_DDL = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?PROC(?:EDURE)?\b|\bALTER\s+PROC(?:EDURE)?\b|"
    r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?FUNCTION\b",
    re.IGNORECASE,
)

#: How many defining items to name in the evidence before summarising.
_MAX_NAMED_SOURCES = 5


@check(
    id="WS-VIEW-ABSTRACTION", ref="4.4.7",
    title="Views/stored procedures used to abstract the semantic-facing layer from physical tables",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.TABLE_SCHEMAS, Resource.PIPELINE_DEFINITIONS,
              Resource.NOTEBOOK_DEFINITIONS],
    required=False,
)
def workspace_defines_a_view_layer_over_its_tables(ctx: CheckContext) -> Verdict:
    """Reports use a view/procedure layer, so a physical table can change without breaking them.

    **What it can determine.** Views and stored procedures the SQL analytics
    endpoint declares (``INFORMATION_SCHEMA.VIEWS`` / ``ROUTINES``, now read by
    the crawl) - the authoritative answer, including objects created in the SQL
    editor - plus ``CREATE VIEW`` / ``CREATE PROCEDURE`` statements in the T-SQL
    a pipeline runs and in notebook SQL, which is the form that can be reviewed
    and promoted through source control.

    **What it cannot - and this is a real limit on the finding.** Whether the
    semantic models actually *bind* to those views rather than to the physical
    tables. It reports that an abstraction layer exists, not that it is used.
    """
    if not ctx.workspace.has(Resource.TABLE_SCHEMAS):
        return not_applicable(_NO_TABLES)
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)

    readable = [r for r in (Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS)
                if ctx.workspace.has(r)]
    # Views and procedures the endpoint itself declares - authoritative, and it
    # includes objects created in the SQL editor that no pipeline or notebook
    # mentions. This is what the check previously could not see.
    declared_views = ctx.workspace.sql_views
    declared_routines = ctx.workspace.sql_routines
    if declared_views or declared_routines:
        names = sorted({v.get("name", "") for v in declared_views})[:_SAMPLE_LIMIT]
        return binary(
            True,
            f"{len(declared_views)} view(s) and {len(declared_routines)} stored "
            f"procedure/function(s) are declared in the SQL analytics endpoint"
            + (f" (e.g. {', '.join(n for n in names if n)})" if names else "")
            + ". Whether the semantic models bind to them rather than to the "
              "physical tables is not readable here",
        )

    if not readable:
        return not_applicable(
            "Neither pipeline nor notebook definitions could be read, and the SQL "
            "endpoint declared no view or routine, so no abstraction layer is observable"
        )

    pipelines = ctx.workspace.pipelines or {}
    notebooks = ctx.workspace.notebooks or {}
    if not pipelines and not notebooks:
        return not_applicable(
            "Workspace holds no pipeline or notebook definitions, and view metadata is not "
            "fetched, so no view or procedure definition is observable"
        )

    view_sources: list[str] = []
    proc_sources: list[str] = []
    for name, defn in sorted(pipelines.items()):
        sql = script_sql(defn)
        if _VIEW_DDL.search(sql):
            view_sources.append(name)
        if _PROC_DDL.search(sql):
            proc_sources.append(name)
    for name, defn in sorted(notebooks.items()):
        code = executable_code(defn)
        if _VIEW_DDL.search(code):
            view_sources.append(name)
        if _PROC_DDL.search(code):
            proc_sources.append(name)

    caveat = (
        " View metadata itself is not fetched (INFORMATION_SCHEMA.COLUMNS returns view "
        "columns but no TABLE_TYPE), so this counts only definitions found in pipeline "
        "Script activities and notebook SQL."
    )
    tables = len(ctx.workspace.tables)
    if view_sources:
        return graded(
            3,
            f"{len(view_sources)} pipeline(s)/notebook(s) define a view over the "
            f"workspace's {tables} table(s): "
            f"{', '.join(view_sources[:_MAX_NAMED_SOURCES])}." + caveat,
        )
    if proc_sources:
        return graded(
            2,
            f"No view definition found, but {len(proc_sources)} pipeline(s)/notebook(s) "
            f"define a stored procedure or function over the workspace's {tables} table(s): "
            f"{', '.join(proc_sources[:_MAX_NAMED_SOURCES])} â€” logic is abstracted, but "
            f"consumers still read the physical tables directly." + caveat,
        )
    return graded(
        0,
        f"No CREATE VIEW / CREATE PROCEDURE / CREATE FUNCTION statement appears in any of "
        f"the {len(pipelines)} pipeline(s) and {len(notebooks)} notebook(s) read, so the "
        f"semantic layer binds straight to the {tables} physical table(s) and any rename or "
        f"retype breaks it." + caveat,
    )


# =============================================================================
# The serving (Gold) items were actually refreshed inside their SLA.
# =============================================================================

#: Words in an *item* name that mark it as serving/Gold rather than an
#: ingestion or staging store. Matched with :func:`name_words`, the shared
#: name-token splitter, so ``LH_Sales_Gold``, ``lh-sales-gold`` and
#: ``SalesGoldMart`` all yield the same tokens. A Warehouse needs no name match
#: at all (see the check docstring) â€” this list only promotes a *Lakehouse* or a
#: *SemanticModel* into the serving population.
_SERVING_ITEM_WORDS: frozenset[str] = frozenset({
    "gold", "serving", "serve", "curated", "mart", "marts", "datamart",
    "presentation", "published", "publish", "consumption", "semantic",
})

#: Item types whose name is consulted for the serving vocabulary above.
_NAME_MARKED_SERVING_TYPES = ("Lakehouse", "SemanticModel")

#: Default freshness SLA, in hours. Deliberately **48**, not 24: the readable
#: signal is a *run/refresh* timestamp, so a perfectly healthy daily batch that
#: ran 25 hours before the audit would fail a 24-hour window purely on when the
#: audit happened to be run. 48 hours still catches the real defect â€” a serving
#: item that has not refreshed for days â€” without failing an estate for clock
#: jitter. Tune per project with ``gold_freshness_sla_hours``.
_DEFAULT_SLA_HOURS = 48

#: How many stale item names to name in the evidence before summarising.
_MAX_NAMED_STALE = 5

#: Item types whose run/refresh history Fabric actually records. A Warehouse or
#: Lakehouse is a *store*, not a job: nothing "runs" it, so ``last_run_utc`` is
#: never populated for one. A freshness check that reads that field on a
#: Warehouse is asking for a value the platform does not keep - which is why
#: this check reported "0 of 6 carry a readable timestamp" on every
#: Warehouse-based estate, and always would have.
_RUNNABLE_ITEM_TYPES = frozenset({
    "DataPipeline", "Notebook", "Dataflow", "SparkJobDefinition", "SemanticModel",
})


def _writes_to(definition: dict, item_ids: set[str]) -> set[str]:
    """Which of ``item_ids`` a pipeline definition references.

    Read from the **id references the definition already carries** - a Fabric
    activity names its target store by ``artifactId``/``workspaceId``, not by
    display name - so this is a fact the pipeline states, not two objects paired
    because their names look alike.
    """
    blob = json.dumps(definition)
    return {item_id for item_id in item_ids if item_id and item_id in blob}


def _store_freshness(ctx: CheckContext, store: Item) -> tuple[str | None, str]:
    """``(timestamp, source)`` for a store that has no run history of its own.

    A Warehouse is refreshed by whatever loads it, so the newest run of a
    pipeline or notebook that *references this store by id* is the readable
    proxy for "when was this data last written". Returns ``(None, "")`` when
    nothing references it, which the caller must treat as unknown rather than
    stale.
    """
    newest: str | None = None
    source = ""
    for item in ctx.workspace.items:
        if item.type not in {"DataPipeline", "Notebook"} or not item.last_run_utc:
            continue
        definition = (ctx.workspace.pipelines or {}).get(item.display_name) \
            or (ctx.workspace.notebooks or {}).get(item.display_name)
        if not definition or not _writes_to(definition, {store.id}):
            continue
        if newest is None or item.last_run_utc > newest:
            newest = item.last_run_utc
            source = item.display_name or item.id
    return newest, source


def _serving_items(ctx: CheckContext) -> list[Item]:
    """The workspace's Gold/serving items.

    Every **Warehouse** qualifies by type: a Fabric Warehouse exists to be
    queried by reports, so it *is* the serving surface regardless of what it is
    called. A **Lakehouse** or **SemanticModel** qualifies only when its name
    carries a serving token - a Bronze/Silver lakehouse is not Gold, and nothing
    else in the item list distinguishes them.
    """
    serving: list[Item] = []
    for item in ctx.workspace.items:
        item_type = item.type or ""
        if item_type == "Warehouse" or (
            item_type in _NAME_MARKED_SERVING_TYPES
            and name_words(item.display_name or "") & _SERVING_ITEM_WORDS
        ):
            serving.append(item)
    return serving


@check(
    id="WS-GOLD-FRESHNESS", ref="5.4.7",
    title="Freshness validation: Gold tables updated within defined SLA",
    pillar=Pillar.DATA_QUALITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=True,
)
def gold_items_refreshed_within_sla(ctx: CheckContext) -> Verdict:
    """The Gold/serving items refreshed within the freshness SLA window.

    **What it measures â€” an actual elapsed time, not a coded control.** Each
    serving item's last run/refresh (``Item.last_run_utc``, filled from the
    job-scheduler history and, for semantic models, the Power BI refresh
    history) is compared against a window read from
    ``gold_freshness_sla_hours`` (default 48 hours â€” long enough that a healthy
    daily batch is not failed for the hour the audit happened to run).

    **What counts as Gold.** Every Warehouse, because a Fabric Warehouse exists
    to be queried by reports; plus any Lakehouse or SemanticModel whose name
    carries a serving token (gold / serving / curated / mart / presentation /
    published / consumption / semantic), matched with the shared
    :func:`name_words` splitter.

    **What it cannot determine.** This is the item's **last run/refresh**, which
    is the closest readable proxy for "the Gold *table* was updated" - Delta
    table commit times are not fetched, so a run that succeeded while writing
    nothing still reads as fresh, and a table updated by a pipeline in another
    workspace reads as stale here. It also cannot read the *agreed* SLA: the
    window is a project setting, not something the tenant publishes.

    **A store carries no run history of its own.** Fabric records
    ``last_run_utc`` only for runnable items - pipelines, notebooks, dataflows,
    Spark jobs, semantic models. A Warehouse or Lakehouse is a store, so nothing
    "runs" it. Reading that field on a Warehouse asked for a value the platform
    does not keep, and the check reported "0 of 6 carry a readable timestamp" on
    every Warehouse-based estate - a structural mismatch, not a permission gap.
    Those stores now take their freshness from the newest run of a pipeline or
    notebook that **references them by id** in its definition, which is the load
    that writes them.

    **Missing timestamps are excluded, never counted stale.** An item with no
    readable last-run stamp leaves the denominator entirely â€” "we could not read
    when it last ran" is not "it is out of SLA". When no serving item exists, or
    none of them has a readable stamp, the check is N/A.

    **Sibling â€” ``NB-TIMELINESS-CONTROL`` (5.2.3), and the difference matters.**
    That compatibility ID now checks whether pipeline refresh/data activities
    have a custom execution timeout chosen for their SLA. This check reads no
    pipeline policy: it asks whether the serving items were in fact refreshed
    recently. A workspace can pass 5.2.3 with well-bounded pipeline activities
    and fail this one because nothing has run for a week, and vice versa.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    if not ctx.workspace.has(Resource.ITEM_RUN_HISTORY):
        return not_applicable(
            "Per-item run/refresh history could not be read from Fabric "
            "(jobs/instances was forbidden or unavailable), so Gold freshness "
            "cannot be measured"
        )

    serving = _serving_items(ctx)
    if not serving:
        return not_applicable(
            f"No Gold/serving item found among the {len(ctx.workspace.items)} item(s): no "
            "Warehouse, and no Lakehouse or semantic model whose name marks it as gold / "
            "serving / curated / mart / presentation"
        )

    dated: list[tuple[Item, object, str]] = []
    for item in serving:
        stamp = parse_stamp(item.last_run_utc)
        source = "its own run/refresh history"
        if stamp is None and item.type in {"Warehouse", "Lakehouse"}:
            # A store has no run history of its own - Fabric records that only
            # for runnable items. The load that writes it does, so use the
            # newest run of a pipeline/notebook that references this store by id.
            loader_stamp, loader = _store_freshness(ctx, item)
            stamp = parse_stamp(loader_stamp)
            if stamp is not None:
                source = f"the load that writes it ({loader})"
        dated.append((item, stamp, source))

    readable = [(i, stamp, source) for i, stamp, source in dated if stamp is not None]
    if not readable:
        return not_applicable(
            f"None of the {len(serving)} Gold/serving item(s) has a readable last-update "
            f"time. A Warehouse or Lakehouse carries no run history of its own - Fabric "
            f"records that only for runnable items - and no pipeline or notebook that "
            f"writes to them has a readable run either, so how recently they were updated "
            f"cannot be measured. Unknown recency is never reported as stale"
        )

    try:
        sla_hours = int(ctx.setting("gold_freshness_sla_hours", _DEFAULT_SLA_HOURS))
    except (TypeError, ValueError):
        sla_hours = _DEFAULT_SLA_HOURS
    if sla_hours <= 0:
        sla_hours = _DEFAULT_SLA_HOURS
    now = datetime.now(timezone.utc)
    stale = sorted(
        item.display_name or item.id
        for item, stamp, _ in readable
        if (now - stamp).total_seconds() > sla_hours * 3600
    )
    via_loader = sum(1 for _, _, source in readable if source.startswith("the load"))
    excluded = len(serving) - len(readable)

    detail = (
        f"{len(readable) - len(stale)} of {len(readable)} Gold/serving item(s) with a "
        f"readable last update were refreshed within the {sla_hours}h SLA "
        f"(gold_freshness_sla_hours)"
    )
    if via_loader:
        detail += (f"; {via_loader} of those are stores whose freshness was read from the "
                   f"pipeline/notebook that writes them, since a store has no run history "
                   f"of its own")
    if stale:
        detail += (f"; stale: {', '.join(stale[:_MAX_NAMED_STALE])}"
                   + (f", â€¦(+{len(stale) - _MAX_NAMED_STALE} more)"
                      if len(stale) > _MAX_NAMED_STALE else ""))
    if excluded:
        detail += (f". {excluded} further serving item(s) had no readable timestamp and are "
                   "excluded rather than counted stale")
    detail += (". This is the item's last run/refresh â€” the closest readable proxy for "
               "\"the Gold table was updated\"; Delta commit times are not fetched.")
    return covered(len(readable) - len(stale), len(readable), detail)
