"""Data Management & Quality · Data Storage — table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from auditfast.core.check._notebook import executable_code, notebook_code
from auditfast.core.check._pipeline import activities as pipeline_activities
from auditfast.core.check._pipeline import script_sql
from auditfast.core.check._recency import parse_stamp
from auditfast.core.check._tables import (
    TABLE_LAYERS,
    col_names,
    columns,
    has_audit_column,
    has_surrogate_key,
    in_warehouse,
    is_audit_column,
    is_audit_table,
    is_dimension,
    is_fact,
    is_key_column,
    is_platform_table,
    is_snake_case,
    is_text_column,
    is_timestamp_column,
    key_referent,
    name_words,
    purpose_tokens,
    store_of,
    tables_by_store,
)
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext, Item

_NO_TABLES = "No lakehouse/warehouse tables were read for this workspace"

#: N/A reason when lakehouse/warehouse tables exist but none are dimensions with
#: readable columns. Named to make the scope explicit — semantic-model tables
#: (e.g. a Power BI ``DateDimension``) are not lakehouse tables and are judged
#: separately by the 5.4.x semantic-model checks.
_NO_DIMS = "No lakehouse/warehouse dimension tables with column metadata"

#: N/A reason when lakehouse/warehouse tables exist but none carry readable column
#: schemas. Scope is made explicit so it is not mistaken for "no columns anywhere"
#: — semantic-model column metadata is not read by these table checks.
_NO_COLS = "No lakehouse/warehouse table column metadata available"

#: Column names implying a date/time value, for the data-type check.
_DATE_NAME = re.compile(r"(date|timestamp|_dt$|_time$)", re.IGNORECASE)


# T-SQL cursor and pandas row-by-row iteration anti-patterns (3.6.7).
# Note: .collect()/.toPandas() are separately caught by NB-COLLECT; this
# targets explicit SQL cursor syntax and pandas row iterators that are
# semantically cursor-equivalent.
_CURSOR = re.compile(
    r"\bDECLARE\s+\w+\s+CURSOR\b"    # T-SQL cursor declaration
    r"|\bFETCH\s+NEXT\b"               # T-SQL cursor fetch
    r"|\bWHILE\s+@@FETCH_STATUS\b"     # T-SQL cursor loop
    r"|\.iterrows\s*\(\s*\)"           # pandas row-by-row iteration
    r"|\.itertuples\s*\(\s*\)",        # pandas tuple-by-tuple iteration
    re.IGNORECASE,
)

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
_TABLES_PATH = re.compile(r"(?:^|/)tables(?:/|$)", re.IGNORECASE)
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

#: Widths above this are treated as oversized — they defeat statistics and inflate row size.
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
            f"{len(warehouses)} Warehouse item(s) found but no pipelines defined — "
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
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_cursor(ctx: CheckContext) -> Verdict:
    """Set-based SQL and DataFrame operations rather than T-SQL cursors or pandas row iteration."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    hits = _CURSOR.findall(code)
    return binary(
        not hits,
        f"{len(hits)} cursor/row-iteration pattern(s) detected "
        "(T-SQL CURSOR / .iterrows() / .itertuples())" if hits
        else "No T-SQL cursors or row-by-row iteration patterns — set-based transformations",
    )


@check(
    id="WS-STAGING", ref="3.6.3",
    title="Staging tables/schema used for Warehouse loads before merge into final tables",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
        "(stg_* / staging_* / stage_*) — a staging schema buffers loads before the final merge",
    )

