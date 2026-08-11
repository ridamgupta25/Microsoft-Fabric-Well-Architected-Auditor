"""Data Management & Quality · Data Storage — table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import re

from auditfast.core.check._pipeline import activities as pipeline_activities
from auditfast.core.check._pipeline import activities as pipeline_activities
from auditfast.core.check._tables import (
    TABLE_LAYERS,
    col_names,
    columns,
    has_audit_column,
    is_dimension,
    is_fact,
    is_snake_case,
)
from auditfast.core.check.helpers import (
    Verdict,
    binary,
    covered,
    graded,
    not_applicable,
    note
)
from auditfast.core.check._notebook import notebook_code
from auditfast.core.check.helpers import (
    Verdict,
    binary,
    covered,
    graded,
    not_applicable,
    note
)
from auditfast.core.check._notebook import notebook_code
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

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

#: A text type carrying a declared width, e.g. ``varchar(4000)`` or ``nvarchar(max)``.
_DECLARED_WIDTH = re.compile(r"^n?(?:varchar|char)\s*\(\s*(max|\d+)\s*\)", re.IGNORECASE)

#: Widths above this are treated as oversized — they defeat statistics and inflate row size.
_MAX_TEXT_WIDTH = 4000

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

_PARTITION_HINT_COLUMNS = (
    "event_date", "business_date", "partition_date", "load_date",
    "event_dt", "load_dt", "year", "month", "day",
)
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
    """Inspectable SQL load logic wraps transactional changes in TRY...CATCH."""
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

    inspected: list[tuple[str, bool]] = []
    opaque_loads: list[str] = []

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
                "Load logic is present but SQL bodies are not inspectable in this snapshot; "
                "cannot verify TRY...CATCH transaction handling. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No inspectable scripted SQL load activity was found")

    compliant = [name for name, ok in inspected if ok]
    return covered(
        len(compliant),
        len(inspected),
        f"{len(compliant)} of {len(inspected)} inspectable SQL load activity/activities use "
        "TRY...CATCH and BEGIN/COMMIT/ROLLBACK transaction handling"
        + (f"; compliant: {', '.join(compliant[:5])}" if compliant else ""),
    )

@check(
    id="WS-WH-INCREMENTAL", ref="3.6.6",
    title="Warehouse loads avoid unnecessary full reloads",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_incremental_loads(ctx: CheckContext) -> Verdict:
    """Inspectable warehouse SQL loads favor incremental patterns over full reloads."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [item for item in ctx.workspace.items if item.type == "Warehouse"]
    if not warehouses:
        return not_applicable("No Warehouse items found in this workspace")
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    inspected: list[tuple[str, bool]] = []
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
            "No inspectable scripted warehouse load was found; Copy/stored-procedure "
            "loads do not expose enough logic in the snapshot to judge incremental vs full reload"
        )

    incremental = [name for name, is_incremental in inspected if is_incremental]
    return covered(
        len(incremental), len(inspected),
        f"{len(incremental)} of {len(inspected)} inspectable warehouse load activity/activities "
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
    if not inspectable_pipelines:
        if opaque_loads:
            return not_applicable(
                "Significant loads are present but SQL bodies are not inspectable in this snapshot; "
                "cannot verify post-load statistics maintenance. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No significant inspectable SQL warehouse/lakehouse load was found")

    compliant = [name for name in inspectable_pipelines if pipeline_stats.get(name)]
    evidence = (
        f"{len(compliant)} of {len(inspectable_pipelines)} pipeline(s) with significant inspectable SQL loads "
        "also run statistics maintenance (UPDATE STATISTICS / sp_updatestats / ANALYZE TABLE)"
    )
    if opaque_loads:
        evidence += f"; {len(opaque_loads)} load activity/activities remain non-inspectable"
    if compliant:
        evidence += f"; compliant pipelines: {', '.join(compliant[:5])}"

    return covered(len(compliant), len(inspectable_pipelines), evidence)
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

_PARTITION_HINT_COLUMNS = (
    "event_date", "business_date", "partition_date", "load_date",
    "event_dt", "load_dt", "year", "month", "day",
)
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
    """Inspectable SQL load logic wraps transactional changes in TRY...CATCH."""
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

    inspected: list[tuple[str, bool]] = []
    opaque_loads: list[str] = []

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
                "Load logic is present but SQL bodies are not inspectable in this snapshot; "
                "cannot verify TRY...CATCH transaction handling. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No inspectable scripted SQL load activity was found")

    compliant = [name for name, ok in inspected if ok]
    return covered(
        len(compliant),
        len(inspected),
        f"{len(compliant)} of {len(inspected)} inspectable SQL load activity/activities use "
        "TRY...CATCH and BEGIN/COMMIT/ROLLBACK transaction handling"
        + (f"; compliant: {', '.join(compliant[:5])}" if compliant else ""),
    )

