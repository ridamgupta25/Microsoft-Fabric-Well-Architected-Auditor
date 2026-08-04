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
