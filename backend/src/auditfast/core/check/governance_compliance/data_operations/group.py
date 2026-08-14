"""Governance & Compliance - Data Operations — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for audit, lineage
and metadata practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext


@group_check(
    id="XW-ACCESS-AUDIT", ref="7.4.3",
    title="Data access audit trail exists (who accessed what data, when)",
    pillar=Pillar.GOVERNANCE, severity=Severity.HIGH,
    requires=[Resource.WAREHOUSE_AUDIT], required=False,
)
def access_audit_consistent(ctx: GroupContext) -> Verdict:
    """Warehouse SQL audit is enabled in every environment, not just production.

    An environment "implements" the audit trail when at least one Warehouse has
    SQL audit enabled. N/A when fewer than two members' Warehouse audit
    configuration could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.WAREHOUSE_AUDIT),
        implements=_xw.has_enabled_warehouse_audit,
        practice="enables a Warehouse SQL audit trail",
        data_name="Warehouse audit configuration",
    )


@group_check(
    id="XW-LINEAGE-E2E", ref="8.1.2",
    title="End-to-end lineage visible from source system to Gold Warehouse and Power BI",
    pillar=Pillar.GOVERNANCE, severity=Severity.MEDIUM, requires=[Resource.ITEMS],
    required=False,
)
def lineage_e2e_consistent(ctx: GroupContext) -> Verdict:
    """Each environment holds the full source -> store -> reporting chain.

    An environment "implements" an end-to-end chain when its inventory contains a
    source item (pipeline / notebook / dataflow), a data store, and a reporting
    item (semantic model / report). N/A when fewer than two members' items could
    be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEMS),
        implements=lambda ws: _xw.has_item_type(ws, _xw.SOURCE_TYPES)
        and _xw.has_item_type(ws, _xw.DATA_STORE_TYPES)
        and _xw.has_item_type(ws, _xw.REPORTING_TYPES),
        practice="expresses a source -> store -> report lineage chain",
        data_name="item inventories",
    )


@group_check(
    id="XW-TECH-METADATA", ref="8.3.2",
    title="Technical metadata (schema, lineage) automatically captured",
    pillar=Pillar.GOVERNANCE, severity=Severity.MEDIUM,
    requires=[Resource.TABLE_COLUMNS, Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def tech_metadata_consistent(ctx: GroupContext) -> Verdict:
    """Every environment captures table schema *and* semantic-model metadata.

    An environment "implements" technical metadata capture when it has column
    definitions for at least one table and at least one semantic model. N/A when
    fewer than two members had both table columns and semantic-model definitions
    readable.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.TABLE_COLUMNS)
        and ws.has(Resource.SEMANTIC_MODEL_DEFINITIONS),
        implements=lambda ws: _xw.has_columns_captured(ws) and bool(ws.semantic_models),
        practice="captures table schema and semantic-model metadata",
        data_name="schema and semantic-model metadata",
    )
