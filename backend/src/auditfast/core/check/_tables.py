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


def store_of(table: dict) -> str:
    """Display name of the Lakehouse/Warehouse holding ``table`` ("" when unknown).

    Populated from the SQL analytics endpoint the columns were read through, so it
    costs no extra call. Empty for a table the REST listing returned but whose
    endpoint could not be read — treat that as *unknown*, never as a mismatch.
    """
    return str(table.get("store") or "")


def store_kind_of(table: dict) -> str:
    """``"Warehouse"`` / ``"Lakehouse"`` for the store holding ``table`` ("" if unknown)."""
    return str(table.get("store_kind") or "")


def in_warehouse(table: dict) -> bool:
    """True only when the table is *known* to live in a Warehouse."""
    return store_kind_of(table) == "Warehouse"


def tables_by_store(tables: dict[str, dict]) -> dict[str, dict[str, dict]]:
    """Group ``{table: schema}`` by owning store, dropping tables of unknown origin.

    The workspace exposes tables in one flat dict, so any check that reasons about
    *which* store holds a table — audit tables separated from business data,
    conformed dimensions not duplicated per domain — needs this regrouping.
    """
    grouped: dict[str, dict[str, dict]] = {}
    for name, table in tables.items():
        store = store_of(table)
        if store:
            grouped.setdefault(store, {})[name] = table
    return grouped


def has_audit_column(table: dict) -> bool:
    """True when any column of ``table`` is an audit/lineage column."""
    return any(is_audit_column(c.get("name") or "") for c in columns(table))


def is_snake_case(name: str) -> bool:
    return bool(_SNAKE.match(name or ""))


# -- name vocabulary ---------------------------------------------------------
#
# Several checks have to ask "what *kind* of table is this?" (audit, config) and
# "what purpose does this name convey?" (conformed dimensions). Both questions
# are answered from words, so the splitting rule lives here once: split on
# separators *and* camelCase boundaries, so ``audit_log``, ``AuditLog`` and
# ``AUDIT-LOG`` all yield ``{"audit", "log"}``. A name written with no boundary
# at all (``auditlog``) yields one word, which is why the vocabularies below
# also carry the common glued spellings.

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def name_words(name: str) -> frozenset[str]:
    """Lower-cased words of a table/column name, split on separators and camelCase."""
    words: list[str] = []
    for chunk in _WORD_SPLIT.split(name or ""):
        if chunk:
            words.extend(part.lower() for part in _CAMEL_SPLIT.split(chunk) if part)
    return frozenset(words)


#: Words that mark a table as audit / logging / data-quality rather than business
#: data. Glued spellings (``auditlog``) are listed because a name written without
#: any separator cannot be split into words.
AUDIT_TABLE_WORDS: frozenset[str] = frozenset({
    "audit", "audits", "auditing", "log", "logs", "logging",
    "dq", "quality", "exception", "exceptions", "error", "errors",
    "reject", "rejects", "rejected", "quarantine", "deadletter",
    "validation", "recon", "reconciliation",
    "auditlog", "auditlogs", "audittable", "errorlog", "runlog", "joblog",
    "loadlog", "dqlog", "dqresult", "dqresults", "dataquality",
})

#: Words that mark a table as ingestion/orchestration *configuration* rather than
#: data. ``metadata`` is included because the point calls the store a Metadata DB.
CONFIG_TABLE_WORDS: frozenset[str] = frozenset({
    "config", "configs", "configuration", "configurations",
    "param", "params", "parameter", "parameters",
    "setting", "settings", "control", "metadata", "watermark", "watermarks",
    "job", "jobs", "schedule", "schedules", "scheduling",
    "controltable", "configtable", "jobconfig", "jobcontrol", "metadatatable",
})

