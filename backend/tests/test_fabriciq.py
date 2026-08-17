"""Tests for the native, read-only FabricIQ Power BI tools.

All offline: :class:`_FakePBI` stands in for the Power BI REST client so the six
tools can be exercised without a tenant, and a fake ``requests`` session covers
the client's own transport helpers.
"""
from __future__ import annotations

import pytest

from auditfast.clients.powerbi import PowerBIClient, PowerBIError
from auditfast.services import fabriciq_service as fq

# -- a fake Power BI client ----------------------------------------------------

class _FakePBI:
    """Serves canned Power BI data and DAX results, recording executeQueries calls."""

    def __init__(self):
        self.groups = [
            {"id": "g1", "name": "Sales WS"},
            {"id": "g2", "name": "Ops WS"},
        ]
        self.datasets = {
            "g1": [{"id": "ds-sales", "name": "Sales Model", "webUrl": "http://x"}],
            "g2": [{"id": "ds-ops", "name": "Ops Model"}],
        }
        self.reports = {
            "g1": [{"id": "rpt-sales", "name": "Sales Report", "datasetId": "ds-sales",
                    "webUrl": "http://r", "embedUrl": "http://e", "reportType": "PowerBIReport"}],
            "g2": [],
        }
        self.pages = {"rpt-sales": [{"name": "ReportSection1", "displayName": "Overview", "order": 0}]}
        self.execute_calls: list[tuple] = []

    def list_groups(self):
        return self.groups

    def list_datasets(self, group_id):
        return self.datasets.get(group_id, [])

    def list_reports(self, group_id):
        return self.reports.get(group_id, [])

    def get_report(self, report_id, group_id=None):
        # /reports/{id} is My-workspace only; our canned reports all live in
        # groups, so a group-less lookup misses and forces find_report_group.
        if group_id is None:
            return None
        for report in self.reports.get(group_id, []):
            if report["id"] == report_id:
                return report
        return None

    def get_report_pages(self, report_id, group_id=None):
        return self.pages.get(report_id, [])

    def find_report_group(self, report_id):
        for gid, reports in self.reports.items():
            for report in reports:
                if report["id"] == report_id:
                    return gid, report
        return None, None

    def find_dataset_group(self, dataset_id):
        for gid, datasets in self.datasets.items():
            for dataset in datasets:
                if dataset["id"] == dataset_id:
                    return gid, dataset
        return None, None

    def execute_queries(self, dataset_id, dax_queries, group_id=None):
        self.execute_calls.append((dataset_id, list(dax_queries), group_id))
        dax = dax_queries[0]
        if "INFO.VIEW.TABLES" in dax:
            rows = [{"[Name]": "Sales", "[IsHidden]": False},
                    {"[Name]": "Customer", "[IsHidden]": False}]
        elif "INFO.VIEW.COLUMNS" in dax:
            rows = [
                {"[Table]": "Customer", "[Name]": "Customer Name", "[DataType]": "String", "[IsHidden]": False},
                {"[Table]": "Sales", "[Name]": "Amount", "[DataType]": "Decimal", "[IsHidden]": False},
            ]
        elif "INFO.VIEW.MEASURES" in dax:
            rows = [{"[Table]": "Sales", "[Name]": "Total Sales",
                     "[Expression]": "SUM(Sales[Amount])", "[DataType]": "Decimal"}]
        elif "INFO.VIEW.RELATIONSHIPS" in dax:
            rows = [{"[FromTable]": "Sales", "[FromColumn]": "CustomerId",
                     "[ToTable]": "Customer", "[ToColumn]": "Id", "[IsActive]": True}]
        elif "SEARCH(" in dax and "Customer Name" in dax:
            rows = [{"Customer[Customer Name]": "Contoso Ltd"}]
        elif "SEARCH(" in dax:
            rows = []
        else:
            rows = [{"[Value]": 1}]
        return {"results": [{"tables": [{"rows": rows}]}]}


# -- DiscoverArtifacts ---------------------------------------------------------