@check(
    id="WS-WH-TRYCATCH", ref="3.6.5",
    title="Warehouse/lakehouse load SQL uses TRY...CATCH with transaction handling",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_try_catch_transactions(ctx: CheckContext) -> Verdict:
    """Load SQL wraps transactional changes in TRY...CATCH.

    **Two sources of SQL.** Stored procedures and functions declared in the
    Warehouse, now read from ``INFORMATION_SCHEMA.ROUTINES`` - which is where a
    ``SqlServerStoredProcedure`` activity's logic actually lives - plus the
    inline T-SQL a pipeline runs through a Script activity. Before the routine
    bodies were fetched, a pipeline that called a stored procedure was recorded
    as an *opaque* load and the whole check reported N/A: the logic existed, it
    simply had not been read.

    **What it cannot determine.** Whether a ``Copy`` activity's implicit load is
    transactional - it runs no SQL of its own - so those are still counted as
    opaque and named in the evidence rather than judged.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    storage_items = [
        item for item in ctx.workspace.items
        if item.type in {"Warehouse", "Lakehouse"}
    ]
    if not storage_items:
        return not_applicable("No Warehouse/Lakehouse items found in this workspace")

    inspected: list[tuple[str, bool]] = []
    opaque_loads: list[str] = []

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

                if activity_type in ("SqlServerStoredProcedure", "StoredProcedure", "Copy"):
                    opaque_loads.append(marker)

    if not inspected:
        if opaque_loads:
            return not_applicable(
                f"{len(opaque_loads)} load activity/activities run through a stored "
                "procedure or Copy whose SQL is not in this snapshot, and the "
                "Warehouse declared no routine body to read, so TRY...CATCH "
                "handling cannot be verified. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No inspectable scripted SQL load activity was found")

    compliant = [name for name, ok in inspected if ok]
    evidence = (
        f"{len(compliant)} of {len(inspected)} inspectable SQL load(s) use "
        "TRY...CATCH and BEGIN/COMMIT/ROLLBACK transaction handling"
        + (f"; compliant: {', '.join(compliant[:5])}" if compliant else "")
    )
    if opaque_loads:
        evidence += (f". {len(opaque_loads)} Copy/stored-procedure activity/activities "
                     f"run no readable SQL and are not judged")
    return covered(len(compliant), len(inspected), evidence)

@check(
    id="WS-WH-INCREMENTAL", ref="3.6.6",
    title="Warehouse loads avoid unnecessary full reloads",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_stats_updated_after_loads(ctx: CheckContext) -> Verdict:
    """Significant inspectable SQL loads are paired with statistics maintenance."""
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

    pipeline_loads: dict[str, list[str]] = {}
    pipeline_stats: dict[str, list[str]] = {}
    opaque_loads: list[str] = []

    for pipeline_name, pipeline_def in ctx.workspace.pipelines.items():
        for activity in pipeline_activities(pipeline_def):
            activity_type = str(activity.get("type", "") or "")
            activity_name = activity.get("name", activity_type) or activity_type
            marker = f"{pipeline_name}/{activity_name}"

            if activity_type == "Script":
                text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
                if _WAREHOUSE_SQL_LOAD.search(text):
                    pipeline_loads.setdefault(pipeline_name, []).append(marker)
                if _STATS_UPDATE_SQL.search(text):
                    pipeline_stats.setdefault(pipeline_name, []).append(marker)
                continue

            if activity_type in ("Copy", "SqlServerStoredProcedure", "StoredProcedure"):
                opaque_loads.append(marker)

    inspectable_pipelines = sorted(pipeline_loads)

    # Statistics maintenance also lives in stored procedures, and the Warehouse's
    # own ``sys.stats`` says whether user-created statistics exist at all - a
    # second, structural answer to the same question.
    routine_loads = [
        r for r in ctx.workspace.sql_routines
        if _WAREHOUSE_SQL_LOAD.search(str(r.get("definition") or ""))
    ]
    routine_stats = [
        r for r in routine_loads
        if _STATS_UPDATE_SQL.search(str(r.get("definition") or ""))
    ]

    if not inspectable_pipelines and not routine_loads:
        if opaque_loads:
            return not_applicable(
                f"{len(opaque_loads)} load activity/activities run through a Copy or "
                "stored procedure whose SQL is not in this snapshot, and the "
                "Warehouse declared no routine body to read, so post-load "
                "statistics maintenance cannot be verified. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No significant readable SQL warehouse/lakehouse load was found")

    total = len(inspectable_pipelines) + len(routine_loads)
    compliant_pipelines = [name for name in inspectable_pipelines if pipeline_stats.get(name)]
    compliant = len(compliant_pipelines) + len(routine_stats)
    evidence = (
        f"{compliant} of {total} readable load(s) also run statistics maintenance "
        "(UPDATE STATISTICS / sp_updatestats / ANALYZE TABLE)"
    )
    if routine_loads:
        evidence += (f"; {len(routine_stats)} of {len(routine_loads)} Warehouse stored "
                     f"procedure(s) that load data maintain statistics")
    if opaque_loads:
        evidence += f"; {len(opaque_loads)} Copy activity/activities run no readable SQL"
    if compliant_pipelines:
        evidence += f"; compliant pipelines: {', '.join(compliant_pipelines[:5])}"

    return covered(compliant, total, evidence)


@check(
    id="TB-NAMING", ref="4.2.1", title="Tables use meaningful, consistent naming conventions (agreed standard)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS], required=True,
)
def shortcut_governance(ctx: CheckContext) -> Verdict:
    """Shortcut paths are structurally governed and avoid loop-prone patterns."""
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
        seen_paths: set[str] = set()
        for row in (shortcuts or []):
            total += 1
            name = str((row or {}).get("name") or "")
            path = str((row or {}).get("path") or "")
            target_type = str((row or {}).get("target_type") or "")
            normalized = path.replace("\\", "/").strip().strip("/")
            normalized_low = normalized.lower()

            issues: list[str] = []
            if not target_type.strip():
                issues.append("missing target type")
            if ".." in normalized_low:
                issues.append("path traversal segment '..'")
            if _TABLES_PATH.search(normalized_low):
                issues.append("shortcut rooted under Tables path")
            if _SHORTCUTS_PATH.search(normalized_low):
                issues.append("nested Shortcut path (loop-prone)")
            if normalized_low in seen_paths and normalized_low:
                issues.append("duplicate shortcut path")
            seen_paths.add(normalized_low)

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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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

def _table_stores(ctx: CheckContext) -> str:
    """Name the lakehouse/warehouse(s) whose tables a workspace check inspected.

    Workspace-scoped table checks aggregate over every store's tables, so the
    engine leaves their object blank. Naming the store(s) here points the finding
    at what was judged — the analogue of a pipeline check naming its pipeline.
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ITEMS], required=True,
)
def table_star_schema(ctx: CheckContext) -> Verdict:
    """The model separates fact tables from dimension tables (not flat wide tables).

    **What it can determine.** Whether the workspace holds both fact-named
    (``fact*``/``fct*``) and dimension-named (``dim*``) tables — the readable
    signature of a dimensional model. The evidence also reports how wide the fact
    tables are, because the point contrasts a star schema with "flat wide
    tables": a fact carrying dozens of columns is the shape that warrants a look.

    **What it cannot determine, and deliberately does not score.** Whether the
    model is *correctly* star-shaped. Column width is reported but **not scored**
    here, for two reasons: how wide is "too wide" is a modelling judgement rather
    than a fact, and the underlying defect — descriptive attributes sitting on a
    fact instead of a dimension — is already scored by ``TB-FACT-PURITY``
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
    facts = {n: t for n, t in tables.items() if is_fact(n)}
    dims = {n: t for n, t in tables.items() if is_dimension(n)}

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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
        f"{compliant} of {assessed} assessable columns have appropriate sizing — "
        f"{oversized_text} oversized text column(s), {imprecise_numeric} "
        "decimal/numeric column(s) with invalid precision/scale"
        + (f"; {lakehouse_defaults} Lakehouse default varchar({_LAKEHOUSE_TEXT_WIDTH}) "
           "column(s) excluded" if lakehouse_defaults else ""),
    )


@check(
    id="TB-SURROGATE-GEN", ref="4.4.4",
    title="Surrogate keys are implemented for dimensions with a generated-key pattern",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def table_surrogate_generated(ctx: CheckContext) -> Verdict:
    """Dimension schemas include surrogate keys with generation-oriented naming hints.

    This is a schema-level proxy for generated-key patterns. It cannot inspect ETL
    code paths (hash/window/key-table logic), so it looks for surrogate-key columns
    plus a generation hint in the declared column names.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)

    compliant = 0
    for table in dims.values():
        names = col_names(table)
        has_surrogate = any(
            n.endswith(("_sk", "_key")) or n in {"surrogate_key", "surrogate_id"}
            for n in names
        )
        has_generated_hint = any(_GENERATED_KEY_HINT.search(n) for n in names)
        has_business_hint = any(_BUSINESS_KEY_HINT.search(n) for n in names)
        if has_surrogate and (has_generated_hint or has_business_hint):
            compliant += 1

    return covered(
        compliant, len(dims),
        f"{compliant} of {len(dims)} dimension table(s) include surrogate keys with "
        "generation-oriented naming evidence",
    )