#: Tokens that describe the *container, tier or version* of a table rather than
#: the business purpose it serves. Stripped by :func:`purpose_tokens` so
#: ``DimCustomer``, ``dim_customer_v2`` and ``DEV_dim_customer`` collapse onto the
#: same purpose. Mirrors the idea used by ``WS-SINGLE-SOURCE`` (ref 1.1.8) for
#: *store* names; kept here rather than imported so no check package depends on
#: another pillar's package.
TABLE_NOISE_TOKENS: frozenset[str] = frozenset({
    "dim", "dims", "dimension", "dimensions", "d",
    "fact", "facts", "fct", "f",
    "tbl", "table", "t", "vw", "view",
    "stg", "stage", "staging", "tmp", "temp", "wrk", "work",
    "hist", "history", "historical", "snapshot", "snap", "current", "cur",
    "copy", "clone", "bak", "backup", "old", "new", "final", "draft",
    "archive", "archived", "v", "ver", "version",
    "master", "mstr", "ref", "reference", "lookup", "lkp",
    "dev", "test", "tst", "qa", "uat", "sit", "prod", "prd", "production",
    "preprod", "gold", "silver", "bronze", "curated", "raw",
    "dbo", "edw", "dw",
})

#: ``v2`` / ``V02`` / a bare ``2`` — a version or copy marker, never a purpose.
_VERSION_TOKEN = re.compile(r"^v?\d+$")


def purpose_tokens(name: str) -> tuple[str, ...]:
    """The business purpose a table name conveys, with container/tier/version noise removed.

    Two tables that reduce to the same tuple describe the same thing under two
    names. Returns an empty tuple when nothing but noise is left — in which case
    the name says too little to compare and the caller must exclude it rather
    than guess.
    """
    return tuple(sorted(
        word for word in name_words(name)
        if word not in TABLE_NOISE_TOKENS and not _VERSION_TOKEN.match(word)
    ))


def is_audit_table_name(name: str) -> bool:
    """True when the table *name* marks it as an audit / log / DQ / exception table."""
    return not is_platform_table(name) and bool(name_words(name) & AUDIT_TABLE_WORDS)


#: Tables the platform creates for itself. They satisfy audit/log vocabulary by
#: construction — ``managed_delta_table_log_files`` carries ``rows_inserted`` and
#: ``commit_time`` in every lakehouse ever created — so crediting them turns a
#: check about a *deliberate* audit practice into one that passes everywhere.
#: ``msdyn_``/``adx_`` are Dynamics and Power Pages system tables; ``dm_``/``sys``
#: are SQL dynamic-management views.
_PLATFORM_TABLE_PREFIXES: tuple[str, ...] = (
    "managed_delta_table_", "dm_db_", "dm_exec_", "dm_", "sys_", "sys.",
    "msdyn_", "adx_", "mspp_", "powerpages", "flowsession", "workflowlog",
)
#: Exact platform table names that carry no distinguishing prefix. The
#: ``queryinsights`` group is Fabric's own SQL-endpoint telemetry: every
#: Lakehouse and Warehouse exposes it, nobody models it, and it satisfies audit
#: vocabulary by construction (``last_run_start_time``, ``rows_inserted``).
_PLATFORM_TABLE_NAMES: frozenset[str] = frozenset({
    "syncerror", "tracelog", "plugintracelog", "ontology",
    "asyncoperation", "systemuser", "audit", "auditbase",
    # queryinsights - Fabric SQL analytics endpoint telemetry views.
    "exec_requests_history", "exec_sessions_history", "frequently_run_queries",
    "long_running_queries", "managed_delta_tables", "sql_pool_insights",
    "sql_query_insights", "query_insights",
})


def is_platform_table(name: str) -> bool:
    """True for a table Fabric, SQL, or Dynamics creates rather than the solution.

    Judged on the *name* alone, so it is stable across workspaces. Used to keep
    platform bookkeeping out of checks that ask whether the team built an audit
    or configuration practice of its own.
    """
    text = (name or "").strip().lower()
    if not text:
        return False
    leaf = text.split(".")[-1]
    return leaf in _PLATFORM_TABLE_NAMES or leaf.startswith(_PLATFORM_TABLE_PREFIXES)


def is_config_table_name(name: str) -> bool:
    """True when the table *name* marks it as ingestion/orchestration configuration."""
    return not is_platform_table(name) and bool(name_words(name) & CONFIG_TABLE_WORDS)


