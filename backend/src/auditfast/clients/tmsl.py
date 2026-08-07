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


def parse_tmsl(document: dict) -> dict:
    """Normalize a TMSL document to ``{tables, measures, relationships}``.

    Accepts either the full ``{"model": {...}}`` envelope or a bare model object.
    """
    if not isinstance(document, dict):
        return {"tables": [], "measures": [], "relationships": []}

    model = document.get("model") if isinstance(document.get("model"), dict) else document
    tables = model.get("tables") or []

    table_names: list[str] = []
    measures: list[dict] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = table.get("name", "")
        if _is_auto_date_table(table_name):
            continue  # skip Power BI auto date/time hidden tables
        table_names.append(table_name)
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

    return {"tables": table_names, "measures": measures, "relationships": relationships, "roles": roles}
