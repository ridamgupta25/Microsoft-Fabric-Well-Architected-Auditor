"""Governance & Compliance · Data Operations — derived lineage and the financial audit trail.

Three points that an earlier triage recorded as unreachable and that are, in
fact, answerable from data the crawl already holds (8.1.1, 8.1.5) or from one
ordinary delegated Fabric REST read (7.2.3).

**Lineage (8.1.1, 8.1.5).** Fabric's lineage view is built in: every workspace
has one, and any user with a workspace role can open it. So "is it enabled" is
not a question — it is always on. What decides whether the view is *accurate* is
how the items are wired: Fabric infers the graph from item-to-item references, so
a notebook or pipeline that reads and writes through a hard-coded ``abfss://`` /
``https://`` path instead of an attached lakehouse or a referenced Fabric item
produces a real data flow that the lineage view cannot draw. That is what these
two checks measure. Neither needs Microsoft Purview: Purview is a separate
product, and these checklist points name the Fabric lineage view specifically.

**Financial audit trail (7.2.3).** Read from each Warehouse's
``settings/sqlAudit`` *configuration*. Audit rows are never fetched.
"""
from __future__ import annotations

import re

from auditfast.core.check._audit import (
    action_groups,
    audit_enabled,
    captures_data_modifications,
    captures_object_changes,
)
from auditfast.core.check._lineage import (
    ATTACHED_REF,
    GUID,
    HARDCODED_PATH,
    ONELAKE_PATH,
    PIPELINE_ITEM_REF,
    attached_lakehouses,
    notebook_texts,
    pipeline_texts,
)
from auditfast.core.check.helpers import Verdict, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_LAYERS = (Layer.OPERATIONS, Layer.MIXED)

#: How many item names the evidence spells out per category.
_MAX_NAMED = 3

#: Storage that is not OneLake at all — an external system by definition, and one
#: the Fabric lineage view can only show as an opaque endpoint.
_EXTERNAL_STORE = re.compile(
    r"(?:wasbs?|s3a?|gs)://|"
    r"https?://[\w.-]*(?:dfs|blob)\.core\.windows\.net|"
    r"abfss://(?![^@\s\"'/]+@[^/\s\"']*onelake\.)",
    re.IGNORECASE,
)


def _wiring(text: str, *, is_pipeline: bool, attached: bool) -> str:
    """Classify one item as ``wired`` / ``mixed`` / ``opaque`` / ``none``.

    ``wired`` — every data reference resolves to a Fabric item, so the lineage
    view can draw it. ``mixed`` — the item is wired *and* also reaches data by a
    hard-coded path, so part of its flow is missing from the view. ``opaque`` —
    only hard-coded paths, so the item appears in lineage with nothing attached.
    ``none`` — no data reference at all, which is not a lineage defect and is
    excluded from the population rather than counted as a failure.
    """
    resolves = attached or bool(
        PIPELINE_ITEM_REF.search(text) if is_pipeline else ATTACHED_REF.search(text)
    )
    hardcoded = bool(HARDCODED_PATH.search(text))
    if resolves and hardcoded:
        return "mixed"
    if resolves:
        return "wired"
    if hardcoded:
        return "opaque"
    return "none"


@check(
    id="GOV-LINEAGE-VIEW", ref="8.1.1",
    title="Fabric lineage view used and accurate for all key data flows",
    pillar=Pillar.DATA_GOVERNANCE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS],
    required=True,
)
def lineage_view_is_accurate(ctx: CheckContext) -> Verdict:
    """Data-processing items are wired so the built-in Fabric lineage view can connect them.

    **What it can determine.** For every pipeline and notebook whose definition
    was read, whether its data references resolve to a Fabric item — an attached
    lakehouse (``metadata.trident.lakehouse``), a table named rather than
    pathed, a notebook/pipeline/lakehouse/warehouse id in a pipeline activity —
    or whether it reaches data through a hard-coded ``abfss://`` / ``https://``
    path with no item behind it. The second kind is a real data flow the lineage
    view cannot draw, which is the defect this point is about. Items using both
    are reported separately: their flow appears in lineage, but incompletely.

    **What it cannot.** The lineage view itself is not an API, so this does not
    read the rendered graph, and it cannot tell whether anyone consults it — that
    part of the point is a human practice. It also cannot follow a reference built
    at runtime from a variable or a parameter; such an item is classified on what
    its definition literally contains. Items with no data reference at all are
    excluded, not failed, and unreadable definitions are N/A, never FAIL.

    **No Purview dependency.** The Fabric lineage view is built into every
    workspace and visible to any workspace-role user, so "enabled" is never the
    question. ``GOV-LINEAGE-E2E`` (ref 8.1.2) remains self-assessed because it
    spans the source system and Power BI reports, neither of which is crawled.
    """
    if not (ctx.workspace.has(Resource.PIPELINE_DEFINITIONS)
            or ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS)):
        return not_applicable(
            "Neither pipeline nor notebook definitions could be read from Fabric, so "
            "how the items are wired for lineage cannot be determined"
        )

    buckets: dict[str, list[str]] = {"wired": [], "mixed": [], "opaque": [], "none": []}
    for name, text in pipeline_texts(ctx.workspace).items():
        buckets[_wiring(text, is_pipeline=True, attached=False)].append(name)
    notebooks = ctx.workspace.notebooks or {}
    for name, text in notebook_texts(ctx.workspace).items():
        attached = bool(attached_lakehouses(notebooks.get(name) or {}))
        buckets[_wiring(text, is_pipeline=False, attached=attached)].append(name)

    population = len(buckets["wired"]) + len(buckets["mixed"]) + len(buckets["opaque"])
    if not population:
        return not_applicable(
            f"None of the {len(buckets['none'])} pipeline/notebook definition(s) read "
            f"contains a data reference, so there is no data flow here for the lineage "
            f"view to connect"
        )

    detail = (f"{len(buckets['wired'])} of {population} data-processing item(s) reference "
              f"their data through an attached lakehouse or a named Fabric item, so the "
              f"built-in lineage view can connect them")
    if buckets["mixed"]:
        detail += (f"; {len(buckets['mixed'])} also read or write via a hard-coded path "
                   f"({', '.join(sorted(buckets['mixed'])[:_MAX_NAMED])}), so part of their "
                   f"flow is missing from the view")
    if buckets["opaque"]:
        detail += (f"; {len(buckets['opaque'])} reference data only by hard-coded "
                   f"abfss:// or https:// path "
                   f"({', '.join(sorted(buckets['opaque'])[:_MAX_NAMED])}) and appear in "
                   f"lineage with nothing attached")
    detail += (". Whether the team consults the view is a human practice and is not "
               "readable here.")
    return covered(len(buckets["wired"]), population, detail)


