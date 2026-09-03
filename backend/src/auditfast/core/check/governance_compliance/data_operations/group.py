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
    ATTACHED_REF,
    GUID,
    HARDCODED_PATH,
    ONELAKE_PATH,
    PIPELINE_ITEM_REF,
    attached_stores,
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


def _cross_domain_refs(ws: WorkspaceContext) -> tuple[int, int]:
    """``(dependencies that leave this workspace, how many are untraceable)``.

    A dependency is a shortcut, a OneLake path into *another* workspace, or a raw
    external storage URL. It is *opaque* when nobody can trace it to an owning
    domain: a shortcut with no name and target type, a path whose workspace or
    item segment is a bare GUID, or an external URL.

    The **count** matters as much as the opacity. Phrased only as "has an opaque
    reference", a workspace holding no dependencies at all answers "no" and passes
    a practice it never performed — on one real tenant two empty workspaces were
    credited with documenting dependencies they did not have.
    """
    own = {
        (ws.display_name or "").strip().lower(),
        (ws.id or "").strip().lower(),
    } - {""}
    total = opaque = 0
    for _lakehouse, shortcuts in (ws.shortcuts or {}).items():
        for shortcut in shortcuts or []:
            if not isinstance(shortcut, dict):
                continue
            total += 1
            if not (shortcut.get("name") and shortcut.get("target_type")):
                opaque += 1
    texts = dict(pipeline_texts(ws))
    texts.update(notebook_texts(ws))
    for text in texts.values():
        for ws_seg, item_seg in ONELAKE_PATH.findall(text):
            if ws_seg.strip().lower() in own:
                continue  # a path back into this same workspace is not cross-domain
            total += 1
            if GUID.match(ws_seg) or GUID.match(item_seg):
                opaque += 1
        for _match in _EXTERNAL_STORE.finditer(text):
            total += 1
            opaque += 1
    return total, opaque


def _model_documents_schema(ws: WorkspaceContext) -> bool:
    """True when a semantic model carries metadata a modeller had to *write*.

    Two signals, both deliberate acts: a measure with a description, or a table
    with a declared ``dataCategory``. Descriptions are the documentation Power BI
    surfaces to consumers; a data category is a semantic annotation nobody sets by
    accident. Both are captured by the TMSL parser.
    """
    for model in (ws.semantic_models or {}).values():
        if not isinstance(model, dict):
            continue
        for measure in model.get("measures") or []:
            if isinstance(measure, dict) and str(measure.get("description") or "").strip():
                return True
        categories = model.get("data_categories") or {}
        if isinstance(categories, dict) and any(str(v or "").strip() for v in categories.values()):
            return True
    return False


def _captures_technical_metadata(ws: WorkspaceContext) -> bool:
    """True when technical metadata is *deliberately* captured.

    Two things count: a separate metadata registry (``*_metadata`` / ``audit_*``
    / a load-list control table), or a semantic model that documents its schema.

    Deliberately **not** counted: the bare existence of a semantic model, and the
    presence of readable table columns. Every reporting workspace has a model, and
    readable columns only say the crawl succeeded — crediting either made this
    check pass all five lines of business on one real tenant, including a
    workspace holding five items and nothing else. A check that cannot fail
    reports nothing.
    """
    return _xw.has_metadata_registry(ws) or _model_documents_schema(ws)


#: SQL audit action groups that record *data access* — a read or write against a
#: table. The three Fabric enables by default (batch completion and the two
#: authentication groups) record logins and job outcomes, not who touched which
#: data, so an audit carrying only those does not satisfy "who accessed what data,
#: when" however firmly it is switched on.
_DATA_ACCESS_ACTION_GROUPS: frozenset[str] = frozenset({
    "SCHEMA_OBJECT_ACCESS_GROUP",
    "DATABASE_OBJECT_ACCESS_GROUP",
    "SCHEMA_OBJECT_CHANGE_GROUP",
    "DATABASE_OBJECT_CHANGE_GROUP",
    "USER_CHANGE_PASSWORD_GROUP",
    "SCHEMA_OBJECT_PERMISSION_CHANGE_GROUP",
    "DATABASE_OBJECT_PERMISSION_CHANGE_GROUP",
})

#: Named in the evidence of every verdict: the tenant-wide half of 7.4.3 needs the
#: admin audit-log APIs this tool deliberately does not call, so no verdict here
#: may be read as covering it.
_TENANT_AUDIT_CAVEAT = (
    "tenant audit logging is not covered — it needs the admin audit-log API this "
    "read-only audit does not call, so this verdict is scoped to the Warehouse "
    "SQL audit configuration only"
)


