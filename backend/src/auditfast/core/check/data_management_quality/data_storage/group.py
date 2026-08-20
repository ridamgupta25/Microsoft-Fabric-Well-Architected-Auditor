"""Data Management & Quality - Data Storage — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for warehouse
modelling practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

import re

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code, layer_words_in, strip_sql_comments
from auditfast.core.check.data_management_quality.data_prep.automated import _WRITE_PATTERN
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


@group_check(
    id="XW-CONFORMED-DIM", ref="4.4.9",
    title="Cross-domain conformed dimensions shared (not duplicated per domain) in the Warehouse",
    pillar=Pillar.DATA_QUALITY, severity=Severity.MEDIUM, requires=[Resource.TABLE_COLUMNS],
    required=False,
)
def conformed_dimensions(ctx: GroupContext) -> Verdict:
    """Every environment carries the group-wide set of conformed dimensions.

    The reference is the union of dimension table names across the group; an
    environment missing a dimension its peers have signals a duplicated or
    per-environment dimension rather than a shared conformed one. N/A when fewer
    than two members' table columns could be read, or no dimensions are found.
    """
    return _xw.superset_consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.TABLE_COLUMNS),
        signature=_xw.dimension_table_names,
        practice="carries every conformed dimension the group declares",
        data_name="dimension tables",
    )


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


def _silver_to_gold_flows(ws) -> tuple[list[str], list[str]]:
    """Return applicable and reconciled Silver-to-Gold notebook names."""
    applicable: list[str] = []
    reconciled: list[str] = []
    for name, definition in ws.notebooks.items():
        code = strip_sql_comments(executable_code(definition))
        if not ({"silver", "gold"} <= layer_words_in(code)):
            continue
        if not _WRITE_PATTERN.search(code):
            continue
        applicable.append(name)
        if _RECON_CONTROL.search(code):
            reconciled.append(name)
    return applicable, reconciled


@group_check(
    id="XW-LAYER-RECON", ref="5.4.6",
    title="Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH,
    requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def cross_layer_reconciliation(ctx: GroupContext) -> Verdict:
    """Detect reconciliation controls across a group's Silver-to-Gold flows.

    This is metadata-only: it inspects notebook definitions already present in
    each workspace snapshot and never queries table rows or business values.
    """
    readable = [member for member in ctx.members
                if member.workspace.has(Resource.NOTEBOOK_DEFINITIONS)]
    if len(readable) < 2:
        return not_applicable(
            "fewer than two workspaces had readable notebook definitions to compare"
        )

    applicable: list[str] = []
    reconciled: list[str] = []
    for member in readable:
        flows, controlled = _silver_to_gold_flows(member.workspace)
        label = _xw.env_label(member)
        applicable.extend(f"{label}/{name}" for name in flows)
        reconciled.extend(f"{label}/{name}" for name in controlled)

    if not applicable:
        return not_applicable(
            "no executable Silver-to-Gold notebook writes were found across the group"
        )

    missing = sorted(set(applicable) - set(reconciled))
    if not missing:
        return covered(
            len(applicable), len(applicable),
            f"all {len(applicable)} Silver-to-Gold notebook flow(s) contain a "
            "count or aggregation reconciliation control; notebook definitions "
            "were inspected without reading client data",
        )
    return covered(
        len(reconciled), len(applicable),
        f"reconciliation control detected in {len(reconciled)} of "
        f"{len(applicable)} Silver-to-Gold notebook flow(s); missing in "
        f"{', '.join(missing)}; no client data was read",
    )
