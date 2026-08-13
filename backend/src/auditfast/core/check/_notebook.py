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


def executable_code_no_strings(definition: dict) -> str:
    """Code cells with both ``#`` comments and string literals removed.

    Import detection must not fire on an ``import``/``from`` that only appears
    inside a docstring or string literal. Because ``from`` is also an ordinary
    English word, a docstring line such as ``from raw we derive silver`` would
    otherwise be misread as an import, so string bodies are dropped too.
    """
    return _STRING_OR_COMMENT.sub("", notebook_code(definition))


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


# -- medallion layer identification -------------------------------------------
#
# Fabric records no medallion (bronze/silver/gold) semantics: it is a design
# pattern, not a platform feature, and Microsoft's own guidance describes it
# structurally ("keep each layer separated in its own lakehouse"). So a check
# that judges a *layer-specific* practice has to work out which layer a notebook
# serves, and it must do so without assuming the customer uses the English
# medallion vocabulary at all.
#
# Signals, strongest first:
#
#   1. the WRITE TARGET - the table/path the notebook actually saves to
#      (``saveAsTable("silver.dim_customer")``). This is the layer the notebook
#      *produces*, and it survives any lakehouse naming convention.
#   2. the ATTACHED DEFAULT LAKEHOUSE name, from the notebook's own metadata.
#      Real (every writing notebook on the reference estate carries one) but an
#      *undocumented* part of the .ipynb payload, so it is a fallback, never a
#      contract.
#
# Deliberately NOT used: "the word appears anywhere in the code". That matched a
# notebook reading *from* bronze and writing *to* silver, and failed it as a
# bronze notebook - a false FAIL on correct code.
#
# When neither signal identifies a layer the answer is "unknown", and the caller
# must report N/A. An estate that names its layers ``raw``/``curated``/``publish``
# - or in any other vocabulary - is then reported as *not assessed*, never as
# failing. Reduced coverage is honest; a confident wrong answer is not.

#: The write *target* of a save: what the notebook produces, ignoring every path
#: it merely reads. Covers the Spark and SQL forms a Fabric notebook uses.
_WRITE_TARGET = re.compile(
    r"""saveAsTable\s*\(\s*f?["']([^"']+)["']"""
    r"""|\.save\s*\(\s*f?["']([^"']+)["']"""
    r"""|(?:INSERT\s+INTO|INSERT\s+OVERWRITE|CREATE\s+(?:OR\s+REPLACE\s+)?TABLE"""
    r"""|CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS|MERGE\s+INTO)\s+(?:TABLE\s+)?"""
    r"""([A-Za-z0-9_.`\[\]/-]+)"""
    r"""|forPath\s*\(\s*\w+\s*,\s*f?["']([^"']+)["']""",
    re.IGNORECASE,
)

#: Layer vocabularies. The canonical medallion words plus the synonyms seen most
#: often in real estates, so a house style that says ``raw``/``curated`` is still
#: recognised. This is *not* exhaustive by design - an unrecognised vocabulary
#: yields "unknown" and therefore N/A, which is the safe direction.
_LAYER_WORDS: dict[str, tuple[str, ...]] = {
    "bronze": ("bronze", "raw", "landing", "land", "staging", "stage", "stg",
               "ingest", "ingestion", "inbound", "l0"),
    "silver": ("silver", "curated", "curate", "clean", "cleansed", "cleaned",
               "conformed", "conform", "refined", "enriched", "integration",
               "integrated", "l1"),
    "gold":   ("gold", "serving", "serve", "presentation", "mart", "datamart",
               "aggregate", "aggregated", "consumption", "l2"),
}

#: Split a name/path into comparable words: ``silver.dim_customer``,
#: ``abfss://…/Silver/…`` and ``LH_Silver`` all yield a ``silver`` token.
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _layer_of_name(name: str) -> str:
    """The medallion layer ``name`` designates, or ``""`` when it names none.

    Whole-token matching only: ``silver`` in ``silver.dim_customer`` counts, but
    a substring inside a longer word (``sliver``, ``golden_gate``) does not.

    Ambiguity is resolved as *unknown*: a path naming two layers (a
    ``bronze_to_silver`` notebook) cannot be attributed to either, and guessing
    would reintroduce the false FAIL this function exists to prevent. Single
    letters and generic code words (``src``, ``agg``, ``semantic``) are
    deliberately absent from the vocabulary - they collide with ordinary source
    paths far more often than they identify a medallion layer.
    """
    tokens = {t.lower() for t in _TOKEN_SPLIT.split(name or "") if t}
    if not tokens:
        return ""
    matched = [layer for layer, words in _LAYER_WORDS.items() if tokens & set(words)]
    return matched[0] if len(matched) == 1 else ""


def write_targets(code: str) -> list[str]:
    """Every table/path the notebook writes to, in source order."""
    out: list[str] = []
    for match in _WRITE_TARGET.finditer(code or ""):
        target = next((g for g in match.groups() if g), "")
        if target:
            out.append(target)
    return out


def default_lakehouse_name(definition: dict) -> str:
    """Name of the lakehouse the notebook is attached to ("" when absent).

    Read from ``metadata.dependencies.lakehouse.default_lakehouse_name``. This
    reflects a real binding made in the Fabric UI rather than anything inferred
    from text, but it is **not** a documented part of the public notebook
    definition - treat a missing value as unknown, never as a finding.
    """
    meta = (definition or {}).get("metadata") or {}
    lakehouse = ((meta.get("dependencies") or {}).get("lakehouse")) or {}
    return str(lakehouse.get("default_lakehouse_name") or "")


def medallion_layer(definition: dict, code: str) -> tuple[str, str]:
    """Best-evidence medallion layer for a notebook.

    Returns ``(layer, how)`` where ``layer`` is ``"bronze"``/``"silver"``/
    ``"gold"`` or ``""`` when it could not be determined, and ``how`` names the
    evidence so a verdict can explain itself.

    The write target wins over the attached lakehouse: a notebook attached to a
    ``Bronze`` lakehouse that writes ``silver.dim_customer`` is producing silver,
    and judging it as bronze is exactly the false FAIL this ordering prevents.
    """
    for target in write_targets(code):
        layer = _layer_of_name(target)
        if layer:
            return layer, f"writes to '{target}'"

    lakehouse = default_lakehouse_name(definition)
    layer = _layer_of_name(lakehouse)
    if layer:
        return layer, f"attached to lakehouse '{lakehouse}'"

    return "", ""


def layer_undetermined_evidence(definition: dict, code: str) -> str:
    """Why the layer could not be determined - stated in the N/A evidence.

    Names what *was* seen (the write targets, the attached lakehouse) so the
    reader can tell "this estate uses a vocabulary we do not recognise" from
    "this notebook writes nothing".
    """
    targets = write_targets(code)
    lakehouse = default_lakehouse_name(definition)
    seen: list[str] = []
    if targets:
        shown = ", ".join(targets[:3])
        seen.append(f"writes to {shown}")
    if lakehouse:
        seen.append(f"attached lakehouse '{lakehouse}'")
    detail = "; ".join(seen) if seen else "no write target or attached lakehouse found"
    return (
        "Medallion layer could not be determined from the notebook's write "
        f"target or attached lakehouse ({detail}). Fabric records no layer "
        "semantics, so a layer-specific practice is not assessed rather than "
        "assumed"
    )

