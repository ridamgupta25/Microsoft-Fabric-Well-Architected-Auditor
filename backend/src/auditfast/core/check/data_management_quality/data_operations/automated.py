"""Data Management & Quality · Data Operations — medallion layer hygiene.

Whether each workspace holds the item types its layer role calls for, and none
that belong to another layer.
"""
from __future__ import annotations

import re

from auditfast.core.check._notebook import executable_code, notebook_code
from auditfast.core.check.helpers import Verdict, binary, not_applicable, note
from auditfast.core.check.registry import check
from auditfast.core.enums import LAYER_ITEM_TYPES, Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_INPUT_READ = re.compile(
    r"spark\.read|\.read\.(?:csv|json|text|format)|json\.loads|from_json\s*\(|"
    r"read_json|read_csv|EAM\s+JSON|EAM_JSON",
    re.IGNORECASE,
)
_SCHEMA_DECLARATION = re.compile(
    r"StructType\s*\(|StructField\s*\(|schema\s*=|\.schema\s*\(|"
    r"expected[_ ]?(?:schema|columns)|schema_of_json|jsonschema|dtypes\s*\(",
    re.IGNORECASE,
)
_COLUMN_TYPE_VALIDATION = re.compile(
    r"StructField\s*\(|expected[_ ]?(?:columns|schema)|\.dtypes\s*\(|"
    r"(?:columns|field|datatype|data_type|type).*?(?:assert|valid|match|equal)|"
    r"assert.*(?:columns|schema|dtypes|datatype|field)",
    re.IGNORECASE | re.DOTALL,
)
_ENCODING = re.compile(r"encoding\s*=\s*[\"']?utf[-_]?8|utf[-_]?8", re.IGNORECASE)
_DELIMITER = re.compile(
    r"(?:delimiter|sep|quote(?:char)?)\s*(?:=|['\"]\s*,|,)|"
    r"columnDelimiter|fieldDelimiter",
    re.IGNORECASE,
)
_JSON_STRUCTURE = re.compile(
    r"json\.loads|json\.dumps|from_json\s*\(|jsonschema|schema_of_json|"
    r"json[_ ]?(?:structure|validation|schema)|EAM\s+JSON|EAM_JSON",
    re.IGNORECASE,
)
_DATE_STANDARDIZATION = re.compile(
    r"to_date\s*\(|to_timestamp\s*\(|date_format\s*\(|strptime\s*\(|"
    r"cast\s*\([^\n]{0,100}\b(?:date|timestamp)\b",
    re.IGNORECASE,
)
_CODE_STANDARDIZATION = re.compile(
    r"\.str\.?(?:upper|lower|strip)|\b(?:upper|lower|trim|ltrim|rtrim)\s*\(|"
    r"regexp_replace\s*\(|normalize[_ ]?code|standardize[_ ]?code|clean[_ ]?code",
    re.IGNORECASE,
)