#: Stores that hold data but expose no ``settings/sqlAudit`` surface. Their access
#: trail lives in the tenant audit log, which needs Fabric-admin scopes this audit
#: does not hold — so they can be *counted* and disclosed, never failed.
_UNAUDITABLE_STORE_TYPES: frozenset[str] = frozenset(
    {"Lakehouse", "SQLDatabase", "MirroredWarehouse"}
)


def _warehouse_audit_coverage(ws: WorkspaceContext) -> tuple[int, int, int, list[str]]:
    """``(properly_audited, warehouses, unauditable_stores, gaps)`` for one environment.

    Coverage is counted **per Warehouse**, not "does any Warehouse have it on".
    An estate with six Warehouses and one audited one has a one-sixth access
    trail; scoring that as a pass was the reviewer's "can incorrectly pass an
    environment even when the complete data-access audit trail is missing".

    A Warehouse counts as properly audited only when audit is enabled, its action
    groups record **data access**, and the records are **retained**.

    Lakehouses and SQL databases are counted separately and never failed: they
    have no per-item audit configuration to read, so "we cannot verify this" must
    not become "this is misconfigured".
    """
    warehouses = [item for item in ws.items if item.type == "Warehouse"]
    unauditable = sum(1 for item in ws.items if item.type in _UNAUDITABLE_STORE_TYPES)
    total = len(warehouses) or len(ws.warehouse_audit)

    audited = 0
    gaps: list[str] = []
    for name, settings in sorted(ws.warehouse_audit.items()):
        if not settings.get("enabled"):
            continue
        groups = {str(g).strip().upper() for g in (settings.get("action_groups") or [])}
        if not groups & _DATA_ACCESS_ACTION_GROUPS:
            gaps.append(
                f"'{name}' audits {', '.join(sorted(groups)) or 'nothing'} — no "
                f"action group records data access"
            )
        elif not settings.get("retention_days"):
            gaps.append(f"'{name}' records data access but retains it for 0 days")
        else:
            audited += 1
    return audited, total, unauditable, gaps


@group_check(
    id="XW-ACCESS-AUDIT", ref="7.4.3",
    title="Data access audit trail exists (who accessed what data, when)",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.HIGH,
    requires=[Resource.WAREHOUSE_AUDIT, Resource.ITEMS], required=False,
)
def access_audit_consistent(ctx: GroupContext) -> Verdict:
    """Every Warehouse records *who read or wrote which data*, and retains it.

    Scored as the share of Warehouses across the group that are properly audited
    — enabled, with an action group that records **data access**, and with
    non-zero **retention**. All three must hold: the three groups Fabric enables
    by default record logins and batch completion, so an audit carrying only
    those answers "who signed in", never "who read this table".

    Counting per Warehouse rather than per environment is what stops one audited
    Warehouse from vouching for five unaudited ones.

    Two things are deliberately **not** failed, only disclosed:

    * an environment with **no Warehouse** is excluded — a reporting workspace has
      no Warehouse SQL audit to switch on;
    * **Lakehouses and SQL databases** have no per-item audit surface at all;
      their access trail lives in the tenant audit log, which needs Fabric-admin
      scopes this read-only audit does not hold. Their count is reported so a
      pass is never mistaken for whole-estate coverage.

    N/A when fewer than two environments hold a Warehouse.
    """
    readable = [m for m in ctx.members if m.workspace.has(Resource.WAREHOUSE_AUDIT)]
    if not readable:
        return not_applicable(
            "no environment in this group had readable Warehouse audit "
            f"configuration. {_TENANT_AUDIT_CAVEAT}"
        )

    judged: list[str] = []
    skipped: list[str] = []
    all_gaps: list[str] = []
    audited_total = warehouse_total = unauditable_total = 0

    for member in readable:
        label = _xw.env_label(member)
        audited, warehouses, unauditable, gaps = _warehouse_audit_coverage(member.workspace)
        unauditable_total += unauditable
        if warehouses == 0:
            skipped.append(
                f"{label} (holds no Warehouse, so there is no Warehouse SQL audit "
                f"to enable)"
            )
            continue
        audited_total += audited
        warehouse_total += warehouses
        judged.append(f"{label} {audited} of {warehouses} Warehouse(s) audited")
        all_gaps.extend(gaps)

    excluded = (f"; {len(skipped)} environment(s) excluded: {'; '.join(skipped)}"
                if skipped else "")
    uncovered = (
        f"; a further {unauditable_total} Lakehouse/SQL database(s) across the group "
        f"expose no audit configuration to read — their access trail is only in the "
        f"tenant audit log"
    ) if unauditable_total else ""

    if len(judged) < 2:
        return not_applicable(
            f"fewer than two environments in this group hold a Warehouse whose SQL "
            f"audit could be compared{excluded}{uncovered}. {_TENANT_AUDIT_CAVEAT}"
        )

    detail = "; ".join(judged)
    if audited_total == warehouse_total:
        return covered(
            warehouse_total, warehouse_total,
            f"all {warehouse_total} Warehouse(s) across {len(judged)} environment(s) "
            f"audit data access with retention: {detail}{excluded}{uncovered}. "
            f"{_TENANT_AUDIT_CAVEAT}",
        )

    reasons = f"; {'; '.join(all_gaps[:3])}" if all_gaps else ""
    return covered(
        audited_total, warehouse_total,
        f"{audited_total} of {warehouse_total} Warehouse(s) across "
        f"{len(judged)} environment(s) record and retain data access: {detail}"
        f"{reasons}{excluded}{uncovered}. {_TENANT_AUDIT_CAVEAT}",
    )


