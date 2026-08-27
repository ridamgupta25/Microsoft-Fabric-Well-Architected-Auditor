"""Tests for the live provider's transport helpers (offline, with a fake session)."""
from __future__ import annotations

from types import SimpleNamespace

from auditfast.clients.live import LiveFabricProvider
from auditfast.services import audit_service


def test_build_provider_uses_configured_fabric_timeout(monkeypatch):
    captured = {}

    class FakeLiveProvider:
        def __init__(self, token, **kwargs):
            captured.update(token=token, **kwargs)

    settings = SimpleNamespace(
        fabric_api_timeout_seconds=240,
        sql_endpoint_enabled=False,
        cache_enabled=False,
        kb_archive_enabled=False,
    )
    monkeypatch.setattr(audit_service, "get_settings", lambda: settings)
    monkeypatch.setattr(audit_service, "LiveFabricProvider", FakeLiveProvider)

    provider = audit_service.build_provider(None, token="token")

    assert isinstance(provider, FakeLiveProvider)
    assert captured["timeout"] == 240


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

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.by_url[url]


class _SequenceGetSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.headers: dict = {}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)


def _provider_with(session: _FakeSession) -> LiveFabricProvider:
    provider = LiveFabricProvider("token")
    provider._session = session
    return provider


class _FakePowerBI:
    """A stand-in for PowerBIClient: canned refresh times and created dates."""

    def __init__(self, by_dataset: dict, created: dict | None = None):
        self.by_dataset = by_dataset
        self.created = created or {}
        self.calls: list[tuple] = []

    def dataset_last_refresh(self, dataset_id, group_id=None):
        self.calls.append((dataset_id, group_id))
        return self.by_dataset.get(dataset_id), ""

    def dataset_created_dates(self, group_id=None):
        return dict(self.created)


class _FakeRefreshSchedulePowerBI:
    """A stand-in for PowerBIClient that serves canned refresh *schedules*.

    Returns ``(schedule, failure)`` per dataset id, so a test can exercise the
    read / no-schedule / forbidden branches independently of HTTP.
    """

    def __init__(self, by_dataset: dict):
        self.by_dataset = by_dataset
        self.calls: list[tuple] = []

    def dataset_refresh_schedule(self, dataset_id, group_id=None):
        self.calls.append((dataset_id, group_id))
        return self.by_dataset.get(dataset_id, (None, ""))

    def dataset_last_refresh(self, dataset_id, group_id=None):
        return None, ""

    def dataset_created_dates(self, group_id=None):
        return {}


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


def test_git_connection_reads_state_not_status():
    """A 200 only means the endpoint answered; gitConnectionState decides connected.

    Fabric returns HTTP 200 even for a not-connected workspace (the body carries
    ``gitConnectionState: NotConnected``), so keying off the status code alone
    would mark every accessible workspace as Git-connected.
    """
    parse = LiveFabricProvider._git_connection
    # 200 + a connected state -> connected, with provider/repo facts surfaced.
    ado = parse({
        "gitConnectionState": "ConnectedAndInitialized",
        "gitProviderDetails": {
            "gitProviderType": "AzureDevOps", "organizationName": "Contoso",
            "projectName": "Fabric", "repositoryName": "Repo", "branchName": "main",
        },
        "gitSyncDetails": {"head": "abc123", "lastSyncTime": "2026-01-01T00:00:00"},
    })
    assert ado["connected"] is True
    assert ado["provider"] == "AzureDevOps"
    assert ado["organization"] == "Contoso"
    assert ado["branch"] == "main"
    assert ado["last_sync_time"] == "2026-01-01T00:00:00"
    # 200 + NotConnected -> not connected (the endpoint still returns 200 here).
    assert parse({"gitProviderDetails": None, "gitSyncDetails": None,
                  "gitConnectionState": "NotConnected"})["connected"] is False
    # GitHub reports ownerName rather than organizationName.
    gh = parse({"gitConnectionState": "Connected",
                "gitProviderDetails": {"gitProviderType": "GitHub", "ownerName": "octocat"}})
    assert gh["connected"] is True
    assert gh["organization"] == "octocat"
    # A missing / unparseable body -> empty facts (fetch treats this as not connected).
    assert parse(None) == {}


