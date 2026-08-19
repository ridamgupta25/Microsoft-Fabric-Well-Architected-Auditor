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
    "long_running_queries", "managed_delta_tables", "external_delta_tables",
    "sql_pool_insights", "sql_query_insights", "query_insights",
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

#: Numeric types - the measures a fact table aggregates.
_NUMERIC_TYPES = (
    "int", "bigint", "smallint", "tinyint", "decimal", "numeric", "money",
    "smallmoney", "float", "real", "double", "long", "short", "byte",
)

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


def is_numeric_column(col: dict) -> bool:
    """True when the column's declared type is a number - a candidate measure."""
    return column_type(col).startswith(_NUMERIC_TYPES)


def is_timestamp_column(col: dict) -> bool:
    """True when a column records a point in time, by declared type or by name."""
    if column_type(col).startswith(_TEMPORAL_TYPES):
        return True
    return bool(name_words(col.get("name") or "") & _TIME_WORDS)


def has_timestamp_column(table: dict) -> bool:
    return any(is_timestamp_column(c) for c in columns(table))


def is_dimension(name: str) -> bool:
    """Name-only dimension test. Prefer :func:`table_role` - see its docstring."""
    return (name or "").lower().startswith("dim")


def is_fact(name: str) -> bool:
    """Name-only fact test. Prefer :func:`table_role` - see its docstring."""
    return (name or "").lower().startswith(("fact", "fct"))


#: A fact must carry at least this many key columns. One key is a lookup table
#: pointing at its parent; a fact sits at the centre of several dimensions.
_FACT_MIN_KEYS = 3

#: Above this share of key/measure columns a table reads as a fact rather than a
#: dimension. Dimensions are mostly descriptive attributes; facts are keys and
#: numbers. Deliberately high: a misread here is worse than an unknown.
_FACT_KEY_SHARE = 0.6

#: A fact must also carry real *measures* - the numbers it exists to aggregate.
#: Without this, a raw landing table of identifiers and codes reads as a fact:
#: measured on a Bronze ``_TMLC`` estate, 383 of 863 tables were classified fact,
#: which is not a believable star schema and quietly poisoned every downstream
#: dimensional verdict. A table of keys alone is a bridge, a log, or staging.
_FACT_MIN_MEASURES = 2

#: Above this width a table is not a modelled fact. A fact is narrow by
#: construction - foreign keys plus the handful of measures it aggregates. A
#: 125-column table is a source-system extract or a wide report table, and its
#: "measures" are usually numeric *attributes* (lead time, safety stock, minimum
#: order quantity) that this code cannot distinguish from additive facts. Two
#: such ERP masters survived every other guard on a real estate; nothing about
#: their schema says fact except that the source system had many numbers.
_FACT_MAX_COLUMNS = 60

#: Store-name words that mark a raw/landing layer. Shape is *not* inferred there
#: - see :func:`table_role`. Kept in step with ``_notebook._LAYER_WORDS`` but
#: local, because ``_tables`` must not import a notebook helper.
_RAW_STORE_WORDS: frozenset[str] = frozenset({
    "bronze", "raw", "landing", "land", "staging", "stage", "stg",
    "ingest", "ingestion", "inbound", "source", "src", "l0",
})

_STORE_TOKENS = re.compile(r"[^A-Za-z0-9]+")


def in_raw_store(table: dict) -> bool:
    """True when the table's owning store names itself a raw/landing layer.

    ``LH_Bronze``, ``staging_lh``, ``raw-landing`` all match; an unknown or
    unnamed store does not, so this only ever *adds* caution where there is
    positive evidence of a raw layer.
    """
    store = store_of(table)
    if not store:
        return False
    tokens = {t.lower() for t in _STORE_TOKENS.split(store) if t}
    return bool(tokens & _RAW_STORE_WORDS)


def _referenced_names(tables: dict[str, dict] | None) -> frozenset[str]:
    """Every table named as an FK target by some *other* table.

    Built once per call rather than re-scanned per table: the naive form made
    :func:`table_role` O(n) in the table count, :func:`facts_in` O(n^2), and a
    check calling ``facts_in`` inside a per-table loop O(n^3). On a 1,845-table
    estate that is billions of comparisons - the audit simply stopped.
    """
    if not tables:
        return frozenset()
    out: set[str] = set()
    for other in tables.values():
        out.update(str(r) for r in ((other or {}).get("references") or ()))
    return frozenset(out)


