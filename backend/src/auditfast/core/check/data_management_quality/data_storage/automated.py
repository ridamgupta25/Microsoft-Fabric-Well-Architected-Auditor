"""Data Management & Quality · Data Storage — table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import re

from auditfast.core.check._pipeline import activities as pipeline_activities
from auditfast.core.check._tables import (
    AUDIT_COLUMNS,
    TABLE_LAYERS,
    col_names,
    columns,
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
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_NO_TABLES = "No lakehouse/warehouse tables were read for this workspace"

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
    id="WS-STAGING", ref="3.6.3
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
    id="TB-NAMING", ref="4.2.1", title="Tables follow a consistent naming convention",
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
    id="TB-MANAGED-DELTA", ref="4.1.1", title="Tables are managed Delta tables",
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
    id="TB-AUDITCOLS", ref="4.2.5", title="Tables carry audit columns",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_audit_columns(ctx: CheckContext) -> Verdict:
    """Each table records lineage via audit columns (created/modified/batch id)."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")
    ok = [n for n, t in tables.items() if any(a in col_names(t) for a in AUDIT_COLUMNS)]
    return covered(len(ok), len(tables), f"{len(ok)} of {len(tables)} tables have audit columns")


@check(
    id="TB-STARSCHEMA", ref="4.4.1", title="Star schema — fact and dimension tables present",
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
    id="TB-DATEDIM", ref="4.4.8", title="A date/time dimension exists",
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
    id="TB-SURROGATE", ref="4.4.7", title="Dimension tables use surrogate keys",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_surrogate_keys(ctx: CheckContext) -> Verdict:
    """Dimensions have a surrogate key column (``*_sk`` / ``*_key``), not just a business key."""
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable("No dimension tables with column metadata")
    ok = [n for n, t in dims.items()
          if any(c.endswith(("_sk", "_key")) for c in col_names(t))]
    return covered(len(ok), len(dims), f"{len(ok)} of {len(dims)} dimensions have a surrogate key")


@check(
    id="TB-COL-NAMING", ref="4.2.3", title="Column names are consistent and self-documenting",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_column_naming(ctx: CheckContext) -> Verdict:
    """Table columns follow a consistent snake_case convention."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")
    names = [c.get("name", "") for t in tables.values() for c in columns(t)]
    ok = [n for n in names if is_snake_case(n)]
    return covered(len(ok), len(names), f"{len(ok)} of {len(names)} columns use snake_case names")


@check(
    id="TB-DATATYPES", ref="4.2.4", title="Data types are appropriate (temporal dates, no oversized text)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_data_types(ctx: CheckContext) -> Verdict:
    """Date-named columns are typed temporally, and declared text widths are sane.

    Only columns that can actually be judged are counted: a date-named column, or a
    text column that declares a width. A bare ``string`` has no width to assess.
    """
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")

    assessed = compliant = stringly_dates = oversized = 0
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
            elif _too_wide(ctype) is not None:
                assessed += 1
                if _too_wide(ctype):
                    oversized += 1
                else:
                    compliant += 1

    if not assessed:
        return not_applicable(
            "No date/time-named columns and no text columns with a declared width"
        )
    return covered(
        compliant, assessed,
        f"{compliant} of {assessed} assessable columns are appropriately typed — "
        f"{stringly_dates} date column(s) typed as text, "
        f"{oversized} text column(s) wider than {_MAX_TEXT_WIDTH}",
    )


def _too_wide(column_type: str) -> bool | None:
    """True/False for a text type with a declared width, None when not assessable."""
    match = _DECLARED_WIDTH.match(column_type)
    if not match:
        return None
    width = match.group(1).lower()
    return True if width == "max" else int(width) > _MAX_TEXT_WIDTH


@check(
    id="WS-SHORTCUT-SCOPE", ref="4.1.2", title="OneLake used as the single data lake",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.SHORTCUTS], required=False,
)
def shortcut_scope(ctx: CheckContext) -> Verdict:
    """Where the workspace's shortcuts point — OneLake versus external storage.

    A shortcut to Dataverse or ADLS is a legitimate governed pattern, so the count
    is reported for review rather than scored as a failure.
    """
    if not ctx.workspace.has(Resource.SHORTCUTS):
        return not_applicable("Shortcuts could not be read from Fabric")
    all_shortcuts = [s for entries in ctx.workspace.shortcuts.values() for s in entries]
    if not all_shortcuts:
        return not_applicable("No OneLake shortcuts in this workspace")

    external_types = sorted({
        (s.get("target_type") or "unknown")
        for s in all_shortcuts
        if (s.get("target_type") or "").strip().lower() != "onelake"
    })
    onelake = sum(1 for s in all_shortcuts
                  if (s.get("target_type") or "").strip().lower() == "onelake")
    external = len(all_shortcuts) - onelake
    return note(
        f"{len(all_shortcuts)} shortcut(s): {onelake} target OneLake, "
        f"{external} target external sources ({', '.join(external_types) or 'none'}). "
        f"External shortcuts are legitimate when governed - confirm each is intended."
    )



@check(
    id="TB-SCD2", ref="4.4.10", title="SCD Type 2 dimensions track validity and a current flag",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_scd2(ctx: CheckContext) -> Verdict:
    """Slowly-changing dimensions carry valid_from, valid_to, and is_current together."""
    dims = {n: t for n, t in ctx.workspace.tables.items() if is_dimension(n) and columns(t)}
    if not dims:
        return not_applicable("No dimension tables with column metadata")
    tracked = ("valid_from", "valid_to", "is_current")
    scd2 = {n: t for n, t in dims.items() if any(col in col_names(t) for col in tracked)}
    if not scd2:
        return not_applicable("No SCD Type 2 dimensions detected")
    ok = [n for n, t in scd2.items() if all(col in col_names(t) for col in tracked)]
    return covered(len(ok), len(scd2),
                   f"{len(ok)} of {len(scd2)} SCD2 dimensions track valid_from/valid_to/is_current")