def test_repository_security_discovery_normalizes_github_and_caches():
    provider = LiveFabricProvider("token", github_repository_security_token="repo-token")
    base = "https://api.github.com/repos/contoso/fabric"
    session = _FakeSession({
        base: _FakeResponse(200, {"security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        }}),
    })
    provider._session = session
    details = {"provider": "GitHub", "organization": "contoso", "repository": "fabric"}

    first = provider._discover_repository_security(details)
    second = provider._discover_repository_security(details)

    assert first == {"enabled": True, "push_protection": True,
                     "verified": True, "reason": None}
    assert second == first
    assert session.calls == [base]


def test_repository_security_discovery_does_not_query_github_without_token():
    provider = LiveFabricProvider("token")
    session = _FakeSession({})
    provider._session = session

    result = provider._discover_repository_security({
        "provider": "GitHub", "organization": "contoso", "repository": "fabric",
    })

    assert result["verified"] is False
    assert result["enabled"] is None
    assert "token not provided" in result["reason"]
    assert session.calls == []


def test_repository_security_discovery_normalizes_azure_devops_enablement():
    provider = LiveFabricProvider(
        "token", azure_devops_repository_security_token="repo-token"
    )
    url = (
        "https://advsec.dev.azure.com/Contoso/Fabric/_apis/management/"
        "repositories/repo-id/enablement?api-version=7.2-preview.1"
    )
    session = _FakeSession({url: _FakeResponse(200, {"advSecEnabled": True})})
    provider._session = session

    result = provider._discover_repository_security({
        "provider": "AzureDevOps", "organization": "Contoso", "project": "Fabric",
        "repository": "Repo", "repository_id": "repo-id",
    })

    assert result == {"enabled": True, "push_protection": None,
                     "verified": True, "reason": None}
    assert session.calls == [url]


def test_notebook_monitoring_reads_latest_session_metrics():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    root = f"{base}/workspaces/w/notebooks/n/livySessions"
    app = f"{root}/livy-1/applications/app-1"
    session = _FakeSession({
        root: _FakeResponse(200, {"value": [{"livyId": "livy-1", "appId": "app-1"}]}),
        f"{root}/livy-1": _FakeResponse(200, {
            "livyId": "livy-1", "appId": "app-1", "executorCores": 4,
        }),
        f"{app}/advice": _FakeResponse(200, {"value": []}),
        f"{app}/resourceUsage": _FakeResponse(200, {
            "duration": 600_000, "idleTime": 60_000, "coreEfficiency": 0.8,
        }),
        f"{app}/stages": _FakeResponse(200, [{"stageId": 1}]),
    })
    provider._session = session

    monitoring = provider._notebook_monitoring("w", "n")

    assert monitoring["livy_id"] == "livy-1"
    assert monitoring["app_id"] == "app-1"
    assert monitoring["resource_usage"]["coreEfficiency"] == 0.8
    assert monitoring["stages"] == [{"stageId": 1}]
    assert len(session.calls) == 5


def test_notebook_monitoring_returns_empty_when_sessions_are_unavailable():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    provider._session = _FakeSession({
        f"{base}/workspaces/w/notebooks/n/livySessions": _FakeResponse(403, {}),
    })

    assert provider._notebook_monitoring("w", "n") == {}


def test_environment_definition_reads_spark_runtime():
    provider = LiveFabricProvider("token")
    import base64

    payload = base64.b64encode(
        b"runtime_version: '1.3'\ndriver_cores: 4\n"
    ).decode()
    provider._definition_parts = lambda workspace_id, item_id, fmt=None: ([
        {"path": "Setting/Sparkcompute.yml", "payload": payload}
    ], "")

    definition, failure = provider._environment_definition("w", "env")

    assert failure == ""
    assert definition == {"runtime_version": "1.3", "driver_cores": 4}


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


def test_fetch_enriches_last_run_from_job_history():
    """A runnable item's last_run_utc is its latest run/refresh time.

    Fabric-native items (notebook) come from jobs/instances; semantic models
    from the Power BI refresh history. Non-runnable types (reports, dashboards)
    are skipped, so no wasted call is made for them.
    """
    provider = LiveFabricProvider("token", powerbi_token="pbi")
    base = provider.BASE
    items = {"value": [
        {"id": "nb-1", "type": "Notebook", "displayName": "Nightly"},
        {"id": "sm-1", "type": "SemanticModel", "displayName": "Sales"},
        {"id": "rp-1", "type": "Report", "displayName": "Dashboard"},
    ]}
    nb_jobs = {"value": [
        {"status": "Completed", "startTimeUtc": "2026-01-01T00:00:00Z",
         "endTimeUtc": "2026-01-01T00:05:00Z"},
        {"status": "Completed", "startTimeUtc": "2026-03-02T09:00:00Z",
         "endTimeUtc": "2026-03-02T09:30:00Z"},
    ]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Prep"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
        f"{base}/workspaces/ws-1/items/nb-1/jobs/instances": _FakeResponse(200, nb_jobs),
    })
    provider._session = session
    provider._powerbi_client = _FakePowerBI(
        {"sm-1": "2024-07-27T16:28:52Z"},
        created={"sm-1": "2024-01-05T00:00:00Z"},
    )

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY])

    by_id = {i.id: i for i in ctx.items}
    assert by_id["nb-1"].last_run_utc == "2026-03-02T09:30:00Z"  # latest of two runs
    assert by_id["sm-1"].last_run_utc == "2024-07-27T16:28:52Z"  # from PBI refresh history
    assert by_id["sm-1"].created_date == "2024-01-05T00:00:00Z"  # from PBI datasets API
    assert by_id["rp-1"].last_run_utc is None  # non-runnable type
    # The semantic model is read via Power BI with the Fabric workspace id as group.
    assert provider._powerbi_client.calls == [("sm-1", "ws-1")]
    # A report never runs a job, so its history endpoint is never queried.
    assert f"{base}/workspaces/ws-1/items/rp-1/jobs/instances" not in session.calls
    assert Resource.ITEM_RUN_HISTORY not in ctx.unavailable