def audit_share(table: dict) -> float:
    """Fraction of ``table``'s columns that are audit/lineage columns (0.0 when unread)."""
    cols = columns(table)
    if not cols:
        return 0.0
    return sum(1 for c in cols if is_audit_column(c.get("name") or "")) / len(cols)


def is_audit_table(name: str, table: dict, *, dominance: float = 0.6) -> bool:
    """True when a table is audit-shaped, by name or by column make-up.

    The column route needs a *dominant* majority of audit/lineage columns — a
    business table with two lineage columns is still a business table. Platform
    tables are excluded on both routes: a Dynamics system table is mostly
    ``createdon``/``modifiedby`` columns and would otherwise qualify on shape
    alone.
    """
    if is_platform_table(name):
        return False
    if is_audit_table_name(name):
        return True
    return len(columns(table)) >= 2 and audit_share(table) >= dominance


# -- column vocabulary -------------------------------------------------------

#: Words that make a column a key/identifier rather than a descriptive attribute.
_KEY_WORDS: frozenset[str] = frozenset({
    "id", "ids", "key", "keys", "sk", "fk", "pk", "guid", "uuid", "no", "num",
    "number", "code", "identifier",
})

#: Names that *end* in a key-looking string without being keys. Checked against
#: the normalised name so ``valid`` cannot read as ``...id``.
_NOT_KEYS: frozenset[str] = frozenset({
    "valid", "invalid", "paid", "unpaid", "void", "avoid", "grid", "hybrid",
    "rapid", "solid", "acid", "said", "raid", "bid", "mid", "kid", "lid", "aid",
})

_GLUED_KEY = re.compile(r"[a-z0-9]{2,}(?:id|key|sk|fk|pk|guid|uuid)$")


def is_key_column(name: str) -> bool:
    """True when a column names a key/identifier (``customer_sk``, ``OrderID``).

    Deliberately generous: over-counting a column as a key only makes the callers
    (fact-table attribute purity, snowflake detection) *more* lenient, never
    more accusatory. A short blocklist stops words like ``valid`` reading as an
    ``...id`` key.
    """
    normalised = normalise_column(name)
    if not normalised or normalised in _NOT_KEYS:
        return False
    if name_words(name) & _KEY_WORDS:
        return True
    return bool(_GLUED_KEY.match(normalised))


def key_referent(name: str) -> tuple[str, ...]:
    """What a key column points at, as a purpose tuple (``customer_sk`` -> ``("customer",)``).

    Strips the key word itself (``sk``/``id``/``key``…) — including the glued
    spelling ``categoryid`` — and then the same container/tier/version noise
    :func:`purpose_tokens` removes, so the result can be compared directly
    against a table's purpose. Empty when nothing identifiable is left.
    """
    if not is_key_column(name):
        return ()
    return tuple(sorted(
        word for word in (_peel_key_suffix(w) for w in name_words(name) if w not in _KEY_WORDS)
        if word and word not in TABLE_NOISE_TOKENS and not _VERSION_TOKEN.match(word)
    ))


def _peel_key_suffix(word: str) -> str:
    """``categoryid`` -> ``category``. A word with no glued key suffix is unchanged."""
    if not _GLUED_KEY.match(word):
        return word
    # Longest suffixes first, so ``customerguid`` does not peel to ``customerg``.
    for suffix in ("guid", "uuid", "key", "sk", "fk", "pk", "id"):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)]
    return word


#: SQL/Delta types that carry free text rather than a measure.
_TEXT_TYPES = ("string", "varchar", "nvarchar", "char", "nchar", "text", "ntext")

#: Types with no internal structure a query can filter or aggregate on.
_BLOB_TYPES = ("binary", "varbinary", "blob", "json", "variant", "object", "struct", "map", "array")

#: Types that carry a point in time.
_TEMPORAL_TYPES = ("timestamp", "datetime", "smalldatetime", "datetimeoffset", "date", "time")

#: Words that name a column as a point in time even when the type is unreadable.
_TIME_WORDS: frozenset[str] = frozenset({
    "date", "datetime", "timestamp", "time", "ts", "dt", "when",
})