@check(
    id="WS-WH-INCREMENTAL", ref="3.6.6",
    title="Warehouse loads avoid unnecessary full reloads",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def wh_incremental_loads(ctx: CheckContext) -> Verdict:
    """Inspectable warehouse SQL loads favor incremental patterns over full reloads."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [item for item in ctx.workspace.items if item.type == "Warehouse"]
    if not warehouses:
        return not_applicable("No Warehouse items found in this workspace")
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    inspected: list[tuple[str, bool]] = []
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
            "No inspectable scripted warehouse load was found; Copy/stored-procedure "
            "loads do not expose enough logic in the snapshot to judge incremental vs full reload"
        )

    incremental = [name for name, is_incremental in inspected if is_incremental]
    return covered(
        len(incremental), len(inspected),
        f"{len(incremental)} of {len(inspected)} inspectable warehouse load activity/activities "
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
    if not inspectable_pipelines:
        if opaque_loads:
            return not_applicable(
                "Significant loads are present but SQL bodies are not inspectable in this snapshot; "
                "cannot verify post-load statistics maintenance. " + _SQL_PERMISSION_HINT
            )
        return not_applicable("No significant inspectable SQL warehouse/lakehouse load was found")

    compliant = [name for name in inspectable_pipelines if pipeline_stats.get(name)]
    evidence = (
        f"{len(compliant)} of {len(inspectable_pipelines)} pipeline(s) with significant inspectable SQL loads "
        "also run statistics maintenance (UPDATE STATISTICS / sp_updatestats / ANALYZE TABLE)"
    )
    if opaque_loads:
        evidence += f"; {len(opaque_loads)} load activity/activities remain non-inspectable"
    if compliant:
        evidence += f"; compliant pipelines: {', '.join(compliant[:5])}"

    return covered(len(compliant), len(inspectable_pipelines), evidence)

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
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_partition_strategy(ctx: CheckContext) -> Verdict:
    """Large-table candidates should expose explicit partition/clustering metadata."""
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
    inspectable = 0
    hint_only: list[str] = []

    for name, meta in large_candidates.items():
        table_meta = meta or {}
        cols = [str(c.get("name", "")).lower() for c in columns(table_meta)]
        explicit = any(table_meta.get(key) for key in _STRATEGY_METADATA_KEYS)
        hinted = any(col in _PARTITION_HINT_COLUMNS or col.endswith(("_date", "_dt", "_month", "_year")) for col in cols)

        if explicit:
            inspectable += 1
            with_strategy.append(name)
        elif hinted:
            hint_only.append(name)

    if inspectable == 0:
        return not_applicable(
            "Large-table candidates were found but no partition/clustering metadata was available to verify strategy. "
            + _ONELAKE_PERMISSION_HINT
        )

    return covered(
        len(with_strategy),
        inspectable,
        f"{len(with_strategy)} of {inspectable} large-table candidate(s) expose explicit partition/clustering strategy"
        + (f"; explicit strategy: {', '.join(with_strategy[:5])}" if with_strategy else ""),
    )

@check(
    id="TB-AUDITCOLS", ref="4.2.5", title="Audit columns present (created_date, modified_date, source_system, batch_id)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_audit_columns(ctx: CheckContext) -> Verdict:
    """Each table records lineage via audit columns (created/modified/batch id).

    Matched on the *normalised* column name, so ``CreatedDate``, ``created_date``
    and ``load_dt`` all count. Business dates (``order_date``, ``birth_date``) do
    not — the event vocabulary is deliberately narrow.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)
    ok = [n for n, t in tables.items() if has_audit_column(t)]
    return covered(len(ok), len(tables), f"{len(ok)} of {len(tables)} tables have audit columns")


