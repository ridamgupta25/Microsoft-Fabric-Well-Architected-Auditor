"""Shared detectors for Spark / Delta notebook-code checks.

Underscore-prefixed so the package auto-loader (which imports every
``automated.py`` / ``manual.py``) skips it: it carries no checks, only the
pattern matchers the Delta/Spark performance checks import.

The patterns encode the Fabric Spark & Delta best practices from the vendored
``fabric-skills`` (spark-authoring, Delta optimization) as conservative,
read-only signals over a notebook's concatenated code cells. They are
deliberately permissive about *presence* and cautious about *absence*: a missing
signal usually yields N/A ("could not determine"), not a failure, because a
notebook that does not do Delta writes is simply out of scope for "OPTIMIZE after
writes", not non-compliant.
"""
from __future__ import annotations

import re

from auditfast.core.enums import Layer

#: Layers whose workspaces are expected to hold transformation notebooks.
NOTEBOOK_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)

# -- write / upsert shapes -----------------------------------------------------
WRITE = re.compile(
    r"\.write\b|\.writeStream\b|saveAsTable|insertInto|\bMERGE\s+INTO\b|"
    r"\bINSERT\s+INTO\b|\bINSERT\s+OVERWRITE\b|\bCREATE\s+TABLE\b|"
    r"\bCREATE\s+OR\s+REPLACE\s+TABLE\b",
    re.IGNORECASE,
)
MERGE = re.compile(r"\bMERGE\s+INTO\b|\.merge\s*\(", re.IGNORECASE)
SEQ_DELETE = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)
SEQ_INSERT = re.compile(r"\bINSERT\s+(?:INTO|OVERWRITE)\b|insertInto", re.IGNORECASE)

# -- Delta maintenance ---------------------------------------------------------
# The Delta ``OPTIMIZE`` SQL command (``OPTIMIZE <table>`` — a table token must
# follow the keyword) or the ``deltaTable.optimize()`` Python API. Requiring a
# table token after ``OPTIMIZE`` stops the English word "Optimize" in a string or
# comment (e.g. the MCEM level "Manage and Optimize") from matching.
OPTIMIZE = re.compile(r'\bOPTIMIZE\s+[`\w]|\.optimize\s*\(', re.IGNORECASE)
VACUUM = re.compile(r"\bVACUUM\b|\.vacuum\s*\(", re.IGNORECASE)
ZORDER = re.compile(r"ZORDER\s+BY|\.zorderBy\s*\(|executeZOrderBy", re.IGNORECASE)
VORDER = re.compile(
    r"v-?order|parquet\.vorder|spark\.sql\.parquet\.vorder", re.IGNORECASE
)
TBLPROPS = re.compile(
    r"TBLPROPERTIES|delta\.autoOptimize|delta\.autoCompact|optimizeWrite|"
    r"autoCompaction|tuneFileSizesForRewrites",
    re.IGNORECASE,
)
RETENTION = re.compile(
    r"logRetentionDuration|deletedFileRetentionDuration", re.IGNORECASE
)

