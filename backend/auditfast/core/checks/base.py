"""Base helpers and registries for the deterministic check library.

Every check is a small pure function that inspects ONE implemented object and
returns a CheckResult with a fixed status/score and a pre-written recommendation
(no AI). Checks register themselves via the decorators below.
"""
from __future__ import annotations

from ..models import CheckResult, Severity, Status
from ..scoring import band_from_coverage, status_from_score

# Registries populated by the decorators.
WORKSPACE_CHECKS: list = []
PIPELINE_CHECKS: list = []

# Pre-written remediation text keyed by checklist ref (loaded from remediation.yaml).
_REMEDIATION: dict = {}


def set_remediation(mapping: dict) -> None:
    global _REMEDIATION
    _REMEDIATION = mapping or {}


def rec(ref: str) -> str:
    return _REMEDIATION.get(ref, "")


def workspace_check(fn):
    WORKSPACE_CHECKS.append(fn)
    return fn


def pipeline_check(fn):
    PIPELINE_CHECKS.append(fn)
    return fn


def _sev(status: Status, fail_severity: Severity) -> Severity:
    return Severity.INFO if status == Status.PASS else fail_severity


def coverage_result(*, check_id, ref, title, pillar, coverage, evidence,
                    workspace, ws_role="", obj="", fail_severity=Severity.HIGH) -> CheckResult:
    coverage = max(0.0, min(1.0, coverage))
    score = band_from_coverage(coverage)
    status = status_from_score(score)
    return CheckResult(
        check_id=check_id, ref=ref, title=title, pillar=pillar, status=status,
        score=score, coverage=coverage, evidence=evidence,
        recommendation="" if status == Status.PASS else rec(ref),
        severity=_sev(status, fail_severity), workspace=workspace,
        workspace_role=ws_role, obj=obj,
    )


def binary_result(*, check_id, ref, title, pillar, ok, evidence,
                  workspace, ws_role="", obj="", fail_severity=Severity.HIGH) -> CheckResult:
    score = 3 if ok else 0
    status = Status.PASS if ok else Status.FAIL
    return CheckResult(
        check_id=check_id, ref=ref, title=title, pillar=pillar, status=status,
        score=score, coverage=1.0 if ok else 0.0, evidence=evidence,
        recommendation="" if ok else rec(ref),
        severity=_sev(status, fail_severity), workspace=workspace,
        workspace_role=ws_role, obj=obj,
    )


def scored_result(*, check_id, ref, title, pillar, score, evidence,
                  workspace, ws_role="", obj="", fail_severity=Severity.HIGH) -> CheckResult:
    status = status_from_score(score)
    return CheckResult(
        check_id=check_id, ref=ref, title=title, pillar=pillar, status=status,
        score=score, coverage=None, evidence=evidence,
        recommendation="" if status == Status.PASS else rec(ref),
        severity=_sev(status, fail_severity), workspace=workspace,
        workspace_role=ws_role, obj=obj,
    )


def info_result(*, check_id, ref, title, pillar, evidence,
                workspace, ws_role="", obj="") -> CheckResult:
    return CheckResult(
        check_id=check_id, ref=ref, title=title, pillar=pillar, status=Status.INFO,
        score=None, coverage=None, evidence=evidence, recommendation="",
        severity=Severity.INFO, workspace=workspace, workspace_role=ws_role,
        obj=obj, scored=False,
    )
