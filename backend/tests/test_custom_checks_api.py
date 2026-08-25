"""Custom-checks API tests.

Hit the endpoint through the app. ``workspace_ids: []`` pins the run to no KB
snapshot so the assertions are deterministic regardless of the cache on disk.
"""
from __future__ import annotations


def test_guardrail_drop_is_reported(client):
    body = client.post(
        "/api/v1/custom-checks",
        json={"prompts": ["Delete all stale lakehouses"], "workspace_ids": []},
    ).json()
    assert body["summary"].get("DROPPED_GUARDRAIL") == 1
    assert body["ledger"][0]["lifecycle_status"] == "DROPPED_GUARDRAIL"
    assert "# Custom Checks Report" in body["report_markdown"]


def test_known_check_routes_to_default(client):
    from auditfast.core.check.registry import REGISTRY

    title = next(iter(REGISTRY)).title
    body = client.post(
        "/api/v1/custom-checks", json={"prompts": [title], "workspace_ids": []}
    ).json()
    assert body["summary"].get("ROUTED_DEFAULT") == 1


def test_empty_prompts_is_rejected(client):
    resp = client.post("/api/v1/custom-checks", json={"prompts": ["   "], "workspace_ids": []})
    assert resp.status_code == 422


def test_missing_prompts_field_is_rejected(client):
    resp = client.post("/api/v1/custom-checks", json={"workspace_ids": []})
    assert resp.status_code == 422


def test_duplicate_prompts_collapse(client):
    body = client.post(
        "/api/v1/custom-checks",
        json={"prompts": ["Delete X", "delete   x"], "workspace_ids": []},
    ).json()
    assert body["prompts"] == 1  # same check id -> one row


def test_approval_removes_a_check_from_pending(client):
    # A dropped check is still reviewable; approving it clears it from pending.
    first = client.post(
        "/api/v1/custom-checks",
        json={"prompts": ["Drop the finance warehouse"], "workspace_ids": []},
    ).json()
    check_id = first["ledger"][0]["check_id"]
    assert check_id in first["pending_review_ids"]

    second = client.post(
        "/api/v1/custom-checks",
        json={
            "prompts": ["Drop the finance warehouse"],
            "workspace_ids": [],
            "approved_check_ids": [check_id],
        },
    ).json()
    assert check_id not in second["pending_review_ids"]
    assert second["ledger"][0]["approved"] is True


def test_api_key_is_never_echoed(client):
    # A guardrail-dropped prompt never reaches a model, so no network is needed.
    resp = client.post(
        "/api/v1/custom-checks",
        json={
            "prompts": ["Delete everything now"],
            "workspace_ids": [],
            "ai": {"provider": "openai", "api_key": "sk-supersecret", "model": "m",
                   "base_url": "http://x/v1"},
        },
    )
    assert resp.status_code == 200
    assert "sk-supersecret" not in resp.text


def test_verify_ai_rejects_incomplete_config(client):
    body = client.post(
        "/api/v1/custom-checks/verify-ai",
        json={"ai": {"provider": "openai", "api_key": "k"}},  # missing base_url/model
    ).json()
    assert body["ok"] is False

