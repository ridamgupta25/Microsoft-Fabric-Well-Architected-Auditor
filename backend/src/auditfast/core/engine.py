"""The audit engine.

Runs selected checks across a set of workspaces. It contains no knowledge of any
particular check, pillar, or artifact type — it dispatches purely on
:class:`~auditfast.domain.enums.Scope`:

    for each workspace:
        select the checks matching (pillars, layer)
        ask the provider for exactly the resources those checks need
        for each scope present:
            for each object of that scope in the workspace:
                run every check registered for that scope

Adding a new artifact type therefore means adding a ``Scope`` member, teaching a
provider to yield those objects, and writing checks tagged with it. This file
does not change.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from .check.helpers import EMPTY_REMEDIATION, RemediationBook, Verdict, not_applicable
from .check.registry import GROUP_REGISTRY, REGISTRY, CheckRegistry, GroupCheckRegistry
from .enums import Layer, Pillar, Resource, Scope, Severity, Status
from .errors import WorkspaceAccessError
from .models import (
    CheckContext,
    CheckResult,
    CheckSpec,
    GroupCheckSpec,
    GroupContext,
    GroupMemberContext,
    WorkspaceContext,
)
from .scoring import status_from_score

#: One workspace to audit: its id and the layer role it plays.
Target = tuple[str, Layer]

#: One member of a project group: its id, layer, and environment level (1..10).
GroupMemberTarget = tuple[str, Layer, int]

#: One project group to audit across: its name and its ordered members.
GroupTarget = tuple[str, tuple[GroupMemberTarget, ...]]


def _resolve_max_parallel_workspaces() -> int:
    """How many workspaces may be crawled at once, from the environment.

    Clamped to 1..8 so a mis-set value can never unleash an unbounded crawl on
    the tenant (which would only trigger throttling and run slower).
    """
    raw = os.environ.get("AUDITFAST_MAX_PARALLEL_WORKSPACES", "8")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 8
    return max(1, min(value, 8))


#: Upper bound on concurrent workspace crawls.
MAX_PARALLEL_WORKSPACES = _resolve_max_parallel_workspaces()

#: One process-wide gate, shared by every audit, so several concurrent audits
#: cannot multiply their per-run pools into a tenant-throttling storm: the total
#: number of workspaces in flight across the whole process never exceeds the cap.
_FETCH_GATE = threading.BoundedSemaphore(MAX_PARALLEL_WORKSPACES)

log = logging.getLogger("auditfast.engine")


def _resolve_workspace_batch_size() -> int:
    """How many workspaces to crawl per batch before an adaptive cooldown.

    ``0`` (the default) disables batching entirely — every workspace is crawled
    in one wave, exactly as before. A positive value splits a large run into
    sequential batches of that size, so a big tenant's Power BI calls do not all
    land inside one rate-limit window.
    """
    raw = os.environ.get("AUDITFAST_WORKSPACE_BATCH_SIZE", "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _resolve_batch_cooldown_seconds() -> float:
    """Seconds to pause after a batch that hit throttling, letting the Power BI
    rate-limit window reset before the next batch. Only paid when a batch was
    actually throttled, so a clean run never waits."""
    raw = os.environ.get("AUDITFAST_BATCH_COOLDOWN_SECONDS", "30")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 30.0


#: Batch size for large runs (0 = off) and the adaptive cooldown between batches.
WORKSPACE_BATCH_SIZE = _resolve_workspace_batch_size()
BATCH_COOLDOWN_SECONDS = _resolve_batch_cooldown_seconds()


def _context_was_throttled(outcome: WorkspaceContext | Exception) -> bool:
    """True when a crawl outcome shows Power BI/Fabric throttling (HTTP 429 etc.).

    A throttled batch is the signal to cool down before the next one; a clean
    batch is not, so the pause is adaptive rather than unconditional.
    """
    if isinstance(outcome, WorkspaceAccessError):
        return outcome.status == 429
    if isinstance(outcome, WorkspaceContext):
        return any(
            (stat.get("transient") or 0) > 0
            for stat in outcome.read_failures.values()
        )
    return False


def _crawl_batch(
    provider,
    chunk: list[tuple[int, tuple[str, Layer, float, list[CheckSpec], set[Resource]]]],
) -> dict[int, WorkspaceContext | Exception]:
    """Crawl one batch of planned workspaces in parallel, keyed by plan index."""
    results: dict[int, WorkspaceContext | Exception] = {}
    workers = min(MAX_PARALLEL_WORKSPACES, len(chunk))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ws-fetch") as pool:
        future_to_idx = {
            pool.submit(_fetch_workspace, provider, wid, lyr, res): idx
            for idx, (wid, lyr, _factor, _specs, res) in chunk
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:  # noqa: BLE001 - reported in Phase 3
                results[idx] = exc
    return results



def _fetch_workspace(
    provider, workspace_id: str, layer: Layer, resources: set[Resource]
) -> WorkspaceContext:
    """Crawl one workspace, holding the process-wide concurrency gate."""
    with _FETCH_GATE:
        return provider.fetch(workspace_id, layer, resources)


#: Friendly plural names used in the "no objects of this kind" N/A message, so a
#: check that cannot run for lack of an object still appears with a clear reason.
_SCOPE_LABEL: dict[Scope, str] = {
    Scope.PIPELINE: "data pipelines",
    Scope.NOTEBOOK: "notebooks",
    Scope.LAKEHOUSE: "lakehouses or warehouses",
    Scope.SEMANTIC_MODEL: "semantic models",
    Scope.REPORT: "reports",
    Scope.EVENTHOUSE: "eventhouses",
}


def _scope_label(scope: Scope) -> str:
    return _SCOPE_LABEL.get(scope, f"{scope.value} objects")


#: The definition resource each object scope depends on. When the provider could
#: not read it (e.g. the token lacked the Item.ReadWrite scope getDefinition
#: needs), the objects are absent *because the read was blocked*, not because the
#: workspace has none — a different, actionable N/A reason.
_SCOPE_DEFINITION_RESOURCE: dict[Scope, Resource] = {
    Scope.PIPELINE: Resource.PIPELINE_DEFINITIONS,
    Scope.NOTEBOOK: Resource.NOTEBOOK_DEFINITIONS,
    Scope.SEMANTIC_MODEL: Resource.SEMANTIC_MODEL_DEFINITIONS,
}


def _no_objects_reason(scope: Scope, workspace: WorkspaceContext) -> str:
    """Why a scope yielded no objects: genuinely none, or a blocked read."""
    resource = _SCOPE_DEFINITION_RESOURCE.get(scope)
    if resource is not None and resource.value in workspace.read_failures:
        stat = workspace.read_failures[resource.value]
        return (
            f"{stat.get('failed', 0)} of {stat.get('attempted', 0)} "
            f"{_scope_label(scope)} could not be read "
            f"({stat.get('forbidden', 0)} forbidden HTTP 401/403, "
            f"{stat.get('transient', 0)} throttled/timeout HTTP 429/5xx) — their "
            f"definitions were blocked. The sign-in token may lack Item.ReadWrite.All "
            f"or a higher workspace role."
        )
    if resource is not None and resource in workspace.unavailable:
        return (
            f"{_scope_label(scope).capitalize()} exist but their definitions could "
            f"not be read — the sign-in token may lack the Item.ReadWrite.All scope "
            f"Fabric requires for getDefinition. Re-sign-in to grant it, then re-run."
        )
    return f"No {_scope_label(scope)} were found in this workspace"


_ACCESS_RECOMMENDATION = (
    "Confirm the workspace name/ID is correct and that the signed-in user has at "
    "least Viewer access, then re-run."
)


def _one_line(text: str) -> str:
    """Collapse report-bound text so it cannot break Markdown table rows."""
    return " ".join(str(text or "").split())


def _short_evidence(evidence: str, limit: int = 240) -> str:
    """Keep the detected gap useful without duplicating a large evidence cell."""
    text = _one_line(evidence)
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 3)
    if cutoff < limit // 2:
        cutoff = limit - 3
    return f"{text[:cutoff].rstrip()}..."


def _recommendation_target(scope: Scope, workspace: str, obj: str) -> str:
    workspace_name = _one_line(workspace) or "unknown workspace"
    object_name = _one_line(obj)
    if scope is Scope.GROUP:
        return f'project group "{workspace_name}"'
    if scope is Scope.WORKSPACE or not object_name:
        return f'workspace "{workspace_name}"'
    return f'{scope.value.replace("_", " ")} "{object_name}" in workspace "{workspace_name}"'


def _finding_recommendation(
    spec: CheckSpec | GroupCheckSpec,
    remediation: RemediationBook,
    *,
    workspace: str,
    obj: str,
    scope: Scope,
    evidence: str,
) -> str:
    """Build deterministic guidance tied to the exact target and observed gap."""
    action = _one_line(remediation.get(spec.ref))
    if not action:
        action = (
            f'Update this target to satisfy "{_one_line(spec.title)}". '
            "Use the observed gap to identify the missing configuration or implementation."
        )
    if not action.endswith((".", "!", "?")):
        action += "."

    observed = _short_evidence(evidence) or "The check did not meet its required condition."
    target = _recommendation_target(scope, workspace, obj)
    return (
        f"Target: {target}. Observed gap: {observed} "
        f"Action: {action} Verification: Re-run the audit and confirm {spec.id} "
        "is PASS for this target, or record a reviewed exception with supporting evidence."
    )


def access_error_result(workspace_id: str, layer: Layer, message: str) -> CheckResult:
    """A visible, non-scored result for a workspace that could not be read.

    Emitted instead of silently skipping, so an unreadable workspace shows up as
    an explicit error rather than quietly shrinking the denominator.
    """
    return CheckResult(
        check_id="WS-ACCESS", ref="-", title="Workspace could not be read",
        pillar=Pillar.ARCHITECTURE, status=Status.FAIL, score=None, coverage=None,
        evidence=message, recommendation=_ACCESS_RECOMMENDATION,
        severity=Severity.CRITICAL, workspace=workspace_id, layer=layer,
        obj="", scope=Scope.WORKSPACE, scored=False,
    )


#: Check id for the "part of this crawl could not be read" warning.
READ_INCOMPLETE_CHECK_ID = "WS-READ-INCOMPLETE"

#: Human labels for the resources whose per-item reads can partially fail.
_RESOURCE_LABEL: dict[str, str] = {
    "activatorDefinitions": "Activator definitions",
    "environmentDefinitions": "Environment definitions",
    "lakehouseFiles": "Lakehouse file listings",
    "notebookDefinitions": "notebook definitions",
    "pipelineDefinitions": "pipeline definitions",
    "semanticModelRefreshSchedule": "semantic model refresh schedules",
    "tableSchemas": "lakehouse table listings",
    "tableColumns": "lakehouse/warehouse column schemas",
    "semanticModelDefinitions": "semantic model definitions",
    "warehouseAudit": "Warehouse audit settings",
    "warehouseSecurity": "Warehouse security policies",
}


def read_incomplete_result(workspace: WorkspaceContext, resource_value: str, stat: dict) -> CheckResult:
    """A visible, unscored warning that part of a crawl could not be read.

    Emitted so a permission-limited or throttled crawl says "42 of 138 notebook
    definitions could not be read (HTTP 401/403)" instead of silently producing a
    believable-looking low score. Never scored: a read we could not make is not a
    failing best practice.
    """
    label = _RESOURCE_LABEL.get(resource_value, resource_value)
    attempted = stat.get("attempted", 0)
    failed = stat.get("failed", 0)
    forbidden = stat.get("forbidden", 0)
    transient = stat.get("transient", 0)
    empty = stat.get("empty", 0)
    kinds = []
    if forbidden:
        kinds.append(f"{forbidden} forbidden (HTTP 401/403)")
    if transient:
        kinds.append(f"{transient} throttled/timed out (HTTP 429/5xx/timeout)")
    if empty:
        kinds.append(f"{empty} returned no usable definition")
    reasons = stat.get("reasons") or {}
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        kinds.append("reasons: " + "; ".join(f"{r} x{c}" for r, c in top))
    artifacts = stat.get("artifacts") or []
    artifact_text = ""
    if artifacts:
        details = []
        for artifact in artifacts:
            name = artifact.get("name") or artifact.get("id") or "unknown artifact"
            artifact_id = artifact.get("id") or ""
            failure = artifact.get("failure") or "unreadable"
            reason = artifact.get("reason") or ""
            identity = f"{name} ({artifact_id})" if artifact_id and artifact_id != name else name
            details.append(f"{identity} [{failure}{f': {reason}' if reason else ''}]")
        artifact_text = " Affected artifacts: " + "; ".join(details) + "."
    reason_text = " ".join(str(reason).lower() for reason in reasons)
    actions = []
    if "pyodbc" in reason_text or "odbc driver" in reason_text:
        actions.append(
            "Install pyodbc and Microsoft ODBC Driver 18 for SQL Server in the backend runtime"
        )
    if "no provisioned sql analytics endpoint" in reason_text:
        actions.append(
            "Confirm the capacity is running and each Lakehouse/Warehouse SQL analytics endpoint "
            "has finished provisioning"
        )
    if forbidden:
        actions.append(
            "Re-sign in with the required delegated scope and a workspace role that can read the artifact"
        )
    if transient:
        actions.append(
            "Run a live refresh again; if it persists, inspect HTTP 429/5xx and network logs"
        )
    if empty:
        actions.append("Open the named artifact in Fabric and verify it returns a usable definition")
    recommendation = ". ".join(actions) + ("." if actions else "Re-run the live audit.")
    if attempted == 0 and reasons:
        evidence = f"{label.capitalize()} unavailable — {', '.join(kinds)}."
    else:
        evidence = (
            f"{failed} of {attempted} {label} could not be read — {', '.join(kinds)}. "
            f"{artifact_text}"
        )
    return CheckResult(
        check_id=READ_INCOMPLETE_CHECK_ID, ref="-",
        title="Incomplete crawl — data could not be read",
        pillar=Pillar.ARCHITECTURE, status=Status.NA, score=None, coverage=None,
        evidence=evidence, recommendation=recommendation,
        severity=Severity.HIGH, workspace=workspace.name, layer=workspace.layer,
        obj=label, scope=Scope.WORKSPACE, scored=False,
    )


def build_result(
    spec: CheckSpec,
    workspace: WorkspaceContext,
    verdict: Verdict,
    obj_name: str = "",
    remediation: RemediationBook = EMPTY_REMEDIATION,
    weight_factor: float = 1.0,
) -> CheckResult:
    """Combine a check's :class:`Verdict` with its registered metadata.

    This is the join that lets a check body stay three lines long: the id, ref,
    title, pillar, severity, weight and scope all come from the spec, and the
    workspace and object names come from the run — so no check repeats them.

    ``weight_factor`` scales the check's roll-up weight for this workspace — the
    cross-workspace environment weight (1.0 by default, so an unweighted run is
    identical). A uniform factor across a workspace cancels in that workspace's
    own percentage, so only cross-workspace roll-ups shift.
    """
    status = verdict.status or status_from_score(verdict.score or 0)
    passed = status is Status.PASS
    unjudged = not verdict.scored
    finding = status in (Status.FAIL, Status.PARTIAL)

    return CheckResult(
        check_id=spec.id,
        ref=spec.ref,
        title=spec.title,
        pillar=spec.pillar,
        status=status,
        score=verdict.score,
        coverage=verdict.coverage,
        evidence=verdict.evidence,
        # Guidance is only useful where there is something to fix.
        recommendation="" if (passed or unjudged) else _finding_recommendation(
            spec,
            remediation,
            workspace=workspace.name,
            obj=verdict.obj if verdict.obj is not None else obj_name,
            scope=spec.scope,
            evidence=verdict.evidence,
        ),
        # Severity describes the *finding*: a FAIL/PARTIAL row carries the spec
        # severity even when it is an unscored detail row (so a named per-object
        # defect is not shown as "Informational"); a pass, a note, or an N/A never
        # does.
        severity=spec.severity if finding else Severity.INFO,
        workspace=workspace.name,
        layer=workspace.layer,
        obj=verdict.obj if verdict.obj is not None else obj_name,
        scope=spec.scope,
        weight=spec.weight * weight_factor,
        scored=verdict.scored,
    )


def build_group_result(
    spec: GroupCheckSpec,
    group_name: str,
    verdict: Verdict,
    remediation: RemediationBook = EMPTY_REMEDIATION,
) -> CheckResult:
    """Combine a group check's :class:`Verdict` with its metadata.

    The finding belongs to the *project*, not a single workspace, so the result
    carries the group name in ``workspace`` and a ``GROUP`` scope. Its weight is
    the spec's own weight — environment weighting applies to per-workspace checks,
    not to a comparison that already spans the whole group.
    """
    status = verdict.status or status_from_score(verdict.score or 0)
    passed = status is Status.PASS
    unjudged = not verdict.scored
    finding = status in (Status.FAIL, Status.PARTIAL)

    return CheckResult(
        check_id=spec.id,
        ref=spec.ref,
        title=spec.title,
        pillar=spec.pillar,
        status=status,
        score=verdict.score,
        coverage=verdict.coverage,
        evidence=verdict.evidence,
        recommendation="" if (passed or unjudged) else _finding_recommendation(
            spec,
            remediation,
            workspace=group_name,
            obj=verdict.obj if verdict.obj is not None else "",
            scope=Scope.GROUP,
            evidence=verdict.evidence,
        ),
        severity=spec.severity if finding else Severity.INFO,
        workspace=group_name,
        layer=Layer.MIXED,
        obj=verdict.obj if verdict.obj is not None else "",
        scope=Scope.GROUP,
        weight=spec.weight,
        scored=verdict.scored,
    )


def _invoke(spec: CheckSpec, ctx: CheckContext) -> list[Verdict]:
    """Call one check, converting a crash into a reportable N/A rather than a stop.

    A buggy check must not abort a whole audit, but it must not silently vanish
    either — the failure is surfaced as an unscored result carrying the error.
    """
    try:
        outcome = spec.fn(ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        return [Verdict(
            evidence=f"Check raised {type(exc).__name__}: {exc}",
            score=None, scored=False, status=Status.NA,
        )]
    if outcome is None:
        return []
    return list(outcome) if isinstance(outcome, (list, tuple)) else [outcome]


def _invoke_group(spec: GroupCheckSpec, ctx: GroupContext) -> list[Verdict]:
    """Call one group check, converting a crash into a reportable N/A.

    Same contract as :func:`_invoke`: a buggy cross-workspace check surfaces as an
    unscored result carrying the error rather than aborting the run.
    """
    try:
        outcome = spec.fn(ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        return [Verdict(
            evidence=f"Group check raised {type(exc).__name__}: {exc}",
            score=None, scored=False, status=Status.NA,
        )]
    if outcome is None:
        return []
    return list(outcome) if isinstance(outcome, (list, tuple)) else [outcome]


def run_audit(
    provider,
    targets: Sequence[Target],
    settings: dict,
    *,
    registry: CheckRegistry = REGISTRY,
    pillars: Iterable[Pillar] | None = None,
    remediation: RemediationBook = EMPTY_REMEDIATION,
    on_progress: Callable[[list[CheckResult]], None] | None = None,
    weights: Mapping[str, float] | None = None,
    groups: Sequence[GroupTarget] | None = None,
    group_registry: GroupCheckRegistry = GROUP_REGISTRY,
) -> list[CheckResult]:
    """Audit every target workspace and return a flat list of results.

    Args:
        provider: anything satisfying the :class:`~auditfast.providers.base.Provider`
            protocol.
        targets: ``(workspace_id, layer)`` pairs to audit.
        settings: the project YAML's ``project:`` block, passed to every check.
        registry: check catalog to draw from. Injectable so tests can register a
            throwaway check without touching the global registry.
        pillars: restrict to these pillars. Applied *before* the run, so
            deselecting a pillar genuinely skips its work and its API calls.
        remediation: pre-written guidance, looked up by checklist ref.
        weights: optional ``workspace_id -> weight`` for cross-workspace
            environment weighting. Absent (or 1.0) leaves the roll-up identical to
            the unweighted mean; a higher weight makes that workspace's checks
            count more in the overall/pillar/layer roll-ups only.
        groups: optional project groups to run cross-workspace (group) checks
            over. Each is ``(name, ((workspace_id, layer, level), ...))``. Absent,
            or with an empty ``group_registry``, no group check runs and the
            per-workspace results are unchanged.
        group_registry: the catalog of cross-workspace checks. Injectable for
            tests; defaults to the (normally empty) global group registry.
    """
    wanted_pillars = list(pillars) if pillars else None
    results: list[CheckResult] = []

    # Group checks reuse the member workspaces' contexts, so cache each fetched
    # context by id and make sure a member's crawl also pulls the resources its
    # group checks need.
    group_specs = group_registry.select(pillars=wanted_pillars) if groups else []
    group_resources = group_registry.required_resources(group_specs) if group_specs else set()
    group_member_ids = {
        wid for _, members in (groups or []) for wid, _, _ in members
    }
    fetched: dict[str, WorkspaceContext] = {}

    # Phase 1 — plan each workspace's work (cheap; keeps target order).
    plan: list[tuple[str, Layer, float, list[CheckSpec], set[Resource]]] = []
    for workspace_id, layer in targets:
        factor = weights.get(workspace_id, 1.0) if weights else 1.0
        # Manual (attestation-only) specs are catalogued but never executed.
        specs = [s for s in registry.select(pillars=wanted_pillars, layer=layer)
                 if not s.manual]
        if not specs:
            continue

        # Only fetch what the selected checks will actually read — plus, for a
        # group member, whatever its group checks need to compare.
        resources: set[Resource] = registry.required_resources(specs)
        if workspace_id in group_member_ids:
            resources = resources | group_resources
        plan.append((workspace_id, layer, factor, specs, resources))

    # Phase 2 — crawl the planned workspaces concurrently. The crawl is
    # network-bound and each workspace is independent, so they are fetched in
    # parallel under the process-wide cap. Evaluation (Phase 3) stays sequential
    # and in target order, so the report is byte-for-byte a serial run's — only
    # the wall-clock shrinks. When AUDITFAST_WORKSPACE_BATCH_SIZE is set, a large
    # run is split into sequential batches with an *adaptive* cooldown: the pause
    # is paid only after a batch that was actually throttled, so a clean tenant
    # never waits, but a big throttled run spaces its Power BI calls across
    # separate rate-limit windows.
    crawled: dict[int, WorkspaceContext | Exception] = {}
    if plan:
        indexed = list(enumerate(plan))
        if WORKSPACE_BATCH_SIZE and len(indexed) > WORKSPACE_BATCH_SIZE:
            batches = [
                indexed[i:i + WORKSPACE_BATCH_SIZE]
                for i in range(0, len(indexed), WORKSPACE_BATCH_SIZE)
            ]
        else:
            batches = [indexed]
        for batch_num, batch in enumerate(batches):
            batch_outcomes = _crawl_batch(provider, batch)
            crawled.update(batch_outcomes)
            is_last = batch_num == len(batches) - 1
            if (not is_last and BATCH_COOLDOWN_SECONDS > 0
                    and any(_context_was_throttled(o) for o in batch_outcomes.values())):
                log.info(
                    "workspace batch %d/%d hit throttling; cooling down %.0fs before the next batch",
                    batch_num + 1, len(batches), BATCH_COOLDOWN_SECONDS,
                )
                time.sleep(BATCH_COOLDOWN_SECONDS)

    # Phase 3 — evaluate each workspace's checks, in target order. ``resources``
    # was consumed during the crawl phase and is unpacked only to keep the plan
    # tuple's shape explicit at the point of use.
    for idx, (workspace_id, layer, factor, specs, _resources) in enumerate(plan):
        outcome = crawled[idx]
        if isinstance(outcome, WorkspaceAccessError):
            results.append(access_error_result(workspace_id, layer, str(outcome)))
            continue
        if isinstance(outcome, Exception):
            results.append(access_error_result(
                workspace_id, layer, f"Could not read workspace '{workspace_id}': {outcome}"))
            continue
        workspace = outcome
        fetched[workspace_id] = workspace

        # Surface any partial crawl — definitions/tables that could not be read —
        # as visible, unscored warnings, so a permission/throttle gap never hides
        # behind a believable-looking low score.
        for resource_value, stat in sorted(workspace.read_failures.items()):
            results.append(read_incomplete_result(workspace, resource_value, stat))

        by_scope: dict[Scope, list[CheckSpec]] = {}
        for spec in specs:
            by_scope.setdefault(spec.scope, []).append(spec)

        for scope in registry.scopes(specs):
            scope_specs = by_scope.get(scope) or []
            objects = list(workspace.objects(scope))
            if not objects and scope is not Scope.WORKSPACE:
                # The checks apply to this layer, but the workspace holds no object
                # of their kind — either genuinely none, or their definitions could
                # not be read. Emit a visible N/A per check (with the accurate
                # reason) so no selected check is silently absent from the report.
                note = not_applicable(_no_objects_reason(scope, workspace))
                for spec in scope_specs:
                    results.append(build_result(spec, workspace, note, "", remediation, factor))
                continue
            for obj_name, obj in objects:
                # A workspace-scoped result has no object name of its own.
                result_obj = "" if scope is Scope.WORKSPACE else obj_name
                ctx = CheckContext(
                    workspace=workspace, settings=settings,
                    obj_name=obj_name, obj=obj,
                )
                for spec in scope_specs:
                    for verdict in _invoke(spec, ctx):
                        results.append(
                            build_result(spec, workspace, verdict, result_obj, remediation, factor)
                        )

        # Emit a partial snapshot after each workspace, so a long-running audit
        # can be shown and polled before every workspace has been processed.
        if on_progress is not None:
            on_progress(list(results))

    # Cross-workspace (group) checks run last, over the members already crawled.
    # A member not in `fetched` (never selected, or unreadable) is lazily fetched
    # here so a group is comparable even when its workspaces were not audited
    # individually; an unreadable member is simply dropped from the comparison.
    if groups and group_specs:
        for group_name, members in groups:
            member_ctxs: list[GroupMemberContext] = []
            for wid, layer, level in members:
                ctx = fetched.get(wid)
                if ctx is None:
                    try:
                        ctx = provider.fetch(wid, layer, group_resources)
                        fetched[wid] = ctx
                    except Exception:  # noqa: BLE001 - an unreadable member is skipped
                        continue
                member_ctxs.append(GroupMemberContext(ctx, level, layer))
            member_ctxs.sort(key=lambda member: member.environment_level)
            group_ctx = GroupContext(
                name=group_name, members=tuple(member_ctxs), settings=settings,
            )
            for spec in group_specs:
                # Fewer than two readable members: nothing to compare, so N/A —
                # never a low score.
                if len(member_ctxs) < 2:
                    results.append(build_group_result(
                        spec, group_name,
                        not_applicable(
                            f"only {len(member_ctxs)} of {len(members)} workspaces in "
                            f"group '{group_name}' could be read — a cross-workspace "
                            f"comparison needs at least two"
                        ),
                        remediation,
                    ))
                    continue
                for verdict in _invoke_group(spec, group_ctx):
                    results.append(build_group_result(spec, group_name, verdict, remediation))
        if on_progress is not None:
            on_progress(list(results))

    return results
