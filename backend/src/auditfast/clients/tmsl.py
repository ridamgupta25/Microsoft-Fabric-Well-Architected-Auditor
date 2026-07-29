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

from typing import Any


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
        relationships.append({
            "name": rel.get("name", ""),
            "from_table": rel.get("fromTable", ""),
            "from_column": rel.get("fromColumn", ""),
            "to_table": rel.get("toTable", ""),
            "to_column": rel.get("toColumn", ""),
            "cross_filter": rel.get("crossFilteringBehavior", "") or "",
            "is_active": bool(rel.get("isActive", True)),
        })

    return {"tables": table_names, "measures": measures, "relationships": relationships}
