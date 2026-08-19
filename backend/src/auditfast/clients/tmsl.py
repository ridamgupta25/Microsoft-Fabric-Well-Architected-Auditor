"""Parse a Tabular Model Scripting Language (TMSL) semantic-model definition.

Fabric's ``getDefinition?format=TMSL`` returns the model as a single JSON document
(the ``model.bim`` shape). This module reduces that to the handful of facts the
audit and the Digital Twin care about — the model's tables, its measures (with
their DAX and descriptions), and its relationships — without pulling in a Tabular
Object Model dependency.

It is pure and defensive: a missing or oddly-shaped section yields empty lists
rather than raising, so a partial or future TMSL variant still parses cleanly.
"""
from __future__ import annotations

import re
from typing import Any

# Power BI's "Auto date/time" feature silently generates one hidden date table per
# date/datetime column (``LocalDateTable_<guid>``) plus a single ``DateTableTemplate_<guid>``.
# These system tables never appear in the Power BI model or report UI, so they are
# excluded from the captured facts to match what a reviewer actually sees.
_AUTO_DATE_TABLE = re.compile(
    r"^(?:LocalDateTable|DateTableTemplate)_"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_auto_date_table(name: str) -> bool:
    """True for a Power BI "Auto date/time" hidden table (not shown in the model UI)."""
    return bool(_AUTO_DATE_TABLE.match(name or ""))


def _expression(value: Any) -> str:
    """A measure/column expression is a string or an array of source lines."""
    if isinstance(value, list):
        return "\n".join(str(part) for part in value)
    return str(value or "")


#: Partition ``source.type`` values, mapped to the storage mode a reader cares
#: about. ``entity`` is Direct Lake (the partition points at a Lakehouse table);
#: ``m``/``query`` are Power Query / native SQL, whose mode comes from the
#: partition's own ``mode`` field; ``calculated`` is a DAX-computed table.
_SOURCE_TYPE_MODE = {
    "entity": "directLake",
    "calculated": "calculated",
    "calculationgroup": "calculationGroup",
}

#: A native SQL / M partition expression is kept (capped) so a check can tell a
#: plain source read apart from an inline transformation. Never row data — the
#: query *text* only, and only up to this many characters.
_MAX_QUERY_EXPRESSION_CHARS = 4000


def _table_storage(table: dict) -> dict:
    """Storage facts for one table, read from its partitions.

    Structure only — partition *definitions*, never the rows behind them. A
    model states its mode per partition, so a table can legitimately be mixed
    (a "dual" or hybrid table); every distinct mode seen is reported.
    """
    modes: set[str] = set()
    source_types: set[str] = set()
    native_queries = 0
    native_expressions: list[str] = []
    for part in table.get("partitions") or []:
        if not isinstance(part, dict):
            continue
        source = part.get("source") if isinstance(part.get("source"), dict) else {}
        source_type = str(source.get("type") or "").lower()
        if source_type:
            source_types.add(source_type)
        # A native SQL / M partition that carries its own query text is a
        # per-refresh transformation *candidate* living in the model rather than
        # upstream. The query text is kept (capped) so a check can tell a plain
        # source read apart from a genuine transform.
        if source_type in {"query", "m"}:
            expression = _expression(source.get("expression")).strip()
            if expression:
                native_queries += 1
                native_expressions.append(expression[:_MAX_QUERY_EXPRESSION_CHARS])
        mode = str(part.get("mode") or "").strip()
        modes.add(mode or _SOURCE_TYPE_MODE.get(source_type, ""))
    return {
        "modes": sorted(m for m in modes if m),
        "source_types": sorted(source_types),
        "native_query_partitions": native_queries,
        "native_query_expressions": native_expressions,
    }


def _table_refresh_policy(table: dict, table_name: str) -> dict | None:
    """The table's incremental-refresh policy, or ``None`` when it has none.

    TMSL records this as ``refreshPolicy`` on the table. Only the *shape* of the
    policy is kept — the windows it declares — never any data it would load.
    """
    policy = table.get("refreshPolicy")
    if not isinstance(policy, dict):
        return None
    return {
        "table": table_name,
        "policy_type": str(policy.get("policyType") or ""),
        "rolling_window_granularity": str(policy.get("rollingWindowGranularity") or ""),
        "rolling_window_periods": policy.get("rollingWindowPeriods"),
        "incremental_granularity": str(policy.get("incrementalGranularity") or ""),
        "incremental_periods": policy.get("incrementalPeriods"),
    }


def _table_aggregations(table: dict, table_name: str) -> list[dict]:
    """Aggregation columns on one table, declared in TMSL as ``alternateOf``.

    A column carrying ``alternateOf`` is an aggregation of a detail column in
    another table — the mechanism that lets a visual answer from a summary
    instead of scanning detail rows.
    """
    found: list[dict] = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        alternate = column.get("alternateOf")
        if not isinstance(alternate, dict):
            continue
        base = alternate.get("baseColumn") if isinstance(alternate.get("baseColumn"), dict) else {}
        found.append({
            "table": table_name,
            "column": column.get("name", ""),
            "summarization": str(alternate.get("summarization") or ""),
            "base_table": str(base.get("table") or ""),
            "base_column": str(base.get("column") or ""),
        })
    return found


def _table_columns(table: dict, table_name: str) -> list[dict]:
    """Column *definitions* for one table — names and declared types only.

    Deliberately shallow: a column's ``dataType`` (its tabular type), its
    ``sourceProviderType`` (the SQL type it was imported from, when the model
    records one), whether it is hidden, and whether the model marks it a key.
    None of that is row data — no distinct-value count, no statistics, nothing
    that would require reading the model's contents.

    ``sourceProviderType`` matters because TMSL's own type system has no
    ``uniqueidentifier``: a GUID column arrives as ``string`` and is otherwise
    indistinguishable from a two-value status code.
    """
    out: list[dict] = []
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or "")
        if not name:
            continue
        out.append({
            "table": table_name,
            "name": name,
            "data_type": str(column.get("dataType") or ""),
            "source_provider_type": str(column.get("sourceProviderType") or ""),
            "source_column": str(column.get("sourceColumn") or ""),
            "is_hidden": bool(column.get("isHidden", False)),
            "is_key": bool(column.get("isKey", False)),
            # The folder a report author sees this column filed under. TMSL carries
            # it per column; without it the "model organisation" half of ref 14.1.8
            # was unassessable and the check said so rather than judging it.
            "display_folder": str(column.get("displayFolder") or ""),
        })
    return out


