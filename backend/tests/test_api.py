"""REST API behaviour, exercised through the ASGI app.

No server and no browser: the TestClient drives the real app, middleware and
exception handlers included.
"""
from __future__ import annotations

import time

from .conftest import EXPECTED_OVERALL


def _wait_for_audit(client, audit_id: str, timeout: float = 30.0) -> dict:
    """Poll an audit until it reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/audit/{audit_id}").json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"audit {audit_id} did not finish within {timeout}s")


# -- health --------------------------------------------------------------------

def test_health_reports_a_loaded_rule_library(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["checks_registered"] == 20


def test_liveness_touches_no_dependencies(client):
    assert client.get("/api/v1/health/live").status_code == 204


def test_root_returns_json_not_html(client):
    """This API renders no HTML; the frontend is a separate deployable."""
    response = client.get("/")
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["docs"] == "/docs"


# -- catalog -------------------------------------------------------------------

def test_catalog_lists_pillars_and_layers(client):
    pillars = client.get("/api/v1/catalog/pillars").json()
    assert {p["name"] for p in pillars} >= {"Security", "Reliability"}
    layers = client.get("/api/v1/catalog/layers").json()
    assert {layer["name"] for layer in layers} >= {"Data Prep", "Data Storage"}


def test_catalog_filters_by_pillar(client):
    rows = client.get("/api/v1/catalog/checks", params={"pillar": "Security"}).json()
    assert rows
    assert {r["pillar"] for r in rows} == {"Security"}


def test_catalog_describes_one_check(client):
    body = client.get("/api/v1/catalog/checks/PL-RETRY").json()
    assert body["ref"] == "2.4.1"
    assert body["scope"] == "pipeline"
    assert body["severity"] == "High"


def test_unknown_check_is_404_with_error_shape(client):
    response = client.get("/api/v1/catalog/checks/NOPE")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "http_error"
    assert body["correlation_id"]


# -- workspaces ----------------------------------------------------------------

def test_workspaces_lists_the_mock_tenant(client):
    rows = client.get("/api/v1/workspaces", params={"mode": "mock"}).json()
    assert {r["id"] for r in rows} == {"ws-prep-01", "ws-store-01", "ws-ops-01"}


def test_live_workspaces_without_sign_in_is_401(client):
    response = client.get("/api/v1/workspaces/live", params={"session": "nope"})
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_error"


# -- audit lifecycle -----------------------------------------------------------

def test_audit_is_accepted_then_completes_with_the_expected_score(client):
    accepted = client.post("/api/v1/audit", json={"mode": "mock"})
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["status"] in {"queued", "running"}

    finished = _wait_for_audit(client, body["audit_id"])
    assert finished["status"] == "succeeded"
    # The API must produce exactly the score the engine does.
    assert finished["report"]["overall"] == EXPECTED_OVERALL


def test_report_endpoint_returns_the_full_scorecard(client):
    audit_id = client.post("/api/v1/audit", json={"mode": "mock"}).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert report["overall"] == EXPECTED_OVERALL
    assert report["total_scored"] == 57
    assert len(report["results"]) == 60
    assert report["by_pillar"]["Performance Efficiency"]["pct"] is None


def test_report_includes_the_pillar_by_layer_matrix(client):
    audit_id = client.post("/api/v1/audit", json={"mode": "mock"}).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert set(report["layers"]) == {"Data Prep", "Data Storage", "Data Operations"}
    assert report["matrix"]["Security"]["Data Prep"] is not None


def test_pillar_selection_narrows_the_report(client):
    audit_id = client.post(
        "/api/v1/audit", json={"mode": "mock", "pillars": ["Security"]}
    ).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert {r["pillar"] for r in report["results"]} == {"Security"}


def test_unreadable_workspace_is_an_error_not_a_failing_check(client):
    audit_id = client.post("/api/v1/audit", json={
        "mode": "mock",
        "workspaces": [{"id": "does-not-exist", "role": "Data Prep"}],
    }).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert report["errors"][0]["workspace"] == "does-not-exist"
    assert report["results"] == []
    assert report["overall"] is None


def test_unknown_audit_is_404(client):
    assert client.get("/api/v1/audit/does-not-exist").status_code == 404


def test_live_audit_without_sign_in_is_rejected_before_scheduling(client):
    response = client.post("/api/v1/audit", json={"mode": "live"})
    assert response.status_code == 401
    assert response.json()["code"] == "authentication_error"


# -- single check --------------------------------------------------------------

def test_single_check_runs_synchronously(client):
    results = client.post("/api/v1/audit/check", json={
        "check_id": "WS-GIT", "workspace_id": "ws-prep-01",
        "mode": "mock", "layer": "Data Prep",
    }).json()
    assert len(results) == 1
    assert results[0]["check_id"] == "WS-GIT"
    assert results[0]["status"] == "PASS"


def test_single_check_with_unknown_id_is_400(client):
    response = client.post("/api/v1/audit/check", json={
        "check_id": "NOPE", "workspace_id": "ws-prep-01", "mode": "mock",
    })
    assert response.status_code == 400
    assert response.json()["code"] == "audit_error"


# -- history and recommendations -----------------------------------------------

def test_history_lists_completed_audits(client):
    audit_id = client.post("/api/v1/audit", json={"mode": "mock"}).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    page = client.get("/api/v1/history").json()
    assert page["total"] >= 1
    assert any(item["audit_id"] == audit_id for item in page["items"])
    assert page["items"][0]["overall"] is not None


def test_history_paging_is_bounded(client):
    """An unbounded list endpoint is a future outage."""
    assert client.get("/api/v1/history", params={"limit": 500}).status_code == 422


def test_recommendations_are_severity_ordered(client):
    audit_id = client.post("/api/v1/audit", json={"mode": "mock"}).json()["audit_id"]
    _wait_for_audit(client, audit_id)

    body = client.get(f"/api/v1/recommendations/{audit_id}").json()
    assert body["total"] > 0
    assert body["ai_enabled"] is False
    assert body["items"][0]["severity"] == "Critical"
    assert all(item["recommendation"] for item in body["items"])


def test_recommendations_for_unknown_audit_is_404(client):
    assert client.get("/api/v1/recommendations/nope").status_code == 404


# -- cross-cutting concerns ----------------------------------------------------

def test_every_response_carries_a_correlation_id(client):
    response = client.get("/api/v1/health")
    assert response.headers["X-Correlation-Id"]


def test_inbound_correlation_id_is_honoured(client):
    response = client.get("/api/v1/health", headers={"X-Correlation-Id": "trace-me"})
    assert response.headers["X-Correlation-Id"] == "trace-me"


def test_validation_errors_use_the_standard_error_shape(client):
    response = client.post("/api/v1/audit", json={"mode": "not-a-mode"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert "mode" in body["detail"]


def test_openapi_documents_every_endpoint(client):
    spec = client.get("/openapi.json").json()
    assert len(spec["paths"]) >= 20
    assert spec["info"]["title"]
