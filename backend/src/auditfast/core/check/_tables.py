"""Shared helpers for lakehouse/warehouse table-metadata checks.

Underscore-prefixed so the package auto-loader skips it: helpers only, no checks.
A ``table`` here is ``{"type": "Managed"|"External", "format": "Delta"|..., "columns":
[{"name": ..., "type": ...}]}``; the workspace exposes them as ``tables`` keyed by
table name.
"""
from __future__ import annotations

import re

from auditfast.core.enums import Layer

#: Layers whose workspaces are expected to hold analytical tables.
TABLE_LAYERS = (Layer.STORAGE, Layer.MIXED)

_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")

#: Column names that count as audit/lineage metadata.
AUDIT_COLUMNS = (
    "created_date", "created_at", "modified_date", "modified_at", "updated_date",
    "source_system", "batch_id", "load_date",
)


def columns(table: dict) -> list[dict]:
    return table.get("columns") or []


def col_names(table: dict) -> list[str]:
    return [(c.get("name") or "").lower() for c in columns(table)]


def is_snake_case(name: str) -> bool:
    return bool(_SNAKE.match(name or ""))


def is_dimension(name: str) -> bool:
    return (name or "").lower().startswith("dim")


def is_fact(name: str) -> bool:
    return (name or "").lower().startswith(("fact", "fct"))
