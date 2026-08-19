"""Data Management & Quality · Data Logs — the shape and handling of the audit store.

Points about the logging estate, answered from data already in the knowledge base:

* **4.6.2** — is ingestion/orchestration configuration held in one metadata
  store, or scattered across several?
* **4.6.8** — are the audit tables structured enough to answer an operational
  question, rather than a timestamp and a blob?
* **4.6.5** — is audit history *appended* to, or rewritten in place? Answered
  from the code that writes it (notebooks) and from the orchestration that runs
  T-SQL against it (pipelines).

The first two are the automated, evidence-based counterparts of the self-assessed
questions ``OPS-METADATA-DB`` (ref 10.2.4) and ``OPS-AUDIT-SCHEMA`` (ref 10.2.1):
those ask a reviewer what the estate does, these read what the schemas say.
"""
from __future__ import annotations

import re

from auditfast.core.check._notebook import NOTEBOOK_LAYERS, executable_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, script_sql, walk_activities
from auditfast.core.check._tables import (
    columns,
    has_timestamp_column,
    is_audit_table,
    is_audit_table_name,
    is_blob_column,
    is_config_table_name,
    is_key_column,
    store_of,
    tables_by_store,
)
from auditfast.core.check.helpers import Verdict, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Layers whose workspaces hold the audit / metadata tables.
_LAYERS = (Layer.LOGS,)

#: The audit store is written from Data Prep / Data Operations notebooks and
#: pipelines as often as from the Data Logs workspace itself, so 4.6.5 is
#: registered for the SOT layer (Data Logs) *and* the layers that hold the
#: writers — judging only the logging workspace would miss every writer.
_AUDIT_WRITER_NOTEBOOK_LAYERS = (Layer.LOGS, *NOTEBOOK_LAYERS)
_AUDIT_WRITER_PIPELINE_LAYERS = (Layer.LOGS, *PIPELINE_LAYERS)

_NO_TABLES = "No lakehouse/warehouse tables were read for this workspace"

#: The minimum number of typed, non-blob columns that makes a table genuinely
#: queryable. Two would be satisfied by "id + payload json".
_MIN_STRUCTURED_COLUMNS = 3


