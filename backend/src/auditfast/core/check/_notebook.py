"""Shared helpers for notebook-definition checks.

Underscore-prefixed so the package auto-loader (which imports every
``automated.py`` / ``manual.py``) skips it: it carries no checks, only helpers
the notebook checks import.
"""
from __future__ import annotations

import re

from auditfast.core.enums import Layer

#: Layers whose workspaces are expected to hold transformation notebooks.
NOTEBOOK_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)

#: A string literal or a comment. Matching literals first means a ``#`` inside a
#: string is never mistaken for the start of a comment.
_STRING_OR_COMMENT = re.compile(
    r"'''[\s\S]*?'''"
    r'|"""[\s\S]*?"""'
    r"|'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|#[^\n]*"
)


def notebook_code(definition: dict) -> str:
    """Concatenate the source of every code cell in an ipynb-style definition."""
    parts: list[str] = []
    for cell in (definition or {}).get("cells") or []:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source")
        parts.append("".join(src) if isinstance(src, list) else (src or ""))
    return "\n".join(parts)


def executable_code(definition: dict) -> str:
    """Code cells with ``#`` comments removed.

    A detector that looks for a *technique* must not be satisfied by a comment
    describing it, nor by code that was commented out — both mean the technique
    is absent.
    """
    return _STRING_OR_COMMENT.sub(
        lambda m: "" if m.group(0).startswith("#") else m.group(0),
        notebook_code(definition),
    )


#: SQL ``/* ... */`` block comment and ``--`` line comment. Spark SQL embedded in
#: ``spark.sql("...")`` strings uses these, which :func:`executable_code` keeps
#: (it only strips Python ``#`` comments and preserves string literals).
_SQL_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")


def strip_sql_comments(code: str) -> str:
    """Remove SQL ``--`` line and ``/* ... */`` block comments from ``code``.

    A detector scanning the SQL inside ``spark.sql("...")`` for a technique (e.g.
    ``DELETE FROM``) must not be satisfied by a *commented-out* statement. Compose
    with :func:`executable_code` — ``strip_sql_comments(executable_code(defn))`` —
    to ignore both Python ``#`` and SQL ``--`` / ``/* */`` commented-out code.
    """
    return _SQL_LINE_COMMENT.sub("", _SQL_BLOCK_COMMENT.sub("", code))


def has_parameters_cell(definition: dict) -> bool:
    """True when any cell is tagged ``parameters`` (papermill / Fabric convention)."""
    for cell in (definition or {}).get("cells") or []:
        tags = (cell.get("metadata") or {}).get("tags") or []
        if "parameters" in tags:
            return True
    return False


def markdown_sources(definition: dict) -> list[str]:
    """Return the concatenated source of every markdown cell in the notebook."""
    out: list[str] = []
    for cell in (definition or {}).get("cells") or []:
        if cell.get("cell_type") != "markdown":
            continue
        src = cell.get("source")
        out.append("".join(src) if isinstance(src, list) else (src or ""))
    return out
