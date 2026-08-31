"""Shared helpers for the cross-workspace (``@group_check``) consistency checks.

A cross-workspace check asks the *same* question of every environment in a
project group (Dev -> UAT -> Prod) and reports whether the practice is applied
**consistently** across them, rather than judging one workspace in isolation.

These helpers turn a per-environment predicate into a single
:class:`~auditfast.core.check.helpers.Verdict`, so each group check stays a few
lines long and obeys the same rules as every check: pure, deterministic, and
**N/A-not-FAIL** — when fewer than two environments can be read there is nothing
to compare, so the result is N/A, never a low score.

The module name starts with ``_`` so the check loader does not import it as a
check module; it is imported explicitly by the ``group.py`` files that use it.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from ..models import GroupContext, GroupMemberContext, WorkspaceContext
from .helpers import Verdict, covered, not_applicable

#: Fabric item types that hold data (a store), used by the medallion, lineage and
#: warehouse-load detectors.
DATA_STORE_TYPES: frozenset[str] = frozenset(
    {"Lakehouse", "Warehouse", "SQLDatabase", "MirroredWarehouse", "SQLEndpoint"}
)

#: Item types that *produce* data (the upstream end of a lineage chain).
SOURCE_TYPES: frozenset[str] = frozenset({"DataPipeline", "Notebook", "Dataflow"})

#: Item types that *serve* data to people (the downstream end of a lineage chain).
REPORTING_TYPES: frozenset[str] = frozenset({"SemanticModel", "Report", "PaginatedReport"})

#: Name tokens that place a store in a medallion tier, mapped to the tier.
MEDALLION_TOKENS: dict[str, str] = {
    "bronze": "Bronze", "raw": "Bronze", "landing": "Bronze", "ingest": "Bronze",
    "ingestion": "Bronze", "source": "Bronze",
    "silver": "Silver", "cleansed": "Silver", "clean": "Silver",
    "conformed": "Silver", "refined": "Silver", "enriched": "Silver",
    "gold": "Gold", "curated": "Gold", "mart": "Gold", "datamart": "Gold",
    "presentation": "Gold", "serving": "Gold",
}

#: Name tokens that declare an environment tier.
TIER_TOKENS: frozenset[str] = frozenset(
    {"dev", "development", "sit", "test", "qa", "uat", "staging", "stage",
     "preprod", "pre-prod", "prod", "production"}
)

#: Column-name synonyms an audit table should carry, one entry per required field.
AUDIT_COLUMN_GROUPS: dict[str, frozenset[str]] = {
    "timestamp": frozenset({"timestamp", "event_timestamp", "eventtime", "event_time",
                            "created_at", "createdon", "modified_at", "modifiedon"}),
    "operation": frozenset({"operation", "operation_type", "action", "action_type"}),
    "user_id": frozenset({"userid", "user_id", "user", "user_name", "username",
                          "created_by", "modified_by"}),
    "status": frozenset({"status", "state"}),
}

#: Name substrings that mark a store/table as holding audit or metadata content.
AUDIT_NAME_HINTS: frozenset[str] = frozenset(
    {"audit", "compliance", "log", "evidence", "metadata"}
)

#: Workspace-role names that grant an operations reader query access.
OPERATIONS_ROLES: frozenset[str] = frozenset(
    {"viewer", "member", "contributor", "admin"}
)


def _tokens(name: str) -> set[str]:
    """Lower-cased word tokens of a name, split on common separators."""
    out: set[str] = set()
    word = []
    for ch in str(name or "").lower():
        if ch.isalnum():
            word.append(ch)
        else:
            if word:
                out.add("".join(word))
                word = []
    if word:
        out.add("".join(word))
    return out


def env_label(member: GroupMemberContext) -> str:
    """A stable, name-plus-level label for one environment in a group."""
    return f"{member.workspace.name} (L{member.environment_level})"


#: Workspace-name tokens that name a human environment tier (Dev / UAT / Prod).
TIER_LABELS: dict[str, str] = {
    "dev": "Dev", "development": "Dev",
    "sit": "SIT", "test": "Test", "qa": "QA", "uat": "UAT",
    "staging": "Staging", "stage": "Staging", "preprod": "Pre-Prod",
    "prod": "Prod", "production": "Prod",
}


def env_tier(member: GroupMemberContext) -> str:
    """A plain environment name (Dev / UAT / Prod / ...) for one group member.

    Derived from the first tier token in the workspace name, so a report never
    prints an opaque ``L{n}``. Falls back to the workspace's own name when the
    name carries no recognised tier token, so the label is always concrete.
    """
    for token in re.findall(r"[a-z0-9]+", member.workspace.name.lower()):
        if token in TIER_LABELS:
            return TIER_LABELS[token]
    return member.workspace.name


def member_label(member: GroupMemberContext) -> str:
    """``Dev (workspace 'MLC_Fabric_DEV')`` — the tier plus the named workspace."""
    tier = env_tier(member)
    name = member.workspace.name
    return f"{tier} (workspace '{name}')" if tier != name else f"workspace '{name}'"


def bold_member(member: GroupMemberContext) -> str:
    """``**Dev** (workspace 'MLC_Fabric_DEV')`` — a bolded tier plus the workspace."""
    tier = env_tier(member)
    name = member.workspace.name
    return f"**{tier}** (workspace '{name}')" if tier != name else f"workspace **'{name}'**"


def and_list(items: list[str]) -> str:
    """``['Dev', 'UAT', 'Prod'] -> 'Dev, UAT and Prod'`` for readable prose."""
    items = [item for item in items if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def consistency(
    ctx: GroupContext,
    *,
    readable: Callable[[WorkspaceContext], bool],
    implements: Callable[[WorkspaceContext], bool],
    practice: str,
    data_name: str,
) -> Verdict:
    """Score whether ``implements`` holds in every *readable* environment.

    ``readable`` decides which members carry the data needed to judge; members
    that do not are left out of the comparison. When fewer than two remain the
    check is N/A. Otherwise the verdict is the coverage ratio of environments
    that implement the practice, so a practice present in Prod but missing in Dev
    is surfaced as drift rather than a pass.
    """
    present: list[str] = []
    absent: list[str] = []
    for member in ctx.members:
        if not readable(member.workspace):
            continue
        (present if implements(member.workspace) else absent).append(env_label(member))
    total = len(present) + len(absent)
    if total < 2:
        return not_applicable(
            f"fewer than two environments in this group had readable {data_name} "
            f"to compare"
        )
    if not absent:
        return covered(total, total,
                       f"all {total} environment(s) {practice}: {', '.join(present)}")
    return covered(
        len(present), total,
        f"{practice} in {len(present)} of {total} environment(s); not in "
        f"{', '.join(absent)}",
    )


def superset_consistency(
    ctx: GroupContext,
    *,
    readable: Callable[[WorkspaceContext], bool],
    signature: Callable[[WorkspaceContext], set[str]],
    practice: str,
    data_name: str,
) -> Verdict:
    """Score whether every environment carries the group-wide *superset* of items.

    ``signature`` returns the set of things (e.g. dimension table names, medallion
    tiers) an environment has. The reference is the union across all readable
    members; an environment is compliant when it contains that whole union, so an
    object present in one environment but missing from another is drift.
    """
    labelled: list[tuple[str, set[str]]] = []
    for member in ctx.members:
        if not readable(member.workspace):
            continue
        labelled.append((env_label(member), signature(member.workspace)))
    if len(labelled) < 2:
        return not_applicable(
            f"fewer than two environments in this group had readable {data_name} "
            f"to compare"
        )
    reference: set[str] = set().union(*(sig for _, sig in labelled))
    if not reference:
        return not_applicable(f"no {data_name} were found to compare across environments")
    present = [label for label, sig in labelled if sig >= reference]
    absent = [label for label, sig in labelled if not sig >= reference]
    if not absent:
        return covered(len(labelled), len(labelled),
                       f"all {len(labelled)} environment(s) {practice}: {', '.join(present)}")
    return covered(
        len(present), len(labelled),
        f"{practice} in {len(present)} of {len(labelled)} environment(s); "
        f"incomplete in {', '.join(absent)}",
    )


# -- per-workspace detectors, shared across the group checks -------------------

def medallion_tiers(ws: WorkspaceContext) -> set[str]:
    """The medallion tiers (Bronze/Silver/Gold) the workspace's stores declare."""
    tiers: set[str] = set()
    for item in ws.items:
        if item.type in DATA_STORE_TYPES:
            for tok in _tokens(item.display_name):
                if tok in MEDALLION_TOKENS:
                    tiers.add(MEDALLION_TOKENS[tok])
    for tok in _tokens(ws.name):
        if tok in MEDALLION_TOKENS:
            tiers.add(MEDALLION_TOKENS[tok])
    return tiers