@check(
    id="TB-CONFIG-SINGLE-STORE", ref="4.6.2",
    title="Metadata DB is the single source of ingestion/orchestration configuration",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=_LAYERS, requires=[Resource.TABLE_SCHEMAS], required=False,
)
def config_lives_in_one_store(ctx: CheckContext) -> Verdict:
    """Config/control/watermark tables are concentrated in one metadata store.

    **What it can determine.** Which tables are configuration-shaped by name
    (config / param / setting / control / metadata / watermark / job / schedule)
    and which store holds each, so it can say whether orchestration
    configuration has one home or several.

    **What it cannot.** Whether the pipelines actually *read* that store, or
    whether a second store holds a legitimately different kind of config. Tables
    whose owning store could not be read are excluded rather than counted
    against the estate.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    by_store = tables_by_store(tables)
    if not by_store:
        return not_applicable(
            "No table could be attributed to an owning Lakehouse/Warehouse — the SQL "
            "analytics endpoints were not readable, so store membership is unknown"
        )

    config_by_store = {
        store: [n for n in store_tables if is_config_table_name(n)]
        for store, store_tables in by_store.items()
    }
    config_by_store = {s: names for s, names in config_by_store.items() if names}
    total = sum(len(names) for names in config_by_store.values())
    if total < 1:
        unattributed = sum(1 for t in tables.values() if not store_of(t))
        return not_applicable(
            f"No configuration/control/metadata-shaped table was found in the "
            f"{len(by_store)} attributed store(s) ({unattributed} table(s) had no "
            f"readable owning store), so there is no configuration to consolidate"
        )

    home, in_home = max(config_by_store.items(), key=lambda kv: (len(kv[1]), kv[0]))
    detail = "; ".join(
        f"{store}: {len(names)} ({', '.join(sorted(names)[:3])})"
        for store, names in sorted(config_by_store.items())
    )
    return covered(
        len(in_home), total,
        f"{len(in_home)} of {total} configuration table(s) live in '{home}'"
        + (f" — configuration is spread across {len(config_by_store)} stores: {detail}"
           if len(config_by_store) > 1
           else f" — {home} is the single configuration store ({detail})"),
    )


@check(
    id="TB-AUDIT-QUERYABLE", ref="4.6.8",
    title="Audit Tables support operational queries (structured, queryable schema)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def audit_tables_are_queryable(ctx: CheckContext) -> Verdict:
    """An audit table can be filtered by time and by run, over typed columns.

    **What it can determine.** Whether each audit-shaped table carries a
    timestamp column, an identifying column (a batch/run/job id or another key),
    and at least three typed, non-blob columns — the schema shape an operator
    needs to ask "what happened on this run, at this time".

    **What it cannot.** Whether the table is populated, indexed, or fast; and it
    cannot see free text *inside* a column — only that the column is declared as
    an opaque payload (json/binary/struct). Audit tables whose columns could not
    be read are excluded, never failed.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)

    audit = {n: t for n, t in tables.items() if is_audit_table(n, t) and columns(t)}
    if not audit:
        return not_applicable(
            "No audit/log/quality-shaped table with readable column metadata was found"
        )

    ok, problems = [], []
    for name, table in sorted(audit.items()):
        cols = columns(table)
        structured = [c for c in cols if c.get("type") and not is_blob_column(c)]
        missing = []
        if not has_timestamp_column(table):
            missing.append("no timestamp column")
        if not any(is_key_column(c.get("name") or "") for c in cols):
            missing.append("no identifying/run key column")
        if len(structured) < _MIN_STRUCTURED_COLUMNS:
            missing.append(f"only {len(structured)} typed non-blob column(s)")
        if missing:
            problems.append(f"{name} ({', '.join(missing)})")
        else:
            ok.append(name)
    return covered(
        len(ok), len(audit),
        f"{len(ok)} of {len(audit)} audit table(s) are queryable — timestamp, run/identifier "
        f"key and at least {_MIN_STRUCTURED_COLUMNS} typed non-blob columns"
        + (f"; not queryable: {'; '.join(problems[:3])}" if problems else ""),
    )


# =============================================================================
# 4.6.5 — audit records are immutable / append-only
# =============================================================================
#
# The defect is specific: *history* being rewritten. Appending to an audit table
# is the correct behaviour, so the detector must resolve the **target** of each
# write and judge only the writes whose target is an audit/log/DQ table
# (``is_audit_table_name``). A notebook that overwrites a staging table and
# appends to ``audit_log`` is compliant; one that runs ``DELETE FROM audit_log``
# is not, however careful the rest of it is.

#: A (possibly schema-qualified) table identifier, optionally quoted.
_IDENT = r"[`\"\[]?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)[`\"\]]?"

#: SQL that rewrites, removes or replaces rows that are already committed.
#: ``MERGE`` is included because a merge into an audit table updates history in
#: place — the point's "no in-place overwrite" is exactly that.
_SQL_REWRITE = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|TRUNCATE\s+TABLE|TRUNCATE|"
    r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?|INSERT\s+OVERWRITE(?:\s+TABLE)?|"
    r"MERGE\s+INTO|CREATE\s+OR\s+REPLACE\s+TABLE|REPLACE\s+TABLE)\s+" + _IDENT,
    re.IGNORECASE,
)

#: SQL that only adds rows.
_SQL_APPEND = re.compile(r"\bINSERT\s+INTO\s+(?:TABLE\s+)?" + _IDENT, re.IGNORECASE)

