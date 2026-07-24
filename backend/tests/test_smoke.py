"""Smoke tests: the mock audit runs end-to-end and produces sensible results."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auditfast.core.checks.base import (PIPELINE_CHECKS, WORKSPACE_CHECKS,
                                        set_remediation)
from auditfast.core.engine import run_audit
from auditfast.clients import MockFabricClient
from auditfast.core.models import Status
from auditfast.core.scoring import aggregate, band_from_coverage, rating

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANT = os.path.join(ROOT, "sample_data", "tenant.json")

SETTINGS = {
    "naming_convention": r"^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$",
    "pipeline_naming_convention": r"^PL_[A-Za-z0-9_]+$",
    "orphan_days": 90,
    "max_admins": 2,
}
WORKSPACES = [("ws-prep-01", "Data Prep"), ("ws-store-01", "Data Storage"), ("ws-ops-01", "Data Operations")]


def _run():
    set_remediation({})
    client = MockFabricClient(TENANT)
    return run_audit(client, WORKSPACES, SETTINGS)


def test_checks_registered():
    assert len(WORKSPACE_CHECKS) >= 8
    assert len(PIPELINE_CHECKS) >= 8


def test_bands():
    assert band_from_coverage(1.0) == 3
    assert band_from_coverage(0.85) == 2
    assert band_from_coverage(0.6) == 1
    assert band_from_coverage(0.2) == 0


def test_rating():
    assert rating(95)[0] == "Excellent"
    assert rating(30)[0] == "Critical"
    assert rating(None)[0] == "Not assessed"


def test_audit_runs_and_scores():
    results = _run()
    assert results, "expected some results"
    agg = aggregate(results)
    assert agg["overall"] is not None
    assert 0 <= agg["overall"] <= 100
    # Security pillar should exist and have scored checks
    assert agg["by_pillar"]["Security"]["count"] > 0


def test_secret_is_detected_and_critical():
    results = _run()
    secret_fails = [r for r in results
                    if r.check_id == "PL-SECRETS" and r.status == Status.FAIL]
    assert secret_fails, "expected the hardcoded-secret pipelines to fail"
    assert all(r.severity.value == "Critical" for r in secret_fails)


def test_naming_fail_for_bad_workspace():
    results = _run()
    name_checks = {(r.workspace, r.status) for r in results if r.check_id == "WS-NAME"}
    assert ("salesops", Status.FAIL) in name_checks


if __name__ == "__main__":
    # Minimal runner if pytest is unavailable.
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} tests passed")