def test_discover_artifacts_prefers_reports_and_matches_by_name():
    result = fq.discover_artifacts("t", "sales", client=_FakePBI())
    assert result["count"] == 2
    # Report sorts before SemanticModel.
    assert result["artifacts"][0]["artifactType"] == "Report"
    assert result["artifacts"][0]["id"] == "rpt-sales"
    assert {a["artifactType"] for a in result["artifacts"]} == {"Report", "SemanticModel"}


def test_discover_artifacts_filters_by_type():
    result = fq.discover_artifacts("t", "sales", artifact_types=["SemanticModel"], client=_FakePBI())
    assert result["count"] == 1
    assert result["artifacts"][0]["artifactType"] == "SemanticModel"


def test_discover_artifacts_empty_query_returns_all():
    result = fq.discover_artifacts("t", "", client=_FakePBI())
    assert result["count"] == 3  # 2 datasets + 1 report


# -- ResolveReportIdFromUrl ----------------------------------------------------

def test_resolve_group_report_url():
    out = fq.resolve_report_id_from_url(
        "https://app.powerbi.com/groups/11111111-1111-1111-1111-111111111111/"
        "reports/22222222-2222-2222-2222-222222222222/ReportSection"
    )
    assert out["workspaceId"] == "11111111-1111-1111-1111-111111111111"
    assert out["reportId"] == "22222222-2222-2222-2222-222222222222"
    assert out["isAppInstance"] is False


def test_resolve_my_workspace_url_has_no_workspace():
    out = fq.resolve_report_id_from_url(
        "https://app.powerbi.com/reports/33333333-3333-3333-3333-333333333333"
    )
    assert out["workspaceId"] is None
    assert out["reportId"] == "33333333-3333-3333-3333-333333333333"


def test_resolve_app_instance_url_is_flagged():
    out = fq.resolve_report_id_from_url(
        "https://app.powerbi.com/groups/me/apps/44444444-4444-4444-4444-444444444444/"
        "reports/55555555-5555-5555-5555-555555555555/ReportSection2"
    )
    assert out["isAppInstance"] is True
    assert out["appId"] == "44444444-4444-4444-4444-444444444444"
    assert out["reportId"] == "55555555-5555-5555-5555-555555555555"
    assert "note" in out


def test_resolve_bad_url_reports_error():
    assert "error" in fq.resolve_report_id_from_url("https://example.com/not-a-report")
    assert "error" in fq.resolve_report_id_from_url("")


# -- GetReportMetadata ---------------------------------------------------------

def test_get_report_metadata_resolves_group_and_model():
    out = fq.get_report_metadata("t", "rpt-sales", client=_FakePBI())
    assert out["workspaceId"] == "g1"
    assert out["semanticModel"] == "ds-sales"
    assert out["pageCount"] == 1
    assert out["pages"][0]["displayName"] == "Overview"


def test_get_report_metadata_missing_report():
    assert "error" in fq.get_report_metadata("t", "nope", client=_FakePBI())


# -- GetSemanticModelSchema ----------------------------------------------------

def test_get_semantic_model_schema_assembles_from_info_views():
    out = fq.get_semantic_model_schema("t", "ds-sales", workspace_id="g1", client=_FakePBI())
    assert out["tableCount"] == 2
    assert out["columnCount"] == 2
    assert out["measureCount"] == 1
    customer = next(t for t in out["tables"] if t["name"] == "Customer")
    assert customer["columns"][0]["name"] == "Customer Name"
    sales = next(t for t in out["tables"] if t["name"] == "Sales")
    assert sales["measures"][0]["name"] == "Total Sales"
    assert out["relationships"][0]["fromTable"] == "Sales"
    assert out["relationships"][0]["toTable"] == "Customer"


def test_get_semantic_model_schema_auto_resolves_workspace():
    fake = _FakePBI()
    out = fq.get_semantic_model_schema("t", "ds-sales", client=fake)
    assert out["workspaceId"] == "g1"


# -- ValueSearch ---------------------------------------------------------------

def test_value_search_finds_canonical_value():
    out = fq.value_search("t", "ds-sales", ["Contoso"], workspace_id="g1", client=_FakePBI())
    assert out["matchCount"] == 1
    assert out["matches"][0] == {"table": "Customer", "column": "Customer Name", "value": "Contoso Ltd"}


def test_value_search_requires_terms():
    assert "error" in fq.value_search("t", "ds-sales", [], workspace_id="g1", client=_FakePBI())


