"""Tests for the live provider's transport helpers (offline, with a fake session)."""
from __future__ import annotations

from auditfast.clients.live import LiveFabricProvider


class _FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _FakeSession:
    """A stand-in for requests.Session that serves canned responses by URL."""

    def __init__(self, by_url: dict):
        self.by_url = by_url
        self.calls: list[str] = []
        self.headers: dict = {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.by_url[url]


def _provider_with(session: _FakeSession) -> LiveFabricProvider:
    provider = LiveFabricProvider("token")
    provider._session = session
    return provider


def test_values_follows_continuation_to_the_last_page():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    session = _FakeSession({
        f"{base}/workspaces/w/items": _FakeResponse(
            200, {"value": [1, 2], "continuationUri": f"{base}/next-page"}),
        f"{base}/next-page": _FakeResponse(200, {"value": [3]}),
    })
    provider._session = session

    rows, known = provider._values("/workspaces/w/items")
    assert known is True
    assert rows == [1, 2, 3]
    assert len(session.calls) == 2


def test_values_single_page_makes_one_call():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    session = _FakeSession({
        f"{base}/workspaces/w/roleAssignments": _FakeResponse(200, {"value": [{"a": 1}]}),
    })
    provider._session = session

    rows, known = provider._values("/workspaces/w/roleAssignments")
    assert known is True
    assert rows == [{"a": 1}]
    assert len(session.calls) == 1


def test_values_first_call_failure_is_unknown_not_empty():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    session = _FakeSession({f"{base}/workspaces/w/git/connection": _FakeResponse(403, {})})
    provider._session = session

    rows, known = provider._values("/workspaces/w/git/connection")
    assert rows == []
    assert known is False