@check(
    id="TB-STARSCHEMA", ref="4.5.1", title="Star schema design implemented (fact + dimension tables, not flat wide tables)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_star_schema(ctx: CheckContext) -> Verdict:
    """The model separates fact tables from dimension tables (not flat wide tables)."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    has_fact = any(is_fact(n) for n in tables)
    has_dim = any(is_dimension(n) for n in tables)
    return binary(has_fact and has_dim,
                  "Both fact and dimension tables present" if has_fact and has_dim
                  else f"fact tables present: {has_fact}; dimension tables present: {has_dim}")



@check(
    id="TB-TYPE-SIZING", ref="4.4.3",
    title="Data types are appropriate and sized for analytical use",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_type_sizing(ctx: CheckContext) -> Verdict:
    """Check numeric precision validity and oversized varchar declarations where available."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")

    inspectable = 0
    sized_ok = 0
    flagged: list[str] = []

    for table_name, table_meta in tables.items():
        # SQL-endpoint sizing checks require warehouse-style typed metadata.
        if "." not in table_name:
            continue
        for column in columns(table_meta):
            col_name = str(column.get("name", "") or "")
            col_type = str(column.get("type", "") or "").strip().lower()
            if not col_type:
                continue

            decimal = _DECIMAL_TYPE.match(col_type)
            varchar = _OVERSIZED_VARCHAR.match(col_type)
            if not decimal and not varchar:
                continue

            inspectable += 1
            if decimal:
                precision = int(decimal.group(1))
                scale = int(decimal.group(2))
                if 0 < precision <= 38 and 0 <= scale <= precision:
                    sized_ok += 1
                else:
                    flagged.append(f"{table_name}.{col_name}={col_type}")
                continue

            width = int(varchar.group(1))
            if width <= 2000:
                sized_ok += 1
            else:
                flagged.append(f"{table_name}.{col_name}=varchar({width})")

    if inspectable == 0:
        return not_applicable(
            "No inspectable decimal(v,s) or varchar(n) declarations were available in metadata to validate sizing. "
            + _SQL_PERMISSION_HINT
        )

    return covered(
        sized_ok,
        inspectable,
        f"{sized_ok} of {inspectable} inspectable column type declaration(s) meet sizing/precision rules"
        + (f"; flagged: {', '.join(flagged[:5])}" if flagged else ""),
    )


