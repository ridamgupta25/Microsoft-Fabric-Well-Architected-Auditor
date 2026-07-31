"""Interactive (self-assessed) checklist points and how they are scored.

Some Well-Architected points cannot be read from Fabric but can still be scored
by asking the reviewer to choose an option during the audit — the Azure
Well-Architected Review model. This module lists those questions for a run and
merges the reviewer's answers into an already-computed report.

It is deliberately **additive**: the engine never runs these checks, and merging
answers only *adds* results, per workspace whose layer the question applies to —
it never changes an automated verdict. Merging is idempotent, so it can be
re-applied after the knowledge base refreshes without double-counting.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..core.check.registry import REGISTRY, CheckRegistry
from ..core.enums import Layer, Pillar, Severity, Status
from ..core.models import CheckOption, CheckResult, CheckSpec
from ..core.scoring import aggregate, status_from_score

#: Sentinel answer meaning "the reviewer explicitly skipped this question".
SKIP_VALUE = "__skip__"

#: Report keys recomputed from the merged result set. Everything else on the
#: report (project name, kb provenance, errors, files, partial flag) is kept.
_AGGREGATE_KEYS = (
    "overall",
    "by_pillar",
    "by_workspace",
    "by_layer",
    "matrix",
    "layers",
    "counts",
    "total_scored",
)


def _match_pillars(names: Iterable[str] | None) -> set[Pillar] | None:
    if not names:
        return None
    wanted = {str(n).strip().lower() for n in names if str(n).strip()}
    if not wanted:
        return None
    return {p for p in Pillar if p.value.lower() in wanted}


def _layers(values: Iterable[str] | None) -> list[Layer] | None:
    if not values:
        return None
    members = [Layer.parse(v) for v in values if str(v).strip()]
    return members or None


def _pillar_order(spec: CheckSpec) -> int:
    scored = Pillar.scored()
    return scored.index(spec.pillar) if spec.pillar in scored else len(scored)


def interactive_specs(
    pillars: Iterable[str] | None = None,
    layers: Iterable[str] | None = None,
    registry: CheckRegistry = REGISTRY,
) -> list[CheckSpec]:
    """The interactive checks in scope for a run, in pillar then id order.

    A check is in scope when its pillar is selected and it applies to at least
    one of the selected workspaces' layers. A ``MIXED`` workspace matches every
    check, so a project with a mixed workspace sees the full questionnaire.
    """
    wanted_pillars = _match_pillars(pillars)
    layer_members = _layers(layers)
    out: list[CheckSpec] = []
    for spec in registry:
        if not spec.interactive:
            continue
        if wanted_pillars is not None and spec.pillar not in wanted_pillars:
            continue
        if layer_members is not None and not any(spec.applies_to(m) for m in layer_members):
            continue
        out.append(spec)
    return sorted(out, key=lambda s: (_pillar_order(s), s.id))


def build_questionnaire(
    pillars: Iterable[str] | None = None,
    workspaces: Sequence[dict] | None = None,
    registry: CheckRegistry = REGISTRY,
) -> list[dict]:
    """The serialized questionnaire for a run's selected pillars and workspaces."""
    layers = [w.get("role") or w.get("layer") for w in (workspaces or [])] or None
    return [spec.to_dict() for spec in interactive_specs(pillars, layers, registry)]


def _find_option(spec: CheckSpec, raw: str | None) -> CheckOption | None:
    """The chosen option, or ``None`` for skip / unanswered / unknown."""
    if not raw or raw == SKIP_VALUE:
        return None
    return next((opt for opt in spec.options if opt.value == raw), None)


def _result_for(
    spec: CheckSpec, option: CheckOption | None, workspace: str, layer: Layer
) -> CheckResult:
    if option is None or option.score is None:
        return CheckResult(
            check_id=spec.id, ref=spec.ref, title=spec.title, pillar=spec.pillar,
            status=Status.NA, score=None, coverage=None,
            evidence="Skipped by the reviewer",
            recommendation="", severity=Severity.INFO,
            workspace=workspace, layer=layer, obj="", scope=spec.scope,
            weight=spec.weight, scored=False,
        )
    status = status_from_score(option.score)
    passed = status is Status.PASS
    return CheckResult(
        check_id=spec.id, ref=spec.ref, title=spec.title, pillar=spec.pillar,
        status=status, score=option.score, coverage=None,
        evidence=f"Self-assessed: {option.label}",
        recommendation="" if passed else option.guidance,
        severity=Severity.INFO if passed else spec.severity,
        workspace=workspace, layer=layer, obj="", scope=spec.scope,
        weight=spec.weight, scored=True,
    )


def _targets_from_results(results: Iterable[CheckResult]) -> list[tuple[str, Layer]]:
    """The distinct ``(workspace, layer)`` pairs actually audited.

    Derived from the automated results so an interactive answer is attributed to
    the same workspace name and layer the engine used — which is what lets it
    roll into the per-workspace and pillar x layer breakdowns.
    """
    seen: dict[str, Layer] = {}
    for r in results:
        if r.workspace and r.workspace not in seen:
            seen[r.workspace] = r.layer
    return list(seen.items())


def build_manual_results(
    answers: dict[str, str],
    targets: Sequence[tuple[str, Layer]],
    question_ids: Iterable[str],
    registry: CheckRegistry = REGISTRY,
) -> list[CheckResult]:
    """Turn reviewer answers into scored results, one per applicable workspace.

    Every question in ``question_ids`` produces a result for each target
    workspace whose layer it applies to: a scored result for a chosen option, or
    an unscored N/A for a skipped or unanswered question — so the report lists
    every checklist point the reviewer was shown.
    """
    results: list[CheckResult] = []
    for spec_id in question_ids:
        spec = registry.get(spec_id)
        if spec is None or not spec.interactive:
            continue
        applicable = [(name, layer) for name, layer in targets if spec.applies_to(layer)]
        if not applicable:
            continue
        option = _find_option(spec, answers.get(spec_id))
        for name, layer in applicable:
            results.append(_result_for(spec, option, name, layer))
    return results


def merge_answers_into_report(
    report: dict,
    answers: dict[str, str],
    question_ids: Iterable[str],
    registry: CheckRegistry = REGISTRY,
) -> dict:
    """Return a copy of ``report`` with the reviewer's answers merged in.

    Idempotent: any previously merged interactive results are dropped first, so
    re-applying after a knowledge-base refresh recomputes cleanly rather than
    double-counting.
    """
    interactive_ids = {s.id for s in registry if s.interactive}
    base_dicts = [
        row for row in report.get("results", [])
        if row.get("check_id") not in interactive_ids
    ]
    automated = [CheckResult.from_dict(row) for row in base_dicts]
    targets = _targets_from_results(automated)
    manual = build_manual_results(answers, targets, question_ids, registry)

    combined = automated + manual
    agg = aggregate(combined)

    merged = dict(report)
    for key in _AGGREGATE_KEYS:
        merged[key] = agg[key]
    merged["results"] = [r.to_dict() for r in combined]
    return merged
