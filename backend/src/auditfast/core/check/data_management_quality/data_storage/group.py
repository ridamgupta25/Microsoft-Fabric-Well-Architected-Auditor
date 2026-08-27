"""Data Management & Quality - Data Storage — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for warehouse
modelling practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code, layer_words_in, strip_sql_comments
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext

#: An actual reconciliation *control*, not the mere word "reconcile": a count
#: comparison, a named count check, or a reconcile routine that is really called.
#: A bare mention in a variable name, string, or leftover token never qualifies.
_RECON_CONTROL = re.compile(
    r"assert(?![^\n]*\.is(?:Not)?Null\s*\()[^\n]*?\.count\s*\([^\n]*?(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"\.count\s*\(\s*\)\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:row|record|source|target|actual|expected|recon)_count\b\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:==|!=|<=|>=|<|>)\s*(?:row|record|source|target|actual|expected|recon)_count\b|"
    r"\breconcile\w*\s*\(|\bcount_check\b|validate[^\n]*count|expect_table_row_count",
    re.IGNORECASE,
)

#: Table-name substrings that mark a detail (fact-grain) or an aggregate table.
_DETAIL_HINTS = ("detail", "fact", "transaction")
_AGGREGATE_HINTS = ("daily", "agg", "aggregate", "summary", "rollup")
_TOTAL_OPERATION = re.compile(r"\b(?:count|sum)\s*\(", re.IGNORECASE)
_RECONCILIATION_NAME = re.compile(
    r"\b(?:reconcil|variance|difference|mismatch|detail\s+vs\s+aggregate)",
    re.IGNORECASE,
)
_SQL_MISMATCH = re.compile(r"\b(?:if|where|having)\b[^;]*(?:<>|!=)", re.IGNORECASE)
_SQL_STOP = re.compile(r"\b(?:throw|raiserror)\b", re.IGNORECASE)


def _names_have_detail_and_aggregate(names) -> bool:
    names = [str(name).lower() for name in names]
    has_detail = any(any(h in n for h in _DETAIL_HINTS) for n in names)
    has_aggregate = any(any(h in n for h in _AGGREGATE_HINTS) for n in names)
    return has_detail and has_aggregate


def _warehouse_is_applicable(ws) -> bool:
    return (
        ws.has(Resource.TABLE_COLUMNS)
        and _names_have_detail_and_aggregate(ws.tables)
    )


def _semantic_model_is_applicable(ws) -> bool:
    if not ws.has(Resource.SEMANTIC_MODEL_DEFINITIONS):
        return False
    names = []
    for model in ws.semantic_models.values():
        names.extend(
            table.get("name", "") if isinstance(table, dict) else table
            for table in (model.get("tables") or [])
        )
    return _names_have_detail_and_aggregate(names)


def _mentions_both_grains(text: str) -> bool:
    lowered = text.lower()
    return (
        any(hint in lowered for hint in _DETAIL_HINTS)
        and any(hint in lowered for hint in _AGGREGATE_HINTS)
    )


def _semantic_model_reconciles(ws) -> bool:
    for model in ws.semantic_models.values():
        for measure in model.get("measures") or []:
            name = str(measure.get("name") or "")
            expression = str(measure.get("expression") or "")
            signal = f"{name} {expression}"
            if (
                _RECONCILIATION_NAME.search(name)
                and _mentions_both_grains(signal)
                and len(_TOTAL_OPERATION.findall(expression)) >= 2
                and "-" in expression
            ):
                return True
    return False


def _warehouse_reconciles(ws) -> bool:
    for sql_object in (*ws.sql_views, *ws.sql_routines):
        sql = strip_sql_comments(str(sql_object.get("definition") or ""))
        if (
            _mentions_both_grains(sql)
            and len(_TOTAL_OPERATION.findall(sql)) >= 2
            and _SQL_MISMATCH.search(sql)
            and _SQL_STOP.search(sql)
        ):
            return True
    return False


def _has_aggregate_reconciliation(ws) -> bool:
    return (
        (_semantic_model_is_applicable(ws) and _semantic_model_reconciles(ws))
        or (_warehouse_is_applicable(ws) and _warehouse_reconciles(ws))
    )


@group_check(
    id="XW-AGG-CONSIST", ref="5.4.3",
    title="Aggregate consistency: sum of detail records equals aggregate totals (no data loss in rollup)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH,
    requires=[Resource.TABLE_COLUMNS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=False,
)
def aggregate_consistency(ctx: GroupContext) -> Verdict:
    """Every applicable environment enforces detail-to-aggregate reconciliation.

    Table/model names determine whether an aggregate rollup exists; they never
    count as proof. A PASS requires either an explicit semantic-model variance
    measure over both grains or Warehouse SQL that compares both totals and stops
    execution on a mismatch.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: (
            _warehouse_is_applicable(ws) or _semantic_model_is_applicable(ws)
        ),
        implements=_has_aggregate_reconciliation,
        practice="implements detail-to-aggregate total reconciliation in the Warehouse or semantic model",
        data_name="applicable Warehouse and semantic-model definitions",
    )