# -- Spark tuning / hygiene ----------------------------------------------------
# Package specs from an install command, for SPARK-LIBPIN. Matches the pip/conda
# magics AND a bare ``pip``/``pip3`` install (which also covers ``python -m pip
# install``, whose "pip install …" substring the bare-pip branch catches).
PIP_INSTALL = re.compile(
    r"(?:%pip|!pip|%conda|!conda|(?<![.\w])pip3?)\s+install\s+([^\n#]+)",
    re.IGNORECASE,
)
# ANY inline dependency install — broader than PIP_INSTALL (which only yields
# parseable package specs for SPARK-LIBPIN). Also catches wheel-URL / VCS installs
# and bare-shell / ``subprocess`` / ``-m pip`` invocations that carry no package name.
INLINE_INSTALL = re.compile(
    r"(?:%pip|!pip|%conda|!conda)\s+install\b"
    r"|(?<![.\w])pip\s+install\b"
    r"|-m\s+pip\s+install\b"
    r"|(?:subprocess\.\w+|os\.system)\s*\([^)]*\bpip\b[^)]*\binstall\b",
    re.IGNORECASE,
)
SPARK_CONF = re.compile(
    r"spark\.conf\.set\s*\(|\.config\s*\(\s*[\"']spark\.|SparkConf\s*\(", re.IGNORECASE
)
SHUFFLE = re.compile(r"spark\.sql\.shuffle\.partitions", re.IGNORECASE)
CACHE = re.compile(r"\.cache\s*\(\s*\)|\.persist\s*\(", re.IGNORECASE)
UNPERSIST = re.compile(r"\.unpersist\s*\(", re.IGNORECASE)
REPARTITION = re.compile(r"\.repartition\s*\(|\.coalesce\s*\(", re.IGNORECASE)
SELECT_STAR = re.compile(r"select\s+\*|\.select\s*\(\s*[\"']\*[\"']\s*\)", re.IGNORECASE)
SELECT = re.compile(r"\bselect\b|\.select\s*\(", re.IGNORECASE)
WIDE_TRANSFORM = re.compile(r"\.join\s*\(|\bgroupBy\s*\(|\.groupby\s*\(|\bJOIN\b|\bGROUP\s+BY\b", re.IGNORECASE)
SPARK_RUNTIME = re.compile(r"Apache-Spark/(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)
PLAIN_SPARK_VERSION = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?:\.\d+)*(?![\d.])")
SQL_SELECT = re.compile(r"\bSELECT\b.*?(?=\bSELECT\b|;|$)", re.IGNORECASE | re.DOTALL)
SQL_FROM = re.compile(r"\bFROM\s+([`A-Za-z_][`\w.]*)", re.IGNORECASE)

# -- Predicate pushdown / shortcut reads (3.5.7) -------------------------------
# Detects reads from external file formats or OneLake shortcut paths that are
# candidates for predicate pushdown. Conservative: only flags explicit API calls
# and well-known path prefixes, never managed spark.table() reads.
EXTERNAL_READ = re.compile(
    r"spark\.read\.(parquet|load|format|json|csv|avro|orc)\s*\("
    r"|abfss://"            # ADLS Gen2 / OneLake shortcut target
    r"|\/Files\/"           # OneLake Files section (shortcut mount)
    r"|Files\.load\b",      # notebookutils shortcut read variant
    re.IGNORECASE,
)
# A filter/where predicate applied to the read — makes predicate pushdown possible.
PUSHDOWN_FILTER = re.compile(
    r"\.filter\s*\(|\.where\s*\(|(?<!\w)WHERE\b",
    re.IGNORECASE,
)

# A token that is not a package spec: a flag, a URL/path, a VCS/requirements ref.
_NON_PACKAGE = re.compile(r"^-|^git\+|^https?:|^/|\.txt$|^\.")
_PACKAGE = re.compile(r"^[A-Za-z0-9_.\-]+(\[[^\]]+\])?([=<>!~].+)?$")


def writes_delta(code: str) -> bool:
    """True when the notebook writes to a managed/Delta table."""
    return bool(WRITE.search(code))


def has_inline_install(code: str) -> bool:
    """True when the notebook installs any dependency inline (magic, shell, or
    programmatic) — including wheel-URL / VCS targets that carry no package name."""
    return bool(INLINE_INSTALL.search(code))


def pip_targets(code: str) -> list[str]:
    """Every package spec across pip/conda install magics (flags/URLs dropped)."""
    targets: list[str] = []
    for match in PIP_INSTALL.finditer(code):
        for token in match.group(1).split():
            token = token.strip().strip("\"'")
            if not token or _NON_PACKAGE.match(token) or not _PACKAGE.match(token):
                continue
            targets.append(token)
    return targets


def unpinned_targets(code: str) -> list[str]:
    """Package specs installed without a pinned ``==`` version."""
    return [t for t in pip_targets(code) if "==" not in t]


