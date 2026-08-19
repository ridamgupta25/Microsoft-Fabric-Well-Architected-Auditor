"""Shared stakeholder-report structure and control-level consolidation."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import groupby

from ..core.enums import (
    SEVERITY_RANK,
    Pillar,
    Severity,
    Status,
)
from ..core.models import CheckResult
from ..core.scoring import percentage, rating
from ..core.validation import is_validated, validation_label

STATUS_RANK = {
    Status.FAIL: 0,
    Status.PARTIAL: 1,
    Status.PASS: 2,
    Status.NA: 3,
    Status.INFO: 4,
}

# Risk Score follows the client-approved SQL Auditor's ordinal bands rather
# than multiplying the displayed likelihood and impact values (High is 7).
RISK_PROFILE = {
    Severity.CRITICAL: (3, 3, 9, "0-7 days"),
    Severity.HIGH: (3, 3, 7, "30 days"),
    Severity.MEDIUM: (2, 2, 4, "90 days"),
    Severity.LOW: (1, 2, 2, "Next planning cycle"),
    Severity.INFO: (1, 1, 1, "Monitor"),
}


def _unique(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _join(values, separator: str = "; ", max_items: int | None = None) -> str:
    unique = _unique(values)
    if max_items is not None and len(unique) > max_items:
        remaining = len(unique) - max_items
        unique = [*unique[:max_items], f"... (+{remaining} more)"]
    return separator.join(unique) or "-"


def _asset(result: CheckResult) -> str:
    if result.obj:
        return f"{result.workspace} / {result.obj}"
    return result.workspace or "Project-wide"


def _evidence_line(result: CheckResult) -> str:
    evidence = (result.evidence or "No additional evidence recorded").strip()
    return f"{_asset(result)}: {evidence}"


def _grouped_evidence(
    results: tuple[CheckResult, ...],
    statuses: tuple[Status, ...] | None = None,
) -> str:
    grouped: dict[str, list[str]] = {}
    for result in results:
        if statuses is not None and result.status not in statuses:
            continue
        evidence = (result.evidence or "No additional evidence recorded").strip()
        grouped.setdefault(evidence, []).append(_asset(result))
    lines = (
        f"{evidence} [Assets: {_join(assets, ', ', max_items=20)}]"
        for evidence, assets in grouped.items()
    )
    return _join(lines, max_items=12)


def category_for(result: CheckResult) -> str:
    """Return a stable checklist-category label without inventing domain taxonomy."""
    parts = result.ref.split(".")
    if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
        return f"Checklist section {parts[0]}.{parts[1]}"
    prefix = (result.ref or result.check_id).split("-", 1)[0]
    return f"{prefix} controls"


@dataclass(frozen=True)
class ConsolidatedControl:
    """All asset-level verdicts for one deterministic checklist control."""

    check_id: str
    ref: str
    title: str
    pillar: Pillar
    results: tuple[CheckResult, ...]

    @property
    def category(self) -> str:
        return category_for(self.results[0])

    @property
    def status(self) -> Status:
        return min((result.status for result in self.results), key=STATUS_RANK.__getitem__)

    @property
    def severity(self) -> Severity:
        relevant = [
            result.severity
            for result in self.results
            if result.status in (Status.FAIL, Status.PARTIAL)
        ]
        candidates = relevant or [result.severity for result in self.results]
        return min(candidates, key=lambda severity: SEVERITY_RANK.get(severity, 99))

    @property
    def score_pct(self) -> float | None:
        return percentage(self.results)

    @property
    def score_summary(self) -> str:
        scores = [result.score for result in self.results if result.score is not None]
        if not scores:
            return "N/A"
        average = sum(scores) / len(scores)
        return f"{min(scores)} ({average:.2f} average)"

    @property
    def validation(self) -> str:
        return validation_label(self.ref)

    @property
    def impacted_assets(self) -> str:
        return _join(
            (
                _asset(result)
                for result in self.results
                if result.status in (Status.FAIL, Status.PARTIAL)
            ),
            max_items=30,
        )

    @property
    def non_impacted_assets(self) -> str:
        return _join(
            (
                _asset(result)
                for result in self.results
                if result.status is Status.PASS
            ),
            max_items=30,
        )

    @property
    def not_assessed(self) -> str:
        return _grouped_evidence(self.results, (Status.NA,))

    @property
    def impacted_evidence(self) -> str:
        impacted = _grouped_evidence(
            self.results,
            (Status.FAIL, Status.PARTIAL),
        )
        if impacted != "-":
            return impacted
        return _grouped_evidence(self.results)

    @property
    def recommendation(self) -> str:
        return _join(result.recommendation.strip() for result in self.results)

    @property
    def scopes(self) -> str:
        return _join(sorted({result.scope.value for result in self.results}), ", ")

    @property
    def assets_assessed(self) -> int:
        return len(
            {
                _asset(result)
                for result in self.results
                if result.status not in (Status.NA, Status.INFO)
            }
        )

    @property
    def finding(self) -> str:
        return f"{self.title} - {self.impacted_evidence}"

    @property
    def risk_profile(self) -> tuple[int, int, int, str]:
        return RISK_PROFILE[self.severity]


def consolidate(results: list[CheckResult]) -> list[ConsolidatedControl]:
    """Roll repeated asset verdicts up to one stakeholder finding per control."""
    sorted_results = sorted(
        results,
        key=lambda result: (
            result.pillar.value,
            result.ref,
            result.check_id,
            result.title,
            result.workspace,
            result.obj,
        ),
    )

    def key(result: CheckResult):
        return (
            result.check_id,
            result.ref,
            result.title,
            result.pillar,
        )
    controls = [
        ConsolidatedControl(
            check_id=group_key[0],
            ref=group_key[1],
            title=group_key[2],
            pillar=group_key[3],
            results=tuple(group),
        )
        for group_key, group in groupby(sorted_results, key=key)
    ]
    pillar_order = {pillar: index for index, pillar in enumerate(Pillar.scored(), start=1)}
    return sorted(
        controls,
        key=lambda control: (
            pillar_order.get(control.pillar, 99),
            control.ref,
            control.check_id,
        ),
    )


def workspace_ids(results: list[CheckResult]) -> dict[str, str]:
    """Return one ordered, report-local ID for every audited workspace."""
    workspaces = sorted({result.workspace for result in results if result.workspace})
    return {
        workspace: f"WS{index}"
        for index, workspace in enumerate(workspaces, start=1)
    }


def workspace_control_score(
    control: ConsolidatedControl,
    workspace: str,
) -> float | str | None:
    """Return the workspace/control weighted raw score or unscored result label."""
    workspace_results = tuple(
        result for result in control.results if result.workspace == workspace
    )
    if not workspace_results:
        return None
    scored = [result for result in workspace_results if result.counts_toward_score]
    total_weight = sum(result.weight for result in scored)
    if total_weight > 0:
        return sum((result.score or 0) * result.weight for result in scored) / total_weight
    return min(
        (result.status for result in workspace_results),
        key=STATUS_RANK.__getitem__,
    ).value


def pillar_controls(
    controls: list[ConsolidatedControl], pillar: Pillar
) -> list[ConsolidatedControl]:
    return [control for control in controls if control.pillar is pillar]


def findings(controls: list[ConsolidatedControl]) -> list[ConsolidatedControl]:
    rows = [
        control
        for control in controls
        if control.status in (Status.FAIL, Status.PARTIAL)
    ]
    return sorted(
        rows,
        key=lambda control: (
            SEVERITY_RANK.get(control.severity, 99),
            control.score_pct if control.score_pct is not None else 999,
            control.ref,
        ),
    )


def strengths(controls: list[ConsolidatedControl]) -> list[ConsolidatedControl]:
    return [control for control in controls if control.status is Status.PASS]


def severity_counts(controls: list[ConsolidatedControl]) -> Counter:
    return Counter(control.severity for control in findings(controls))


def validation_counts(controls: list[ConsolidatedControl]) -> tuple[int, int]:
    validated = sum(1 for control in controls if is_validated(control.ref))
    return validated, len(controls)


def assessment_weight(results: list[CheckResult], pillar: Pillar) -> float:
    scored = [result for result in results if result.counts_toward_score]
    total = sum(result.weight for result in scored)
    if not total:
        return 0.0
    pillar_weight = sum(result.weight for result in scored if result.pillar is pillar)
    return pillar_weight / total


def executive_narrative(agg: dict, controls: list[ConsolidatedControl]) -> str:
    overall = agg["overall"]
    label, _ = rating(overall)
    risk_rows = findings(controls)
    counts = severity_counts(controls)
    critical_high = counts[Severity.CRITICAL] + counts[Severity.HIGH]
    scored_pillars = [
        (pillar, agg["by_pillar"][pillar]["pct"])
        for pillar in Pillar.scored()
        if agg["by_pillar"][pillar]["pct"] is not None
    ]
    weakest = min(scored_pillars, key=lambda item: item[1])[0] if scored_pillars else None
    strongest = max(scored_pillars, key=lambda item: item[1])[0] if scored_pillars else None
    score_text = "not assessed" if overall is None else f"{overall:.1f}%"
    comparison = ""
    if weakest and strongest:
        comparison = (
            f" The strongest assessed area is {strongest.value}; the greatest "
            f"exposure is in {weakest.value}."
        )
    return (
        f"The assessment produced an overall score of {score_text}, corresponding "
        f"to a {label} risk rating. {len(risk_rows)} consolidated control finding(s) "
        f"require action, including {critical_high} Critical or High finding(s)."
        f"{comparison} Priorities below group repeated asset-level failures into "
        "control themes so stakeholders can assign ownership and track remediation."
    )
