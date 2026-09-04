"""Tests for the gated custom-checks live FetchProvider and its chaining.

All offline: a FAKE getter stands in for live Fabric — no network, no token. These
prove the gate, the read-only limits, and provider chaining.
"""
from __future__ import annotations

from auditfast.ai.orchestrator.live_provider import ChainedFetchProvider, LiveFetchProvider


class _Plan:
    """A tiny FetchPlan-like object carrying just an endpoint + field."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.field = "notebooks"


def _getter(mapping):
    def get(path):
        if path in mapping:
            return 200, mapping[path]
        return 404, None
    return get


def test_gate_off_never_fetches():
    calls = []

    def getter(path):
        calls.append(path)
        return 200, {"x": 1}

    provider = LiveFetchProvider(getter, enabled=False)
    resp = provider.fetch(_Plan("/workspaces/1/notebooks"), "item_rest")
    assert resp.status == 404
    assert calls == []  # gate off => getter never called


def test_live_fetch_returns_data_on_item_rest():
    provider = LiveFetchProvider(_getter({"/workspaces/1/notebooks": {"value": [1]}}), enabled=True)
    resp = provider.fetch(_Plan("/workspaces/1/notebooks"), "item_rest")
    assert resp.status == 200
    assert resp.body == {"value": [1]}


def test_live_fetch_skips_non_item_rest_strategies():
    provider = LiveFetchProvider(_getter({"/x": {"ok": 1}}), enabled=True)
    assert provider.fetch(_Plan("/x"), "git_artifact").status == 404
    assert provider.fetch(_Plan("/x"), "workspace_bundle").status == 404


def test_live_fetch_blocks_unsafe_endpoint():
    provider = LiveFetchProvider(_getter({}), enabled=True)
    assert provider.fetch(_Plan("http://evil.example/x"), "item_rest").status == 404


def test_live_fetch_call_budget():
    provider = LiveFetchProvider(_getter({"/a": {"v": 1}}), enabled=True, max_calls=1)
    assert provider.fetch(_Plan("/a"), "item_rest").status == 200
    assert provider.fetch(_Plan("/a"), "item_rest").status == 429  # budget spent


def test_live_fetch_size_cap_reports_no_data():
    big = {"blob": "x" * 5000}
    provider = LiveFetchProvider(_getter({"/a": big}), enabled=True, max_bytes=100)
    resp = provider.fetch(_Plan("/a"), "item_rest")
    assert resp.status == 200 and resp.body is None  # oversize -> treated as no data


def test_chained_provider_prefers_first_200():
    first = LiveFetchProvider(_getter({}), enabled=True)  # 404 for /a
    second = LiveFetchProvider(_getter({"/a": {"v": 2}}), enabled=True)
    resp = ChainedFetchProvider(first, second).fetch(_Plan("/a"), "item_rest")
    assert resp.status == 200 and resp.body == {"v": 2}


def test_chained_provider_returns_last_when_all_miss():
    a = LiveFetchProvider(_getter({}), enabled=True)
    b = LiveFetchProvider(_getter({}), enabled=True)
    assert ChainedFetchProvider(a, b).fetch(_Plan("/a"), "item_rest").status == 404


def test_build_live_provider_is_none_when_gate_off(monkeypatch):
    from types import SimpleNamespace

    from auditfast.services import custom_checks_service

    monkeypatch.setattr(
        custom_checks_service,
        "get_settings",
        lambda: SimpleNamespace(
            custom_checks_live_fetch_enabled=False,
            custom_checks_live_fetch_max_calls=20,
            custom_checks_live_fetch_max_bytes=2_000_000,
        ),
    )
    assert custom_checks_service.build_live_provider("some-token") is None


def test_build_live_provider_is_none_without_token(monkeypatch):
    from types import SimpleNamespace

    from auditfast.services import custom_checks_service

    monkeypatch.setattr(
        custom_checks_service,
        "get_settings",
        lambda: SimpleNamespace(
            custom_checks_live_fetch_enabled=True,
            custom_checks_live_fetch_max_calls=20,
            custom_checks_live_fetch_max_bytes=2_000_000,
        ),
    )
    assert custom_checks_service.build_live_provider(None) is None


# -- endpoint template resolution (GET-only, {id} substitution, /v1 de-dup) -----

def test_resolve_single_workspace_template():
    p = LiveFetchProvider(_getter({}), enabled=True)
    p.bind_workspaces(["ws1"])
    # strips the "GET " prefix, drops the leading /v1, substitutes {id}.
    assert p._resolve_paths("GET /v1/workspaces/{id}/roleAssignments") == [
        "/workspaces/ws1/roleAssignments"
    ]


def test_resolve_expands_one_path_per_workspace():
    p = LiveFetchProvider(_getter({}), enabled=True)
    p.bind_workspaces(["wsA", "wsB"])
    assert p._resolve_paths("GET /v1/workspaces/{id}/reports") == [
        "/workspaces/wsA/reports",
        "/workspaces/wsB/reports",
    ]


def test_resolve_declines_per_item_and_non_get():
    p = LiveFetchProvider(_getter({}), enabled=True)
    p.bind_workspaces(["wsA"])
    assert p._resolve_paths("GET /v1/workspaces/{id}/items/{id}/shortcuts") == []
    assert p._resolve_paths("POST /v1/workspaces/{id}/x") == []


def test_resolve_endpoint_without_id_needs_no_workspace():
    p = LiveFetchProvider(_getter({}), enabled=True)
    assert p._resolve_paths("GET /v1/deploymentPipelines") == ["/deploymentPipelines"]


def test_multi_workspace_fetch_combines_value_lists():
    getter = _getter(
        {
            "/workspaces/wsA/reports": {"value": [1, 2]},
            "/workspaces/wsB/reports": {"value": [3]},
        }
    )
    p = LiveFetchProvider(getter, enabled=True)
    p.bind_workspaces(["wsA", "wsB"])
    resp = p.fetch(_Plan("GET /v1/workspaces/{id}/reports"), "item_rest")
    assert resp.status == 200
    assert resp.body == {"value": [1, 2, 3]}  # every workspace's rows, concatenated


def test_multi_workspace_fetch_counts_calls_against_budget():
    getter = _getter(
        {
            "/workspaces/wsA/reports": {"value": [1]},
            "/workspaces/wsB/reports": {"value": [2]},
        }
    )
    p = LiveFetchProvider(getter, enabled=True, max_calls=1)
    p.bind_workspaces(["wsA", "wsB"])
    # The first workspace spends the only allowed call; the second trips the budget.
    assert p.fetch(_Plan("GET /v1/workspaces/{id}/reports"), "item_rest").status == 429

