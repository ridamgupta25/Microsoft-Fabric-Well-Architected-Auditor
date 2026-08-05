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


def test_fetch_reads_and_persists_connection_metadata_without_tls_claims():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    connection = {
        "id": "connection-1",
        "displayName": "Orders API",
        "gatewayId": "gateway-1",
        "connectivityType": "ShareableCloud",
        "connectionDetails": {
            "type": "Web",
            "path": "https://api.example.test/orders",
        },
        "credentialDetails": {
            "credentialType": "OAuth2",
            "singleSignOnType": "MicrosoftEntraID",
            "connectionEncryption": "Encrypted",
            "skipTestConnection": False,
        },
        "connectionRecency": {
            "lastCredentialUsedDateTime": "2026-08-04T10:00:00Z",
        },
    }
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Prep"}),
        f"{base}/connections": _FakeResponse(200, {"value": [connection]}),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.CONNECTIONS])

    assert ctx.connections == [{
        "id": "connection-1",
        "display_name": "Orders API",
        "gateway_id": "gateway-1",
        "connectivity_type": "ShareableCloud",
        "connection_type": "Web",
        "endpoint": "https://api.example.test/orders",
        "credential_type": "OAuth2",
        "single_sign_on_type": "MicrosoftEntraID",
        "connection_encryption": "Encrypted",
        "skip_test_connection": False,
        "created_date_time": None,
        "last_bound_date_time": None,
        "last_credential_used_date_time": "2026-08-04T10:00:00Z",
        "minimum_tls_version": None,
        "status": "unknown",
    }]
    restored = type(ctx).from_dict(ctx.to_dict())
    assert restored.connections == ctx.connections


# -- token refresh tests -------------------------------------------------------

class _CountingFakeSession:
    """Tracks calls and returns different responses per call count."""

    def __init__(self):
        self.post_calls: list[str] = []
        self.get_calls: list[str] = []
        self.headers: dict = {}
        self._post_responses: list[_FakeResponse] = []
        self._get_responses: dict[str, list[_FakeResponse]] = {}

    def queue_post(self, response: _FakeResponse):
        self._post_responses.append(response)

    def queue_get(self, url: str, response: _FakeResponse):
        self._get_responses.setdefault(url, []).append(response)

    def post(self, url, timeout=None):
        self.post_calls.append(url)
        return self._post_responses.pop(0)

    def get(self, url, timeout=None):
        self.get_calls.append(url)
        responses = self._get_responses.get(url)
        if responses:
            return responses.pop(0)
        return _FakeResponse(404, {})

    def update(self, headers):
        self.headers.update(headers)


def test_definition_parts_refreshes_token_on_401_and_retries():
    """On HTTP 401 the provider refreshes the token and retries the same item."""
    refreshed = []

    def fake_refresher():
        refreshed.append(True)
        return "new-token"

    provider = LiveFabricProvider("old-token", token_refresher=fake_refresher)
    session = _CountingFakeSession()
    # First call returns 401 (expired), second call after refresh returns 200
    session.queue_post(_FakeResponse(401, {}))
    import base64, json
    payload = base64.b64encode(json.dumps({"activities": []}).encode()).decode()
    session.queue_post(_FakeResponse(200, {
        "definition": {"parts": [{"path": "pipeline-content.json", "payload": payload}]}
    }))
    provider._session = session
    provider._session.headers = {}

    parts, failure = provider._definition_parts("ws-1", "item-1")

    assert failure == ""
    assert parts == [{"path": "pipeline-content.json", "payload": payload}]
    assert len(refreshed) == 1  # refresher was called once
    assert len(session.post_calls) == 2  # original + retry
    assert provider._session.headers.get("Authorization") == "Bearer new-token"


def test_definition_parts_reports_forbidden_on_403_without_refresh():
    """HTTP 403 (permission denied) is not retried — reported as forbidden."""
    refreshed = []

    def fake_refresher():
        refreshed.append(True)
        return "new-token"

    provider = LiveFabricProvider("token", token_refresher=fake_refresher)
    session = _CountingFakeSession()
    session.queue_post(_FakeResponse(403, {}))
    provider._session = session
    provider._session.headers = {}

    parts, failure = provider._definition_parts("ws-1", "item-1")

    assert failure == "forbidden"
    assert parts == []
    assert len(refreshed) == 0  # refresher NOT called for 403


def test_definition_parts_401_without_refresher_reports_forbidden():
    """Without a refresher, 401 falls through to forbidden."""
    provider = LiveFabricProvider("token", token_refresher=None)
    session = _CountingFakeSession()
    session.queue_post(_FakeResponse(401, {}))
    provider._session = session
    provider._session.headers = {}

    parts, failure = provider._definition_parts("ws-1", "item-1")

    assert failure == "forbidden"
    assert parts == []


def test_workspace_read_retries_on_401_after_refresh():
    """The initial workspace GET retries after a successful token refresh."""
    refreshed = []

    def fake_refresher():
        refreshed.append(True)
        return "new-token"

    provider = LiveFabricProvider("old-token", token_refresher=fake_refresher)
    base = provider.BASE
    session = _CountingFakeSession()
    # First workspace read returns 401, second returns 200
    ws_url = f"{base}/workspaces/ws-1"
    session.queue_get(ws_url, _FakeResponse(401, {}))
    session.queue_get(ws_url, _FakeResponse(200, {"displayName": "MyWS", "capacityId": "cap1"}))
    # Items endpoint (needed by fetch)
    items_url = f"{base}/workspaces/ws-1/items"
    session.queue_get(items_url, _FakeResponse(200, {"value": []}))
    provider._session = session
    provider._session.headers = {}

    from auditfast.core.enums import Resource
    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS])

    assert ctx.display_name == "MyWS"
    assert len(refreshed) == 1
    assert len(session.get_calls) == 3  # ws(401) + ws(200) + items