#: ``.mode("overwrite") … .saveAsTable("x")``. The filler may span lines (a
#: pretty-printed write chain) but must not cross another ``.write`` or
#: ``mode(``, so a later append cannot be paired with an earlier overwrite.
_DF_OVERWRITE = re.compile(
    r"""mode\s*\(\s*["'](?:overwrite|complete)["']\s*\)"""
    r"""(?:(?!\.write\b|mode\s*\()[\s\S]){0,240}?"""
    r"""(?:saveAsTable|insertInto)\s*\(\s*["']([\w.$]+)["']""",
    re.IGNORECASE | re.VERBOSE,
)
#: The keyword spelling — ``saveAsTable("x", mode="overwrite")`` /
#: ``insertInto("x", overwrite=True)``.
_DF_OVERWRITE_KW = re.compile(
    r"""(?:saveAsTable|insertInto)\s*\(\s*["']([\w.$]+)["'][^)]{0,120}?"""
    r"""(?:mode\s*=\s*["'](?:overwrite|complete)["']|overwrite\s*=\s*True)""",
    re.IGNORECASE | re.VERBOSE,
)
_DF_APPEND = re.compile(
    r"""mode\s*\(\s*["']append["']\s*\)"""
    r"""(?:(?!\.write\b|mode\s*\()[\s\S]){0,240}?"""
    r"""(?:saveAsTable|insertInto)\s*\(\s*["']([\w.$]+)["']""",
    re.IGNORECASE | re.VERBOSE,
)
_DF_APPEND_KW = re.compile(
    r"""(?:saveAsTable|insertInto)\s*\(\s*["']([\w.$]+)["'][^)]{0,120}?"""
    r"""mode\s*=\s*["']append["']""",
    re.IGNORECASE | re.VERBOSE,
)
#: ``DeltaTable.forName(spark, "audit_log") … .delete(`` / ``.update(`` — the
#: DataFrame-API way of mutating committed rows.
_DELTA_MUTATE = re.compile(
    r"""DeltaTable\s*\.\s*for(?:Name|Path)\s*\([^)]*["']([\w./$]+)["'][^)]*\)"""
    r"""[\s\S]{0,300}?\.\s*(?:delete|update)\s*\(""",
    re.IGNORECASE | re.VERBOSE,
)

_REWRITE_PATTERNS = (_SQL_REWRITE, _DF_OVERWRITE, _DF_OVERWRITE_KW, _DELTA_MUTATE)
_APPEND_PATTERNS = (_SQL_APPEND, _DF_APPEND, _DF_APPEND_KW)


def _table_leaf(name: str) -> str:
    """``lakehouse.dbo.audit_log`` -> ``audit_log``; strips quoting."""
    return (name or "").rsplit(".", 1)[-1].strip("`\"[] ")


def _audit_targets(patterns, text: str) -> set[str]:
    """Audit-named tables that appear as the *target* of one of ``patterns``.

    Only the resolved target name decides. A table name held in a variable
    cannot be resolved from source text, so it is simply not found — the check
    reports what it could resolve rather than guessing.
    """
    found: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            leaf = _table_leaf(match.group(1))
            if leaf and is_audit_table_name(leaf):
                found.add(leaf)
    return found


def _immutability_verdict(rewritten: set[str], appended: set[str], where: str) -> Verdict:
    """Shared verdict shape for the notebook and pipeline halves of 4.6.5."""
    touched = rewritten | appended
    if not touched:
        return not_applicable(
            f"{where} writes no audit/log/DQ-named table, so there is no audit "
            f"history here to preserve"
        )
    safe = sorted(touched - rewritten)
    if not rewritten:
        return covered(
            len(safe), len(touched),
            f"All {len(touched)} audit table(s) written by {where.lower()} are "
            f"append-only: {', '.join(safe[:4])}",
        )
    return covered(
        len(safe), len(touched),
        f"{len(rewritten)} of {len(touched)} audit table(s) written by {where.lower()} are "
        f"rewritten in place rather than appended to: {', '.join(sorted(rewritten)[:4])}"
        + (f" (append-only: {', '.join(safe[:3])})" if safe else ""),
    )