def captured_spark_versions(definition: dict) -> list[tuple[int, int, int]]:
    """Spark versions captured in notebook outputs/metadata, excluding source text.

    Prefer the direct output of a cell that asks for ``spark.version``. Notebook
    outputs can also contain historical Delta ``Apache-Spark/...`` strings from
    commands like DESCRIBE HISTORY; those are useful fallback evidence, but they
    are not as authoritative as the current session's printed Spark version.
    """
    direct: list[tuple[int, int, int]] = []
    fallback: list[tuple[int, int, int]] = []

    def version_tuple(match) -> tuple[int, int, int]:
        return tuple(int(part or 0) for part in match.groups())

    def output_text(value) -> str:
        if isinstance(value, dict):
            return "\n".join(output_text(child) for child in value.values())
        if isinstance(value, list):
            return "\n".join(output_text(child) for child in value)
        return value if isinstance(value, str) else ""

    for cell in (definition or {}).get("cells") or []:
        source = cell.get("source")
        source_text = "".join(source) if isinstance(source, list) else str(source or "")
        if "spark.version" not in source_text:
            continue
        for match in PLAIN_SPARK_VERSION.finditer(output_text(cell.get("outputs") or [])):
            direct.append(version_tuple(match))
        for match in SPARK_RUNTIME.finditer(output_text(cell.get("outputs") or [])):
            direct.append(version_tuple(match))

    if direct:
        return direct

    def visit(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key != "source":
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for match in SPARK_RUNTIME.finditer(value):
                fallback.append(version_tuple(match))

    visit(definition or {})
    return fallback


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Parse a deterministic ``major.minor[.patch]`` project threshold."""
    match = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.(\d+))?\s*", str(value))
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def fabric_runtime_to_spark(value: str) -> tuple[int, int, int] | None:
    """Map Fabric Environment runtime versions to Spark major versions."""
    runtime = parse_version(value)
    if runtime is None:
        return None
    mapping = {"1.1": (3, 3, 0), "1.2": (3, 4, 0), "1.3": (3, 5, 0)}
    return mapping.get(f"{runtime[0]}.{runtime[1]}")


def partitioned_sql_reads(
    code: str, partition_columns: dict[str, list[str]],
) -> list[tuple[str, bool]]:
    """Return configured partitioned-table SQL reads and predicate compliance.

    Each SELECT is evaluated independently, preventing an unrelated WHERE in a
    different query from making an unfiltered read look compliant.
    """
    configured = {
        str(table).strip("`").lower(): [str(column).lower() for column in columns]
        for table, columns in partition_columns.items()
        if isinstance(columns, list) and columns
    }
    reads: list[tuple[str, bool]] = []
    for query_match in SQL_SELECT.finditer(code):
        query = query_match.group(0)
        table_match = SQL_FROM.search(query)
        if not table_match:
            continue
        table = table_match.group(1).strip("`").lower()
        columns = configured.get(table) or configured.get(table.rsplit(".", 1)[-1])
        if not columns:
            continue
        where = re.search(r"\bWHERE\b(.*)$", query, re.IGNORECASE | re.DOTALL)
        predicate = where.group(1) if where else ""
        filtered = any(re.search(rf"\b{re.escape(column)}\b", predicate, re.IGNORECASE)
                       for column in columns)
        reads.append((table, filtered))
    return reads


def monitoring(definition: dict) -> dict:
    """Provider-enriched Spark monitoring evidence for this notebook."""
    value = (definition or {}).get("_auditfast_monitoring")
    return value if isinstance(value, dict) else {}


def collection(value) -> list[dict]:
    """Normalize Fabric collection responses that use value/data or a raw list."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        rows = value.get("value") or value.get("data") or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return []


def number(value, default: float = 0.0) -> float:
    """Convert a monitoring metric to float without raising in a check."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def performance_issues(definition: dict, heavy_shuffle_bytes: int) -> list[str]:
    """Deterministic skew, spill, and heavy-shuffle issues in monitoring data."""
    evidence = monitoring(definition)
    issues: set[str] = set()
    for advice in collection(evidence.get("advice")):
        text = " ".join(str(advice.get(key) or "") for key in ("name", "description"))
        if re.search(r"skew", text, re.IGNORECASE):
            issues.add("skew advice")
        if re.search(r"spill", text, re.IGNORECASE):
            issues.add("spill advice")
        if re.search(r"shuffle", text, re.IGNORECASE):
            issues.add("shuffle advice")
    for stage in collection(evidence.get("stages")):
        if number(stage.get("diskBytesSpilled")) > 0 or number(stage.get("memoryBytesSpilled")) > 0:
            issues.add("stage spill")
        shuffle = max(
            number(stage.get("shuffleWriteBytes")),
            number(stage.get("shuffleReadBytes")),
        )
        if shuffle > heavy_shuffle_bytes:
            issues.add("heavy shuffle")
    return sorted(issues)
