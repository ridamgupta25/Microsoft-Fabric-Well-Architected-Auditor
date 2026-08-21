"""Operations & Reliability · Data Operations — cross-workspace (group) checks.

Checks here compare the members of a project group (Dev → UAT → Prod) against
one another, rather than judging a single workspace in isolation. They register
into the separate ``GROUP_REGISTRY`` via :func:`group_check`, run once per group,
and obey the same rules as every check: pure, deterministic, and **N/A-not-FAIL**
when the data needed to compare is missing.
"""
from __future__ import annotations

import json

import re

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext


def _table_signatures(member: GroupMemberContext) -> dict[str, frozenset[tuple[str, str]]] | None:
    """A member's ``{table -> {(column, type)}}`` signature, or None if unreadable.

    Table and column names are lower-cased because SQL identifiers are not
    case-sensitive, so a mere casing difference is not schema drift.
    """
    workspace = member.workspace
    if not workspace.has(Resource.TABLE_COLUMNS):
        return None
    signature: dict[str, frozenset[tuple[str, str]]] = {}
    for name, table in workspace.tables.items():
        columns = table.get("columns") or []
        if not columns:
            continue
        signature[str(name).lower()] = frozenset(
            (str(col.get("name", "")).lower(), str(col.get("type", "")).lower())
            for col in columns
            if isinstance(col, dict) and col.get("name")
        )
    return signature


@group_check(
    id="XW-SCHEMA-DRIFT",
    ref="11.4.3",
    title="Schema is consistent across environments (no drift)",
    pillar=Pillar.RELIABILITY,
    severity=Severity.HIGH,
    requires=[Resource.TABLE_COLUMNS],
)
def schema_drift(ctx: GroupContext) -> Verdict:
    """Table schemas match across the group's environments (Dev/UAT/Prod).

    Drift is either a table present in some environments but not others, or a
    table whose column set (name + type) differs between them. A workspace whose
    column schemas could not be read is left out of the comparison; when fewer
    than two members remain there is nothing to compare and the check is N/A.
    """
    labelled = []
    for member in ctx.members:
        signature = _table_signatures(member)
        if signature is None:
            continue
        label = f"{member.workspace.display_name} (L{member.environment_level})"
        labelled.append((label, signature))

    if len(labelled) < 2:
        return not_applicable(
            "fewer than two environments in this group had readable table "
            "schemas (SQL analytics endpoint) to compare"
        )

    all_tables = sorted(set().union(*(set(sig) for _, sig in labelled)))
    if not all_tables:
        return not_applicable("no tables were found to compare across environments")

    labels = [label for label, _ in labelled]
    drifted: list[str] = []
    for table in all_tables:
        present_in = [label for label, sig in labelled if table in sig]
        if len(present_in) != len(labelled):
            missing = sorted(set(labels) - set(present_in))
            drifted.append(f"{table} (missing in {', '.join(missing)})")
            continue
        distinct = {sig[table] for _, sig in labelled}
        if len(distinct) > 1:
            drifted.append(f"{table} (column mismatch)")

    consistent = len(all_tables) - len(drifted)
    if not drifted:
        return covered(
            consistent, len(all_tables),
            f"all {len(all_tables)} table(s) match across {len(labels)} "
            f"environments ({', '.join(labels)})",
        )
    shown = "; ".join(drifted[:5])
    more = "" if len(drifted) <= 5 else f" (+{len(drifted) - 5} more)"
    return covered(
        consistent, len(all_tables),
        f"{len(drifted)} of {len(all_tables)} table(s) drift across "
        f"{', '.join(labels)}: {shown}{more}",
    )


@group_check(
    id="XW-MEDALLION-CONSIST", ref="1.1.5",
    title="Medallion architecture (Bronze -> Silver -> Gold) implemented consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, requires=[Resource.ITEMS],
    required=False,
)
def medallion_consistent(ctx: GroupContext) -> Verdict:
    """Every environment declares the medallion tiers, not just production.

    An environment "implements" the architecture when its data stores (or its own
    name) name at least one medallion tier. Comparing across the group catches a
    medallion built in Prod but never carried back to Dev/UAT. N/A when fewer than
    two members' item inventories could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEMS),
        implements=lambda ws: bool(_xw.medallion_tiers(ws)),
        practice="declares medallion tiers",
        data_name="item inventories",
    )


@group_check(
    id="XW-PIPELINE-SLA", ref="9.4.2",
    title="Pipeline completion SLAs are monitored consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def pipeline_sla_monitored(ctx: GroupContext) -> Verdict:
    """Pipelines carry run history (are monitored) in every environment.

    Run history is the readable evidence that a pipeline's completion is being
    tracked. An environment whose pipelines have no recorded runs is not being
    monitored to the same standard as its peers. N/A when fewer than two members'
    run history could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEM_RUN_HISTORY) and ws.has(Resource.ITEMS),
        implements=lambda ws: _xw.has_run_history(ws, {"DataPipeline"}),
        practice="monitors pipeline completion (run history present)",
        data_name="pipeline run history",
    )


