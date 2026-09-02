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
from auditfast.core.check.helpers import Verdict, covered, graded, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext


def _names(names: list[str], cap: int = 5) -> str:
    """A comma-joined, capped list of names with a ``+N more`` suffix."""
    shown = ", ".join(names[:cap])
    if len(names) > cap:
        shown += f" (+{len(names) - cap} more)"
    return shown


def _pipelines_without_run_history(ws: WorkspaceContext) -> list[str]:
    """Names of DataPipeline items that have no recorded run history."""
    return sorted(
        item.display_name or item.id
        for item in ws.items
        if item.type == "DataPipeline" and not ws.run_history.get(item.id)
    )


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
    ref="11.4.3b",
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
    """How much of Bronze -> Silver -> Gold *every* environment implements.

    Scored on the tiers common to all readable environments **that hold a data
    store** — the architecture the project actually has everywhere — using the
    same 0-3 ladder as the per-workspace ``WS-MEDALLION`` on this ref, so the two
    cannot disagree about the same estate: 3 tiers = 3, 2 = 2, 1 = 1, none = 0.

    Counting merely *some* tier as implementation was the earlier flaw: an estate
    with only a Bronze Lakehouse scored full marks against a check whose title
    promises the whole progression.

    An environment holding **no Lakehouse, Warehouse or database** is excluded,
    not failed. It implements no medallion tier because it has nothing to place
    in one; scoring a pure reporting workspace 0 for "not declaring its layers"
    is a finding about a practice it cannot have.

    Distinct from ``XW-MEDALLION-DRIFT`` (11.4.3a), which asks whether the
    environments *agree*; a group where every environment is equally missing Gold
    has no drift but an incomplete architecture. A tier held only in some
    environments is named here but scored there. N/A when fewer than two
    environments hold a data store.
    """
    readable = [m for m in ctx.members if m.workspace.has(Resource.ITEMS)]
    if len(readable) < 2:
        return not_applicable(
            "fewer than two environments in this group had readable item "
            "inventories to compare"
        )

    # A workspace holding no data store implements no medallion tier -- there is
    # nothing there to name. Scoring it 0 says "you failed to declare your layers"
    # to a reporting workspace that has no layers to declare, which is the same
    # category error as telling a Warehouse-less workspace to enable SQL audit.
    # The per-workspace WS-MEDALLION on this ref already returns N/A here.
    storeless = [
        _xw.env_label(m) for m in readable
        if not any(item.type in _xw.DATA_STORE_TYPES for item in m.workspace.items)
    ]
    judged = [
        m for m in readable
        if any(item.type in _xw.DATA_STORE_TYPES for item in m.workspace.items)
    ]
    excluded = (
        f"; {len(storeless)} environment(s) excluded, holding no Lakehouse, "
        f"Warehouse or database to place in a tier: {', '.join(storeless)}"
    ) if storeless else ""

    if len(judged) < 2:
        return not_applicable(
            "fewer than two environments in this group hold a data store whose "
            f"medallion tiers could be compared{excluded}"
        )

    per_env = {_xw.env_label(m): _xw.medallion_tiers(m.workspace) for m in judged}
    common = set.intersection(*per_env.values())
    union = set().union(*per_env.values())

    named = ", ".join(sorted(common, key=_xw.MEDALLION_ORDER.index))
    missing = [t for t in _xw.MEDALLION_ORDER if t not in common]
    total = len(judged)
    where = ", ".join(sorted(per_env))

    # A tier some environments have and others do not is real information, but it
    # is 11.4.3a's verdict to score -- name it here, do not double-count it.
    partial_tiers = sorted(union - common, key=_xw.MEDALLION_ORDER.index)
    drift = ""
    if partial_tiers:
        drift = "; " + "; ".join(
            f"{tier} is named only in "
            f"{', '.join(sorted(label for label, tiers in per_env.items() if tier in tiers))}"
            for tier in partial_tiers
        )

    if not common:
        return graded(
            0,
            f"no medallion tier is named in all {total} environment(s) with a data "
            f"store ({where}). No store or workspace name declares Bronze/Raw, "
            f"Silver/Cleansed or Gold/Curated, so the layer boundaries are not "
            f"expressed anywhere a reader can see them{drift}{excluded}",
        )

    if missing:
        return graded(
            len(common),
            f"{len(common)} of 3 medallion tier(s) are implemented in every "
            f"environment ({named}, across {where}); no store or workspace name "
            f"declares {' or '.join(missing)}"
            f"{_missing_gold_hint(missing, judged)}{drift}{excluded}",
        )

    return graded(
        3,
        f"Bronze -> Silver -> Gold are all named in every one of the {total} "
        f"environment(s) ({where}){drift}{excluded}",
    )