@check(
    id="TB-SURROGATE-PATTERN", ref="4.4.4",
    title="Dimension surrogate keys use a generated-key pattern",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_surrogate_pattern(ctx: CheckContext) -> Verdict:
    """Dimensions should expose surrogate key columns consistent with generated-key patterns."""
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable("No dimension tables with column metadata")

    inspectable = 0
    patterned = 0
    missing: list[str] = []
    for name, table_meta in dims.items():
        if "." not in name:
            continue
        names = col_names(table_meta)
        if not names:
            continue
        inspectable += 1
        has_pattern = any(_SURROGATE_KEY_NAME.search(col) or col.endswith(("_sk", "_key")) for col in names)
        if has_pattern:
            patterned += 1
        else:
            missing.append(name)

    if inspectable == 0:
        return not_applicable("No dimension columns were inspectable for surrogate key pattern checks")

    return covered(
        patterned,
        inspectable,
        f"{patterned} of {inspectable} dimension table(s) expose surrogate key naming patterns"
        + (f"; missing: {', '.join(missing[:5])}" if missing else ""),
    )


@check(
    id="TB-PKFK-DECLARED", ref="4.4.5",
    title="Primary and foreign key constraints are declared where supported",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_pkfk_declared(ctx: CheckContext) -> Verdict:
    """Use structural metadata hints for declared PK/FK patterns; Fabric Warehouse enforcement is documented separately."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")

    inspectable = 0
    hinted = 0
    missing: list[str] = []
    for name, table_meta in tables.items():
        if "." not in name:
            continue
        names = col_names(table_meta)
        if not names:
            continue
        inspectable += 1
        has_hint = any(
            _PK_FK_NAME_HINT.search(col)
            or col.endswith(("_id", "_sk", "_fk", "_key"))
            for col in names
        )
        if has_hint:
            hinted += 1
        else:
            missing.append(name)

    if inspectable == 0:
        return not_applicable("No tables were inspectable for PK/FK declaration hints")

    return covered(
        hinted,
        inspectable,
        f"{hinted} of {inspectable} table(s) include PK/FK structural naming hints"
        + (f"; no hints: {', '.join(missing[:5])}" if missing else "")
        + "; note: Fabric Warehouse constraints are declarative and not enforced",
    )


@check(
    id="WS-STATS-STRATEGY", ref="4.4.6",
    title="Statistics maintenance strategy is defined and automated",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS], required=True,
)
def stats_strategy_defined(ctx: CheckContext) -> Verdict:
    """Inspect pipeline SQL automation for recurring stats maintenance patterns."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    storage_items = [item for item in ctx.workspace.items if item.type in {"Warehouse", "Lakehouse"}]
    if not storage_items:
        return not_applicable("No Warehouse/Lakehouse items found in this workspace")

    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    inspectable = 0
    automated = 0
    opaque = 0

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

    if inspectable == 0:
        if opaque:
            return not_applicable(
                "Storage load automation is present but SQL bodies are not inspectable, so statistics strategy cannot be verified. "
                + _SQL_PERMISSION_HINT
            )
        return not_applicable("No inspectable storage-load automation was found to evaluate statistics strategy")

    return covered(
        automated,
        inspectable,
        f"{automated} of {inspectable} inspectable load automation step(s) include statistics maintenance",
        obj="inspectable load automation steps",
    )


@check(
    id="TB-TYPE-SIZING", ref="4.4.3",
    title="Data types are appropriate and sized for analytical use",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_type_sizing(ctx: CheckContext) -> Verdict:
    """Check numeric precision validity and oversized varchar declarations where available."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")

    inspectable = 0
    sized_ok = 0
    flagged: list[str] = []

    for table_name, table_meta in tables.items():
        # SQL-endpoint sizing checks require warehouse-style typed metadata.
        if "." not in table_name:
            continue
        for column in columns(table_meta):
            col_name = str(column.get("name", "") or "")
            col_type = str(column.get("type", "") or "").strip().lower()
            if not col_type:
                continue

            decimal = _DECIMAL_TYPE.match(col_type)
            varchar = _OVERSIZED_VARCHAR.match(col_type)
            if not decimal and not varchar:
                continue

            inspectable += 1
            if decimal:
                precision = int(decimal.group(1))
                scale = int(decimal.group(2))
                if 0 < precision <= 38 and 0 <= scale <= precision:
                    sized_ok += 1
                else:
                    flagged.append(f"{table_name}.{col_name}={col_type}")
                continue

            width = int(varchar.group(1))
            if width <= 2000:
                sized_ok += 1
            else:
                flagged.append(f"{table_name}.{col_name}=varchar({width})")

    if inspectable == 0:
        return not_applicable(
            "No inspectable decimal(v,s) or varchar(n) declarations were available in metadata to validate sizing. "
            + _SQL_PERMISSION_HINT
        )

    return covered(
        sized_ok,
        inspectable,
        f"{sized_ok} of {inspectable} inspectable column type declaration(s) meet sizing/precision rules"
        + (f"; flagged: {', '.join(flagged[:5])}" if flagged else ""),
    )


@check(
    id="TB-SURROGATE-PATTERN", ref="4.4.4",
    title="Dimension surrogate keys use a generated-key pattern",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_surrogate_pattern(ctx: CheckContext) -> Verdict:
    """Dimensions should expose surrogate key columns consistent with generated-key patterns."""
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable("No dimension tables with column metadata")

    inspectable = 0
    patterned = 0
    missing: list[str] = []
    for name, table_meta in dims.items():
        if "." not in name:
            continue
        names = col_names(table_meta)
        if not names:
            continue
        inspectable += 1
        has_pattern = any(_SURROGATE_KEY_NAME.search(col) or col.endswith(("_sk", "_key")) for col in names)
        if has_pattern:
            patterned += 1
        else:
            missing.append(name)

    if inspectable == 0:
        return not_applicable("No dimension columns were inspectable for surrogate key pattern checks")

    return covered(
        patterned,
        inspectable,
        f"{patterned} of {inspectable} dimension table(s) expose surrogate key naming patterns"
        + (f"; missing: {', '.join(missing[:5])}" if missing else ""),
    )


@check(
    id="TB-PKFK-DECLARED", ref="4.4.5",
    title="Primary and foreign key constraints are declared where supported",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=True,
)
def table_pkfk_declared(ctx: CheckContext) -> Verdict:
    """Use structural metadata hints for declared PK/FK patterns; Fabric Warehouse enforcement is documented separately."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")

    inspectable = 0
    hinted = 0
    missing: list[str] = []
    for name, table_meta in tables.items():
        if "." not in name:
            continue
        names = col_names(table_meta)
        if not names:
            continue
        inspectable += 1
        has_hint = any(
            _PK_FK_NAME_HINT.search(col)
            or col.endswith(("_id", "_sk", "_fk", "_key"))
            for col in names
        )
        if has_hint:
            hinted += 1
        else:
            missing.append(name)

    if inspectable == 0:
        return not_applicable("No tables were inspectable for PK/FK declaration hints")

    return covered(
        hinted,
        inspectable,
        f"{hinted} of {inspectable} table(s) include PK/FK structural naming hints"
        + (f"; no hints: {', '.join(missing[:5])}" if missing else "")
        + "; note: Fabric Warehouse constraints are declarative and not enforced",
    )


@check(
    id="WS-STATS-STRATEGY", ref="4.4.6",
    title="Statistics maintenance strategy is defined and automated",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS], required=True,
)
def stats_strategy_defined(ctx: CheckContext) -> Verdict:
    """Inspect pipeline SQL automation for recurring stats maintenance patterns."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    storage_items = [item for item in ctx.workspace.items if item.type in {"Warehouse", "Lakehouse"}]
    if not storage_items:
        return not_applicable("No Warehouse/Lakehouse items found in this workspace")

    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    inspectable = 0
    automated = 0
    opaque = 0

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

    if inspectable == 0:
        if opaque:
            return not_applicable(
                "Storage load automation is present but SQL bodies are not inspectable, so statistics strategy cannot be verified. "
                + _SQL_PERMISSION_HINT
            )
        return not_applicable("No inspectable storage-load automation was found to evaluate statistics strategy")

    return covered(
        automated,
        inspectable,
        f"{automated} of {inspectable} inspectable load automation step(s) include statistics maintenance",
        obj="inspectable load automation steps",
    )

@check(
    id="TB-DATEDIM", ref="4.5.7", title="Date/Time dimension exists with all required attributes (fiscal periods, quarter, holidays)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_date_dimension(ctx: CheckContext) -> Verdict:
    """A dedicated date/calendar dimension backs time-based analytics."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    found = any(
        ("date" in n.lower() or "calendar" in n.lower())
        and (is_dimension(n) or "dim" in n.lower() or "calendar" in n.lower())
        for n in tables
    )
    return binary(found, "A date/time dimension table exists" if found
                  else "No date/time dimension table found")


