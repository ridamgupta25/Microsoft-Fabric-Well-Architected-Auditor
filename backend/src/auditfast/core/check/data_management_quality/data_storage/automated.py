"""Data Management & Quality · Data Storage — table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import re

from auditfast.core.check._tables import (
    AUDIT_COLUMNS,
    TABLE_LAYERS,
    col_names,
    columns,
    is_dimension,
    is_fact,
    is_snake_case,
)
from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_NO_TABLES = "No lakehouse/warehouse tables were read for this workspace"

#: Column names implying a date/time value, for the data-type check.
_DATE_NAME = re.compile(r"(date|timestamp|_dt$|_time$)", re.IGNORECASE)


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
    found = any(is_dimension(n) and ("date" in n.lower() or "calendar" in n.lower())
                for n in tables)
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
    id="TB-DATATYPES", ref="4.2.4", title="Date/time columns use a temporal type (not string)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def table_data_types(ctx: CheckContext) -> Verdict:
    """Columns whose name implies a date/time are typed as temporal, not string."""
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable("No table column metadata available")
    date_cols = [(c.get("name", ""), (c.get("type", "") or "").lower())
                 for t in tables.values() for c in columns(t)
                 if _DATE_NAME.search(c.get("name", ""))]
    if not date_cols:
        return not_applicable("No date/time-named columns to type-check")
    ok = [n for n, ty in date_cols if ty and not ty.startswith(("string", "varchar", "char"))]
    return covered(len(ok), len(date_cols),
                   f"{len(ok)} of {len(date_cols)} date/time columns use a temporal type")


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
