"""Data Management & Quality · Data Storage — table design & dimensional model.

Reads lakehouse/warehouse table metadata (names, storage type/format, and column
schemas) to judge naming, managed-Delta usage, audit columns, and the star-schema
model. Each check is workspace-scoped and aggregates across every table found.
"""
from __future__ import annotations

import re

from auditfast.core.check._tables import (
    TABLE_LAYERS,
    col_names,
    columns,
    has_audit_column,
    is_dimension,
    is_fact,
    is_snake_case,
)
from auditfast.core.check.helpers import Verdict, binary, covered, not_applicable
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
_DECIMAL_PRECISION = re.compile(r"^(?:decimal|numeric)\((\d+)\s*,\s*(\d+)\)$", re.IGNORECASE)

#: Signals a column that can drive partitioning/cluster strategy on large tables.
_PARTITION_HINT = re.compile(
    r"(?:^|_)(date|dt|year|month|region|country|tenant|partition)(?:$|_)",
    re.IGNORECASE,
)

#: Naming hints that a surrogate key was generated (hash/window/sequence style).
_GENERATED_KEY_HINT = re.compile(
    r"(?:hash|row_?number|row_?num|sequence|seq|surrogate)",
    re.IGNORECASE,
)

#: Natural/business key hints used alongside a surrogate key column.
_BUSINESS_KEY_HINT = re.compile(
    r"(?:business|natural|code|number|_bk$)",
    re.IGNORECASE,
)

#: How many table names to list in evidence before truncating — keeps the
#: star-schema finding readable on workspaces with hundreds of tables.
_SAMPLE_LIMIT = 8


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
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ITEMS], required=True,
)
def table_star_schema(ctx: CheckContext) -> Verdict:
    """The model separates fact tables from dimension tables (not flat wide tables)."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    stores = _table_stores(ctx)
    has_fact = any(is_fact(n) for n in tables)
    has_dim = any(is_dimension(n) for n in tables)
    if has_fact and has_dim:
        return binary(True, "Both fact (fact*/fct*) and dimension (dim*) tables are present", obj=stores)
    reasons = []
    if not has_fact:
        reasons.append("no fact tables (named fact*/fct*)")
    if not has_dim:
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


@check(
    id="TB-DATEDIM", ref="4.5.7", title="Date/Time dimension exists with all required attributes (fiscal periods, quarter, holidays)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ITEMS], required=False,
)
def table_date_dimension(ctx: CheckContext) -> Verdict:
    """A dedicated date/calendar dimension backs time-based analytics."""
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    stores = _table_stores(ctx)
    found = any(
        ("date" in n.lower() or "calendar" in n.lower())
        and (is_dimension(n) or "dim" in n.lower() or "calendar" in n.lower())
        for n in tables
    )
    if found:
        return binary(True, "A date/time dimension table exists", obj=stores)
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


@check(
    id="TB-PARTITION-STRATEGY", ref="4.2.2",
    title="Partitioning / clustering strategy defined for large tables",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def table_partition_strategy(ctx: CheckContext) -> Verdict:
    """Likely large tables define a partition-driving key in their schema.

    Fabric's table metadata does not expose physical partition specs directly, so
    this check uses schema-level evidence: likely large tables (fact-like names or
    wide schemas) should carry a partition-driving key column (date/region/tenant
    style) that enables a partitioning strategy.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    tables = {n: t for n, t in ctx.workspace.tables.items() if columns(t)}
    if not tables:
        return not_applicable(_NO_COLS)

    likely_large = {
        name: table
        for name, table in tables.items()
        if is_fact(name) or len(columns(table)) >= 30
    }
    if not likely_large:
        return not_applicable(
            "No likely large table found (fact-like name or wide schema)"
        )

    with_strategy = [
        name for name, table in likely_large.items()
        if any(_PARTITION_HINT.search(col) for col in col_names(table))
    ]
    return covered(
        len(with_strategy), len(likely_large),
        f"{len(with_strategy)} of {len(likely_large)} likely large table(s) "
        "define a partition-driving key column",
    )


@check(
    id="TB-DATATYPE-SIZING", ref="4.4.3",
    title="Data types are appropriate and sized correctly",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=TABLE_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=True,
)
def table_datatype_sizing(ctx: CheckContext) -> Verdict:
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
    """Fact tables are represented in declared semantic-model relationships.

    Fabric Warehouse PK/FK constraints are metadata (not enforced), and direct
    constraint metadata is not available in ``WorkspaceContext``. This check uses
    semantic-model relationships as the machine-readable declaration of PK/FK
    structure for workspace storage tables.
    """
    if not ctx.workspace.tables:
        return not_applicable(_NO_TABLES)
    if not ctx.workspace.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return not_applicable("Semantic model definitions could not be read from Fabric")
    models = ctx.workspace.semantic_models
    if not models:
        return not_applicable("No semantic models were read for this workspace")

    facts = [name for name in ctx.workspace.tables if is_fact(name)]
    if not facts:
        return not_applicable("No fact-like tables found to assess for declared FK relationships")

    table_names = {_norm_name(name) for name in ctx.workspace.tables}
    linked_tables: set[str] = set()
    for model in models.values():
        for rel in model.get("relationships") or []:
            from_table = _norm_name(str(rel.get("from_table") or rel.get("fromTable") or ""))
            to_table = _norm_name(str(rel.get("to_table") or rel.get("toTable") or ""))
            if from_table in table_names:
                linked_tables.add(from_table)
            if to_table in table_names:
                linked_tables.add(to_table)

    if not linked_tables:
        return covered(
            0, len(facts),
            "No semantic-model relationships reference workspace storage tables"
        )

    linked_facts = [name for name in facts if _norm_name(name) in linked_tables]
    return covered(
        len(linked_facts), len(facts),
        f"{len(linked_facts)} of {len(facts)} fact-like table(s) participate in declared "
        "semantic-model relationships",
    )


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