#: A *data* write (not a DDL CREATE) and its target: what the flow produces.
_DATA_WRITE_TARGET = re.compile(
    r"""(?:\.saveAsTable\s*\(\s*|\.insertInto\s*\(\s*"""
    r"""|INSERT\s+(?:INTO|OVERWRITE(?:\s+TABLE)?)\s+)"""
    r"""(?:[rubf]{0,2})?["'`]?([\w.\[\]/-]+)""",
    re.IGNORECASE,
)
#: Target-name tokens that place a write in the Gold serving tier or a Warehouse.
_GOLD_TARGET_TOKENS = frozenset(
    {"gold", "serving", "serve", "presentation", "mart", "datamart",
     "aggregate", "aggregated", "consumption", "warehouse", "edw"}
)
#: Target-name tokens that mark a write as landing in a metadata/log registry,
#: not a data table — the tell of a DDL/metadata-bootstrap notebook.
_METADATA_TARGET = re.compile(
    r"\b(?:meta|metadata|loadlist|field_standards?|registry|catalog|_ddl"
    r"|log|audit|control|sequence_counter|validation|dictionary|lineage)\b",
    re.IGNORECASE,
)
#: A pipeline whose sink or target names the Gold serving tier / a Warehouse.
_PL_GOLD_SINK = re.compile(
    r"DataWarehouseSink|DataWarehouse\b|\bWarehouse\b|\bgold\b|\bmart\b|\bEDW\b",
    re.IGNORECASE,
)
_PL_SILVER_SOURCE = re.compile(r"\bsilver\b", re.IGNORECASE)
_PL_NAME_SILVER_TO_GOLD = re.compile(r"silver.{0,20}gold", re.IGNORECASE)
#: A pipeline record-count reconciliation control (compare source vs target).
_PL_RECON_CONTROL = re.compile(
    r"source[_ ]?count|target[_ ]?count|record[_ ]?count|reconcil"
    r"|count[_ ]?check|control[_ ]?total",
    re.IGNORECASE,
)


def _write_targets(code: str) -> list[str]:
    return [m.group(1) for m in _DATA_WRITE_TARGET.finditer(code)]


def _writes_gold_data(code: str) -> bool:
    """True when the code writes *data* to a Gold-tier / Warehouse target.

    A DDL ``CREATE TABLE`` is not a data write, and a target named for a metadata
    registry is not a gold data table, so neither counts here.
    """
    for target in _write_targets(code):
        tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", target) if t}
        if tokens & _GOLD_TARGET_TOKENS:
            return True
    return False


def _is_metadata_only_notebook(code: str) -> bool:
    """True when the notebook only defines/loads metadata, not data.

    A DDL/metadata-bootstrap notebook (``nb_metadata_ddl_script``) creates schemas
    and a metadata/log/registry table and seeds it; it moves no Silver data to a
    Gold target, so it must not be mistaken for a Silver-to-Gold flow. It is
    metadata-only when it has no data write at all, or every data write targets a
    metadata/log/registry table.
    """
    targets = _write_targets(code)
    if not targets:
        return True
    return all(_METADATA_TARGET.search(target) for target in targets)


