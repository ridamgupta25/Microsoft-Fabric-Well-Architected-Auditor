"""Shared helpers for semantic-model (TMSL) checks.

Underscore-prefixed so the package auto-loader skips it: helpers only, no checks.
"""
from __future__ import annotations

import re

from ._tables import name_words

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

    if _LARGE_TEXT_SOURCE_TYPE.search(source_type):
        # Only an *unbounded* source type (varchar(max), text, json, xml) is
        # flagged. A column is not judged free text on its name alone: a
        # ``Description`` mapped 1:1 to a low-cardinality key has that key's
        # cardinality, which the name cannot reveal and the rows are never read.
        return "free text"

    return ""


def is_row_identifier(column: dict) -> bool:
    """True when the column is a per-row identifier by declaration or by name.

    ``isKey`` is the model saying so outright. The name test is deliberately
    **narrower than** :func:`is_key_column`, which also counts ``code``, ``no``,
    ``num`` and ``number``: that breadth is right where over-counting only makes a
    check more lenient, but here it makes it more *accusatory*. ``PostalCode``,
    ``OrderNumber`` and ``ProductCode`` are business attributes a report author
    legitimately puts on a visual - flagging them as technical keys that must be
    hidden produced a finding against a correctly built model.

    What remains is the vocabulary of a *technical* key: an explicit surrogate
    (``_sk``), a warehouse key suffix (``CustomerKey``), a GUID, or a bare
    ``…Id``. Those are the columns a report consumer has no use for.
    """
    if column.get("is_key"):
        return True
    return _is_technical_key_name(str(column.get("name") or ""))


#: Trailing words that mark a *technical* key. Narrower than ``_KEY_WORDS`` in
#: ``_tables.py`` on purpose - see :func:`is_row_identifier`.
_TECHNICAL_KEY_WORDS: frozenset[str] = frozenset({
    "sk", "key", "id", "guid", "uuid", "pk", "fk",
})


def _is_technical_key_name(name: str) -> bool:
    """True when the *last word* of ``name`` is a technical-key token.

    Word-splitting is what makes this safe. ``CustomerKey`` and ``customer_key``
    both split to ``[customer, key]``; requiring the *trailing* word also keeps
    ``key_account_manager`` out.

    Glued lower-case spellings are real and common - ``customerid`` appears 73
    times and ``customerkey`` 294 times on the reference estate - so they are
    matched too. The cost is that ordinary words which merely *end* in a key
    token (``monkey``, ``turkey``) would qualify, which is why they are named in
    :data:`_KEY_LOOKALIKES`. A short blocklist is honest about being a list of
    exceptions; widening the pattern instead would silently drop real keys.
    """
    words = _ordered_name_words(name)
    if not words:
        return False
    if words[-1] in _TECHNICAL_KEY_WORDS:
        return True
    if len(words) != 1:
        return False
    word = words[0]
    return word not in _KEY_LOOKALIKES and bool(_GLUED_TECHNICAL_KEY.match(word))


#: A single all-lower-case word ending in a key token - ``customerid``,
#: ``orderguid``, ``customerkey``. Only reached for a name with no separator and
#: no camelCase boundary; anything else is answered by the word test above.
_GLUED_TECHNICAL_KEY = re.compile(r"^[a-z0-9]{3,}(?:sk|key|guid|uuid|id)$", re.IGNORECASE)

#: Ordinary words that end in a key token without being keys. Matched against the
#: whole single-word name, so ``monkey_id`` (two words) is unaffected.
_KEY_LOOKALIKES: frozenset[str] = frozenset({
    "monkey", "turkey", "donkey", "hockey", "jockey", "whiskey", "key",
    "valid", "invalid", "paid", "unpaid", "void", "avoid", "grid", "hybrid",
    "rapid", "solid", "acid", "said", "raid", "bid", "mid", "kid", "lid", "aid",
    "squid", "druid", "fluid", "humid", "timid", "vivid", "candid", "morbid",
})
_NAME_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_NAME_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _ordered_name_words(name: str) -> list[str]:
    """Lower-cased words of a column name, in order, split on separators and camelCase."""
    words: list[str] = []
    for chunk in _NAME_WORD_SPLIT.split(name or ""):
        if chunk:
            words.extend(part.lower() for part in _NAME_CAMEL_SPLIT.split(chunk) if part)
    return words
