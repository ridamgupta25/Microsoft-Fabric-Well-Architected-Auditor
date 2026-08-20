"""Governance & Compliance - Data Operations — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for audit, lineage
and metadata practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

import re

from auditfast.core.check import _xw
from auditfast.core.check._lineage import (
    GUID,
    ONELAKE_PATH,
    notebook_texts,
    pipeline_texts,
)
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext

#: Non-OneLake storage — an external system the Fabric lineage view can only show
#: as an opaque endpoint, so a raw reference to it is not a documented dependency.
_EXTERNAL_STORE = re.compile(
    r"(?:wasbs?|s3a?|gs)://|"
    r"https?://[\w.-]*(?:dfs|blob)\.core\.windows\.net|"
    r"abfss://(?![^@\s\"'/]+@[^/\s\"']*onelake\.)",
    re.IGNORECASE,
)


def _has_opaque_cross_domain_ref(ws: WorkspaceContext) -> bool:
    """True when a dependency leaving this workspace cannot be named/traced.

    Mirrors the identifiable-vs-opaque test of the per-workspace
    ``GOV-LINEAGE-CROSSDOMAIN`` check: a shortcut without a name and target type,
    a OneLake path whose workspace/item segment is a bare GUID, or a raw external
    storage URL is an *undocumented* cross-domain dependency.
    """
    own = {
        (ws.display_name or "").strip().lower(),
        (ws.id or "").strip().lower(),
    } - {""}
    for _lakehouse, shortcuts in (ws.shortcuts or {}).items():
        for shortcut in shortcuts or []:
            if isinstance(shortcut, dict) and not (
                shortcut.get("name") and shortcut.get("target_type")
            ):
                return True
    texts = dict(pipeline_texts(ws))
    texts.update(notebook_texts(ws))
    for text in texts.values():
        for ws_seg, item_seg in ONELAKE_PATH.findall(text):
            if ws_seg.strip().lower() in own:
                continue  # a path back into this same workspace is not cross-domain
            if GUID.match(ws_seg) or GUID.match(item_seg):
                return True
        if _EXTERNAL_STORE.search(text):
            return True
    return False


@group_check(
    id="XW-ACCESS-AUDIT", ref="7.4.3",
    title="Data access audit trail exists (who accessed what data, when)",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM, requires=[Resource.ITEMS],
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
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM,
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


@group_check(
    id="XW-LINEAGE-CROSSDOMAIN", ref="8.1.5",
    title="Cross-domain data dependencies documented in lineage "
          "(identifiable across every environment)",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM,
    requires=[
        Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS,
        Resource.SHORTCUTS,
    ],
    required=False,
)
def lineage_crossdomain_consistent(ctx: GroupContext) -> Verdict:
    """Every environment keeps its cross-domain dependencies identifiable.

    Where ``GOV-LINEAGE-CROSSDOMAIN`` scores one workspace, this compares across
    the group: an environment "implements" the practice when *none* of its
    cross-workspace dependencies is opaque — every one is a named shortcut or a
    path that names its workspace and item, rather than a bare GUID or a raw
    external URL nobody can trace to an owning domain. Surfacing it per group
    catches an environment that documents its dependencies while a peer leaves
    them undocumented. N/A when fewer than two members' definitions/shortcuts
    could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.PIPELINE_DEFINITIONS)
        or ws.has(Resource.NOTEBOOK_DEFINITIONS)
        or ws.has(Resource.SHORTCUTS),
        implements=lambda ws: not _has_opaque_cross_domain_ref(ws),
        practice="documents its cross-domain dependencies as identifiable references",
        data_name="item definitions and shortcuts",
    )