def test_fetch_semantic_models_without_powerbi_token_is_na():
    """No Power BI token -> semantic-model recency is unknown (N/A), never stale.

    This is the "My workspace" case: only semantic models, no PBI token, so the
    resource is unavailable and the staleness check reports N/A rather than a
    blanket FAIL — and caching is never blocked.
    """
    provider = LiveFabricProvider("token")  # no powerbi_token
    base = provider.BASE
    items = {"value": [
        {"id": "sm-1", "type": "SemanticModel", "displayName": "A"},
        {"id": "sm-2", "type": "SemanticModel", "displayName": "B"},
    ]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "My workspace"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY])

    assert all(i.last_run_utc is None for i in ctx.items)
    assert Resource.ITEM_RUN_HISTORY in ctx.unavailable
    assert ctx.read_failures == {}


def test_fetch_run_history_all_forbidden_marks_resource_unavailable():
    """When every runnable item's history is 403, the resource is N/A, not stale."""
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "nb-1", "type": "Notebook", "displayName": "N"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Prep"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
        f"{base}/workspaces/ws-1/items/nb-1/jobs/instances": _FakeResponse(403, {}),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY])

    assert ctx.items[0].last_run_utc is None
    assert Resource.ITEM_RUN_HISTORY in ctx.unavailable
    # A forbidden run-history read must not block caching via read_failures.
    assert ctx.read_failures == {}


def test_fetch_retains_every_run_stamp_for_the_observed_cadence():
    """The cadence signal comes from the *same* jobs/instances page, not a new call.

    ``Item.last_run_utc`` keeps only the newest run, which cannot express an
    interval. The page already contains the rest, so retaining it is a parsing
    change with no extra HTTP cost - which is what makes ref 10.4.2 answerable.
    """
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [
        {"id": "pl-1", "type": "DataPipeline", "displayName": "PL_Monitoring_Refresh"},
    ]}
    jobs = {"value": [
        {"status": "Completed", "startTimeUtc": "2026-03-02T07:00:00Z",
         "endTimeUtc": "2026-03-02T07:05:00Z"},
        {"status": "Completed", "startTimeUtc": "2026-03-02T09:00:00Z",
         "endTimeUtc": "2026-03-02T09:05:00Z"},
        {"status": "Failed", "startTimeUtc": "2026-03-02T08:00:00Z"},  # still running/failed
    ]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Logs"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
        f"{base}/workspaces/ws-1/items/pl-1/jobs/instances": _FakeResponse(200, jobs),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY])

    # Newest first, and a run with no end time contributes its start time.
    assert ctx.run_history == {"pl-1": [
        "2026-03-02T09:05:00Z", "2026-03-02T08:00:00Z", "2026-03-02T07:05:00Z",
    ]}
    assert ctx.items[0].last_run_utc == "2026-03-02T09:05:00Z"
    # One call, not two: recency and cadence come from the same page.
    assert session.calls.count(f"{base}/workspaces/ws-1/items/pl-1/jobs/instances") == 1
    restored = type(ctx).from_dict(ctx.to_dict())
    assert restored.run_history == ctx.run_history