@check(
    id="GOV-LINEAGE-CROSSDOMAIN", ref="8.1.5",
    title="Cross-domain data dependencies documented in lineage",
    pillar=Pillar.DATA_GOVERNANCE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS,
              Resource.SHORTCUTS],
    required=True,
)
def cross_domain_dependencies_identifiable(ctx: CheckContext) -> Verdict:
    """Every dependency that leaves this workspace is declared as something a human can name.

    **What it can determine.** Which references cross the workspace boundary —
    a OneLake ``abfss://`` path whose workspace segment is not this workspace, an
    external storage URL (ADLS / Blob / S3 / GCS), or a OneLake shortcut — and
    whether each is *identifiable* (a shortcut, which is a first-class Fabric
    object with a name and a target type, or a path that names its workspace and
    item) or *opaque* (a bare GUID or a raw URL with no Fabric item behind it).
    An opaque reference is a cross-domain dependency nobody can trace back to an
    owning team.

    **What the Fabric lineage view itself can show, and cannot.** It shows an
    upstream external source only **one level up**, and it shows no downstream
    cross-workspace consumer at all. So even a perfectly identifiable dependency
    is only half-visible there: the downstream half has to be documented outside
    the view. This check therefore scores whether the dependency is *nameable*,
    which is the precondition for documenting it — not whether it is documented.

    **What it cannot.** It cannot resolve a GUID to an item (that would need a
    read on another workspace, which the crawl does not perform), cannot see a
    dependency expressed only at runtime, and cannot see a consumer in another
    workspace reading *from* here. No cross-boundary reference at all is N/A —
    a self-contained workspace has no cross-domain dependency to document.
    """
    readable = [r for r in (Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS,
                            Resource.SHORTCUTS) if ctx.workspace.has(r)]
    if not readable:
        return not_applicable(
            "Neither the item definitions nor the shortcuts could be read from Fabric, "
            "so cross-workspace dependencies cannot be identified"
        )

    workspace_names = {
        (ctx.workspace.display_name or "").strip().lower(),
        (ctx.workspace.id or "").strip().lower(),
    } - {""}

    identifiable: list[str] = []
    opaque: list[str] = []

    for lakehouse, shortcuts in (ctx.workspace.shortcuts or {}).items():
        for shortcut in shortcuts or []:
            if not isinstance(shortcut, dict):
                continue
            label = f"{lakehouse}/{shortcut.get('name') or '?'}"
            if shortcut.get("name") and shortcut.get("target_type"):
                identifiable.append(f"{label} -> {shortcut.get('target_type')}")
            else:
                opaque.append(label)

    texts = dict(pipeline_texts(ctx.workspace))
    texts.update(notebook_texts(ctx.workspace))
    for name, text in texts.items():
        for workspace_segment, item_segment in ONELAKE_PATH.findall(text):
            if workspace_segment.strip().lower() in workspace_names:
                continue  # a path back into this same workspace is not cross-domain
            if GUID.match(workspace_segment) or GUID.match(item_segment):
                opaque.append(f"{name} -> {workspace_segment}/{item_segment}")
            else:
                identifiable.append(f"{name} -> {workspace_segment}/{item_segment}")
        if _EXTERNAL_STORE.search(text):
            opaque.append(f"{name} -> external storage URL")

    total = len(identifiable) + len(opaque)
    if not total:
        return not_applicable(
            "No reference in this workspace's shortcuts or item definitions leaves the "
            "workspace, so there is no cross-domain dependency to document"
        )

    detail = (f"{len(identifiable)} of {total} cross-workspace/external reference(s) name "
              f"their source (a shortcut or a named workspace/item path)")
    if identifiable:
        detail += f": {', '.join(sorted(identifiable)[:_MAX_NAMED])}"
    if opaque:
        detail += (f"; {len(opaque)} are opaque — a bare GUID or a raw storage URL with no "
                   f"Fabric item behind it ({', '.join(sorted(opaque)[:_MAX_NAMED])})")
    detail += (". Fabric's lineage view shows an external source only one level upstream "
               "and no downstream cross-workspace consumer, so the downstream half of "
               "each dependency has to be documented outside the view.")
    return covered(len(identifiable), total, detail)