#: Table names normalised for cross-source matching. A semantic model names a
#: table as the modeller typed it ("Sales Order"), the SQL endpoint as the store
#: holds it ("sales_order", or "dbo.sales_order"), so the two only meet once
#: separators and the schema prefix are normalised away.
def normalise_table_name(name: str) -> str:
    """A table name reduced to a form comparable across sources."""
    text = (name or "").strip().lower()
    text = text.split(".")[-1]                      # drop a schema/db prefix
    return re.sub(r"[\s\-_]+", "", text)


def related_columns(
    semantic_models: dict[str, dict] | None,
) -> set[tuple[str, str]]:
    """``{(normalised table, normalised column)}`` for every column in a relationship.

    **Why this matters.** A relationship is the modeller *stating* that this
    column resolves to that table. Where one exists, a check never has to guess
    from the column's name whether it points at a dimension - the estate has
    already said so.

    Both ends are returned. The "many" end is the fact's foreign key (the column
    a degenerate-dimension test asks about); the "one" end is the dimension's own
    key. Either way the column participates in a declared join, which is the fact
    a caller needs.

    Inactive relationships are included deliberately: a column reachable only via
    ``USERELATIONSHIP`` is still modelled, not orphaned.

    Empty when no model is readable - the normal case for a Bronze workspace.
    Callers must read that as "no declarative evidence", never "no relationships
    exist", and fall back to name-based evidence rather than reporting a finding.
    """
    if not semantic_models:
        return set()
    pairs: set[tuple[str, str]] = set()
    for model in semantic_models.values():
        if not isinstance(model, dict):
            continue
        for rel in model.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            for table_key, column_key in (("from_table", "from_column"),
                                          ("to_table", "to_column")):
                table = normalise_table_name(str(rel.get(table_key) or ""))
                column = normalise_column(str(rel.get(column_key) or ""))
                if table and column:
                    pairs.add((table, column))
    return pairs


#: Declared types whose value set is inherently small - the columns a junk
#: dimension exists to collapse. A ``bit`` holds two values; a ``varchar(500)``
#: holds free text and is no junk-dimension candidate whatever it is called.
_LOW_CARDINALITY_TYPES = (
    "bit", "boolean", "bool", "tinyint", "byte",
)

#: Character width above which a column reads as prose rather than a coded
#: value. ``order_status varchar(10)`` and ``payment_type varchar(20)`` are the
#: classic junk-dimension inputs; ``rejection_reason varchar(500)`` is a
#: comment field. The line is drawn generously - this check only *names
#: candidates* for a reviewer, so excluding a real one costs more than
#: including a doubtful one.
_MAX_CODED_VALUE_WIDTH = 50


def is_low_cardinality_shape(col: dict) -> bool:
    """True when a column's *declared type* suggests a small set of values.

    A shape proxy, not a measured distinct count - no rows are ever read.
    Booleans and narrow character types qualify; wide character types do not,
    because a 500-character column holds prose and collapsing prose into a junk
    dimension buys nothing.

    Used to stop a name pattern alone deciding that ``rejection_reason
    varchar(500)`` is a junk-dimension candidate: the name matches, the shape
    says otherwise.
    """
    declared = column_type(col)
    if not declared:
        return False
    if declared.startswith(_LOW_CARDINALITY_TYPES):
        return True
    if declared.startswith(_TEXT_TYPES):
        width = re.search(r"\((\d+)\)", declared)
        # An unbounded string (`varchar`, `string`, `varchar(max)`) states no
        # width, so it cannot be shown to be narrow.
        return bool(width) and int(width.group(1)) <= _MAX_CODED_VALUE_WIDTH
    # Small integers and enumerated numeric codes are already covered above;
    # anything else (dates, decimals, blobs) is not a junk-dimension input.
    return False


#: ``dataCategory`` values that name a table as descriptive reference data.
#: From the TMSL table object: 1-TIME, 6-ACCOUNTS, 7-CUSTOMERS, 8-PRODUCTS,
#: 15-ORGANIZATION, 17-GEOGRAPHY. Power BI sets "Time" automatically when a
#: table is marked as the date table, which makes it the one commonly present.
_DIMENSION_DATA_CATEGORIES: frozenset[str] = frozenset({
    "time", "accounts", "customers", "products", "organization", "geography",
    "scenario", "quantitative", "utility", "channel", "promotion",
})