def test_fetch_reads_warehouse_sql_audit_configuration():
    """The audit *setting* is plain delegated Fabric REST - no tenant-admin, no rows."""
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "wh-1", "type": "Warehouse", "displayName": "Finance"}]}
    setting = {
        "state": "Enabled",
        "auditActionsAndGroups": ["BATCH_COMPLETED_GROUP", "SCHEMA_OBJECT_CHANGE_GROUP"],
        "retentionDays": 90,
    }
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Logs"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
        f"{base}/workspaces/ws-1/warehouses/wh-1/settings/sqlAudit": _FakeResponse(200, setting),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.WAREHOUSE_AUDIT])

    assert ctx.warehouse_audit == {"Finance": {
        "state": "Enabled",
        "enabled": True,
        "action_groups": ["BATCH_COMPLETED_GROUP", "SCHEMA_OBJECT_CHANGE_GROUP"],
        "retention_days": 90,
    }}
    assert Resource.WAREHOUSE_AUDIT not in ctx.unavailable
    restored = type(ctx).from_dict(ctx.to_dict())
    assert restored.warehouse_audit == ctx.warehouse_audit


def test_fetch_warehouse_audit_forbidden_is_unavailable_not_disabled():
    """Without the Audit permission the setting is unknown - which must read as N/A.

    Recording a 403 as ``enabled=False`` would fail a Warehouse that is in fact
    audited, purely because the crawl identity could not ask.
    """
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "wh-1", "type": "Warehouse", "displayName": "Finance"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Logs"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
        f"{base}/workspaces/ws-1/warehouses/wh-1/settings/sqlAudit": _FakeResponse(403, {}),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.WAREHOUSE_AUDIT])

    assert ctx.warehouse_audit == {}
    assert Resource.WAREHOUSE_AUDIT in ctx.unavailable


def test_sql_audit_settings_normalises_the_payload_spellings():
    """Fabric has spelled this payload more than one way; checks must see one shape."""
    provider = LiveFabricProvider("token")
    nested = provider._sql_audit_settings({"properties": {
        "auditState": "disabled",
        "auditActionGroups": [" batch_completed_group "],
        "retentionInDays": 30,
    }})
    assert nested == {
        "state": "disabled",
        "enabled": False,
        "action_groups": ["batch_completed_group"],
        "retention_days": 30,
    }
    bare = provider._sql_audit_settings({})
    assert bare == {"state": "", "enabled": False, "action_groups": [], "retention_days": None}


def test_powerbi_dataset_last_refresh_falls_back_to_personal_workspace():
    """My-workspace models 404 on the group form, so the no-group form is used."""
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    history = {"value": [
        {"status": "Completed", "startTime": "2024-04-20T18:11:00Z",
         "endTime": "2024-04-20T18:11:44Z"},
    ]}
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshes?$top=1": _FakeResponse(404, {}),
        f"{base}/datasets/d1/refreshes?$top=1": _FakeResponse(200, history),
    })
    client._session = session

    assert client.dataset_last_refresh("d1", group_id="ws") == ("2024-04-20T18:11:44Z", "")


def test_powerbi_dataset_last_refresh_401_is_forbidden_not_never_refreshed():
    """A rejected token must classify as 'forbidden' (N/A), not '200-empty' (never ran)."""
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshes?$top=1": _FakeResponse(401, {}),
        f"{base}/datasets/d1/refreshes?$top=1": _FakeResponse(401, {}),
    })
    client._session = session

    assert client.dataset_last_refresh("d1", group_id="ws") == (None, "forbidden")


def test_powerbi_dataset_refresh_schedule_reads_notify_option():
    """notifyOption is an ordinary delegated Datasets read - no tenant-admin scope."""
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    payload = {
        "days": ["Monday", "Tuesday"],
        "times": ["06:00", "18:00"],
        "enabled": True,
        "localTimeZoneId": "UTC",
        "notifyOption": "MailOnFailure",
    }
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshSchedule": _FakeResponse(200, payload),
    })
    client._session = session

    schedule, failure = client.dataset_refresh_schedule("d1", group_id="ws")
    assert failure == ""
    assert schedule == {
        "enabled": True,
        "notify_option": "MailOnFailure",
        "notifies_on_failure": True,
        "days": ["Monday", "Tuesday"],
        "times": ["06:00", "18:00"],
        "local_time_zone_id": "UTC",
    }


