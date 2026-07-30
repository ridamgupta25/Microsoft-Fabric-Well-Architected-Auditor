"""Shared helpers for notebook-definition checks.

Underscore-prefixed so the package auto-loader (which imports every
``automated.py`` / ``manual.py``) skips it: it carries no checks, only helpers
the notebook checks import.
"""
from __future__ import annotations

from auditfast.core.enums import Layer

#: Layers whose workspaces are expected to hold transformation notebooks.
NOTEBOOK_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)


def notebook_code(definition: dict) -> str:
    """Concatenate the source of every code cell in an ipynb-style definition."""
    parts: list[str] = []
    for cell in (definition or {}).get("cells") or []:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source")
        parts.append("".join(src) if isinstance(src, list) else (src or ""))
    return "\n".join(parts)


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
