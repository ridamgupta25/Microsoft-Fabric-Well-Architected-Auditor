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
from auditfast.core.check.helpers import Verdict, covered, not_applicable
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


def _reporting_stage_present(ws: WorkspaceContext) -> bool:
    """True when the workspace holds a reporting stage (semantic model / report).

    Reads the *reporting* end of the lineage chain from any of the readable
    surfaces: a reporting-type item in the inventory, a captured semantic-model
    definition, or a captured report binding.
    """
    return (
        _xw.has_item_type(ws, _xw.REPORTING_TYPES)
        or bool(ws.semantic_models)
        or bool(ws.reports)
    )


def _lineage_stage_summary(ws: WorkspaceContext) -> str:
    """A counted source -> store -> report breakdown, so a gap names the missing stage."""
    counts: dict[str, int] = {}
    for item in ws.items:
        counts[item.type] = counts.get(item.type, 0) + 1
    pipelines = counts.get("DataPipeline", 0) or len(ws.pipelines)
    notebooks = counts.get("Notebook", 0)
    dataflows = counts.get("Dataflow", 0)
    stores = sum(counts.get(t, 0) for t in _xw.DATA_STORE_TYPES)
    semantic = max(counts.get("SemanticModel", 0), len(ws.semantic_models))
    reports = max(counts.get("Report", 0) + counts.get("PaginatedReport", 0), len(ws.reports))
    return (
        f"source: {pipelines} pipeline(s), {notebooks} notebook(s), {dataflows} dataflow(s); "
        f"store: {stores} Lakehouse/Warehouse item(s); "
        f"reporting: {semantic} semantic model(s), {reports} report(s)"
    )


def _captures_technical_metadata(ws: WorkspaceContext) -> bool:
    """True when technical metadata is captured in a model *or* in files/tables.

    Either form counts: table schema (column/type definitions), a semantic model,
    or a separate metadata registry (``*_metadata`` / ``audit_*`` / load-list
    control table).
    """
    return (
        _xw.has_columns_captured(ws)
        or bool(ws.semantic_models)
        or _xw.has_metadata_registry(ws)
    )


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
    """Each fully-captured environment holds the source -> store -> report chain.

    Judged from the *item inventory* only — a source item (pipeline / notebook /
    dataflow), a data store (Lakehouse / Warehouse), and a reporting item
    (semantic model / report), the chain the Fabric REST surface can attest. It
    makes no Purview / lineage-graph-completeness claim, which the REST surface
    cannot retrieve.

    An environment whose reporting inventory was **not fully captured** — no
    semantic model or report *and* the crawl hit recoverable read failures — is
    excluded as *not fetched* (N/A for that member), never counted as a missing
    stage: "we could not read it" is not "it is absent". N/A when fewer than two
    fully-captured environments remain to compare.
    """
    present: list[str] = []
    absent: list[str] = []
    excluded: list[str] = []
    for member in ctx.members:
        ws = member.workspace
        if not ws.has(Resource.ITEMS):
            continue
        label = _xw.env_label(member)
        has_source = _xw.has_item_type(ws, _xw.SOURCE_TYPES)
        has_store = _xw.has_item_type(ws, _xw.DATA_STORE_TYPES)
        has_report = _reporting_stage_present(ws)
        if has_source and has_store and has_report:
            present.append(label)
        elif not has_report and _xw.has_recoverable_read_failures(ws):
            excluded.append(label)
        else:
            absent.append(f"{label} [{_lineage_stage_summary(ws)}]")

    judged = len(present) + len(absent)
    excl_note = (
        f"; {', '.join(excluded)} excluded (reporting inventory not fully captured "
        "— transient read failures during the crawl)" if excluded else ""
    )
    if judged < 2:
        return not_applicable(
            "fewer than two environments had a fully-captured item inventory to "
            f"compare the source -> store -> report chain{excl_note or ''}"
        )
    if not absent:
        return covered(
            judged, judged,
            f"the source -> store -> report item chain is present in all {judged} "
            f"fully-captured environment(s): {', '.join(present)}{excl_note}",
        )
    return covered(
        len(present), judged,
        f"the source -> store -> report item chain is present in {len(present)} of "
        f"{judged} fully-captured environment(s); incomplete in "
        f"{', '.join(absent)}{excl_note}",
    )


@group_check(
    id="XW-TECH-METADATA", ref="8.3.2",
    title="Technical metadata (schema, lineage) automatically captured",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM,
    requires=[Resource.TABLE_COLUMNS, Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def tech_metadata_consistent(ctx: GroupContext) -> Verdict:
    """Every environment captures technical metadata — in a model *or* in files.

    Technical metadata counts when captured in **either** form: (a) table *schema*
    metadata — column/type definitions read for at least one table; (b) a
    *semantic model*; or (c) a separate metadata registry — a ``*_metadata`` /
    ``audit_*`` / load-list control table. Schema and semantic metadata are scored
    together but not conflated: an environment with readable table schema is
    credited even when its semantic model was not fetched.

    An environment that captures none of these *and* whose crawl hit recoverable
    read failures is excluded as *not fetched* (N/A), never failed. N/A when fewer
    than two fully-captured environments remain.
    """
    present: list[str] = []
    absent: list[str] = []
    excluded: list[str] = []
    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.TABLE_COLUMNS) or ws.has(Resource.SEMANTIC_MODEL_DEFINITIONS)):
            continue
        label = _xw.env_label(member)
        if _captures_technical_metadata(ws):
            present.append(label)
        elif _xw.has_recoverable_read_failures(ws):
            excluded.append(label)
        else:
            absent.append(label)

    judged = len(present) + len(absent)
    excl_note = (
        f"; {', '.join(excluded)} excluded (metadata not fully captured — "
        "transient read failures)" if excluded else ""
    )
    if judged < 2:
        return not_applicable(
            "fewer than two environments had readable schema or semantic-model "
            f"metadata to compare{excl_note or ''}"
        )
    if not absent:
        return covered(
            judged, judged,
            f"technical metadata (table schema and/or semantic model / metadata "
            f"registry) is captured in all {judged} environment(s): "
            f"{', '.join(present)}{excl_note}",
        )
    return covered(
        len(present), judged,
        f"technical metadata is captured in {len(present)} of {judged} "
        f"environment(s); missing in {', '.join(absent)}{excl_note}",
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