def test_powerbi_refresh_schedule_no_notification_is_read_not_failed():
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshSchedule":
            _FakeResponse(200, {"enabled": True, "notifyOption": "NoNotification"}),
    })
    client._session = session

    schedule, failure = client.dataset_refresh_schedule("d1", group_id="ws")
    assert failure == ""
    assert schedule["notifies_on_failure"] is False


def test_powerbi_refresh_schedule_404_means_no_schedule_not_a_read_failure():
    """A Direct Lake / push model has no schedule; that is an answer, not an error."""
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshSchedule": _FakeResponse(404, {}),
    })
    client._session = session

    assert client.dataset_refresh_schedule("d1", group_id="ws") == (None, "")


def test_powerbi_refresh_schedule_401_is_forbidden_not_no_schedule():
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    session = _FakeSession({
        f"{base}/groups/ws/datasets/d1/refreshSchedule": _FakeResponse(401, {}),
        f"{base}/datasets/d1/refreshSchedule": _FakeResponse(401, {}),
    })
    client._session = session

    assert client.dataset_refresh_schedule("d1", group_id="ws") == (None, "forbidden")


def test_powerbi_refresh_schedule_retries_throttling(monkeypatch):
    from auditfast.clients import powerbi

    client = powerbi.PowerBIClient("pbi")
    session = _SequenceGetSession([
        _FakeResponse(429, {}),
        _FakeResponse(503, {}),
        _FakeResponse(200, {"enabled": True, "notifyOption": "MailOnFailure"}),
    ])
    client._session = session
    monkeypatch.setattr(powerbi.time, "sleep", lambda _seconds: None)

    schedule, failure = client.dataset_refresh_schedule("d1", group_id="ws")

    assert failure == ""
    assert schedule["notifies_on_failure"] is True
    assert len(session.calls) == 3


def test_fetch_reads_refresh_schedules_and_round_trips_them():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "sm-1", "type": "SemanticModel", "displayName": "Sales"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Reporting"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._session = session
    provider._powerbi_client = _FakeRefreshSchedulePowerBI({
        "sm-1": ({"enabled": True, "notify_option": "MailOnFailure",
                  "notifies_on_failure": True, "days": [], "times": [],
                  "local_time_zone_id": ""}, ""),
    })

    from auditfast.core.enums import Resource

    ctx = provider.fetch(
        "ws-1",
        resources=[Resource.ITEMS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE],
    )

    assert ctx.refresh_schedules["Sales"]["notifies_on_failure"] is True
    assert Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE not in ctx.unavailable
    restored = type(ctx).from_dict(ctx.to_dict())
    assert restored.refresh_schedules == ctx.refresh_schedules


