from __future__ import annotations

from auditfast.clients.onelake import OneLakeClient


class _Response:
    def __init__(self, status_code: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, responses: list[_Response]):
        self.responses = responses
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)


def _client(responses: list[_Response]) -> OneLakeClient:
    client = OneLakeClient("token", max_entries=10)
    client._session = _Session(responses)
    return client


def test_onelake_summary_aggregates_without_persisting_file_paths():
    client = _client([_Response(200, {"paths": [
        {"name": "lh1/Files/sap/2026/08/11/part-1.parquet", "contentLength": str(200 * 1024 * 1024)},
        {"name": "lh1/Files/oracle/year=2026/month=08/day=11/part-2.parquet",
         "contentLength": str(2 * 1024 * 1024 * 1024)},
        {"name": "lh1/Files/_delta_log/000.json", "contentLength": "100"},
        {"name": "lh1/Files/sap/empty.marker", "contentLength": "0"},
        {"name": "lh1/Files/sap", "isDirectory": "true"},
    ]})])

    summary, failure = client.lakehouse_files_summary("ws", "lh1")

    assert failure == ""
    assert summary["file_count"] == 4
    assert summary["data_file_count"] == 2
    assert summary["excluded_file_count"] == 2
    assert summary["size_buckets"]["128mb_1gb"] == 1
    assert summary["size_buckets"]["over_1gb"] == 1
    assert summary["top_level_folders"] == ["oracle", "sap"]
    assert summary["dated_path_count"] == 2
    assert "_top_level" not in summary
    assert "part-1.parquet" not in repr(summary)


def test_onelake_summary_caps_large_listings():
    client = OneLakeClient("token", max_entries=2)
    client._session = _Session([_Response(200, {"paths": [
        {"name": "lh1/Files/src/2026-08-11/a.parquet", "contentLength": str(200 * 1024 * 1024)},
        {"name": "lh1/Files/src/2026-08-11/b.parquet", "contentLength": str(200 * 1024 * 1024)},
        {"name": "lh1/Files/src/2026-08-11/c.parquet", "contentLength": str(200 * 1024 * 1024)},
    ]})])

    summary, failure = client.lakehouse_files_summary("ws", "lh1")

    assert failure == ""
    assert summary["file_count"] == 2
    assert summary["truncated"] is True
    assert summary["sampled"] is True


def test_onelake_summary_classifies_unreadable_listing():
    forbidden = _client([_Response(403)])
    assert forbidden.lakehouse_files_summary("ws", "lh1") == ({}, "forbidden")

    transient = _client([_Response(503)])
    assert transient.lakehouse_files_summary("ws", "lh1") == ({}, "transient")
