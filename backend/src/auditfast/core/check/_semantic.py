"""Shared helpers for semantic-model (TMSL) checks.

Underscore-prefixed so the package auto-loader skips it: helpers only, no checks.
"""
from __future__ import annotations

import re

from ._tables import is_key_column, name_words

#: TMSL spells a denial as ``metadataPermission: "none"``; anything else grants.
_DENIED = "none"


def restricts_objects(model: dict) -> bool:
    """True when a role hides a column or a whole table — object-level security.

    Both shapes count: a ``columnPermissions`` entry set to ``none`` (classic OLS)
    and a ``tablePermissions`` entry whose own ``metadataPermission`` is ``none``
    (the whole table hidden). A permission that *grants* access is not a
    restriction and must not be read as one.
    """
    for role in model.get("roles") or []:
        for table in role.get("table_permissions") or []:
            if _denied(table.get("metadata_permission")):
                return True
            for column in table.get("column_permissions") or []:
                if _denied(column.get("permission")):
                    return True
    return False


def _denied(permission: object) -> bool:
    return str(permission or "").strip().lower() == _DENIED


def rls_roles(model: dict) -> tuple[int, int]:
    """``(roles carrying an RLS filter, roles defined)`` for one model.

    A role with table permissions but no ``filterExpression`` restricts no rows,
    so it is defined but not actually filtering.
    """
    defined = filtering = 0
    for role in model.get("roles") or []:
        defined += 1
        if any((tp.get("filter") or "").strip()
               for tp in role.get("table_permissions") or []):
            filtering += 1
    return filtering, defined


def hidden_columns(model: dict) -> set[str]:
    """Lower-cased column names a role hides via OLS."""
    hidden: set[str] = set()
    for role in model.get("roles") or []:
        for table in role.get("table_permissions") or []:
            for column in table.get("column_permissions") or []:
                if _denied(column.get("permission")):
                    hidden.add(str(column.get("column") or "").lower())
    return hidden


# -- column *shape* (a proxy for cardinality, never a measurement) -------------
#
# True cardinality is the number of DISTINCT VALUES in a column, and that can
# only be known by reading the rows. Rows are deliberately never fetched, so
# nothing here measures cardinality. What the TMSL *does* state is each column's
# declared type and name, and some shapes are one-value-per-row (or close to it)
# by construction regardless of the data behind them. Those shapes are what the
# helpers below find, and every caller must say so in its evidence.

#: A SQL source type that is a GUID. TMSL's own type system has no
#: ``uniqueidentifier``, so a GUID column arrives as ``string``; the source
#: provider type is the only place the distinction survives.
_GUID_SOURCE_TYPE = re.compile(r"uniqueidentifier|\bguid\b", re.IGNORECASE)

#: Words that name a column as a GUID even when no source type was recorded.
_GUID_WORDS: frozenset[str] = frozenset({"guid", "uuid", "rowguid", "uniqueidentifier"})

#: A SQL source type with no length bound — a free-text blob, not a category.
_LARGE_TEXT_SOURCE_TYPE = re.compile(
    r"(?:var)?char\s*\(\s*max\s*\)|\bn?text\b|\bclob\b|\bxml\b|\bjson\b",
    re.IGNORECASE,
)

#: Words that name a column as free text rather than a category a user slices by.
#: Deliberately narrow: ``name``, ``city`` and ``status`` are *not* here — they
#: are legitimate low-cardinality attributes and flagging them would turn this
#: into a check every model fails.
_FREE_TEXT_WORDS: frozenset[str] = frozenset({
    "description", "descriptions", "desc", "comment", "comments", "note", "notes",
    "remark", "remarks", "message", "text", "body", "detail", "details",
    "address", "address1", "address2", "street", "email", "url", "uri", "link",
    "json", "payload", "xml", "reason", "feedback", "summary",
})

#: Words that mean a temporal column carries a *time of day*, not just a date.
#: A column at second precision has roughly one distinct value per row, where the
#: same column split into a date key plus a time key has 365-ish and 86400-ish.
_TIME_PRECISION_WORDS: frozenset[str] = frozenset({
    "timestamp", "datetime", "time", "ts", "at", "on",
})
#: …and the words that mean it is a plain date, which is fine as it stands.
_DATE_ONLY_WORDS: frozenset[str] = frozenset({"date", "day", "dt"})

#: SQL source types that carry a time of day. ``date`` deliberately absent.
_TIME_SOURCE_TYPE = re.compile(
    r"datetime2?|smalldatetime|datetimeoffset|timestamp|\btime\b", re.IGNORECASE
)


def column_type_of(column: dict) -> str:
    return str(column.get("data_type") or "").strip().lower()


def source_type_of(column: dict) -> str:
    return str(column.get("source_provider_type") or "").strip()


def relationship_columns(model: dict) -> set[tuple[str, str]]:
    """``(table, column)`` pairs, lower-cased, that a relationship binds.

    A GUID or identity column on either end of a relationship is *load-bearing*:
    the model cannot join without it, so its shape is a cost the design requires
    rather than a column imported by accident. Callers exempt these.
    """
    pairs: set[tuple[str, str]] = set()
    for rel in model.get("relationships") or []:
        for table_key, column_key in (("from_table", "from_column"), ("to_table", "to_column")):
            table = str(rel.get(table_key) or "").strip().lower()
            column = str(rel.get(column_key) or "").strip().lower()
            if table and column:
                pairs.add((table, column))
    return pairs


def high_cardinality_shape(column: dict) -> str:
    """Why this column's *declared shape* is inherently high-cardinality, or ``""``.

    Returns a short human-readable reason so the evidence can name the defect.
    An empty string means "nothing in the declaration says this is
    high-cardinality" — which is **not** the same as "this column is
    low-cardinality". Only the rows could say that, and they are never read.
    """
    name = str(column.get("name") or "")
    words = name_words(name)
    data_type = column_type_of(column)
    source_type = source_type_of(column)

    if _GUID_SOURCE_TYPE.search(source_type) or (words & _GUID_WORDS):
        return "GUID"

    if data_type in {"datetime", "date"} or _TIME_SOURCE_TYPE.search(source_type):
        # Only flag when the column really carries a time of day. A source type
        # of plain ``date`` is authoritative that it does not; otherwise fall
        # back to the name, and treat an ambiguous name as a date (the lenient
        # reading), so a column called ``order_date`` is never flagged.
        timed = bool(_TIME_SOURCE_TYPE.search(source_type))
        if not source_type:
            timed = bool(words & _TIME_PRECISION_WORDS) and not (words & _DATE_ONLY_WORDS)
        if timed:
            return "full-precision datetime"

    if (data_type == "string" or source_type) and (
        _LARGE_TEXT_SOURCE_TYPE.search(source_type) or (words & _FREE_TEXT_WORDS)
    ):
        return "free text"

    return ""


def is_row_identifier(column: dict) -> bool:
    """True when the column is a per-row identifier by declaration or by name.

    ``isKey`` is the model saying so outright; the name test reuses the table
    vocabulary (``customer_sk``, ``OrderID``) so an identity column imported
    without being marked a key is still recognised.
    """
    return bool(column.get("is_key")) or is_key_column(str(column.get("name") or ""))