def _pipeline_is_silver_to_gold(name: str, definition: dict) -> bool:
    """True when a pipeline moves Silver data into a Gold-tier / Warehouse target."""
    if _PL_NAME_SILVER_TO_GOLD.search(name):
        return True
    blob = json.dumps(definition)
    return bool(_PL_SILVER_SOURCE.search(blob) and _PL_GOLD_SINK.search(blob))


def _silver_to_gold_flows(ws) -> tuple[list[str], list[str]]:
    """Return applicable and reconciled Silver-to-Gold flow names (nb + pipeline).

    A notebook flow reads Silver and writes *data* to a Gold-tier target, and is
    not a DDL/metadata notebook. A pipeline flow moves Silver into a Warehouse /
    Gold sink (e.g. the ``Silver To Gold`` pipeline). Each flow is then checked
    for a record-count reconciliation control.
    """
    applicable: list[str] = []
    reconciled: list[str] = []
    for name, definition in ws.notebooks.items():
        code = strip_sql_comments(executable_code(definition))
        if "silver" not in layer_words_in(code):
            continue
        if _is_metadata_only_notebook(code):
            continue
        if not _writes_gold_data(code):
            continue
        applicable.append(name)
        if _RECON_CONTROL.search(code):
            reconciled.append(name)
    for name, definition in ws.pipelines.items():
        if not _pipeline_is_silver_to_gold(name, definition):
            continue
        applicable.append(name)
        if _PL_RECON_CONTROL.search(json.dumps(definition)):
            reconciled.append(name)
    return applicable, reconciled


@group_check(
    id="XW-LAYER-RECON", ref="5.4.6",
    title="Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH,
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS], required=False,
)
def cross_layer_reconciliation(ctx: GroupContext) -> Verdict:
    """Detect reconciliation controls across a group's Silver-to-Gold flows.

    Silver-to-Gold flows are the notebooks that read Silver and write data to a
    Gold-tier target (DDL/metadata notebooks are excluded) and the pipelines that
    move Silver into a Warehouse / Gold sink. This is metadata-only: it inspects
    the definitions already in each snapshot and never queries table rows or
    business values.
    """
    readable = [member for member in ctx.members
                if member.workspace.has(Resource.NOTEBOOK_DEFINITIONS)]
    if len(readable) < 2:
        return not_applicable(
            "fewer than two workspaces had readable notebook definitions to compare"
        )

    applicable_total = 0
    reconciled_total = 0
    all_by_tier: list[tuple[str, list[str]]] = []
    missing_by_tier: list[tuple[str, list[str]]] = []
    for member in readable:
        flows, controlled = _silver_to_gold_flows(member.workspace)
        if not flows:
            continue
        controlled_set = set(controlled)
        tier = _xw.env_tier(member)
        applicable_total += len(flows)
        reconciled_total += len(controlled)
        all_by_tier.append((tier, flows))
        missing = [name for name in flows if name not in controlled_set]
        if missing:
            missing_by_tier.append((tier, missing))

    if applicable_total == 0:
        return not_applicable(
            "no Silver-to-Gold data flow (notebook write to a Gold-tier target, or "
            "a Silver-to-Warehouse pipeline) was found across the group"
        )

    def _grouped(pairs: list[tuple[str, list[str]]]) -> str:
        return "; ".join(
            f"**{tier}** — " + ", ".join(f"'{name}'" for name in names)
            for tier, names in pairs
        )

    if reconciled_total == applicable_total:
        return covered(
            applicable_total, applicable_total,
            "Gold record counts reconcile with Silver (accounting for aggregation) in "
            f"all {applicable_total} Silver-to-Gold flow(s): {_grouped(all_by_tier)}",
        )
    if reconciled_total == 0:
        return covered(
            0, applicable_total,
            f"None of the {applicable_total} pipelines/notebooks that load data from "
            "Silver into Gold check for row loss — they don't compare how many rows "
            "were read from Silver against how many were written to Gold, so if a load "
            f"silently dropped rows nobody would know. The {applicable_total} flow(s): "
            f"{_grouped(all_by_tier)}.",
        )
    return covered(
        reconciled_total, applicable_total,
        f"{reconciled_total} of {applicable_total} Silver-to-Gold flow(s) compare "
        "Silver source rows against Gold target rows; the rest do not check for row "
        f"loss: {_grouped(missing_by_tier)}.",
    )