def _missing_gold_hint(missing: list[str], members: list[GroupMemberContext]) -> str:
    """Point at a Warehouse that is probably the unnamed Gold layer.

    The serving layer is often present but not *named* for its tier, which is
    what this check can see. Naming the candidate turns "Gold is missing" into
    something a reviewer can act on. Purely evidence -- it never changes the
    score, because inferring a tier from an item's type is exactly the guess that
    produces false verdicts.
    """
    if "Gold" not in missing:
        return ""
    candidates = [
        f"{_xw.env_label(member)} holds Warehouse "
        f"'{item.display_name or item.id}'"
        for member in members
        for item in member.workspace.items
        if item.type == "Warehouse" and not _xw.medallion_tiers_of(item.display_name or "")
    ]
    if not candidates:
        return ""
    return (
        f". A serving Warehouse is present but unnamed for its tier "
        f"({'; '.join(sorted(candidates)[:3])}) — if that is the Gold layer, name "
        f"it so the boundary is readable"
    )


@group_check(
    id="XW-PIPELINE-SLA", ref="9.4.2",
    title="Pipeline completion SLAs are monitored consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY,
              Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE], required=False,
)
def pipeline_sla_monitored(ctx: GroupContext) -> Verdict:
    """Pipeline completion SLAs are set and monitored in every environment.

    Two things must be true for each environment: pipeline *completions* are
    visible (there is DataPipeline run history) and an *SLA* is actually
    enforced (an enabled schedule plus a way to be alerted on failure). An
    environment with no pipelines is N/A (nothing to monitor), not a failure.
    A group where completions are visible everywhere but nothing enforces a
    deadline is PARTIAL, not a pass. N/A when fewer than two environments have
    pipelines to compare.
    """
    recorded: list[tuple[str, int]] = []   # (env, pipelines with recorded runs)
    gaps: list[tuple[str, list[str]]] = []  # (env, pipelines with no run history)
    no_pipelines: list[str] = []            # env has no pipelines at all (N/A)
    disabled_sched: list[str] = []          # env has schedules but all disabled
    no_sched: list[str] = []                # env has no schedule or trigger
    active_sla: list[str] = []              # env has enabled schedule + failure alert
    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.ITEMS) and ws.has(Resource.ITEM_RUN_HISTORY)):
            continue
        tier = _xw.env_tier(member)
        if _xw.pipeline_item_count(ws) == 0:
            no_pipelines.append(tier)
            continue
        runs = _xw.typed_run_history_count(ws, {"DataPipeline"})
        if runs > 0:
            recorded.append((tier, runs))
        else:
            gaps.append((tier, _pipelines_without_run_history(ws)))
        if _xw.has_enabled_schedule(ws) and _has_failure_notification(ws):
            active_sla.append(tier)
        elif ws.refresh_schedules:
            disabled_sched.append(tier)
        else:
            no_sched.append(tier)

    judged = len(recorded) + len(gaps)
    if judged < 2:
        extra = (f" ({_xw.and_list(no_pipelines)} have no pipelines to monitor)"
                 if no_pipelines else "")
        return not_applicable(
            "fewer than two environments in this group have pipelines whose "
            f"completion SLAs could be compared{extra}"
        )

    if not gaps:
        counts = ", ".join(f"{tier} {n}" for tier, n in recorded)
        visible = (
            f"Pipeline runs are recorded in all {judged} environments "
            f"({counts} pipelines have recorded runs), so completions are visible."
        )
    else:
        rec = ", ".join(f"{tier} {n}" for tier, n in recorded) or "none"
        gap_clauses = [
            f"{tier} (no run history: {_names(names)})" if names else tier
            for tier, names in gaps
        ]
        visible = (
            f"Pipeline runs are recorded in {rec}, but {_xw.and_list(gap_clauses)} "
            f"{'has' if len(gaps) == 1 else 'have'} pipelines with no recorded runs, "
            "so their completions cannot be confirmed."
        )

    fully_enforced = bool(active_sla) and not disabled_sched and not no_sched
    if fully_enforced:
        sla = (
            f"Every environment has an enabled schedule and a failure alert "
            f"({_xw.and_list(active_sla)}), so completion deadlines are enforced."
        )
    else:
        clauses: list[str] = []
        if disabled_sched:
            verb = "has" if len(disabled_sched) == 1 else "have"
            clauses.append(f"{_xw.and_list(disabled_sched)} {verb} schedules "
                           "but all are disabled")
        if no_sched:
            verb = "has" if len(no_sched) == 1 else "have"
            clauses.append(f"{_xw.and_list(no_sched)} {verb} no schedule or trigger")
        sla = (
            "But there is no active SLA: " + "; ".join(clauses)
            + " — so nothing enforces a completion deadline or raises a failure alert."
        )

    na = (f" {_xw.and_list(no_pipelines)} have no pipelines (N/A)."
          if no_pipelines else "")
    evidence = f"{visible} {sla}{na}"

    if gaps:
        return covered(len(recorded), judged, evidence)
    if fully_enforced:
        return graded(3, evidence)
    return graded(1, evidence)