def declared_roles(
    tables: dict[str, dict] | None,
    semantic_models: dict[str, dict] | None,
) -> dict[str, str]:
    """``{normalised table name: role}`` from what the estate *declares*.

    **Why this outranks every inference.** Microsoft's star-schema guidance is
    explicit that no property marks a table as a fact or a dimension - *"It's in
    fact determined by the model relationships"* - and that in a one-to-many
    relationship *"the 'one' side is always a dimension table while the 'many'
    side is always a fact table"*
    (``learn.microsoft.com/power-bi/guidance/star-schema``). A semantic-model
    relationship is therefore not a hint about the role: it *is* the role, stated
    by whoever built the model.

    Two declarative sources are read, and a table named by both must agree:

    * **Relationship cardinality.** ``from_table`` is the many side (fact),
      ``to_table`` the one side (dimension). Power BI's auto date/time tables are
      already dropped by the TMSL parser, so they cannot pollute this.
    * **``dataCategory``.** "Time", "Customers", "Products", "Geography" and
      friends are a modeller stating that a table is reference data.

    **A table appearing on both sides is left out.** A dimension that is itself a
    fact's target *and* references another dimension (a snowflake outrigger) is
    genuinely both, and guessing between them would be worse than saying nothing:
    it is excluded so the caller falls back to other evidence rather than
    inheriting a coin flip.

    Returns an empty mapping when no model is readable - which is the normal case
    for a Bronze/landing workspace, and the caller must treat it as "no
    declarative evidence", never as "no dimensional model exists".
    """
    if not tables or not semantic_models:
        return {}

    known = {normalise_table_name(name) for name in tables}
    fact_side: set[str] = set()
    dimension_side: set[str] = set()
    categorised: dict[str, str] = {}

    for model in semantic_models.values():
        if not isinstance(model, dict):
            continue
        for rel in model.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            # An inactive relationship is still a declared structural link; it is
            # inactive for filter propagation, not untrue.
            many = normalise_table_name(str(rel.get("from_table") or rel.get("fromTable") or ""))
            one = normalise_table_name(str(rel.get("to_table") or rel.get("toTable") or ""))
            if many and many in known:
                fact_side.add(many)
            if one and one in known:
                dimension_side.add(one)

        for table_name, category in (model.get("data_categories") or {}).items():
            key = normalise_table_name(str(table_name))
            if key in known and str(category).strip().lower() in _DIMENSION_DATA_CATEGORIES:
                categorised[key] = "dimension"

    roles: dict[str, str] = {}
    for name in dimension_side - fact_side:
        roles[name] = "dimension"
    for name in fact_side - dimension_side:
        roles[name] = "fact"
    # dataCategory only fills gaps: a relationship is the stronger statement.
    for name, role in categorised.items():
        roles.setdefault(name, role)
    return roles


def table_roles(
    tables: dict[str, dict],
    semantic_models: dict[str, dict] | None = None,
) -> dict[str, str]:
    """``{table name: role}`` for every table, computed in one pass.

    Prefer this over calling :func:`table_role` in a loop: it shares the
    foreign-key reverse index across every table instead of rebuilding it.

    ``semantic_models`` is optional and additive. When supplied, a role the model
    *declares* (see :func:`declared_roles`) wins over anything inferred from the
    table's own shape - so an estate that models its star schema in Power BI is
    judged on what it stated, and one that does not is unaffected.
    """
    referenced = _referenced_names(tables)
    declared = declared_roles(tables, semantic_models)
    roles: dict[str, str] = {}
    for name, table in tables.items():
        stated = declared.get(normalise_table_name(name))
        # Platform tables are never given a role, even if a model names them.
        if stated and not is_platform_table(name):
            roles[name] = stated
        else:
            roles[name] = _role(name, table, referenced)
    return roles