@check(
    id="TB-REL-DECLARED", ref="4.4.5",
    title="Primary/foreign key relationships are declared where supported",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.TABLE_SCHEMAS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def table_relationships_declared(ctx: CheckContext) -> Verdict:
    """Fact tables declare their key relationships somewhere machine-readable.

    **Two sources, strongest first.** A Warehouse can declare ``NOT ENFORCED``
    PK/FK constraints, and the crawl now reads them from ``sys.foreign_keys`` -
    that is the point stated literally, so a table carrying one satisfies it
    outright. Where no constraint is declared (a Lakehouse table, or a Warehouse
    that never declared any), semantic-model relationships are the fallback:
    they are the same structure expressed in the model rather than the database.

    **What it cannot determine.** Whether a declared relationship is *correct* -
    Fabric does not enforce these constraints, so a declaration is a statement of
    intent, not a guarantee that the data honours it. ``NB-FK-INTEGRITY`` (5.3.2)
    is what looks for code that actually validates the values.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)

    facts = [name for name in ctx.workspace.tables if is_fact(name)]
    if not facts:
        return not_applicable("No fact-like tables found to assess for declared FK relationships")

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
        return not_applicable(
            "Neither declared database constraints nor semantic-model "
            "relationships could be read, so key relationships cannot be assessed"
        )

    evidence = (f"{len(declared)} of {len(facts)} fact-like table(s) declare their key "
                f"relationships")
    if with_constraint:
        evidence += (f" ({len(with_constraint)} through a Warehouse FK constraint, the "
                     f"rest through semantic-model relationships)")
    else:
        evidence += (" through semantic-model relationships; no Warehouse FK constraint "
                     "is declared, which Fabric supports as NOT ENFORCED metadata")
    return covered(len(declared), len(facts), evidence)


@check(
    id="WS-STATS-STRATEGY", ref="4.4.6",
    title="Statistics maintenance strategy is defined and automated",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS], required=True,
)
def stats_strategy_defined(ctx: CheckContext) -> Verdict:
    """A statistics-maintenance strategy is defined and automated.

    Three sources of evidence, strongest first: statistics objects the Warehouse
    actually holds (``sys.stats``, counted per table during the crawl), stored
    procedures that maintain them, and pipeline Script activities that run
    ``UPDATE STATISTICS``. The first is structural - it says statistics exist,
    not merely that some code mentions them.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    storage_items = [item for item in ctx.workspace.items if item.type in {"Warehouse", "Lakehouse"}]
    if not storage_items:
        return not_applicable("No Warehouse/Lakehouse items found in this workspace")

    inspectable = 0
    automated = 0
    opaque = 0

    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        for _, pipeline_def in (ctx.workspace.pipelines or {}).items():
            for activity in pipeline_activities(pipeline_def):
                activity_type = str(activity.get("type", "") or "")
                if activity_type == "Script":
                    text = _to_text((activity.get("typeProperties") or {}).get("scripts") or [])
                    if _STATS_UPDATE_SQL.search(text):
                        inspectable += 1
                        automated += 1
                    elif _WAREHOUSE_SQL_LOAD.search(text):
                        inspectable += 1
                    continue

                if activity_type in ("Copy", "SqlServerStoredProcedure", "StoredProcedure"):
                    opaque += 1

    # Stored procedures that load data: do they maintain statistics too?
    for routine in ctx.workspace.sql_routines:
        text = str(routine.get("definition") or "")
        if _STATS_UPDATE_SQL.search(text):
            inspectable += 1
            automated += 1
        elif _WAREHOUSE_SQL_LOAD.search(text):
            inspectable += 1

    # Statistics the Warehouse actually holds - the structural signal.
    tables_with_stats = sum(
        1 for table in (ctx.workspace.tables or {}).values()
        if table.get("statistics")
    )

    if inspectable == 0:
        if tables_with_stats:
            return note(
                f"No load automation could be inspected for a statistics strategy, but "
                f"{tables_with_stats} table(s) carry statistics objects, so statistics "
                f"exist even though nothing readable maintains them on a schedule"
            )
        if opaque:
            return not_applicable(
                f"{opaque} load activity/activities run through a Copy or stored "
                "procedure whose SQL is not in this snapshot, and no Warehouse "
                "routine was readable, so a statistics strategy cannot be verified. "
                + _SQL_PERMISSION_HINT
            )
        return not_applicable("No readable storage-load automation was found to evaluate statistics strategy")

    evidence = (f"{automated} of {inspectable} readable load automation step(s) include "
                f"statistics maintenance")
    if tables_with_stats:
        evidence += f"; {tables_with_stats} table(s) carry statistics objects"
    return covered(automated, inspectable, evidence,
                   obj="readable load automation steps")

@check(
    id="TB-DATEDIM", ref="4.5.7", title="Date/Time dimension exists with all required attributes (fiscal periods, quarter, holidays)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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

    An ``…AlternateKey`` never counts: AdventureWorks uses that for the natural
    key, which is the distinction this point is about.

    **What it cannot determine.** Whether the column is genuinely system
    generated - a load-time property, not visible in a schema.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_column_naming(ctx: CheckContext) -> Verdict:
    """Column names follow *one* convention across the workspace — whichever one.

    **Consistency is what is scored, not a particular house style.** Each name is
    classified as ``snake_case``, ``UPPER_CASE``, ``PascalCase``, ``camelCase`` or
    ``mixed``; the dominant convention is found, and the score is the share of
    columns that follow it. Requiring ``snake_case`` specifically marked down any
    estate that had standardised on something else — Microsoft's own AdventureWorks
    sample is PascalCase throughout — which measured style preference rather than
    quality. A ``mixed`` name (a space, ``Customer_ID`` blending Pascal with
    underscores) can never be dominant: it follows no convention at all.

    **Deliberately different from ``TB-WH-NAME-CONSISTENCY`` (ref 4.4.2)**, which
    asks the same question but only of tables known to live in a **Warehouse**,
    and judges table names as well as column names. This one covers **every**
    table in the workspace, Lakehouse included, and only its columns — so a
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
            f"a single naming convention — every name mixes styles (a space, or "
            f"capitals joined by underscores)",
        )
    mixed = sum(1 for style in styles if style == "mixed")
    return covered(
        following, len(styles),
        f"{following} of {len(styles)} column name(s) across {len(tables)} table(s) "
        f"follow the dominant convention ({convention})"
        + (f"; {mixed} name(s) follow no convention at all" if mixed else "")
        + ". Consistency is what is scored — any one convention counts, provided "
          "the estate sticks to it.",
    )


@check(
    id="TB-DATATYPES", ref="4.2.4", title="Data types are appropriate (no stringly-typed dates, no oversized varchars)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
        f"{compliant} of {assessed} assessable columns are appropriately typed — "
        f"{stringly_dates} date column(s) typed as text, "
        f"{oversized} text column(s) wider than {_MAX_TEXT_WIDTH}"
        + (f"; {lakehouse_defaults} Lakehouse column(s) excluded — a Lakehouse SQL "
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

#: A local or UNC filesystem path — ``C:\Users\...`` or ``\\server\share``.
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

#: A personal cloud drive rather than a governed team site.
_PERSONAL_CLOUD = re.compile(r"-my\.sharepoint\.com|/personal/|onedrive", re.IGNORECASE)

#: An endpoint that is a single data file, not a managed store.
_FILE_ENDPOINT = re.compile(r"\.(?:csv|tsv|xlsx?|xlsb|json|txt|parquet|xml)(?:\?|$)", re.IGNORECASE)


def _shadow_reason(conn: dict) -> str | None:
    """Why this connection is ungoverned shadow storage, or None when it is governed.

    Deliberately conservative: an enterprise object store reached through a
    shareable cloud connection (ADLS, S3, GCS, a SQL source) is *not* shadow
    storage — it is a governed external source, which the point allows. What it
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
    title="OneLake used as the single data lake — no ungoverned shadow storage",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS, Resource.CONNECTIONS], required=False,
)
def shortcut_scope(ctx: CheckContext) -> Verdict:
    """Data reaches the workspace through OneLake or a governed source, not a side door.

    Two populations answer this. **Shortcuts** show where OneLake itself points;
    a shortcut to Dataverse or ADLS is a legitimate governed pattern, so it is
    reported for review rather than failed. **Connections** are where shadow
    storage actually shows up — a spreadsheet on someone's laptop reached through
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
            f"judged — {shortcut_note}"
        )
    connections = ctx.workspace.connections or []
    if not connections:
        return not_applicable(f"No Fabric source connections were returned — {shortcut_note}")

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
        f"{' — ' + breakdown if breakdown else ''}. {shortcut_note}. "
        f"External shortcuts and external sources are legitimate when governed — "
        f"confirm each is intended.",
    )



@check(
    id="TB-SCD2", ref="4.5.9", title="SCD Type 2 includes valid_from, valid_to, and is_current flag correctly maintained (where used)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_scd2(ctx: CheckContext) -> Verdict:
    """Detect semantic SCD2 trios, then enforce the canonical column names."""
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    readable = {name: table for name, table in ctx.workspace.tables.items() if columns(table)}
    if not readable:
        return not_applicable("Table metadata is present, but no table columns were readable")

    aliases = {
        "start": ("valid_from", "effective_date", "start_date"),
        "end": ("valid_to", "end_date", "expiration_date"),
        "current": ("is_current", "active_flag", "current_flag", "is_active"),
    }

    candidates: dict[str, tuple[str, str, str]] = {}
    for name, table in readable.items():
        names = set(col_names(table))
        matched = tuple(
            next((column for column in choices if column in names), "")
            for choices in aliases.values()
        )
        if all(matched):
            candidates[name] = matched

    if not candidates:
        return not_applicable(
            f"Column metadata is present, but no SCD2 pattern was found across "
            f"{len(readable)} table(s) (start date + end date + current flag)"
        )

    canonical = ("valid_from", "valid_to", "is_current")
    compliant = [name for name, matched in candidates.items() if matched == canonical]
    deviations = [
        f"{name}: {'/'.join(matched)}"
        for name, matched in sorted(candidates.items())
        if matched != canonical
    ]
    evidence = (
        f"{len(compliant)} of {len(candidates)} SCD2 candidate table(s) use canonical "
        "valid_from/valid_to/is_current"
    )
    if deviations:
        evidence += f"; non-standard SCD2 column names: {'; '.join(deviations[:10])}"
        if len(deviations) > 10:
            evidence += f"; plus {len(deviations) - 10} more"
    return covered(len(compliant), len(candidates), evidence)


# =============================================================================
# Store-aware table checks and dimensional purity checks.
# (4.5.3, 4.5.4, 4.5.8).
#
# The store-aware ones read ``store``/``store_kind``, which the crawler fills in
# from the SQL analytics endpoint a table's columns were read through. An empty
# store means the endpoint could not be read — *unknown*, never a mismatch — so
# every one of them excludes those tables and reports N/A when nothing is left.
# =============================================================================

#: N/A reason when no table could be attributed to an owning store.
_NO_STORE = (
    "No table could be attributed to an owning Lakehouse/Warehouse — the SQL "
    "analytics endpoints were not readable, so store membership is unknown"
)


@check(
    id="TB-WH-MODELED", ref="1.2.6",
    title="Gold Warehouse is consumption-ready and modeled (star schema) for the semantic layer",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def warehouse_is_modeled(ctx: CheckContext) -> Verdict:
    """Each Warehouse holds both fact and dimension tables, so it is modeled, not a dump.

    **What it can determine.** Which tables are *known* to live in a Warehouse
    (from the SQL endpoint they were read through), and whether each such
    Warehouse carries both fact-named and dimension-named tables — the readable
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
    for store, store_tables in sorted(by_store.items()):
        facts = [n for n in store_tables if is_fact(n)]
        dims = [n for n in store_tables if is_dimension(n)]
        if facts and dims:
            modeled.append(store)
            detail.append(f"{store}: {len(facts)} fact / {len(dims)} dimension table(s)")
        else:
            detail.append(
                f"{store}: {len(facts)} fact / {len(dims)} dimension table(s) — not modeled"
            )
    return covered(
        len(modeled), len(by_store),
        f"{len(modeled)} of {len(by_store)} Warehouse(s) hold both fact and dimension "
        f"tables — {'; '.join(detail)}. Whether a semantic model in another workspace "
        f"consumes them is not readable here.",
    )