def _has_failure_notification(ws: WorkspaceContext) -> bool:
    """True when a failure on a pipeline run would notify someone.

    Any of: a refresh schedule set to notify on failure, a pipeline activity
    that emails/messages on a failure path, or an active Data Activator.
    """
    for schedule in (ws.refresh_schedules or {}).values():
        if schedule.get("notifies_on_failure") or schedule.get("notify_option"):
            return True
    return _has_pipeline_failure_alert(ws) or _xw.has_active_activator(ws)


#: A pipeline activity type that can raise a failure alert (email / Teams / web).
_FAILURE_ALERT_TYPE = re.compile(
    r'"type"\s*:\s*"(?:Office365Outlook|Teams|WebActivity|WebHook|Web)"',
    re.IGNORECASE,
)


def _has_pipeline_failure_alert(ws: WorkspaceContext) -> bool:
    """True when a pipeline runs an email/Teams/web activity on a failure path."""
    for definition in (ws.pipelines or {}).values():
        blob = json.dumps(definition)
        if '"Failed"' in blob and _FAILURE_ALERT_TYPE.search(blob):
            return True
    return False


@group_check(
    id="XW-SLA-ALERTS", ref="9.4.3",
    title="SLA breaches trigger alerts (Data Activator) consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.HIGH,
    requires=[Resource.ACTIVATOR_DEFINITIONS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
              Resource.PIPELINE_DEFINITIONS], required=False,
)
def sla_alerts_consistent(ctx: GroupContext) -> Verdict:
    """SLA-breach alerting exists on *some* surface in every environment.

    Alerting is not only a Data Activator. Three surfaces are inspected: a Data
    Activator rule; a refresh schedule whose ``notify_option`` sends mail on
    failure; and a pipeline failure path that runs an email / Teams / web
    activity. State matters: a ``MailOnFailure`` schedule that is *disabled* is
    configured-but-inactive, so it is reported as PARTIAL, not a pass. Any
    activator rule is classified as SLA-relevant here (failure / lateness), which
    keeps this check distinct from capacity CU-consumption alerting so one rule is
    not double-counted. N/A when fewer than two environments can be compared.
    """
    active: list[str] = []
    inactive: list[str] = []
    none_of: list[str] = []
    for member in ctx.members:
        ws = member.workspace
        label = _xw.env_label(member)
        activator = _xw.has_active_activator(ws)
        mail_active = any(
            s.get("enabled") and "mail" in str(s.get("notify_option", "")).lower()
            for s in ws.refresh_schedules.values()
        )
        mail_configured = any(
            "mail" in str(s.get("notify_option", "")).lower() or s.get("notifies_on_failure")
            for s in ws.refresh_schedules.values()
        )
        pipeline_alert = _has_pipeline_failure_alert(ws)
        if activator or mail_active or pipeline_alert:
            active.append(label)
        elif mail_configured:
            inactive.append(label)
        else:
            none_of.append(label)

    total = len(active) + len(inactive) + len(none_of)
    if total < 2:
        return not_applicable(
            "fewer than two environments in this group could be compared for SLA "
            "alerting"
        )
    none_note = f"; no alerting surface in {', '.join(none_of)}" if none_of else ""
    if active and not inactive and not none_of:
        return covered(total, total,
                       f"SLA-breach alerting is active in all {total} environment(s): "
                       f"{', '.join(active)}")
    if active:
        return covered(
            len(active), total,
            f"SLA-breach alerting is active in {len(active)} of {total} "
            f"environment(s) ({', '.join(active)}); "
            f"configured-but-disabled in {', '.join(inactive) or 'none'}{none_note}",
        )
    if inactive:
        return graded(
            1,
            f"no active SLA-breach alerting: Data Activator absent, but a "
            f"failure-notification (MailOnFailure) is configured yet disabled in "
            f"{', '.join(inactive)}{none_note}. Enable the failure notification or "
            "add a Data Activator rule so breaches reach an owner.",
        )
    return covered(
        0, total,
        f"no SLA-breach alerting on any surface (Data Activator, failure "
        f"notification, or pipeline failure activity) in {total} environment(s): "
        f"{', '.join(none_of)}",
    )