def _model_names_a_source(model: dict) -> bool:
    """True when a semantic model's TMSL says where its data comes from.

    This is the model -> store hop, and the reason 8.1.2 could previously only
    check that a model *existed* beside a store rather than that it reads from
    one. Three readable forms, in the order Fabric writes them:

    * a **native query / M partition** carries its own source query text;
    * a **Direct Lake** partition names an ``entityName`` (the Lakehouse or
      Warehouse table) and/or the model-level expression that resolves the store;
    * a **model-level shared expression** names the SQL endpoint the tables read.

    A model with none of these declares no source at all — a hand-built or
    push-only model that no lineage edge can hang off.
    """
    storage = (model or {}).get("storage") or {}
    for table in storage.values():
        if not isinstance(table, dict):
            continue
        if table.get("native_query_expressions"):
            return True
        for entity in table.get("entity_sources") or []:
            if isinstance(entity, dict) and (
                entity.get("entity") or entity.get("expression_source")
            ):
                return True
    return bool((model or {}).get("expressions"))


def _traceable_items(ws: WorkspaceContext) -> tuple[int, int, list[str]]:
    """``(traceable, total, examples_of_gaps)`` for the items that carry lineage.

    Fabric infers its lineage graph from how items reference each other, so an
    item that moves data through a hard-coded ``abfss://``/``https://`` path
    instead of an attached store or a named Fabric item is **invisible in the
    lineage view even though the data flow is real**. That, not the mere
    coexistence of a pipeline and a lakehouse in the same workspace, is what
    "end-to-end lineage is visible" means.

    Four populations, each judged by whether Fabric can draw an edge from it:

    * **pipelines** — traceable when an activity names a Fabric item
      (``artifactId``/``notebookId``/``pipelineId``… or a Fabric linked-service
      type);
    * **notebooks** — traceable when a store is attached, or the code reaches the
      catalog rather than a URL. A notebook that touches no data at all (a helper
      or a utility) is not counted either way — it is not part of any chain;
    * **reports** — traceable when bound to a semantic model (``datasetId``),
      which is the only report-side edge the REST surface exposes;
    * **semantic models** — traceable when their TMSL names a source, closing the
      report -> model -> store chain. Without this the middle of the chain was
      simply assumed.
    """
    traceable = total = 0
    gaps: list[str] = []
    # Computed once: notebook_texts strips comments from every notebook, so
    # calling it per notebook would re-parse the whole workspace each time.
    texts = notebook_texts(ws)

    for name, text in pipeline_texts(ws).items():
        total += 1
        if PIPELINE_ITEM_REF.search(text):
            traceable += 1
        else:
            gaps.append(f"pipeline '{name}' names no Fabric item")

    for name, definition in (ws.notebooks or {}).items():
        code = texts.get(name, "")
        attached = attached_stores(definition)
        wired = bool(attached) or bool(ATTACHED_REF.search(code))
        if not wired and not HARDCODED_PATH.search(code):
            continue  # touches no data at all - not part of a lineage chain
        total += 1
        if wired:
            traceable += 1
        else:
            gaps.append(f"notebook '{name}' reads/writes only through a hard-coded path")

    for report in ws.reports or []:
        total += 1
        if report.get("dataset_id"):
            traceable += 1
        else:
            gaps.append(
                f"report '{report.get('name') or report.get('id')}' is bound to no "
                f"semantic model"
            )

    for name, model in (ws.semantic_models or {}).items():
        if not isinstance(model, dict) or "expressions" not in model:
            # This snapshot predates the TMSL parser keeping the Direct Lake
            # entity and the model-level expressions. Its source was parsed away,
            # not absent -- and "we cannot tell" must never be scored as "no
            # source". Re-crawl to judge these models.
            continue
        total += 1
        if _model_names_a_source(model):
            traceable += 1
        else:
            gaps.append(f"semantic model '{name}' names no source store")

    return traceable, total, gaps


