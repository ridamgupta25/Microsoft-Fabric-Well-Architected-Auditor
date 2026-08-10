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

#: Column names that count as audit/lineage metadata. Kept as the canonical
#: spellings for documentation and tests; matching goes through
#: :func:`is_audit_column`, which also accepts the many real-world variants.
AUDIT_COLUMNS = (
    "created_date", "created_at", "modified_date", "modified_at", "updated_date",
    "source_system", "batch_id", "load_date",
)

#: The event an audit column records. ``process`` is deliberately absent: on a
#: real estate it matched ``processid``, ``processname``, ``processversion`` and
#: ``processmapversion`` — Dataverse business-process metadata, not lineage.
_AUDIT_EVENT = (
    r"creat(?:e|ed|ion)?|insert(?:ed)?|modif(?:y|ied)|updat(?:e|ed)|chang(?:e|ed)|"
    r"load(?:ed)?|ingest(?:ed|ion)?|extract(?:ed)?|refresh(?:ed)?"
)
#: What it records about that event — when it happened or who did it.
_AUDIT_FACET = r"date|datetime|timestamp|time|ts|dt|at|on|by|user"

#: An audit/lineage column, matched against the *normalised* name so that
#: ``CreatedDate``, ``created_date``, ``_CREATED_DT`` and ``createdDate`` are one
#: thing. Exact-tuple matching missed all but the first spelling, which reported
#: almost every real estate as having no audit columns at all.
_AUDIT_COLUMN_RE = re.compile(
    # <event><optional filler><facet> — createdDate, load_ts, createdOnBehalfBy.
    rf"^(?:\w{{0,6}}?)(?:{_AUDIT_EVENT})\w{{0,8}}?(?:{_AUDIT_FACET})$|"
    # The event alone, where the column name is the fact — last_modified, date_created.
    rf"^(?:last|date|dt|sys|row|rec|audit)?(?:{_AUDIT_EVENT})$|"
    # Batch / run identity. The prefix lets ``collection_batch_id`` and
    # ``root_batch_id`` count, which a leading-anchor-only pattern missed.
    r"^\w{0,12}?(?:etl|elt|dw|edw|audit)?(?:batch|run|job|execution)"
    r"(?:id|no|number|key)?$|"
    # Provenance: which system, file, or table the row came from. ``id``/``name``
    # are excluded — ``SourceName`` and ``SourceID`` are as often a business
    # attribute (lead source, order source) as a lineage one.
    r"^(?:data)?source(?:system|file|table|application|app)$|"
    r"^(?:src|source)(?:sys|system)$",
    re.IGNORECASE,
)


def normalise_column(name: str) -> str:
    """Lower-cased column name with separators removed, for spelling-insensitive tests."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def is_audit_column(name: str) -> bool:
    """True when a column records *how the row got here* rather than business data.

    Deliberately conservative about the event vocabulary: ``order_date``,
    ``birth_date``, ``start_date`` and ``due_date`` all name a business event and
    must not read as lineage metadata.
    """
    return bool(_AUDIT_COLUMN_RE.match(normalise_column(name)))


def columns(table: dict) -> list[dict]:
    return table.get("columns") or []


def col_names(table: dict) -> list[str]:
    return [(c.get("name") or "").lower() for c in columns(table)]


def has_audit_column(table: dict) -> bool:
    """True when any column of ``table`` is an audit/lineage column."""
    return any(is_audit_column(c.get("name") or "") for c in columns(table))


def is_snake_case(name: str) -> bool:
    return bool(_SNAKE.match(name or ""))


def is_dimension(name: str) -> bool:
    return (name or "").lower().startswith("dim")


def is_fact(name: str) -> bool:
    return (name or "").lower().startswith(("fact", "fct"))
