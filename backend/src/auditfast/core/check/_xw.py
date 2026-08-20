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
