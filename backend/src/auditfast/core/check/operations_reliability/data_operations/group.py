"""Operations & Reliability · Data Operations — cross-workspace (group) checks.

Checks here compare the members of a project group (Dev → UAT → Prod) against
one another, rather than judging a single workspace in isolation. They register
into the separate ``GROUP_REGISTRY`` via :func:`group_check`, run once per group,
and obey the same rules as every check: pure, deterministic, and **N/A-not-FAIL**
when the data needed to compare is missing.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, GroupMemberContext


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
    pillar=Pillar.OPERATIONS,
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
    title="Medallion architecture properly implemented (Bronze Lakehouse -> Silver Lakehouse -> Gold Warehouse) with clear layer boundaries",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM, requires=[Resource.ITEMS],
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
    title="Pipeline completion SLAs set and monitored",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
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
    title="SLA breach triggers alerts (Data Activator, email, Teams)",
    pillar=Pillar.OPERATIONS, severity=Severity.HIGH,
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
    title="Historical SLA compliance tracked and reported",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
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
    title="Separate workspaces for Dev, QA, and Production per layer (9 total)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
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
    title="Schema drift between environments is detectable and reconciled",
    pillar=Pillar.OPERATIONS, severity=Severity.HIGH, requires=[Resource.ITEMS],
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