def declares_env_tier(ws: WorkspaceContext) -> bool:
    """True when the workspace name declares an environment tier."""
    return bool(_tokens(ws.name) & TIER_TOKENS)


def dimension_table_names(ws: WorkspaceContext) -> set[str]:
    """Lower-cased names of the workspace's dimension-like tables."""
    names: set[str] = set()
    for name in ws.tables:
        low = str(name).lower()
        bare = low.split(".")[-1]
        if bare.startswith("dim") or "_dim_" in low or bare.endswith(("_dimension", "_dimensions")):
            names.add(bare)
    return names


def _columns(table: dict) -> set[str]:
    return {
        str(col.get("name", "")).lower()
        for col in (table.get("columns") or [])
        if isinstance(col, dict) and col.get("name")
    }


def has_structured_audit_table(ws: WorkspaceContext) -> bool:
    """True when an audit-named table carries all required audit column groups."""
    for name, table in ws.tables.items():
        if not (_tokens(name) & AUDIT_NAME_HINTS):
            continue
        cols = _columns(table)
        if all(cols & group for group in AUDIT_COLUMN_GROUPS.values()):
            return True
    return False


def audit_store_items(ws: WorkspaceContext) -> list:
    """Store items whose name marks them as holding audit or metadata content."""
    return [
        item for item in ws.items
        if item.type in DATA_STORE_TYPES and (_tokens(item.display_name) & AUDIT_NAME_HINTS)
    ]