@check(
    id="TB-SURROGATE", ref="4.5.6", title="Surrogate keys used for dimension tables (not business keys as PKs in facts)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_surrogate_keys(ctx: CheckContext) -> Verdict:
    """Dimensions have a surrogate key column (``*_sk`` / ``*_key``), not just a business key."""
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)
    ok = [n for n, t in dims.items()
          if any(c.endswith(("_sk", "_key")) for c in col_names(t))]
    return covered(len(ok), len(dims), f"{len(ok)} of {len(dims)} dimensions have a surrogate key")


@check(
    id="TB-COL-NAMING", ref="4.2.3", title="Column naming is consistent and self-documenting",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def table_column_naming(ctx: CheckContext) -> Verdict:
    """Table columns follow a consistent snake_case convention."""
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)
    names = [c.get("name", "") for t in tables.values() for c in columns(t)]
    ok = [n for n in names if is_snake_case(n)]
    return covered(len(ok), len(names), f"{len(ok)} of {len(names)} columns use snake_case names")


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
    """Slowly-changing dimensions carry valid_from, valid_to, and is_current together."""
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable(_NO_DIMS)
    tracked = ("valid_from", "valid_to", "is_current")
    scd2 = {n: t for n, t in dims.items() if any(col in col_names(t) for col in tracked)}
    if not scd2:
        return not_applicable("No SCD Type 2 dimensions detected")
    ok = [n for n, t in scd2.items() if all(col in col_names(t) for col in tracked)]
    return covered(len(ok), len(scd2),
                   f"{len(ok)} of {len(scd2)} SCD2 dimensions track valid_from/valid_to/is_current")