@group_check(
    id="XW-SLA-HISTORY", ref="9.4.4",
    title="Historical SLA compliance is tracked consistently across environments",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=False,
)
def sla_history_consistent(ctx: GroupContext) -> Verdict:
    """Runnable items retain execution history in every environment that has any.

    Historical SLA reporting needs recorded runs to report on, so this verifies
    the *raw material* exists: pipelines and notebooks whose runs Fabric has
    retained. Whether anyone turns that into a periodic attainment figure is
    self-assessed (``OPS-SLA-HISTORY``) — the capacity-metrics and monitoring
    admin APIs that would show it are not called here, and the evidence says so.

    An environment holding **no pipeline or notebook** is excluded, not failed: a
    reporting workspace runs nothing, so it has no execution history to retain
    and telling its owner to keep some is not a finding.

    Run history is resolved **by item type**, never by "the workspace recorded
    some run somewhere" — a semantic-model refresh is not pipeline history. N/A
    when fewer than two environments hold a runnable item.
    """
    runnable = {"DataPipeline", "Notebook"}
    retained: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []

    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.ITEMS) and ws.has(Resource.ITEM_RUN_HISTORY)):
            continue
        label = _xw.env_label(member)
        total = sum(1 for item in ws.items if item.type in runnable)
        if total == 0:
            skipped.append(f"{label} (holds no pipeline or notebook to run)")
            continue
        recorded = _xw.typed_run_history_count(ws, runnable)
        if recorded:
            retained.append(f"{label} ({recorded} of {total} have recorded runs)")
        else:
            missing.append(f"{label} (none of {total} have recorded runs)")

    scope = (
        ". Whether the retained runs are reported as an attainment figure is not "
        "readable here — it needs the monitoring admin APIs this audit does not call"
    )
    excluded = (f"; {len(skipped)} environment(s) excluded: {'; '.join(skipped)}"
                if skipped else "")

    judged = len(retained) + len(missing)
    if judged < 2:
        return not_applicable(
            "fewer than two environments in this group hold a pipeline or notebook "
            f"whose execution history could be compared{excluded}{scope}"
        )
    if not missing:
        return covered(
            judged, judged,
            f"all {judged} environment(s) retain execution history for SLA "
            f"reporting: {'; '.join(retained)}{excluded}{scope}",
        )
    return covered(
        len(retained), judged,
        f"{len(retained)} of {judged} environment(s) retain execution history for "
        f"SLA reporting; not in {'; '.join(missing)}{excluded}{scope}",
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
    id="XW-MEDALLION-DRIFT", ref="11.4.3a",
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

#: How much of the scanned definitions must have been read before "no reference
#: found" counts as a judgement rather than a blind spot. A crawl is *technically*
#: incomplete the moment one definition is throttled, but 98 of 99 notebooks is a
#: sound basis for a verdict and 5 of 50 is not. Below this bar the environment is
#: set aside as indeterminate; at or above it the environment is judged and the
#: small remainder is disclosed instead.
_MATERIAL_READ_COVERAGE = 0.90


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
    mutable artifact.

    **Evidence found and evidence absent are not equally conclusive.** A
    reference discovered in a partially-captured environment is real and is
    reported; finding *none* there proves less, because the definitions that were
    never read were never searched. How much less depends on the size of the gap:
    an environment whose scanned definitions were **mostly** captured is judged
    normally with the remainder disclosed, while one that was substantially
    unread is **set aside as indeterminate** — never credited as isolated, and
    never counted against the score either. A *flagged* environment stays flagged,
    but its reference list is marked as possibly short, so a reviewer is not sent
    to remove three references when twenty went unsearched. Every blind spot is
    named in the evidence with its counts. N/A when fewer than two environments
    can be judged.
    """
    members = [m for m in ctx.members if _inspectable(m.workspace)]
    unreadable = [
        _xw.env_label(m) for m in ctx.members if not _inspectable(m.workspace)
    ]
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

    # A reference found in a partial crawl is still a real reference, so an
    # offender stays an offender. Finding *nothing* is only conclusive in
    # proportion to how much was actually searched: near-complete is judged with
    # the remainder disclosed, substantially-unread is set aside as indeterminate
    # rather than credited as isolated.
    confirmed: list[str] = []
    indeterminate: list[str] = []
    partial_offenders: list[str] = []
    caveated: list[str] = []
    for member in members:
        label = _xw.env_label(member)
        gaps = _xw.incomplete_reads(member.workspace, _ISOLATION_RESOURCES)
        gap_detail = f"{label} ({_xw.and_list(gaps)} could not be read)"
        if label in offenders:
            # Already proven non-isolated, so the bucket does not change — but the
            # *list* of references is only as complete as the crawl, and a reviewer
            # told to remove them needs to know more may be hiding.
            if gaps:
                partial_offenders.append(gap_detail)
        elif not gaps:
            confirmed.append(label)
        elif _xw.read_coverage(
            member.workspace, _ISOLATION_RESOURCES
        ) >= _MATERIAL_READ_COVERAGE:
            # Near-complete: judged on the evidence, with the remainder disclosed.
            confirmed.append(label)
            caveated.append(gap_detail)
        else:
            indeterminate.append(gap_detail)

    notes: list[str] = []
    if indeterminate:
        notes.append(
            f"{len(indeterminate)} environment(s) excluded as indeterminate — a "
            "cross-environment reference could not be ruled out where too little of "
            f"the scanned definitions was captured: {'; '.join(indeterminate)}"
        )
    if caveated:
        notes.append(
            f"{len(caveated)} environment(s) judged on a near-complete crawl, with a "
            f"small unread remainder: {'; '.join(caveated)}"
        )
    if partial_offenders:
        notes.append(
            f"{len(partial_offenders)} flagged environment(s) may hold further "
            "cross-environment references that were never searched: "
            f"{'; '.join(partial_offenders)}"
        )
    if unreadable:
        notes.append(
            f"{len(unreadable)} environment(s) excluded with no readable pipeline, "
            f"notebook, shortcut or report metadata: {', '.join(unreadable)}"
        )
    note_text = f"; {'; '.join(notes)}" if notes else ""

    judged = len(confirmed) + len(offenders)
    if judged < 2:
        return not_applicable(
            "fewer than two environments in this group could be judged for "
            f"cross-environment dependencies{note_text}"
        )

    if not offenders:
        return covered(
            judged, judged,
            f"all {judged} judged environment(s) are isolated: no pipelines, "
            f"notebooks, shortcuts or reports reference another environment's "
            f"workspace or artifacts, and no referenced connection is "
            f"shared{note_text}",
        )

    detail = "; ".join(
        f"{label} depends on {', '.join(sorted(refs))}"
        for label, refs in sorted(offenders.items())[:3]
    )
    return covered(
        len(confirmed), judged,
        f"{len(offenders)} of {judged} judged environment(s) have a "
        f"cross-environment dependency: {detail}{note_text}",
    )
