"""Governance & Compliance · Data Prep — reconciliation control checks."""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, notebook_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities
from auditfast.core.check.helpers import Verdict, binary, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_RECONCILIATION = re.compile(
    r"reconcil|reconcile|control[_ -]?total|balance[_ -]?check|variance|"
    r"source[_ -]?target|source[_ -]?to[_ -]?target",
    re.IGNORECASE,
)
_METRIC = re.compile(
    r"count\s*\(|\.count\s*\(|row[_ -]?count|record[_ -]?count|"
    r"sum\s*\(|amount[_ -]?total|control[_ -]?total",
    re.IGNORECASE,
)
_SOURCE_TARGET = re.compile(r"source.*target|target.*source", re.IGNORECASE | re.DOTALL)
_DATA_MOVE_TYPES = frozenset({"Copy", "Script", "TridentNotebook", "SqlServerStoredProcedure", "Lookup"})


def _has_reconciliation(blob: str) -> bool:
    """True when named reconciliation compares source and target control metrics."""
    return bool(_RECONCILIATION.search(blob) and _METRIC.search(blob) and _SOURCE_TARGET.search(blob))


@check(
    id="PL-RECONCILE", ref="7.2.6",
    title="Source-to-target reconciliation exists for financial data (completeness and accuracy)",
    pillar=Pillar.GOVERNANCE, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_reconciliation(ctx: CheckContext) -> Verdict:
    """Financial pipeline definitions compare source and target count or amount controls."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    if not any((activity.get("type") or "") in _DATA_MOVE_TYPES for activity in activities(ctx.obj)):
        return not_applicable("No data-movement activity to assess for reconciliation")
    blob = json.dumps(ctx.obj)
    if not _RECONCILIATION.search(blob):
        return not_applicable("No source-to-target reconciliation signal found")
    return binary(
        _has_reconciliation(blob),
        "Source-to-target reconciliation compares count or amount control totals"
        if _has_reconciliation(blob) else
        "Reconciliation is mentioned but no source-target count or amount comparison was found",
    )


@check(
    id="NB-RECONCILE", ref="7.2.6",
    title="Source-to-target reconciliation exists for financial data (completeness and accuracy)",
    pillar=Pillar.GOVERNANCE, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_reconciliation(ctx: CheckContext) -> Verdict:
    """Financial notebook code compares source and target count or amount controls."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _RECONCILIATION.search(code):
        return not_applicable("No source-to-target reconciliation signal found")
    return binary(
        _has_reconciliation(code),
        "Source-to-target reconciliation compares count or amount control totals"
        if _has_reconciliation(code) else
        "Reconciliation is mentioned but no source-target count or amount comparison was found",
    )