def table_role(name: str, table: dict | None = None,
               tables: dict[str, dict] | None = None) -> str:
    """``"fact"``, ``"dimension"`` or ``"unknown"`` for one table.

    **Why this exists.** The name-only :func:`is_fact`/:func:`is_dimension` pair
    answers only for estates that prefix ``dim_``/``fact_``. Measured against two
    real tenants, 47 of 1,845 tables carried such a name - so checks gated on
    them returned N/A on thousands of readable tables. That is not a false
    failure, but it is no coverage at all.

    **Evidence order**, strongest first:

    1. **Declared foreign keys.** A table other tables *reference* is a
       dimension; a table that references several others is a fact. This is
       structural, naming-independent, and read from ``sys.foreign_keys``.
    2. **Naming.** An explicit ``dim_``/``fact_`` prefix is the author stating
       the role outright. It ranks above column shape because shape is an
       inference about intent whereas the name *is* the intent - a wide fact
       with no key-shaped column names must not be re-labelled a dimension.
    3. **Column shape**, but only outside a raw/landing store, and only for a
       table narrow enough to be a model table. For estates that use no
       convention: a table with several keys *and* several numeric measures,
       dominated by the two, is fact-shaped; one with few keys and mostly
       descriptive attributes is dimension-shaped.
    4. **Unknown**, so a caller can return N/A rather than guess.

    **Shape is never inferred in a Bronze/raw store.** A landing zone holds
    source tables copied as-is, and an ERP master table is full of *numeric
    attributes* - lead time, safety stock, minimum order quantity - which this
    code cannot tell from additive measures. Measured on a real estate, that
    read 316 raw ERP extracts as fact tables and graded a landing zone against
    star-schema rules. Facts and dimensions are built downstream, so in a raw
    store only a declared constraint or an explicit ``fact_``/``dim_`` name -
    both statements of intent, not inferences - assigns a role.

    **Operational tables are never given a role.** An audit/run-log table and a
    config/control table are infrastructure, not part of the dimensional model:
    ``audit_table`` (run ids, timestamps, row counts) and ``control_table``
    (source/target names, watermarks) both read as dimensions on shape alone,
    and a "conformed dimension" finding about a watermark table is noise.

    **Platform tables are never given a role.** Fabric's own telemetry views
    (``queryinsights.exec_requests_history`` and friends) are key-and-measure
    shaped and would otherwise read as facts, putting system telemetry into a
    dimensional-model finding.

    ``table`` and ``tables`` are optional: with neither, this degrades to the
    naming test, which is what the old helpers did.
    """
    return _role(name, table, _referenced_names(tables))


def _role(name: str, table: dict | None, referenced: frozenset[str]) -> str:
    """:func:`table_role` with the FK reverse index passed in, not rebuilt."""
    if is_platform_table(name):
        return "unknown"
    meta = table or {}

    # 1. Declared constraints - the only evidence that is not an inference.
    references = len(meta.get("references") or ())
    if name in referenced and not references:
        return "dimension"
    if references >= 2:
        return "fact"

    # 2. A declared name beats an inferred shape.
    if is_fact(name):
        return "fact"
    if is_dimension(name):
        return "dimension"

    # 3. Column shape - never in a raw/landing store, where a table's shape is
    #    the source system's, not a modelling decision, and never for the
    #    operational tables that support the pipeline rather than the model.
    if in_raw_store(meta) or is_audit_table(name, meta) or is_config_table_name(name):
        return "unknown"
    cols = columns(meta)
    if 4 <= len(cols) <= _FACT_MAX_COLUMNS:
        keys = sum(1 for c in cols if is_key_column(c.get("name") or ""))
        measures = sum(1 for c in cols if is_numeric_column(c))
        descriptive = len(cols) - keys - measures
        if (keys >= _FACT_MIN_KEYS and measures >= _FACT_MIN_MEASURES
                and (keys + measures) / len(cols) >= _FACT_KEY_SHARE):
            return "fact"
        # A dimension describes one thing: it needs an identifier *and* real
        # descriptive columns. Without both this is some other kind of table -
        # a log, a config row, a staging buffer - and stays unknown.
        if 1 <= keys <= 2 and descriptive >= 2 and descriptive > measures:
            return "dimension"

    return "unknown"


def facts_in(tables: dict[str, dict],
             semantic_models: dict[str, dict] | None = None) -> dict[str, dict]:
    """Every fact-role table: declared by a model where possible, else inferred.

    ``semantic_models`` is optional so existing callers keep working; passing it
    lets a relationship the modeller declared outrank a shape guess.
    """
    roles = table_roles(tables, semantic_models)
    return {n: t for n, t in tables.items() if roles[n] == "fact"}


def dimensions_in(tables: dict[str, dict],
                  semantic_models: dict[str, dict] | None = None) -> dict[str, dict]:
    """Every dimension-role table: declared by a model where possible, else inferred."""
    roles = table_roles(tables, semantic_models)
    return {n: t for n, t in tables.items() if roles[n] == "dimension"}


def role_evidence(tables: dict[str, dict],
                  semantic_models: dict[str, dict] | None = None) -> dict[str, int]:
    """How many roles came from a declaration versus an inference.

    Lets a check say *how* it knows what it knows: a verdict over roles the
    estate declared is a different quality of finding from one over roles this
    code guessed, and the evidence should not pretend otherwise.
    """
    declared = declared_roles(tables, semantic_models)
    roles = table_roles(tables, semantic_models)
    modelled = sum(1 for n in tables
                   if normalise_table_name(n) in declared and not is_platform_table(n))
    assigned = sum(1 for r in roles.values() if r != "unknown")
    return {
        "declared": modelled,
        "inferred": max(0, assigned - modelled),
        "assigned": assigned,
        "total": len(tables),
    }


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