@group_check(
    id="XW-SLA-ALERTS", ref="9.4.3",
    title="SLA breaches trigger alerts (Data Activator) consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.HIGH,
    requires=[Resource.ACTIVATOR_DEFINITIONS], required=False,
)
def sla_alerts_consistent(ctx: GroupContext) -> Verdict:
    """A Data Activator with rules exists in every environment, not just one.

    Alerting configured only in Prod leaves Dev/UAT breaches silent. An
    environment "implements" alerting when it holds at least one Activator with a
    configured rule. N/A when fewer than two members' Activator definitions could
    be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ACTIVATOR_DEFINITIONS),
        implements=_xw.has_active_activator,
        practice="has a Data Activator rule for SLA breaches",
        data_name="Data Activator definitions",
    )


@group_check(
    id="XW-SLA-HISTORY", ref="9.4.4",
    title="Historical SLA compliance is tracked consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def sla_history_consistent(ctx: GroupContext) -> Verdict:
    """Runnable items retain execution history in every environment.

    Historical SLA reporting needs recorded runs to report on. An environment
    whose pipelines and notebooks have no run history cannot show historical
    compliance. N/A when fewer than two members' run history could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEM_RUN_HISTORY) and ws.has(Resource.ITEMS),
        implements=lambda ws: _xw.has_run_history(ws, {"DataPipeline", "Notebook"}),
        practice="retains execution history for SLA reporting",
        data_name="run history",
    )


@group_check(
    id="XW-TIER-SEP", ref="11.3.1",
    title="Each environment in the group declares a distinct Dev/QA/Prod tier",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE], required=False,
)
def tier_separation(ctx: GroupContext) -> Verdict:
    """Every member workspace names its environment tier.

    A project group is meant to span separated Dev/QA/Prod workspaces; a member
    whose name declares no tier cannot be placed in that separation. N/A when the
    group has fewer than two members.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: True,
        implements=_xw.declares_env_tier,
        practice="declares an environment tier in its name",
        data_name="workspace names",
    )


@group_check(
    id="XW-MEDALLION-DRIFT", ref="11.4.3",
    title="Medallion tiers are present in every environment (no tier drift)",
    pillar=Pillar.RELIABILITY, severity=Severity.HIGH, requires=[Resource.ITEMS],
    required=False,
)
def medallion_no_drift(ctx: GroupContext) -> Verdict:
    """Every environment carries the full set of medallion tiers the group uses.

    The reference is the union of tiers declared across the group; an environment
    missing a tier its peers have is tier drift. N/A when fewer than two members'
    item inventories could be read, or no environment declares any tier.
    """
    return _xw.superset_consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEMS),
        signature=_xw.medallion_tiers,
        practice="carries every medallion tier the group declares",
        data_name="medallion tiers",
    )


#: Resources scanned for a cross-environment workspace reference.
_ISOLATION_RESOURCES = (
    Resource.PIPELINE_DEFINITIONS,
    Resource.NOTEBOOK_DEFINITIONS,
    Resource.SHORTCUTS,
    Resource.REPORTS,
)


def _inspectable(ws: WorkspaceContext) -> bool:
    """True when the member has an id and at least one scannable definition set."""
    return bool(ws.id) and any(ws.has(r) for r in _ISOLATION_RESOURCES)


def _reference_blob(ws: WorkspaceContext) -> str:
    """The member's definitions and report bindings as lower-cased metadata.

    Workspace references inside a definition are GUIDs, so a single-workspace
    crawl cannot tell whose workspace they point at. In a group we hold every
    member's id, so a substring match resolves the reference.
    """
    parts: list[str] = []
    if ws.has(Resource.PIPELINE_DEFINITIONS):
        parts.append(json.dumps(ws.pipelines))
    if ws.has(Resource.NOTEBOOK_DEFINITIONS):
        parts.append(json.dumps(ws.notebooks))
    if ws.has(Resource.SHORTCUTS):
        parts.append(json.dumps(ws.shortcuts))
    if ws.has(Resource.REPORTS):
        parts.append(json.dumps(ws.reports))
    return " ".join(parts).lower()


def _connection_catalog(members: list[GroupMemberContext]) -> dict[str, str]:
    """Tenant connection id -> readable label, deduplicated across snapshots."""
    catalog: dict[str, str] = {}
    for member in members:
        workspace = member.workspace
        if not workspace.has(Resource.CONNECTIONS):
            continue
        for connection in workspace.connections:
            connection_id = str(connection.get("id") or "").strip().lower()
            if connection_id:
                catalog[connection_id] = str(
                    connection.get("display_name") or connection.get("endpoint")
                    or connection_id
                )
    return catalog


#: A physical storage location hardcoded in a definition. OneLake paths are
#: excluded below because they embed a workspace/item id already matched as a
#: cross-workspace reference; what remains is external storage (ADLS / blob /
#: S3 / GCS) that two environments can point at the same mutable copy of.
_EXTERNAL_STORE_RE = re.compile(
    r"(?:abfss|wasbs?|s3a?|gs)://[^\s\"'`),;]+"
    r"|https://[\w.-]*(?:dfs|blob)\.core\.windows\.net[^\s\"'`),;]*",
    re.IGNORECASE,
)


def _external_stores(ws: WorkspaceContext) -> set[str]:
    """Non-OneLake storage locations hardcoded in this member's definitions."""
    stores: set[str] = set()
    texts: list[str] = []
    if ws.has(Resource.NOTEBOOK_DEFINITIONS):
        texts.extend(executable_code(defn) for defn in ws.notebooks.values())
    if ws.has(Resource.PIPELINE_DEFINITIONS):
        texts.extend(json.dumps(defn) for defn in ws.pipelines.values())
    for text in texts:
        for match in _EXTERNAL_STORE_RE.finditer(text):
            location = match.group(0).split("?", 1)[0].rstrip("/\\\"'`").lower()
            if "onelake" not in location:
                stores.add(location)
    return stores


