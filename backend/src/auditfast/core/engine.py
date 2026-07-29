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

from collections.abc import Iterable, Sequence

from ..clients.errors import WorkspaceAccessError
from .check.helpers import EMPTY_REMEDIATION, RemediationBook, Verdict, not_applicable
from .check.registry import REGISTRY, CheckRegistry
from .enums import Layer, Pillar, Resource, Scope, Severity, Status
from .models import CheckContext, CheckResult, CheckSpec, WorkspaceContext
from .scoring import status_from_score

#: One workspace to audit: its id and the layer role it plays.
Target = tuple[str, Layer]

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


_ACCESS_RECOMMENDATION = (
    "Confirm the workspace name/ID is correct and that the signed-in user has at "
    "least Viewer access, then re-run."
)


def access_error_result(workspace_id: str, layer: Layer, message: str) -> CheckResult:
    """A visible, non-scored result for a workspace that could not be read.

    Emitted instead of silently skipping, so an unreadable workspace shows up as
    an explicit error rather than quietly shrinking the denominator.
    """
    return CheckResult(
        check_id="WS-ACCESS", ref="-", title="Workspace could not be read",
        pillar=Pillar.FOUNDATION, status=Status.FAIL, score=None, coverage=None,
        evidence=message, recommendation=_ACCESS_RECOMMENDATION,
        severity=Severity.CRITICAL, workspace=workspace_id, layer=layer,
        obj="", scope=Scope.WORKSPACE, scored=False,
    )


def build_result(
    spec: CheckSpec,
    workspace: WorkspaceContext,
    verdict: Verdict,
    obj_name: str = "",
    remediation: RemediationBook = EMPTY_REMEDIATION,
) -> CheckResult:
    """Combine a check's :class:`Verdict` with its registered metadata.

    This is the join that lets a check body stay three lines long: the id, ref,
    title, pillar, severity, weight and scope all come from the spec, and the
    workspace and object names come from the run — so no check repeats them.
    """
    status = verdict.status or status_from_score(verdict.score or 0)
    passed = status is Status.PASS
    unjudged = not verdict.scored

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
        recommendation="" if (passed or unjudged) else remediation.get(spec.ref),
        # Severity describes the *finding*, so a passing check is never "High".
        severity=Severity.INFO if (passed or unjudged) else spec.severity,
        workspace=workspace.name,
        layer=workspace.layer,
        obj=verdict.obj if verdict.obj is not None else obj_name,
        scope=spec.scope,
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


def run_audit(
    provider,
    targets: Sequence[Target],
    settings: dict,
    *,
    registry: CheckRegistry = REGISTRY,
    pillars: Iterable[Pillar] | None = None,
    remediation: RemediationBook = EMPTY_REMEDIATION,
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
    """
    wanted_pillars = list(pillars) if pillars else None
    results: list[CheckResult] = []

    for workspace_id, layer in targets:
        # Manual (attestation-only) specs are catalogued but never executed.
        specs = [s for s in registry.select(pillars=wanted_pillars, layer=layer)
                 if not s.manual]
        if not specs:
            continue

        # Only fetch what the selected checks will actually read.
        resources: set[Resource] = registry.required_resources(specs)
        try:
            workspace = provider.fetch(workspace_id, layer, resources)
        except WorkspaceAccessError as exc:
            results.append(access_error_result(workspace_id, layer, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - any provider failure is reportable
            results.append(access_error_result(
                workspace_id, layer, f"Could not read workspace '{workspace_id}': {exc}"))
            continue

        by_scope: dict[Scope, list[CheckSpec]] = {}
        for spec in specs:
            by_scope.setdefault(spec.scope, []).append(spec)

        for scope in registry.scopes(specs):
            scope_specs = by_scope.get(scope) or []
            objects = list(workspace.objects(scope))
            if not objects and scope is not Scope.WORKSPACE:
                # The checks apply to this layer, but the workspace holds no object
                # of their kind. Emit a visible N/A per check (with the reason) so
                # no selected check is silently absent from the report.
                note = not_applicable(
                    f"No {_scope_label(scope)} were found in this workspace")
                for spec in scope_specs:
                    results.append(build_result(spec, workspace, note, "", remediation))
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
                            build_result(spec, workspace, verdict, result_obj, remediation)
                        )

    return results