@check(
    id="TB-AUDIT-SEPARATED", ref="1.2.8",
    title="Audit Tables role clearly defined and separated from business data",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def audit_tables_separated(ctx: CheckContext) -> Verdict:
    """Audit/DQ tables live in a store of their own rather than beside business tables.

    **What it can determine.** Which tables are audit-shaped — either their name
    says so (audit / log / quality / exception / reject) or their columns are
    dominated by lineage columns — and which store holds each, so it can say
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
        # A store is an audit store when audit tables dominate it — a couple of
        # reference tables alongside the logs is "few", not a mixed store.
        dedicated = business <= len(audit_names) // 4
        if dedicated:
            separated += len(audit_names)
        detail.append(
            f"{store}: {len(audit_names)} audit ({_sample(audit_names)}) / "
            f"{business} business"
            + (f" ({_sample(business_names)})" if business else "")
            + f" — {'dedicated' if dedicated else 'mixed'}"
        )
    return covered(
        separated, total_audit,
        f"{separated} of {total_audit} audit table(s) sit in a store dedicated to audit "
        f"data — {'; '.join(detail)}",
    )


def _sample(names: list[str], limit: int = 3) -> str:
    """Up to ``limit`` table names, with the remainder summarised as a count."""
    shown = sorted(names)[:limit]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f", +{rest} more" if rest > 0 else "")


@check(
    id="TB-CONFORMED-DIM", ref="4.4.9",
    title="Cross-domain conformed dimensions shared, not duplicated per domain",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    for store, store_tables in by_store.items():
        for name in store_tables:
            if not is_dimension(name):
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def fact_tables_have_no_descriptive_attributes(ctx: CheckContext) -> Verdict:
    """Fact tables carry keys, measures and lineage columns — descriptions belong in dimensions.

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
    facts = {n: t for n, t in tables.items()
             if is_fact(n) and any(c.get("type") for c in columns(t))}
    if not facts:
        return not_applicable(
            "No fact table with readable column types — nothing to judge for "
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def dimensions_are_denormalized(ctx: CheckContext) -> Verdict:
    """A dimension is flat: it does not key out to another dimension table.

    **What it can determine.** A key column on one dimension whose name resolves
    to *another* dimension table present in the workspace (``dim_product`` with
    ``category_sk`` beside a ``dim_category``). That is the snowflake shape the
    point warns about.

    **What it cannot.** Whether a snowflake was justified — a genuinely large,
    slowly-changing outrigger is a legitimate exception — so the evidence names
    the links for review rather than asserting they are wrong. A key pointing at
    a dimension that is not in this workspace is not counted, because it cannot
    be confirmed.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in tables.items() if is_dimension(n) and columns(t)}
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
        + (f"; snowflake links: {'; '.join(sorted(snowflaked)[:3])} — confirm each is a "
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
#: ``is_active``/``is_deleted`` are excluded for the same reason — they are
#: business state flags. A row being active says nothing about whether its
#: history is versioned.
_SCD_MARKERS: frozenset[str] = frozenset({
    "valid_from", "valid_to", "validfrom", "validto", "valid_until", "validuntil",
    "effective_from", "effective_to", "effectivefrom", "effectiveto",
    "effective_start_date", "effective_end_date",
    "row_effective_date", "row_expiry_date", "record_start_date", "record_end_date",
    "is_current", "iscurrent", "current_flag", "currentflag", "current_ind",
    "row_hash", "rowhash", "hash_diff", "hashdiff", "row_version", "rowversion",
    "record_version", "scd_type", "scdtype",
})


@check(
    id="TB-SCD-STRATEGY", ref="4.5.8",
    title="SCD strategy defined and implemented per dimension (Type 1 / Type 2 / Hybrid)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def scd_strategy_per_dimension(ctx: CheckContext) -> Verdict:
    """Each dimension shows a declared change-handling strategy in its schema.

    **What it can determine.** Whether a dimension carries Type-2/hybrid markers
    (validity dates, a current flag, a row hash, a version) — the only schema
    evidence that a strategy was *chosen* per dimension.

    **What it cannot.** Whether an overwrite-in-place (Type 1) dimension was a
    deliberate decision or an unexamined default: both look identical in the
    schema. Type 1 is a legitimate strategy, so a dimension with no markers is
    never a hard FAIL. The bands reflect exactly that: **3** when every dimension
    declares its handling in the schema, **2** when some do (the estate knows the
    pattern and applied it where needed), and **1** — partial, not zero — when
    none do, because the tables may all be correctly Type 1 with the decision
    recorded somewhere this check cannot read.

    Broader than ``TB-SCD2`` (ref 4.5.9), which asks only whether dimensions
    *already identified as Type 2* carry the full valid_from/valid_to/is_current
    triple. This one asks whether any strategy is evident at all.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)

    declared = [n for n, t in dims.items()
                if any(c.replace(" ", "") in _SCD_MARKERS for c in col_names(t))]
    if len(declared) == len(dims):
        return graded(
            3,
            f"All {len(dims)} dimension(s) declare their change handling in the schema "
            f"(validity dates, current flag, row hash or version)",
        )
    if declared:
        return graded(
            2,
            f"{len(declared)} of {len(dims)} dimension(s) declare an SCD strategy "
            f"({', '.join(sorted(declared)[:4])}); the rest overwrite in place (Type 1 "
            f"by default), which may be correct but is not evident in the schema",
        )
    return graded(
        1,
        f"None of the {len(dims)} dimension(s) carry SCD markers — all are Type 1 by "
        f"default. That is a legitimate strategy, but no schema evidence shows it was "
        f"chosen per dimension; confirm and record the decision",
    )


# =============================================================================
# 4.5.2 — fact grain, and 4.5.11 — degenerate / junk dimension candidates
#
# Both points are only *partly* readable, and the two halves are named in each
# docstring rather than blurred:
#
# * 4.5.2 asks for a grain that is "clearly defined **and documented**". No
#   Fabric REST call and no SQL analytics endpoint query returns a table
#   description, an extended property or a column comment, so the documented
#   half is out of reach entirely. Only the "clearly defined" half is scored.
# * 4.5.11 asks for degenerate/junk dimensions "where appropriate". Whether a
#   modelling choice is appropriate is a judgement, and the deciding fact
#   (cardinality) needs row data, which this tool must not fetch. That point is
#   therefore reported as an unscored note listing candidates for review.
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def fact_grain_is_identifiable(ctx: CheckContext) -> Verdict:
    """Each fact table's schema shows what one row *is* — the keys that define its grain.

    **What it can determine.** For every fact table with readable columns, the
    distinct grain components its schema declares: foreign keys resolving to
    something other than the fact itself (``customer_sk``, ``product_id``,
    ``date_sk``) plus a time component from a non-key timestamp column. Two or
    more such components mean the schema states a grain — "one row per customer
    per day" — that a reviewer can read off the table. Fewer means the grain is
    not evident from the schema at all.

    **What it cannot — half the checklist point.** *Documented* is not readable.
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
    a duplicate-grain assertion — code, not schema, and uniqueness, not
    definition.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    facts = {n: t for n, t in tables.items() if is_fact(n) and columns(t)}
    if not facts:
        return not_applicable(
            "No fact table with readable column metadata — there is no grain to read"
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
        f"{len(defined)} of {len(facts)} fact table(s) declare a readable grain — at least "
        f"{_MIN_GRAIN_COMPONENTS} distinct grain key(s) in the schema"
        + (f"; grain not evident on {'; '.join(undefined[:3])}" if undefined else "")
        + ". Whether the grain is *documented* is not readable from any Fabric or SQL "
          "endpoint API, so only the 'clearly defined' half of the point is scored here.",
    )


#: Column names that read as a low-cardinality flag or indicator — the kind of
#: attribute a junk dimension is built to collapse. Matched on the whole
#: (lower-cased) name so ``is_active``, ``paid_flag`` and ``order_status`` count
#: while ``flag_description`` does not.
_FLAG_COLUMN = re.compile(
    r"^(?:is|has|can|was|are)_\w+$|"
    r"^\w+_(?:flag|flg|ind|indicator|status|type|code|category|reason)$|"
    r"^(?:flag|status|indicator)$",
    re.IGNORECASE,
)

#: Below this many flag columns on one fact, collapsing them into a junk
#: dimension buys nothing — the pattern exists to remove *several* low-value
#: columns, not one.
_MIN_JUNK_CANDIDATES = 3


@check(
    id="TB-DEGENERATE-JUNK-DIM", ref="4.5.11",
    title="Degenerate and junk dimensions used where appropriate",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def degenerate_and_junk_dimension_candidates(ctx: CheckContext) -> Verdict:
    """Report fact columns shaped like a degenerate or junk dimension — unscored, for review.

    **Deliberately unscored (a `note`).** The point says "where appropriate", and
    appropriateness is a modelling judgement no API can make: a degenerate
    dimension is *correct* when an order number genuinely has no attributes of
    its own, and *wrong* when the attributes exist and were simply never
    modelled. The deciding fact for a junk dimension — column cardinality — needs
    row data, which this tool must not fetch. Scoring either way would be a
    guess, so this reports the candidates and names them instead.

    **What it can determine.** *Degenerate candidates*: key-shaped columns on a
    fact table whose referent matches no dimension table in this workspace and is
    not the fact's own identity (``order_number`` on ``fact_sales`` with no
    ``dim_order``) — the exact shape of a degenerate dimension. *Junk
    candidates*: three or more flag/indicator/status-shaped columns on one fact,
    the cluster a junk dimension exists to collapse.

    **What it cannot.** Column cardinality (no row data is read), whether the
    referenced dimension lives in another workspace, whether a degenerate column
    is intentional, or whether an existing junk dimension is already in use
    somewhere it cannot see. Every name below is a *candidate for review*, never
    a finding.

    **Sibling.** ``TB-FACT-PURITY`` (ref 4.5.3) scores *text* attributes on a
    fact that belong on a dimension. This looks at key- and flag-shaped columns,
    which that check deliberately ignores, and judges neither.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    facts = {n: t for n, t in tables.items() if is_fact(n) and columns(t)}
    if not facts:
        return not_applicable(
            "No fact table with readable column metadata — no degenerate or junk "
            "dimension candidate can be identified"
        )

    dimension_purposes = {
        purpose_tokens(n) for n in tables if is_dimension(n) and purpose_tokens(n)
    }

    degenerate: list[str] = []
    junk: list[str] = []
    for name, table in sorted(facts.items()):
        own = purpose_tokens(name)
        orphan_keys = sorted({
            column.get("name") or ""
            for column in columns(table)
            if (referent := key_referent(column.get("name") or ""))
            and referent != own
            and referent not in dimension_purposes
        })
        if orphan_keys:
            degenerate.append(f"{name}: {', '.join(orphan_keys[:4])}")
        flags = sorted({
            (column.get("name") or "")
            for column in columns(table)
            if _FLAG_COLUMN.match((column.get("name") or "").strip())
        })
        if len(flags) >= _MIN_JUNK_CANDIDATES:
            junk.append(f"{name}: {len(flags)} flag column(s) ({', '.join(flags[:4])})")

    if not degenerate and not junk:
        return note(
            f"No degenerate or junk dimension candidate found across {len(facts)} fact "
            f"table(s): every key column resolves to a dimension in this workspace and no "
            f"fact carries {_MIN_JUNK_CANDIDATES}+ flag/status columns. Whether the "
            f"existing model uses the patterns *appropriately* is a modelling judgement "
            f"this check does not make."
        )
    parts = []
    if degenerate:
        parts.append(
            f"{len(degenerate)} fact table(s) carry a key with no matching dimension "
            f"(degenerate-dimension candidates) — {'; '.join(degenerate[:3])}"
        )
    if junk:
        parts.append(
            f"{len(junk)} fact table(s) carry {_MIN_JUNK_CANDIDATES}+ flag/status columns "
            f"(junk-dimension candidates) — {'; '.join(junk[:3])}"
        )
    return note(
        "; ".join(parts)
        + ". Reported for review, not scored: cardinality is not readable without querying "
          "rows, and whether each pattern is appropriate here is a modelling judgement."
    )