@check(
    id="GOV-FIN-CHANGE-AUDIT", ref="7.2.3",
    title="Audit trail for all data modifications in financial-relevant data",
    pillar=Pillar.COMPLIANCE, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=_LAYERS, requires=[Resource.ITEMS, Resource.WAREHOUSE_AUDIT], required=True,
)
def financial_data_modifications_audited(ctx: CheckContext) -> Verdict:
    """Warehouse auditing is configured to capture data modifications, not only logins.

    **What it can determine.** For every Warehouse whose ``settings/sqlAudit``
    could be read: whether auditing is on, and whether the configured action
    groups actually record data modifications. Only a batch/statement group
    (``BATCH_COMPLETED_GROUP``) puts an INSERT / UPDATE / DELETE / MERGE in the
    audit; the object-change groups record DDL and permission changes, and the
    authentication groups record connections. Auditing that is enabled but
    configured with authentication groups alone answers "who signed in", never
    "what changed", and is scored as the partial control it is.

    **What it cannot — deliberately.** It never reads audit *rows*
    (``sys.fn_get_audit_file_v2``): those are runtime data, out of scope for a
    configuration auditor and explicitly excluded from the knowledge base. So it
    cannot confirm a modification was actually recorded, cannot say how long the
    output is retained in practice, and cannot see a modification made outside
    the Warehouse — a Lakehouse table rewritten by a notebook is not covered by
    SQL audit at all. It also does not know which schema is "Finance": every
    Warehouse is judged, because the audit setting is per-Warehouse, not per
    schema. A workspace with no Warehouse, or one whose setting could not be
    read, is N/A.

    **Sibling.** ``GOV-WH-AUDIT`` (ref 7.4.6) asks only whether auditing is
    *enabled*; this asks whether what it captures answers the change-audit
    question. A Warehouse with login-only auditing passes there and does not
    pass here.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [i for i in ctx.workspace.items if i.type == "Warehouse"]
    if not warehouses:
        return not_applicable(
            "This workspace holds no Warehouse, so there is no Warehouse audit "
            "configuration to judge"
        )
    if not ctx.workspace.has(Resource.WAREHOUSE_AUDIT):
        return not_applicable(
            f"The SQL audit setting could not be read for any of the {len(warehouses)} "
            f"Warehouse(s) (the Audit permission on the item is required)"
        )
    settings = ctx.workspace.warehouse_audit or {}
    if not settings:
        return not_applicable(
            f"No SQL audit setting was returned for the {len(warehouses)} Warehouse(s) "
            f"in this workspace, so what auditing captures cannot be determined"
        )

    modifications = sorted(n for n, s in settings.items() if captures_data_modifications(s))
    ddl_only = sorted(n for n, s in settings.items()
                      if n not in modifications and captures_object_changes(s))
    login_only = sorted(n for n, s in settings.items()
                        if audit_enabled(s) and n not in modifications and n not in ddl_only)
    off = sorted(n for n, s in settings.items() if not audit_enabled(s))

    detail = (f"{len(modifications)} of {len(settings)} Warehouse(s) audit data "
              f"modifications (a batch/statement action group is configured)")
    if modifications:
        detail += f": {', '.join(modifications[:_MAX_NAMED])}"
    if ddl_only:
        groups = sorted(action_groups(settings[ddl_only[0]]))[:3]
        detail += (f"; {len(ddl_only)} audit structural change only "
                   f"({', '.join(ddl_only[:_MAX_NAMED])} — {', '.join(groups)}), so a row "
                   f"edit leaves no trace")
    if login_only:
        detail += (f"; {len(login_only)} have auditing on but capture connections only "
                   f"({', '.join(login_only[:_MAX_NAMED])})")
    if off:
        detail += f"; {len(off)} have auditing disabled ({', '.join(off[:_MAX_NAMED])})"
    if len(settings) < len(warehouses):
        detail += (f"; the setting could not be read for "
                   f"{len(warehouses) - len(settings)} further Warehouse(s), which are "
                   f"excluded rather than failed")
    detail += (". Judged from the audit configuration only — audit rows are runtime data "
               "and are never read.")

    if not modifications and (ddl_only or login_only):
        # Enabled everywhere but capturing the wrong thing is a real, partial
        # control: better than nothing, and not the thing this point asks for.
        return graded(1, detail)
    return covered(len(modifications), len(settings), detail)