# -- ExecuteQuery --------------------------------------------------------------

def test_execute_query_returns_rows_and_truncates():
    out = fq.execute_query("t", "ds-sales", ['EVALUATE ROW("x", 1)'],
                           max_rows=250, workspace_id="g1", client=_FakePBI())
    assert out["queryCount"] == 1
    assert out["results"][0]["rowCount"] == 1
    assert out["results"][0]["truncated"] is False


def test_execute_query_rejects_multiple_evaluate():
    out = fq.execute_query("t", "ds-sales", ["EVALUATE A EVALUATE B"],
                           workspace_id="g1", client=_FakePBI())
    assert "error" in out


def test_execute_query_rejects_too_many_queries():
    out = fq.execute_query("t", "ds-sales", ["EVALUATE A"] * 5,
                           workspace_id="g1", client=_FakePBI())
    assert "error" in out


def test_execute_query_requires_a_query():
    assert "error" in fq.execute_query("t", "ds-sales", [], workspace_id="g1", client=_FakePBI())


def test_execute_query_truncates_to_max_rows():
    class _Big(_FakePBI):
        def execute_queries(self, dataset_id, dax_queries, group_id=None):
            return {"results": [{"tables": [{"rows": [{"[V]": i} for i in range(10)]}]}]}

    out = fq.execute_query("t", "ds-sales", ["EVALUATE X"], max_rows=3,
                           workspace_id="g1", client=_Big())
    assert out["results"][0]["rowCount"] == 3
    assert out["results"][0]["truncated"] is True


# -- client transport ----------------------------------------------------------

class _FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, get_map=None, post_map=None):
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.headers: dict = {}

    def get(self, url, timeout=None):
        return self.get_map[url]

    def post(self, url, json=None, timeout=None):
        return self.post_map[url]


def _client_with(session) -> PowerBIClient:
    client = PowerBIClient("token")
    client._session = session
    return client


def test_client_values_reads_collection():
    base = PowerBIClient.BASE
    client = _client_with(_FakeSession(
        get_map={f"{base}/groups?$top=5000": _FakeResponse(200, {"value": [{"id": "g1"}]})}
    ))
    assert client.list_groups() == [{"id": "g1"}]


def test_list_reports_falls_back_to_root_for_personal_workspace():
    """"My workspace" is not a group; its reports come from the myorg root."""
    base = PowerBIClient.BASE
    personal = "personal-ws"
    client = _client_with(_FakeSession(get_map={
        f"{base}/groups/{personal}/reports": _FakeResponse(403, None),
        f"{base}/groups?$top=5000": _FakeResponse(200, {"value": [{"id": "g1"}]}),
        f"{base}/reports": _FakeResponse(200, {"value": [{"id": "r1", "datasetId": "ds-1"}]}),
    }))
    rows, readable = client.list_reports_known(personal)
    assert readable is True
    assert rows == [{"id": "r1", "datasetId": "ds-1"}]


def test_list_reports_does_not_fall_back_for_a_known_named_workspace():
    """A named workspace whose group listing fails stays unreadable, never swapped."""
    base = PowerBIClient.BASE
    client = _client_with(_FakeSession(get_map={
        f"{base}/groups/g1/reports": _FakeResponse(403, None),
        f"{base}/groups?$top=5000": _FakeResponse(200, {"value": [{"id": "g1"}]}),
    }))
    rows, readable = client.list_reports_known("g1")
    assert readable is False
    assert rows == []


def test_client_execute_queries_raises_on_error():
    base = PowerBIClient.BASE
    url = f"{base}/groups/g/datasets/d/executeQueries"
    client = _client_with(_FakeSession(
        post_map={url: _FakeResponse(401, {"error": {"code": "TokenExpired", "message": "bad"}})}
    ))
    with pytest.raises(PowerBIError) as exc:
        client.execute_queries("d", ["EVALUATE X"], "g")
    assert exc.value.status == 401
    assert exc.value.code == "TokenExpired"


def test_clean_key_strips_table_framing():
    assert fq._clean_key("'Sales'[Amount]") == "Amount"
    assert fq._clean_key("[Name]") == "Name"
    assert fq._clean_key("Plain") == "Plain"