@check(
    id="WS-LAYER-CONTENT", ref="1.1.1", title="Clear separation of concerns across the 67 workspaces (Data Prep / Data Store / Data Consumption × Dev / QA / Prod)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def layer_content(ctx: CheckContext) -> Verdict:
    """The workspace holds at least one item type its layer role calls for."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    layer = ctx.workspace.layer
    expected = LAYER_ITEM_TYPES.get(layer)
    if not expected:
        return note(f"role '{layer.value}' has no layer-specific expectation")
    present = ctx.workspace.item_types()
    return binary(
        bool(present & expected),
        f"expected any of {sorted(expected)}; found {sorted(present) or ['none']}",
    )


@check(
    id="WS-LAYER-SEP", ref="1.1.9", title="Data Prep workspaces (`MLC_DATAPREP_*`) contain only Pipelines and Notebooks — no Lakehouses or Warehouses (all storage resides in the Data Store workspace)",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS], required=True,
)
def layer_separation(ctx: CheckContext) -> Verdict:
    """The workspace does not hold item types that belong to a different layer."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    layer = ctx.workspace.layer
    expected = LAYER_ITEM_TYPES.get(layer)
    if not expected:
        return note(f"role '{layer.value}' has no separation rule")
    foreign_types: set[str] = set()
    for other_layer, types in LAYER_ITEM_TYPES.items():
        if other_layer is not layer:
            foreign_types |= types
    foreign = ctx.workspace.item_types() & (foreign_types - expected)
    return binary(not foreign, f"foreign item types found: {sorted(foreign) or ['none']}")


@check(
    id="NB-SCHEMA-VALIDATE", ref="5.2.1",
    title="Schema validation: incoming records match expected schema (column count, names, data types) — incl. EAM JSON",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=(Layer.OPERATIONS,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_schema_validation(ctx: CheckContext) -> Verdict:
    """Incoming records validate expected column names, count, and data types."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _INPUT_READ.search(code):
        return not_applicable("Notebook has no recognizable incoming file or JSON read")
    if not _SCHEMA_DECLARATION.search(code):
        return binary(False, "Incoming records have no explicit expected schema validation")
    if not _COLUMN_TYPE_VALIDATION.search(code):
        return binary(
            False,
            "Schema is declared but column names/count and data types are not explicitly validated",
        )
    return binary(
        True,
        "Incoming records validate expected column names/count and data types",
    )


@check(
    id="NB-FORMAT-VALIDATE", ref="5.2.4",
    title="Format validation: expected encoding (UTF-8), delimiters, and JSON structure for EAM",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_format_validation(ctx: CheckContext) -> Verdict:
    """Incoming records validate UTF-8, delimiters, and EAM JSON structure."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _INPUT_READ.search(code):
        return not_applicable("Notebook has no recognizable incoming file or JSON read")

    checks = {"UTF-8 encoding": bool(_ENCODING.search(code))}
    if re.search(r"csv|delimiter|sep\s*=|columnDelimiter|fieldDelimiter", code, re.IGNORECASE):
        checks["delimiter handling"] = bool(_DELIMITER.search(code))
    if re.search(r"json|EAM", code, re.IGNORECASE):
        checks["JSON structure validation"] = bool(_JSON_STRUCTURE.search(code))
    missing = [name for name, present in checks.items() if not present]
    return binary(
        not missing,
        "Input format validation covers UTF-8, delimiters, and JSON structure"
        if not missing else
        f"Input format validation missing: {', '.join(missing)}",
    )


@check(
    id="NB-STANDARDIZE", ref="5.3.5",
    title="Standardization: consistent formatting (dates, codes)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_standardization(ctx: CheckContext) -> Verdict:
    """Notebook transformations standardize incoming dates and codes.

    Reference mapping was deliberately dropped from this point (commit 7ce7af1):
    a ``lookup``/``join`` against a code table is too common a shape to read as
    evidence of *standardisation*, and requiring it produced false failures.

    Reads ``executable_code`` so a commented-out ``to_date(...)`` cannot pass.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _INPUT_READ.search(code):
        return not_applicable("Notebook has no recognizable incoming file or JSON read")

    checks = {
        "date formatting": bool(_DATE_STANDARDIZATION.search(code)),
        "code formatting": bool(_CODE_STANDARDIZATION.search(code)),
    }
    present = [name for name, ok in checks.items() if ok]
    missing = [name for name, ok in checks.items() if not ok]
    if not missing:
        return binary(True, (
            "Incoming data is standardized — date formatting (to_date / to_timestamp / "
            "date_format / cast to date) and code formatting (upper / lower / trim / "
            "regexp_replace) are both present"
        ))
    hints = {
        "date formatting":
            "to_date / to_timestamp / date_format / strptime / cast to date or timestamp",
        "code formatting":
            "upper / lower / trim / ltrim / rtrim / regexp_replace / normalize_code",
    }
    missing_desc = "; ".join(f"{m} (looked for {hints[m]})" for m in missing)
    found_desc = ", ".join(present) if present else "none"
    return binary(False, (
        f"Standardization missing: {missing_desc}. Present: {found_desc}"
    ))