def grants_operations_read(ws: WorkspaceContext) -> bool:
    """True when a workspace role assignment grants an operations reader access."""
    return any(str(r.role).lower() in OPERATIONS_ROLES for r in ws.role_assignments)


def has_item_type(ws: WorkspaceContext, types: Iterable[str]) -> bool:
    """True when the workspace inventory holds an item of one of ``types``."""
    wanted = set(types)
    if wanted & {"DataPipeline"} and ws.pipelines:
        return True
    return any(item.type in wanted for item in ws.items)


def item_ids_of_types(ws: WorkspaceContext, types: Iterable[str]) -> list[str]:
    wanted = set(types)
    return [item.id for item in ws.items if item.type in wanted]


def has_run_history(ws: WorkspaceContext, types: Iterable[str]) -> bool:
    """True when at least one item of ``types`` has recorded run history."""
    ids = set(item_ids_of_types(ws, types))
    if any(ws.run_history.get(i) for i in ids):
        return True
    # Pipelines are sometimes fetched into ``pipelines`` rather than ``items``;
    # fall back to "any run history at all" when the type is present by name.
    return bool(ws.run_history) and has_item_type(ws, types)


def has_active_activator(ws: WorkspaceContext) -> bool:
    """True when the workspace has a Data Activator with at least one rule."""
    return any(int(a.get("rules", 0) or 0) > 0 for a in ws.activators.values())


def has_enabled_warehouse_audit(ws: WorkspaceContext) -> bool:
    """True when at least one Warehouse has SQL audit enabled."""
    return any(a.get("enabled") for a in ws.warehouse_audit.values())


def has_columns_captured(ws: WorkspaceContext) -> bool:
    """True when at least one table has column definitions captured."""
    return any(_columns(table) for table in ws.tables.values())


# -- capture-completeness signal ----------------------------------------------

def has_recoverable_read_failures(ws: WorkspaceContext) -> bool:
    """True when the crawl hit a *recoverable* (forbidden/throttled) read failure.

    A workspace's :attr:`WorkspaceContext.is_complete` is also false when a core
    list (role assignments) simply needs a higher role — which says nothing about
    whether its data-plane inventory (semantic models, reports, definitions) was
    captured. This narrower signal is true only when a per-item definition/table
    read was *blocked* (401/403) or *throttled* (429/5xx), i.e. the crawl was
    genuinely degraded and its object inventory may be short of the real estate.
    A group check must not read a missing object as "absent" for such a member —
    it is "not fetched", which is N/A, not FAIL.
    """
    return any(
        stat.get("forbidden") or stat.get("transient")
        for stat in ws.read_failures.values()
    )


# -- strict, type-resolved run history ----------------------------------------

def has_typed_run_history(ws: WorkspaceContext, types: Iterable[str]) -> bool:
    """True when an item *whose type is in* ``types`` has recorded run history.

    Unlike :func:`has_run_history` this never falls back to "any run history at
    all" — a workspace whose only recorded runs are Notebook runs is *not*
    counted as having DataPipeline history. Resolving each ``run_history`` key to
    its item type is what stops a notebook-only environment being miscounted as
    pipeline-monitored.
    """
    wanted = set(types)
    id_to_type = {item.id: item.type for item in ws.items}
    for item_id, stamps in ws.run_history.items():
        if not stamps:
            continue
        if id_to_type.get(item_id) in wanted:
            return True
    return False