@check(
    id="NB-AUDIT-IMMUTABLE", ref="4.6.5",
    title="Audit records are immutable / append-only (no in-place overwrite of history)",
    pillar=Pillar.DATA_MODELING, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=_AUDIT_WRITER_NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS],
    required=True,
)
def notebook_audit_history_is_append_only(ctx: CheckContext) -> Verdict:
    """A notebook adds rows to the audit tables; it never updates or overwrites them.

    Severity is High rather than the checklist's Medium for one reason: an
    overwritten audit row is *unrecoverable* evidence. Every other data defect
    can be recomputed from the source; a deleted audit trail cannot.

    **What it can determine.** Which audit/log/DQ-named tables the notebook
    writes, and whether each is reached by an append (``INSERT INTO``,
    ``mode("append")``) or by a rewrite (``UPDATE`` / ``DELETE`` / ``TRUNCATE`` /
    ``MERGE INTO`` / ``mode("overwrite")`` / ``DeltaTable…delete()``). Only the
    *target* decides, so overwriting a staging table is not held against the
    notebook.

    **What it cannot.** Resolve a target held in a variable or built by string
    formatting; those writes are invisible and the check neither credits nor
    blames them. It also cannot see table-level protections (Delta time travel,
    warehouse permissions) that might make a rewrite impossible in practice.

    Read with ``executable_code`` so a commented-out ``DELETE FROM audit_log``
    cannot be scored as a live defect — nor a commented ``INSERT INTO`` as a
    safeguard. Distinct from ``NB-AUDIT-LOG`` (4.6.4), which asks whether an
    audit log is written at all; this asks how it is maintained afterwards.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    return _immutability_verdict(
        _audit_targets(_REWRITE_PATTERNS, code),
        _audit_targets(_APPEND_PATTERNS, code),
        "Notebook",
    )


@check(
    id="PL-AUDIT-IMMUTABLE", ref="4.6.5",
    title="Audit records are immutable / append-only (no in-place overwrite of history)",
    pillar=Pillar.DATA_MODELING, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=_AUDIT_WRITER_PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
    required=True,
)
def pipeline_audit_history_is_append_only(ctx: CheckContext) -> Verdict:
    """A pipeline appends to the audit tables; it never truncates or updates them.

    The sibling of ``NB-AUDIT-IMMUTABLE`` under the same ref, and a genuinely
    different signal: a pipeline rewrites audit history through T-SQL it carries
    inline — a Script activity, or a Copy activity's ``preCopyScript``, which is
    the usual home of ``TRUNCATE TABLE`` — not through Spark code. Neither check
    can see the other's surface, so a workspace needs both.

    **What it can determine.** The audit-named tables targeted by the pipeline's
    inline SQL and by Copy sinks, and whether each is appended to or rewritten.
    Activities nested inside ForEach / If / Switch are included
    (``walk_activities``).

    **What it cannot.** Read a stored procedure's body — ``EXEC
    dbo.usp_reset_audit`` is opaque to any REST caller — nor resolve a table
    name supplied by a pipeline expression. Such writes are not counted either
    way.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    sql_parts: list[str] = [script_sql(ctx.obj)]
    sink_tables: set[str] = set()
    for activity in walk_activities(ctx.obj):
        props = activity.get("typeProperties") or {}
        sink = props.get("sink") if isinstance(props.get("sink"), dict) else {}
        pre_copy = sink.get("preCopyScript")
        if isinstance(pre_copy, str):
            sql_parts.append(pre_copy)
        table = sink.get("tableName") or sink.get("table")
        if isinstance(table, str):
            leaf = _table_leaf(table)
            if leaf and is_audit_table_name(leaf):
                sink_tables.add(leaf)

    sql = "\n".join(part for part in sql_parts if part)
    rewritten = _audit_targets(_REWRITE_PATTERNS, sql)
    appended = _audit_targets(_APPEND_PATTERNS, sql) | sink_tables
    return _immutability_verdict(rewritten, appended, "Pipeline")


# =============================================================================
# 4.6.3 — who can write to the metadata store
#
# The point asks that *only framework identities can write* to the Metadata DB.
# There is no readable per-table or per-database grant: Fabric's REST API returns
# role assignments at the **workspace** level, and the SQL analytics endpoint is
# read-only for this tool, so `sys.database_permissions` is not queried. What can
# be judged is therefore strictly narrower — who holds a write-capable role on
# the workspace that *holds* the metadata store — and the evidence says so
# rather than implying a table-level grant was verified.
# =============================================================================