def test_fetch_refresh_schedule_without_powerbi_token_is_unavailable_not_silent():
    """No Power BI-audience token means unknown; recording it as 'no alert' would be a lie."""
    provider = LiveFabricProvider("token")  # no powerbi_token
    base = provider.BASE
    items = {"value": [{"id": "sm-1", "type": "SemanticModel", "displayName": "Sales"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Reporting"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._session = session

    from auditfast.core.enums import Resource

    ctx = provider.fetch(
        "ws-1",
        resources=[Resource.ITEMS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE],
    )

    assert ctx.refresh_schedules == {}
    assert Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE in ctx.unavailable


def test_fetch_model_with_no_schedule_is_read_not_unavailable():
    """A model that genuinely has no schedule read cleanly - the resource stays available."""
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "sm-1", "type": "SemanticModel", "displayName": "Sales"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Reporting"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._session = session
    provider._powerbi_client = _FakeRefreshSchedulePowerBI({"sm-1": (None, "")})

    from auditfast.core.enums import Resource

    ctx = provider.fetch(
        "ws-1",
        resources=[Resource.ITEMS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE],
    )

    assert ctx.refresh_schedules == {}
    assert Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE not in ctx.unavailable


def test_fetch_refresh_schedule_failure_records_model_identity():
    provider = LiveFabricProvider("token")
    base = provider.BASE
    items = {"value": [{"id": "sm-1", "type": "SemanticModel", "displayName": "Sales"}]}
    provider._session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Reporting"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._powerbi_client = _FakeRefreshSchedulePowerBI({"sm-1": (None, "forbidden")})

    from auditfast.core.enums import Resource

    ctx = provider.fetch(
        "ws-1",
        resources=[Resource.ITEMS, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE],
    )

    artifacts = ctx.read_failures[Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE.value]["artifacts"]
    assert artifacts == [{
        "id": "sm-1",
        "name": "Sales",
        "failure": "forbidden",
        "reason": "Power BI rejected the token (HTTP 401/403) - the sign-in lacks "
                  "Dataset.Read.All, or the tenant setting for it is off",
    }]


def test_fetch_sets_created_date_even_when_refresh_history_empty():
    """The real-world case: no refresh history, but createdDate is still available."""
    provider = LiveFabricProvider("token", powerbi_token="pbi")
    base = provider.BASE
    items = {"value": [{"id": "sm-1", "type": "SemanticModel", "displayName": "M"}]}
    session = _FakeSession({
        f"{base}/workspaces/ws-1": _FakeResponse(200, {"displayName": "Workshop"}),
        f"{base}/workspaces/ws-1/items": _FakeResponse(200, items),
    })
    provider._session = session
    provider._powerbi_client = _FakePowerBI(
        {},  # dataset_last_refresh -> (None, "") : never refreshed
        created={"sm-1": "2023-07-10T09:11:22Z"},
    )

    from auditfast.core.enums import Resource

    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY])

    assert ctx.items[0].last_run_utc is None                     # genuinely never refreshed
    assert ctx.items[0].created_date == "2023-07-10T09:11:22Z"   # but createdDate is known


def test_powerbi_dataset_created_dates_falls_back_to_personal_workspace():
    """My-workspace models 401 on the group datasets form, so /datasets is used."""
    from auditfast.clients.powerbi import PowerBIClient

    client = PowerBIClient("pbi")
    base = client.BASE
    session = _FakeSession({
        f"{base}/groups/ws/datasets": _FakeResponse(401, {}),
        f"{base}/datasets": _FakeResponse(200, {"value": [
            {"id": "d1", "createdDate": "2024-04-20T18:11:44Z"},
            {"id": "d2"},  # no createdDate -> excluded
        ]}),
    })
    client._session = session

    assert client.dataset_created_dates(group_id="ws") == {"d1": "2024-04-20T18:11:44Z"}


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
    import base64
    import json
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


def test_definition_parts_retries_on_429_then_succeeds(monkeypatch):
    """A throttled getDefinition (429) is retried and can then succeed."""
    import auditfast.clients.live as live_mod
    monkeypatch.setattr(live_mod.time, "sleep", lambda *_a, **_k: None)

    provider = LiveFabricProvider("token")
    session = _CountingFakeSession()
    session.queue_post(_FakeResponse(429, {}))
    import base64
    import json
    payload = base64.b64encode(json.dumps({"activities": []}).encode()).decode()
    session.queue_post(_FakeResponse(200, {
        "definition": {"parts": [{"path": "pipeline-content.json", "payload": payload}]}
    }))
    provider._session = session
    provider._session.headers = {}

    parts, failure = provider._definition_parts("ws-1", "item-1")

    assert failure == ""
    assert len(session.post_calls) == 2  # original + retry


def test_workspace_read_retries_on_429_not_dropped(monkeypatch):
    """A workspace throttled with 429 is retried instead of being skipped."""
    import auditfast.clients.live as live_mod
    monkeypatch.setattr(live_mod.time, "sleep", lambda *_a, **_k: None)

    provider = LiveFabricProvider("token")
    base = provider.BASE
    session = _CountingFakeSession()
    ws_url = f"{base}/workspaces/ws-1"
    session.queue_get(ws_url, _FakeResponse(429, {}))
    session.queue_get(ws_url, _FakeResponse(200, {"displayName": "MyWS"}))
    session.queue_get(f"{base}/workspaces/ws-1/items", _FakeResponse(200, {"value": []}))
    provider._session = session
    provider._session.headers = {}

    from auditfast.core.enums import Resource
    ctx = provider.fetch("ws-1", resources=[Resource.ITEMS])

    assert ctx.display_name == "MyWS"  # not dropped