@group_check(
    id="XW-LINEAGE-E2E", ref="8.1.2",
    title="End-to-end lineage visible from source system to Gold Warehouse and Power BI",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM,
    requires=[
        Resource.ITEMS, Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS,
        Resource.REPORTS, Resource.SEMANTIC_MODEL_DEFINITIONS,
    ],
    required=False,
)
def lineage_e2e_consistent(ctx: GroupContext) -> Verdict:
    """How much of each environment's data flow Fabric can actually trace.

    Scored on **connectivity**, not coexistence: every pipeline, data-touching
    notebook and report is asked whether it carries the reference Fabric needs to
    draw a lineage edge — a named item, an attached store, or a bound semantic
    model. An item that moves data through a hard-coded path is real work that the
    lineage view cannot see, and that is the defect this point is about.

    The earlier implementation asked only whether a source item, a store and a
    reporting item *coexisted* in the same workspace. That failed every correctly
    layered estate — a reporting workspace holds no pipelines and a data workspace
    holds no reports, by design — while passing a workspace whose three stages
    were entirely unconnected.

    It makes no Purview claim, and cannot see the semantic-model → store hop,
    which no readable surface exposes. N/A when no environment holds a traceable
    item.
    """
    readable = [m for m in ctx.members if m.workspace.has(Resource.ITEMS)]
    per_env: list[tuple[str, int, int, list[str]]] = []
    for member in readable:
        traceable, total, gaps = _traceable_items(member.workspace)
        if total:
            per_env.append((_xw.env_label(member), traceable, total, gaps))

    if not per_env:
        return not_applicable(
            "no environment in this group holds a pipeline, data-touching notebook "
            "or report whose lineage wiring could be inspected"
        )

    traceable = sum(entry[1] for entry in per_env)
    total = sum(entry[2] for entry in per_env)
    breakdown = "; ".join(
        f"{label} {done} of {count}" for label, done, count, _ in per_env
    )

    if traceable == total:
        return covered(
            total, total,
            f"every one of the {total} lineage-bearing item(s) across "
            f"{len(per_env)} environment(s) declares the reference Fabric needs to "
            f"trace it ({breakdown})",
        )

    examples = [gap for _, _, _, gaps in per_env for gap in gaps][:3]
    return covered(
        traceable, total,
        f"{traceable} of {total} lineage-bearing item(s) across {len(per_env)} "
        f"environment(s) are traceable ({breakdown}); the rest move data without a "
        f"reference Fabric can draw an edge from: {'; '.join(examples)}",
    )


@group_check(
    id="XW-TECH-METADATA", ref="8.3.2",
    title="Technical metadata (schema, lineage) automatically captured",
    pillar=Pillar.DATA_GOVERNANCE, severity=Severity.MEDIUM,
    requires=[Resource.TABLE_COLUMNS, Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def tech_metadata_consistent(ctx: GroupContext) -> Verdict:
    """Every environment captures technical metadata deliberately.

    Technical metadata counts when a **capture mechanism** exists: a separate
    metadata registry (a ``*_metadata`` / ``audit_*`` / load-list control table),
    or a semantic model that documents its schema (measure descriptions, or a
    declared data category). The mere presence of a semantic model, or of readable
    table columns, does not count — see :func:`_captures_technical_metadata`.

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
    them undocumented.

    An environment with **no** dependency leaving it is excluded, not passed:
    there is nothing to document, and crediting it would inflate the group score
    on an empty workspace. N/A when fewer than two environments have one.
    """
    refs: dict[str, tuple[int, int]] = {}

    def counted(ws) -> tuple[int, int]:
        if ws.id not in refs:
            refs[ws.id] = _cross_domain_refs(ws)
        return refs[ws.id]

    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.PIPELINE_DEFINITIONS)
        or ws.has(Resource.NOTEBOOK_DEFINITIONS)
        or ws.has(Resource.SHORTCUTS),
        applicable=lambda ws: counted(ws)[0] > 0,
        implements=lambda ws: counted(ws)[1] == 0,
        practice="documents its cross-domain dependencies as identifiable references",
        data_name="item definitions and shortcuts",
        inapplicable_reason="holding no dependency that leaves the workspace",
    )