@group_check(
    id="XW-ENV-ISOLATION", ref="1.1.3",
    title="Environment isolation enforced (Dev / QA / Prod workspaces have no "
          "shared mutable artifacts or cross-env dependencies)",
    pillar=Pillar.ARCHITECTURE, severity=Severity.MEDIUM,
    requires=[
        Resource.WORKSPACE, Resource.ITEMS, Resource.PIPELINE_DEFINITIONS,
        Resource.NOTEBOOK_DEFINITIONS, Resource.SHORTCUTS, Resource.REPORTS,
        Resource.CONNECTIONS,
    ],
    required=False,
)
def environment_isolation_consistent(ctx: GroupContext) -> Verdict:
    """No environment references another's workspace/artifacts or shares a connection.

    Pipeline, notebook, shortcut and report metadata is matched against every
    member's workspace and artifact ids. Tenant connections are only considered
    shared when the same stable connection id actually occurs in metadata from
    more than one environment; merely appearing in the tenant-wide connection
    catalog is not evidence of use. A non-OneLake storage location (ADLS / blob /
    S3 / GCS) hardcoded in more than one environment is flagged as a shared
    mutable artifact. N/A when fewer than two members have inspectable metadata.
    """
    members = [m for m in ctx.members if _inspectable(m.workspace)]
    if len(members) < 2:
        return not_applicable(
            "fewer than two environments in this group had readable pipeline, "
            "notebook, shortcut or report metadata to compare for cross-environment "
            "dependencies"
        )

    id_to_label = {
        m.workspace.id.lower(): _xw.env_label(m) for m in members
    }
    blobs = {m.workspace.id.lower(): _reference_blob(m.workspace) for m in members}
    artifact_targets: dict[str, str] = {}
    for member in members:
        target_label = _xw.env_label(member)
        for item in member.workspace.items:
            item_id = item.id.strip().lower()
            if item_id:
                artifact_targets[item_id] = (
                    f"{target_label} {item.type} '{item.display_name or item.id}'"
                )

    offenders: dict[str, set[str]] = {}
    for member in members:
        own_id = member.workspace.id.lower()
        blob = blobs[own_id]
        findings = {
            f"workspace {label}"
            for other_id, label in id_to_label.items()
            if other_id != own_id and other_id in blob
        }
        own_item_ids = {item.id.strip().lower() for item in member.workspace.items}
        findings.update(
            f"artifact {target}"
            for item_id, target in artifact_targets.items()
            if item_id not in own_item_ids and item_id in blob
        )
        if findings:
            offenders[_xw.env_label(member)] = findings

    connection_catalog = _connection_catalog(members)
    connection_users: dict[str, list[GroupMemberContext]] = {}
    for connection_id in connection_catalog:
        users = [m for m in members if connection_id in blobs[m.workspace.id.lower()]]
        if len(users) > 1:
            connection_users[connection_id] = users
    for connection_id, users in connection_users.items():
        connection_label = connection_catalog[connection_id]
        for member in users:
            offenders.setdefault(_xw.env_label(member), set()).add(
                f"shared connection '{connection_label}'"
            )

    stores_by_env = {
        m.workspace.id.lower(): _external_stores(m.workspace) for m in members
    }
    for store in set().union(*stores_by_env.values()) if stores_by_env else set():
        sharers = [m for m in members if store in stores_by_env[m.workspace.id.lower()]]
        if len(sharers) > 1:
            for member in sharers:
                offenders.setdefault(_xw.env_label(member), set()).add(
                    f"shared external storage '{store}'"
                )

    total = len(members)
    if not offenders:
        return covered(
            total, total,
            f"all {total} environment(s) are isolated: no pipelines, notebooks, "
            f"shortcuts or reports reference another environment's workspace or "
            f"artifacts, and no referenced connection is shared",
        )

    detail = "; ".join(
        f"{label} depends on {', '.join(sorted(refs))}"
        for label, refs in sorted(offenders.items())[:3]
    )
    return covered(
        total - len(offenders), total,
        f"{len(offenders)} of {total} environment(s) have a cross-environment "
        f"dependency: {detail}",
    )
