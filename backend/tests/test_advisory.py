"""The advisory (non-deterministic) partition and its separate report.

Advisory checks assert data-level correctness or read structure the provider
cannot reliably fetch. They are kept out of the deterministic scorecard and the
main report, and surfaced in a separate, same-format Advisory report.
"""
from __future__ import annotations

import time

from auditfast.core.advisory import ADVISORY_REFS, is_advisory
from auditfast.core.check.registry import REGISTRY

from .conftest import (
    AUTHENTICATED_SESSION,
    EXPECTED_ADVISORY_RESULT_ROWS,
    EXPECTED_ADVISORY_SCORED,
)


def _wait_for_audit(client, audit_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/audit/{audit_id}").json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"audit {audit_id} did not finish within {timeout}s")


def _report(client) -> dict:
    audit_id = client.post(
        "/api/v1/audit", json={"auth_session": AUTHENTICATED_SESSION}
    ).json()["audit_id"]
    _wait_for_audit(client, audit_id)
    return audit_id, client.get(f"/api/v1/reports/{audit_id}").json()


def test_every_advisory_ref_is_a_registered_check():
    """A typo in the advisory list must fail fast, not silently flag nothing."""
    registered = {spec.ref for spec in REGISTRY.all()}
    missing = sorted(ADVISORY_REFS - registered)
    assert not missing, f"advisory refs with no registered check: {missing}"


def test_advisory_and_deterministic_results_are_disjoint(client):
    _, report = _report(client)

    # The deterministic report contains no advisory checks…
    assert report["results"]
    assert all(not is_advisory(r["ref"]) for r in report["results"])

    # …and the advisory block contains only advisory checks.
    advisory = report["advisory"]
    assert advisory["results"]
    assert all(is_advisory(r["ref"]) for r in advisory["results"])
    assert all(r["advisory"] for r in advisory["results"])


def test_advisory_block_has_its_own_scorecard(client):
    _, report = _report(client)
    advisory = report["advisory"]

    assert advisory["total_scored"] == EXPECTED_ADVISORY_SCORED
    assert len(advisory["results"]) == EXPECTED_ADVISORY_RESULT_ROWS
    # The advisory roll-up is independent of the deterministic overall.
    assert advisory["overall"] is not None
    assert advisory["overall"] != report["overall"]


def test_separate_advisory_report_files_are_generated_and_downloadable(client):
    audit_id, report = _report(client)

    assert report["files"].get("advisory_excel") == "advisory-report.xlsx"
    assert report["files"].get("advisory_markdown") == "advisory-report.md"

    excel = client.get(f"/api/v1/reports/{audit_id}/download/advisory-excel")
    assert excel.status_code == 200
    markdown = client.get(f"/api/v1/reports/{audit_id}/download/advisory-markdown")
    assert markdown.status_code == 200