def column_type(col: dict) -> str:
    return (col.get("type") or "").strip().lower()


def is_text_column(col: dict) -> bool:
    """True when the column's declared type carries free text."""
    return column_type(col).startswith(_TEXT_TYPES)


def is_blob_column(col: dict) -> bool:
    """True when the column holds an opaque payload (json/binary/struct) a query cannot filter."""
    return column_type(col).startswith(_BLOB_TYPES)


def is_timestamp_column(col: dict) -> bool:
    """True when a column records a point in time, by declared type or by name."""
    if column_type(col).startswith(_TEMPORAL_TYPES):
        return True
    return bool(name_words(col.get("name") or "") & _TIME_WORDS)


def has_timestamp_column(table: dict) -> bool:
    return any(is_timestamp_column(c) for c in columns(table))


def is_dimension(name: str) -> bool:
    return (name or "").lower().startswith("dim")


def is_fact(name: str) -> bool:
    return (name or "").lower().startswith(("fact", "fct"))


#: Trailing words that mark a column as a *key*. Matched against the final word of
#: the split name, so ``customer_sk``, ``CustomerKey`` and ``CUSTOMER_ID`` are one
#: thing while ``monkey`` and ``turkey`` (which split to a single word) are not.
#: Trailing words that mark a column as a *surrogate* key. Deliberately narrower
#: than :data:`_KEY_WORDS` above (which is the generous "is this any kind of
#: identifier?" vocabulary used by fact-purity and snowflake detection): a
#: surrogate key is spelled ``sk``, ``key`` or ``id``, never ``number``/``code``.
#: Named distinctly so it cannot shadow the broader set.
_SURROGATE_KEY_WORDS: frozenset[str] = frozenset({"sk", "key", "id"})

#: Words marking a key as the *business/natural* key rather than the surrogate.
#: Verified against Microsoft's AdventureWorksDW script, where ``CustomerKey`` is
#: an ``IDENTITY(1,1)`` surrogate and ``CustomerAlternateKey`` is the source
#: identifier - so an alternate/natural/business key must never satisfy 4.5.6.
_NATURAL_KEY_WORDS: frozenset[str] = frozenset({
    "alternate", "alt", "natural", "business", "source", "src", "nk", "bk",
})


def _ordered_words(name: str) -> list[str]:
    """Lower-cased words of a name, in order (:func:`name_words` is unordered)."""
    words: list[str] = []
    for chunk in _WORD_SPLIT.split(name or ""):
        if chunk:
            words.extend(part.lower() for part in _CAMEL_SPLIT.split(chunk) if part)
    return words


def is_surrogate_key_column(name: str) -> bool:
    """True when ``name`` reads as a *surrogate* key column.

    Naming is an implementation convention, not a standard: Microsoft's own
    material shows both spellings - ``Salesperson_SK`` in the Fabric
    dimensional-modelling guidance, and ``CustomerKey`` / ``ProductKey`` /
    ``DateKey`` (all ``IDENTITY(1,1)`` columns) in AdventureWorksDW. Matching
    only the underscored form reported ``0 of 19`` on an estate where more than
    half the dimensions were correctly modelled, so both are accepted.

    Word splitting is what keeps this safe: ``monkey`` and ``turkey`` are single
    words, never ``…|key``, so they cannot match. An *alternate* key is excluded
    because AdventureWorks uses ``CustomerAlternateKey`` for the business key -
    the very distinction this check exists to draw.

    **What it cannot determine:** whether the column is genuinely system
    generated. That is a load-time property, not visible in a schema, so a
    business key named ``customer_key`` would pass.
    """
    words = _ordered_words(name)
    if not words:
        return False
    if set(words) & _NATURAL_KEY_WORDS:
        return False
    # The *last* word must be the key token: ``customer_key`` yes, ``key_lookup`` no.
    return words[-1] in _SURROGATE_KEY_WORDS


def has_surrogate_key(table: dict) -> bool:
    """True when the table declares at least one surrogate-key column."""
    return any(is_surrogate_key_column(c.get("name") or "") for c in columns(table))