#: Workspace roles that can create, alter or delete items and their data. Viewer
#: is the only read-only role, so anything else is write-capable.
_WRITE_CAPABLE_ROLES: frozenset[str] = frozenset({"Admin", "Member", "Contributor"})

#: Principal types that are a framework/automation identity rather than a person.
_FRAMEWORK_PRINCIPALS: frozenset[str] = frozenset({
    "ServicePrincipal", "ManagedIdentity", "Application", "ServicePrincipalProfile",
})


@check(
    id="WS-METADATA-WRITE", ref="4.6.3",
    title="Metadata DB access is restricted (only framework identities can write)",
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=_LAYERS, requires=[Resource.TABLE_SCHEMAS, Resource.ROLE_ASSIGNMENTS],
    required=True,
)
def metadata_store_write_access_is_restricted(ctx: CheckContext) -> Verdict:
    """Write access to the workspace holding the metadata store is not held by named people.

    **What it can determine — and only this.** That this workspace holds a
    metadata/config/control/watermark-shaped table, and how many principals hold
    a *write-capable* workspace role (Admin / Member / Contributor) over it, split
    into framework identities (service principal / managed identity), groups, and
    **named individual users**. A named person with Contributor on the workspace
    that holds the Metadata DB is the readable form of the defect the point
    describes, and is what is scored.

    **What it cannot — and this is the larger half.** This is a *workspace-level
    proxy, not a table-level grant*. Fabric exposes role assignments per
    workspace; there is no readable per-table, per-schema or per-database
    permission for a Lakehouse/Warehouse table, so "only framework identities can
    write **to the metadata DB**" is not verified — only "who can write anywhere
    in the workspace that holds it". Nor can a group grant be expanded to its
    members: a group counted here as non-personal may contain any number of
    people. A workspace where a SQL-level grant already restricts writes will
    still score down here if named users hold Contributor, and that is the
    check's limit, not a finding about the SQL grants.

    **N/A, not FAIL, whenever the evidence is missing** — including the common
    case where the tenant returns no role assignments for the sign-in: an
    unreadable ACL is not an open one.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable(_NO_TABLES)
    config_tables = sorted(n for n in tables if is_config_table_name(n))
    if not config_tables:
        return not_applicable(
            "No metadata/config/control/watermark-shaped table in this workspace, so "
            "there is no metadata store here whose write access could be restricted"
        )
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(
            f"{len(config_tables)} metadata/config table(s) present, but workspace role "
            f"assignments could not be read from Fabric — who can write is unknown, "
            f"which is not the same as unrestricted"
        )
    assignments = ctx.workspace.role_assignments
    if not assignments:
        return not_applicable(
            f"{len(config_tables)} metadata/config table(s) present, but the workspace "
            f"returned no role assignments to judge"
        )

    writers = [a for a in assignments if a.role in _WRITE_CAPABLE_ROLES]
    if not writers:
        return graded(
            3,
            f"No principal holds a write-capable workspace role (Admin/Member/Contributor) "
            f"over the {len(config_tables)} metadata/config table(s) here; "
            f"{len(assignments)} assignment(s) are read-only. Workspace-level evidence "
            f"only — per-table grants are not readable.",
        )

    personal = [a for a in writers if a.is_individual]
    individuals = sorted({a.display_name or a.principal_id or "?" for a in personal})
    framework = [a for a in writers if a.principal_type in _FRAMEWORK_PRINCIPALS]
    groups = [a for a in writers
              if not a.is_individual and a.principal_type not in _FRAMEWORK_PRINCIPALS]
    non_personal = len(writers) - len(personal)
    return covered(
        non_personal, len(writers),
        f"{non_personal} of {len(writers)} write-capable grant(s) over the workspace "
        f"holding {len(config_tables)} metadata/config table(s) are non-personal "
        f"({len(framework)} framework identity/identities, {len(groups)} group(s)); "
        f"{len(personal)} named individual user(s)"
        + (f": {', '.join(individuals[:4])}" if individuals else "")
        + ". Workspace-level proxy only: Fabric exposes no per-table or per-database "
          "grant, and a group grant cannot be expanded to its members.",
    )