def parse_tmsl(document: dict) -> dict:
    """Normalize a TMSL document to the facts the audit and Digital Twin need.

    Accepts either the full ``{"model": {...}}`` envelope or a bare model object.

    Everything here is **model metadata** — table and partition *definitions*,
    measure DAX, relationships, roles, refresh policies, column *declarations*.
    No row data is read or stored; the semantic model's actual contents stay in
    Fabric. In particular no column *cardinality* is computed: a distinct-value
    count needs the rows, and rows never enter the knowledge base.
    """
    if not isinstance(document, dict):
        return {
            "tables": [], "measures": [], "relationships": [], "roles": [],
            "storage": {}, "refresh_policies": [], "aggregations": [],
            "columns": [], "direct_lake_behavior": "",
        }

    model = document.get("model") if isinstance(document.get("model"), dict) else document
    tables = model.get("tables") or []

    table_names: list[str] = []
    measures: list[dict] = []
    model_columns: list[dict] = []
    storage: dict[str, dict] = {}
    refresh_policies: list[dict] = []
    aggregations: list[dict] = []
    #: Per-table ``dataCategory``. Microsoft's star-schema guidance is explicit
    #: that no property marks a table as fact or dimension - role is determined
    #: by relationships - but ``dataCategory`` is a *declared* hint when a
    #: modeller sets it ("Time" on a date table is set automatically by Power BI).
    #: Stored so the role classifier can prefer a stated intent over a guess.
    data_categories: dict[str, str] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name", "")
        if _is_auto_date_table(table_name):
            continue  # skip Power BI auto date/time hidden tables
        table_names.append(table_name)
        model_columns.extend(_table_columns(table, table_name))
        category = str(table.get("dataCategory") or "")
        if category:
            data_categories[table_name] = category
        # Partition modes, incremental-refresh policy and aggregation columns.
        # These three were previously initialised and returned but never filled,
        # so refs 14.2.1, 14.2.2, 14.2.4, 14.2.6 and 14.5.2 read an empty
        # structure and returned N/A on every audit - coverage on the catalog,
        # none in the report.
        storage[table_name] = _table_storage(table)
        policy = _table_refresh_policy(table, table_name)
        if policy is not None:
            refresh_policies.append(policy)
        aggregations.extend(_table_aggregations(table, table_name))
        for measure in table.get("measures") or []:
            if not isinstance(measure, dict):
                continue
            measures.append({
                "name": measure.get("name", ""),
                "table": table_name,
                "expression": _expression(measure.get("expression")),
                "description": measure.get("description", "") or "",
                "is_hidden": bool(measure.get("isHidden", False)),
                "format_string": measure.get("formatString", "") or "",
                "display_folder": str(measure.get("displayFolder") or ""),
            })

    relationships: list[dict] = []
    for rel in model.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        from_table = rel.get("fromTable", "")
        to_table = rel.get("toTable", "")
        if _is_auto_date_table(from_table) or _is_auto_date_table(to_table):
            continue  # drop relationships that point at the hidden auto date tables
        relationships.append({
            "name": rel.get("name", ""),
            "from_table": from_table,
            "from_column": rel.get("fromColumn", ""),
            "to_table": to_table,
            "to_column": rel.get("toColumn", ""),
            "cross_filter": rel.get("crossFilteringBehavior", "") or "",
            #: Declared relationship cardinality — structural metadata (never row
            #: data). TMSL omits these for a standard many-to-one relationship, so
            #: an empty string means "defaulted", not "unknown"; both ends set to
            #: ``many`` is a direct many-to-many relationship (no bridge).
            "from_cardinality": str(rel.get("fromCardinality", "") or "").strip().lower(),
            "to_cardinality": str(rel.get("toCardinality", "") or "").strip().lower(),
            "is_active": bool(rel.get("isActive", True)),
        })

    roles: list[dict] = []
    for role in model.get("roles") or []:
        if not isinstance(role, dict):
            continue
        perms = role.get("tablePermissions") or []
        roles.append({
            "name": role.get("name", ""),
            "model_permission": role.get("modelPermission", "") or "",
            "table_permissions": [
                {
                    "table": p.get("name", ""),
                    "filter": _expression(p.get("filterExpression")),
                    # "None" here hides the whole table — table-level OLS.
                    "metadata_permission": p.get("metadataPermission", "") or "",
                    "column_permissions": [
                        {"column": cp.get("name", ""), "permission": cp.get("metadataPermission", "")}
                        for cp in (p.get("columnPermissions") or []) if isinstance(cp, dict)
                    ],
                }
                for p in perms if isinstance(p, dict)
            ],
        })

    return {
        "tables": table_names,
        "measures": measures,
        "relationships": relationships,
        "roles": roles,
        #: Declared ``dataCategory`` per table ("Time", "Customers", ...), when
        #: the modeller set one. Empty for every table on most models.
        "data_categories": data_categories,
        #: Per-table partition modes / source types (structure, not rows).
        "storage": storage,
        #: Tables carrying an incremental-refresh policy.
        "refresh_policies": refresh_policies,
        #: Aggregation columns declared via ``alternateOf``.
        "aggregations": aggregations,
        #: Every column *declaration* in the model (name + declared types +
        #: hidden/key flags). Structure only — never a distinct-value count.
        "columns": model_columns,
        #: Model-level Direct Lake fallback: automatic | directLakeOnly | directQueryOnly.
        "direct_lake_behavior": str(model.get("directLakeBehavior") or ""),
    }
