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


def _table_storage(table: dict) -> dict:
    """Storage facts for one table, read from its partitions.

    Structure only — partition *definitions*, never the rows behind them. A
    model states its mode per partition, so a table can legitimately be mixed
    (a "dual" or hybrid table); every distinct mode seen is reported.
    """
    modes: set[str] = set()
    source_types: set[str] = set()
    native_queries = 0
    for part in table.get("partitions") or []:
        if not isinstance(part, dict):
            continue
        source = part.get("source") if isinstance(part.get("source"), dict) else {}
        source_type = str(source.get("type") or "").lower()
        if source_type:
            source_types.add(source_type)
        # A native SQL / M partition that carries its own query text is a
        # per-refresh transformation living in the model rather than upstream.
        if source_type in {"query", "m"} and _expression(source.get("expression")).strip():
            native_queries += 1
        mode = str(part.get("mode") or "").strip()
        modes.add(mode or _SOURCE_TYPE_MODE.get(source_type, ""))
    return {
        "modes": sorted(m for m in modes if m),
        "source_types": sorted(source_types),
        "native_query_partitions": native_queries,
    }


def parse_tmsl(document: dict) -> dict:
    """Normalize a TMSL document to the facts the audit and Digital Twin need.

    Accepts either the full ``{"model": {...}}`` envelope or a bare model object.

    Everything here is **model metadata** — table and partition *definitions*,
    measure DAX, relationships, roles, refresh policies. No row data is read or
    stored; the semantic model's actual contents stay in Fabric.
    """
    if not isinstance(document, dict):
        return {
            "tables": [], "measures": [], "relationships": [], "roles": [],
            "storage": {}, "refresh_policies": [], "aggregations": [],
            "direct_lake_behavior": "",
        }

    model = document.get("model") if isinstance(document.get("model"), dict) else document
    tables = model.get("tables") or []

    table_names: list[str] = []
    measures: list[dict] = []
    storage: dict[str, dict] = {}
    refresh_policies: list[dict] = []
    aggregations: list[dict] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name", "")
        if _is_auto_date_table(table_name):
            continue  # skip Power BI auto date/time hidden tables
        table_names.append(table_name)
        storage[table_name] = _table_storage(table)
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
        #: Per-table partition modes / source types (structure, not rows).
        "storage": storage,
        #: Tables carrying an incremental-refresh policy.
        "refresh_policies": refresh_policies,
        #: Aggregation columns declared via ``alternateOf``.
        "aggregations": aggregations,
        #: Model-level Direct Lake fallback: automatic | directLakeOnly | directQueryOnly.
        "direct_lake_behavior": str(model.get("directLakeBehavior") or ""),
    }
