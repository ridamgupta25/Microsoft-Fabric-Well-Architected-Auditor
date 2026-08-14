"""Operations & Reliability - Data Logs — cross-workspace (group) checks.

These compare the members of a project group (Dev -> UAT -> Prod) for logging,
monitoring and audit-table practices that should hold in *every* environment,
not only production. They register into the separate ``GROUP_REGISTRY`` via
:func:`group_check`, run once per group, and obey N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext


@group_check(
    id="XW-SPARK-LOGS", ref="10.1.2",
    title="Spark application logs captured for historical analysis",
    pillar=Pillar.OPERATIONS, severity=Severity.LOW,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def spark_logs_consistent(ctx: GroupContext) -> Verdict:
    """Notebooks retain run history (Spark logs) in every environment.

    Recorded notebook runs are the readable evidence that Spark application logs
    are retained for later analysis. An environment whose notebooks have no run
    history is not capturing them to the same standard. N/A when fewer than two
    members' run history could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEM_RUN_HISTORY) and ws.has(Resource.ITEMS),
        implements=lambda ws: _xw.has_run_history(ws, {"Notebook"}),
        practice="retains notebook (Spark) run history",
        data_name="notebook run history",
    )


@group_check(
    id="XW-WH-LOAD-MON", ref="10.1.5",
    title="Warehouse load jobs monitored (duration, failures, row counts)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def warehouse_load_monitored(ctx: GroupContext) -> Verdict:
    """Every environment with a Warehouse also records its load-job runs.

    An environment "implements" load monitoring when it holds a Warehouse and its
    load jobs (pipelines / notebooks) have recorded run history. N/A when fewer
    than two members' run history could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEM_RUN_HISTORY) and ws.has(Resource.ITEMS),
        implements=lambda ws: _xw.has_item_type(ws, _xw.DATA_STORE_TYPES)
        and _xw.has_run_history(ws, {"DataPipeline", "Notebook"}),
        practice="monitors warehouse load jobs (run history present)",
        data_name="warehouse load run history",
    )


@group_check(
    id="XW-AUDIT-SCHEMA", ref="10.2.1",
    title="Audit Tables schema designed for queryability (structured, not free-text)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
    requires=[Resource.TABLE_COLUMNS], required=False,
)
def audit_schema_consistent(ctx: GroupContext) -> Verdict:
    """A well-structured audit table exists in every environment.

    An environment "implements" a queryable audit schema when it has an
    audit-named table carrying the required column groups (timestamp, operation,
    user, status). N/A when fewer than two members' table columns could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.TABLE_COLUMNS),
        implements=_xw.has_structured_audit_table,
        practice="has a queryable audit-table schema",
        data_name="audit-table columns",
    )


@group_check(
    id="XW-AUDIT-QUERYABLE", ref="10.2.5",
    title="Audit Tables and Metadata DB are queryable by operations (not just developers)",
    pillar=Pillar.OPERATIONS, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ROLE_ASSIGNMENTS], required=False,
)
def audit_queryable_consistent(ctx: GroupContext) -> Verdict:
    """Operations can query the audit/metadata stores in every environment.

    An environment "implements" this when it holds an audit/metadata-named store
    and grants an operations reader role. N/A when fewer than two members' items
    and role assignments could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.ITEMS) and ws.has(Resource.ROLE_ASSIGNMENTS),
        implements=lambda ws: bool(_xw.audit_store_items(ws)) and _xw.grants_operations_read(ws),
        practice="exposes audit tables to an operations reader",
        data_name="audit stores and role assignments",
    )