def typed_run_history_count(ws: WorkspaceContext, types: Iterable[str]) -> int:
    """How many items *whose type is in* ``types`` have recorded run history.

    Counts distinct items with at least one recorded run, so "8 pipelines have
    recorded runs" is the number of monitored pipelines, not the number of run
    events.
    """
    wanted = set(types)
    id_to_type = {item.id: item.type for item in ws.items}
    return sum(
        1 for item_id, stamps in ws.run_history.items()
        if stamps and id_to_type.get(item_id) in wanted
    )


def has_enabled_schedule(ws: WorkspaceContext) -> bool:
    """True when at least one refresh/trigger schedule is *enabled* (not just present)."""
    return any(schedule.get("enabled") for schedule in ws.refresh_schedules.values())


def pipeline_item_count(ws: WorkspaceContext) -> int:
    """How many DataPipeline items the workspace inventory holds."""
    items = sum(1 for item in ws.items if item.type == "DataPipeline")
    # Pipelines are also fetched by definition into ``pipelines``; use whichever
    # is larger so a workspace with pipeline definitions but no typed item rows
    # (older fixtures) is still counted as having pipelines.
    return max(items, len(ws.pipelines))


# -- queryable audit-table schema (no acting-user requirement) -----------------

#: Column-name synonyms for the four groups that make an ETL/run audit table
#: *queryable* — deliberately without an acting-user column, which belongs to a
#: data-access audit, not an operational run log.
AUDIT_QUERYABLE_GROUPS: dict[str, frozenset[str]] = {
    "event_time": frozenset({
        "timestamp", "event_timestamp", "eventtime", "event_time", "created_at",
        "createdon", "modified_at", "modifiedon", "start_time", "end_time",
        "start_time_utc", "end_time_utc", "run_time", "log_time", "load_date",
        "load_timestamp", "execution_timestamp",
    }),
    "operation": frozenset({
        "operation", "operation_type", "action", "action_type", "pipeline_name",
        "activity_name", "notebook_name", "item_name", "stage", "task_name",
        "job_name", "process_name", "event", "event_type", "source_name",
        "target_name",
    }),
    "status": frozenset({"status", "state", "severity", "result", "outcome"}),
}

#: Identifier columns — any of these, or any column whose name ends in ``id``.
_AUDIT_ID_NAMES: frozenset[str] = frozenset({
    "id", "log_id", "run_id", "pipeline_run_id", "log_details_id", "batch_id",
    "load_id", "execution_id", "correlation_id", "job_id", "task_id",
})


def _has_identifier_column(cols: set[str]) -> bool:
    return bool(cols & _AUDIT_ID_NAMES) or any(c.endswith("id") for c in cols)


def has_queryable_audit_table(ws: WorkspaceContext) -> bool:
    """True when an audit-named table has a structured, queryable schema.

    "Queryable" means typed columns for *when* (an event timestamp), *what*
    (the pipeline/activity/notebook or an operation/action), *the outcome*
    (status/severity/state), and *an identifier* — i.e. structured, not
    free-text. An acting-user column is **not** required: these are ETL run audit
    tables (audit_master / audit_detail / pipeline_run_log), not data-access
    audits, and requiring a user column wrongly failed every one of them.
    """
    for name, table in ws.tables.items():
        if not (_tokens(name) & AUDIT_NAME_HINTS):
            continue
        cols = _columns(table)
        if not cols:
            continue
        if all(cols & group for group in AUDIT_QUERYABLE_GROUPS.values()) and \
                _has_identifier_column(cols):
            return True
    return False


#: Name tokens that mark a table as a technical-metadata registry/catalog.
METADATA_TABLE_HINTS: frozenset[str] = frozenset(
    {"audit", "log", "metadata", "meta", "registry", "catalog", "lineage",
     "dictionary", "loadlist", "control"}
)


def has_metadata_registry(ws: WorkspaceContext) -> bool:
    """True when a table captures technical metadata outside a semantic model.

    Metadata can be captured *in* a semantic model or in *separate* metadata
    files/tables (a metadata registry, ``*_metadata`` / ``audit_*`` / a load-list
    control table). This recognises the second form so a workspace that keeps its
    lineage/schema metadata in tables is credited even without a semantic model.
    """
    for name in ws.tables:
        low = str(name).lower()
        bare = low.split(".")[-1]
        if (_tokens(name) & METADATA_TABLE_HINTS) or "_metadata" in low or \
                "metadata_" in low or bare.endswith("_meta"):
            return True
    return False