# =============================================================================
# 4.4.1 — Warehouse schema organization
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    same gating the other store-aware checks use — a Lakehouse has no comparable
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
            f"schema qualifier — the SQL-endpoint reader records the table name "
            f"without its INFORMATION_SCHEMA.TABLE_SCHEMA — so schema organisation "
            f"cannot be assessed from this snapshot"
        )

    scores = {store: _schema_score(counts) for store, counts in by_store.items()}
    business_tables = len(warehouse_tables) - excluded_system
    detail = "; ".join(
        f"'{store}': " + ", ".join(
            f"{schema or '(unqualified)'} ({count} table(s))"
            for schema, count in sorted(by_store[store].items())
        )
        for store in sorted(by_store)
    )
    return graded(
        sum(scores.values()) // len(scores),
        f"{len(by_store)} Warehouse(s) judged on schema layout — {detail}. "
        f"{qualified} of {business_tables} business Warehouse table(s) carry a schema "
        f"qualifier; a Warehouse holding everything in dbo, or with no staging "
        f"schema separate from its presentation schemas, scores below full. "
        f"Excluded {excluded_system} Fabric system-schema table(s).",
    )


# =============================================================================
# 4.4.2 — Warehouse naming *consistency* (one convention, whichever it is)
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

    ``"mixed"`` covers everything that follows no one convention — a space in
    the name, ``Customer_ID`` mixing Pascal with underscores, ``LDP Course
    Name/Domain`` — and is what makes an estate's naming *inconsistent*
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def warehouse_naming_is_internally_consistent(ctx: CheckContext) -> Verdict:
    """Every Warehouse table and column follows *one* convention — whichever one.

    Each name is classified as ``snake_case``, ``UPPER_CASE``, ``PascalCase``,
    ``camelCase``, or ``mixed`` (no single convention — a space, or Pascal words
    joined by underscores). The dominant convention is then found separately for
    table names and for column names, and the score is the share of names that
    follow their own group's dominant convention. A Warehouse written entirely
    in PascalCase scores full marks; one that is half snake and half Pascal does
    not.

    **Deliberately different from ``TB-COL-NAMING`` (ref 4.2.3)**, which scores
    the share of columns that are specifically ``snake_case``, across *every*
    table in the workspace. Two differences, both real: this one is scoped to
    tables known to live in a **Warehouse** (``in_warehouse``), and it mandates
    **no particular convention** — it measures internal consistency, so an
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
    detail += (". Consistency is what is scored — any one convention counts, "
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

#: A stored procedure or a user-defined function — abstraction over the physical
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
            f"{', '.join(proc_sources[:_MAX_NAMED_SOURCES])} — logic is abstracted, but "
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
#: at all (see the check docstring) — this list only promotes a *Lakehouse* or a
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
#: audit happened to be run. 48 hours still catches the real defect — a serving
#: item that has not refreshed for days — without failing an estate for clock
#: jitter. Tune per project with ``gold_freshness_sla_hours``.
_DEFAULT_SLA_HOURS = 48

#: How many stale item names to name in the evidence before summarising.
_MAX_NAMED_STALE = 5

def _serving_items(ctx: CheckContext) -> list[Item]:
    """The workspace's Gold/serving items.

    Every **Warehouse** qualifies by type: a Fabric Warehouse exists to be
    queried by reports, so it *is* the serving surface regardless of what it is
    called. A **Lakehouse** or **SemanticModel** qualifies only when its name
    carries a serving token — a Bronze/Silver lakehouse is not Gold, and nothing
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
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=True,
)
def gold_items_refreshed_within_sla(ctx: CheckContext) -> Verdict:
    """The Gold/serving items refreshed within the freshness SLA window.

    **What it measures — an actual elapsed time, not a coded control.** Each
    serving item's last run/refresh (``Item.last_run_utc``, filled from the
    job-scheduler history and, for semantic models, the Power BI refresh
    history) is compared against a window read from
    ``gold_freshness_sla_hours`` (default 48 hours — long enough that a healthy
    daily batch is not failed for the hour the audit happened to run).

    **What counts as Gold.** Every Warehouse, because a Fabric Warehouse exists
    to be queried by reports; plus any Lakehouse or SemanticModel whose name
    carries a serving token (gold / serving / curated / mart / presentation /
    published / consumption / semantic), matched with the shared
    :func:`name_words` splitter.

    **What it cannot determine.** This is the item's **last run/refresh**, which
    is the closest readable proxy for "the Gold *table* was updated" — Delta
    table commit times are not fetched, so a run that succeeded while writing
    nothing still reads as fresh, and a table updated by a pipeline in another
    workspace reads as stale here. It also cannot read the *agreed* SLA: the
    window is a project setting, not something the tenant publishes.

    **Missing timestamps are excluded, never counted stale.** An item with no
    readable last-run stamp leaves the denominator entirely — "we could not read
    when it last ran" is not "it is out of SLA". When no serving item exists, or
    none of them has a readable stamp, the check is N/A.

    **Sibling — ``NB-TIMELINESS-CONTROL`` (5.2.3), and the difference matters.**
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

    dated = [(i, parse_stamp(i.last_run_utc)) for i in serving]
    readable = [(i, stamp) for i, stamp in dated if stamp is not None]
    if not readable:
        return not_applicable(
            f"None of the {len(serving)} Gold/serving item(s) carries a readable last "
            "run/refresh timestamp, so how recently they were updated cannot be measured "
            "— unknown recency is never reported as stale"
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
        for item, stamp in readable
        if (now - stamp).total_seconds() > sla_hours * 3600
    )
    excluded = len(serving) - len(readable)

    detail = (
        f"{len(readable) - len(stale)} of {len(readable)} Gold/serving item(s) with a "
        f"readable last run/refresh were updated within the {sla_hours}h SLA "
        f"(gold_freshness_sla_hours)"
    )
    if stale:
        detail += (f"; stale: {', '.join(stale[:_MAX_NAMED_STALE])}"
                   + (f", …(+{len(stale) - _MAX_NAMED_STALE} more)"
                      if len(stale) > _MAX_NAMED_STALE else ""))
    if excluded:
        detail += (f". {excluded} further serving item(s) had no readable timestamp and are "
                   "excluded rather than counted stale")
    detail += (". This is the item's last run/refresh — the closest readable proxy for "
               "\"the Gold table was updated\"; Delta commit times are not fetched.")
    return covered(len(readable) - len(stale), len(readable), detail)
