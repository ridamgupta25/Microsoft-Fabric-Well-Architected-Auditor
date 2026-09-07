"""Tests for the gated custom-checks live FetchProvider and its chaining.

All offline: a FAKE getter stands in for live Fabric — no network, no token. These
prove the gate, the read-only limits, and provider chaining.
"""
from __future__ import annotations

from auditfast.ai.orchestrator.live_provider import ChainedFetchProvider, LiveFetchProvider


class _Plan:
    """A tiny FetchPlan-like object carrying just an endpoint + field."""

    def __init__(self, endpoint: str, workspace_ids=None):
        self.endpoint = endpoint
        self.field = "notebooks"
        self.workspace_ids = list(workspace_ids or [])


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


# -- CodeFetchProvider: executes the AI-generated fetch code -------------------

_FETCH = (
    "def fetch(client, workspace_id):\n"
    "    return client.get(f'/v1/workspaces/{workspace_id}/roleAssignments')\n"
)


class _Check:
    """A tiny CustomCheck-like object carrying just the AI fetch code."""

    def __init__(self, fetch_code):
        self.fetch_code = fetch_code


def test_code_provider_executes_ai_fetch_code():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    getter = _getter({"/workspaces/wsA/roleAssignments": {"value": [1, 2]}})
    p = CodeFetchProvider(getter, enabled=True)
    p.bind_workspaces(["wsA"])
    p.bind_check(_Check(_FETCH))
    resp = p.fetch(_Plan("GET /v1/workspaces/{id}/roleAssignments"), "item_rest")
    assert resp.status == 200
    assert resp.body == {"value": [1, 2]}  # /v1 stripped, real GET, body returned


def test_code_provider_declines_without_fetch_code():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    p = CodeFetchProvider(_getter({}), enabled=True)
    p.bind_workspaces(["wsA"])  # no bind_check -> no fetch_code
    assert p.fetch(_Plan("GET /v1/workspaces/{id}/x"), "item_rest").status == 404


def test_code_provider_gate_off_declines():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    p = CodeFetchProvider(_getter({}), enabled=False)
    p.bind_check(_Check(_FETCH))
    assert p.fetch(_Plan("GET /v1/workspaces/{id}/x"), "item_rest").status == 404


def test_code_provider_multi_workspace_combines():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    getter = _getter({
        "/workspaces/wsA/roleAssignments": {"value": [1]},
        "/workspaces/wsB/roleAssignments": {"value": [2]},
    })
    p = CodeFetchProvider(getter, enabled=True)
    p.bind_workspaces(["wsA", "wsB"])
    p.bind_check(_Check(_FETCH))
    resp = p.fetch(_Plan("GET /v1/workspaces/{id}/roleAssignments"), "item_rest")
    assert resp.status == 200
    assert resp.body == {"value": [1, 2]}  # rows from both workspaces concatenated


def test_chain_binds_and_runs_code_after_snapshot_miss():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    # First provider (offline) has no data -> 404; the code provider then runs.
    snapshot = LiveFetchProvider(_getter({}), enabled=True)
    code = CodeFetchProvider(
        _getter({"/workspaces/wsA/roleAssignments": {"value": [9]}}), enabled=True
    )
    chain = ChainedFetchProvider(snapshot, code)
    chain.bind_workspaces(["wsA"])       # delegates to both children
    chain.bind_check(_Check(_FETCH))     # only the code provider takes it
    resp = chain.fetch(_Plan("GET /v1/workspaces/{id}/roleAssignments"), "item_rest")
    assert resp.status == 200
    assert resp.body == {"value": [9]}


# -- per-workspace targeting: fetch only the workspaces missing the field -------

def test_endpoint_provider_targets_plan_workspaces_and_returns_by_workspace():
    getter = _getter({
        "/workspaces/ws2/reports": {"value": [2]},
        "/workspaces/ws3/reports": {"value": [3]},
        "/workspaces/ws1/reports": {"value": [1]},  # present, must NOT be fetched
    })
    p = LiveFetchProvider(getter, enabled=True)
    p.bind_workspaces(["ws1", "ws2", "ws3"])       # all selected
    # Plan targets only the missing ones (ws2, ws3).
    resp = p.fetch(_Plan("GET /v1/workspaces/{id}/reports", ["ws2", "ws3"]), "item_rest")
    assert resp.status == 200
    assert resp.by_workspace == {"ws2": {"value": [2]}, "ws3": {"value": [3]}}
    assert resp.body == {"value": [2, 3]}  # ws1 NOT fetched (not in the plan)


def test_code_provider_targets_plan_workspaces_by_workspace():
    from auditfast.ai.orchestrator.live_provider import CodeFetchProvider

    getter = _getter({
        "/workspaces/ws2/roleAssignments": {"value": ["b"]},
        "/workspaces/ws3/roleAssignments": {"value": ["c"]},
    })
    p = CodeFetchProvider(getter, enabled=True)
    p.bind_workspaces(["ws1", "ws2", "ws3"])
    p.bind_check(_Check(_FETCH))
    resp = p.fetch(_Plan("GET /v1/workspaces/{id}/roleAssignments", ["ws2", "ws3"]), "item_rest")
    assert resp.status == 200
    assert resp.by_workspace == {"ws2": {"value": ["b"]}, "ws3": {"value": ["c"]}}


