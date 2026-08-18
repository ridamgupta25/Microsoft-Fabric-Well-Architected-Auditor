"""Data Management & Quality - Data Storage — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for warehouse
modelling practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext

#: Table-name substrings that mark a detail (fact-grain) or an aggregate table.
_DETAIL_HINTS = ("detail", "fact", "transaction")
_AGGREGATE_HINTS = ("daily", "agg", "aggregate", "summary", "rollup")


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


def _has_detail_and_aggregate(ws) -> bool:
    names = [str(n).lower() for n in ws.tables]
    has_detail = any(any(h in n for h in _DETAIL_HINTS) for n in names)
    has_aggregate = any(any(h in n for h in _AGGREGATE_HINTS) for n in names)
    return has_detail and has_aggregate


@group_check(
    id="XW-AGG-CONSIST", ref="5.4.3",
    title="Aggregate consistency: sum of detail records equals aggregate totals (no data loss in rollup)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH, requires=[Resource.TABLE_COLUMNS],
    required=False,
)
def aggregate_consistency(ctx: GroupContext) -> Verdict:
    """A detail + aggregate table pair exists in every environment.

    Aggregate-vs-detail *value* reconciliation is row-level data and out of scope
    for a read-only configuration auditor; what is deterministically readable is
    whether every environment even models the pair (a detail/fact table and a
    matching aggregate/summary table). N/A when fewer than two members' table
    columns could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.TABLE_COLUMNS) and bool(ws.tables),
        implements=_has_detail_and_aggregate,
        practice="models a detail + aggregate table pair",
        data_name="detail/aggregate tables",
    )
