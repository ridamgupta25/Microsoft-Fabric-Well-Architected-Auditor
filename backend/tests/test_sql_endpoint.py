"""SQL analytics endpoint: discovery, degradation, and the Lakehouse width trap.

The endpoint is the only source of column schemas and Warehouse RLS, so two
properties matter more than any individual query:

* **It must never fail an audit.** No token, no ODBC driver, a blocked port 1433 -
  every one of them has to leave the data absent so the checks report N/A, exactly
  as they did before the endpoint existed.
* **A Lakehouse's forced ``varchar(8000)`` must not read as a finding.** Fabric maps
  every Delta ``string`` to that width whatever the author intended; judging it
  would fail every string column in every lakehouse.
"""
from __future__ import annotations

import pytest

from auditfast.clients.sqlendpoint import (
    SqlEndpoint,
    SqlEndpointReader,
    _classify,
    _render_type,
    discover_endpoints,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    _is_lakehouse_default_text,
    table_data_types,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext

# --- discovery ---------------------------------------------------------------

def _fake_get(payloads: dict):
    def get_json(path: str):
        for key, body in payloads.items():
            if path.endswith(key):
                return body
        return {}
    return get_json


def test_discovery_reads_both_lakehouses_and_warehouses():
    get_json = _fake_get({
        "/lakehouses": {"value": [{
            "displayName": "Bronze",
            "properties": {"sqlEndpointProperties": {
                "connectionString": "abc.datawarehouse.fabric.microsoft.com",
                "provisioningStatus": "Success"}},
        }]},
        "/warehouses": {"value": [{
            "displayName": "Mart",
            "properties": {"connectionString": "abc.datawarehouse.fabric.microsoft.com"},
        }]},
    })
    found = discover_endpoints(get_json, "ws")
    assert [(e.kind, e.name) for e in found] == [("Lakehouse", "Bronze"), ("Warehouse", "Mart")]


def test_unprovisioned_endpoints_are_skipped():
    """An endpoint still provisioning is not queryable, so it is not offered."""
    get_json = _fake_get({"/lakehouses": {"value": [{
        "displayName": "New",
        "properties": {"sqlEndpointProperties": {
            "connectionString": "abc", "provisioningStatus": "InProgress"}},
    }]}})
    assert discover_endpoints(get_json, "ws") == []


def test_discovery_failure_yields_no_endpoints_rather_than_raising():
    """No endpoints is 'no column data', which the checks already report as N/A."""
    def boom(path: str):
        raise RuntimeError("network down")
    assert discover_endpoints(boom, "ws") == []


@pytest.mark.parametrize("raw,expected,why", [
    ("abc.datawarehouse.fabric.microsoft.com", "abc.datawarehouse.fabric.microsoft.com",
     "a plain FQDN passes through"),
    ("abc.datawarehouse.fabric.microsoft.com,1433", "abc.datawarehouse.fabric.microsoft.com",
     "a port already in the connection string must not be doubled"),
    ("tcp:abc.datawarehouse.fabric.microsoft.com", "abc.datawarehouse.fabric.microsoft.com",
     "a tcp: prefix is not part of the host"),
    ("  abc.datawarehouse.fabric.microsoft.com  ", "abc.datawarehouse.fabric.microsoft.com",
     "surrounding whitespace is not part of the host"),
])
def test_endpoint_host_is_normalised(raw, expected, why):
    """``Server=host,1433,1433`` fails only as a timeout - indistinguishable from
    a blocked port, and therefore the worst possible way to get this wrong."""
    assert SqlEndpoint("Lakehouse", "db", raw).host == expected, why


# --- degradation -------------------------------------------------------------

def test_reader_without_a_token_is_unavailable_not_broken():
    reader = SqlEndpointReader(None)
    assert reader.available is False
    assert "token" in reader.unavailable_reason


def test_a_read_without_a_token_returns_none_and_records_why():
    """None means 'we could not look' - distinct from an empty result."""
    reader = SqlEndpointReader(None)
    assert reader.columns(SqlEndpoint("Lakehouse", "Bronze", "srv")) is None
    assert "Bronze" in reader.failures


@pytest.mark.parametrize("message,expected", [
    ("Couldn't complete the operation because we reached a system limit", "too many"),
    ("Login failed for user '<token-identified principal>'", "no access"),
    ("Login timeout expired", "port 1433"),
    ("TCP Provider: timed out", "port 1433"),
    ("network-related or instance-specific error", "port 1433"),
    ("Query timeout expired", "did not finish in time"),
])
def test_driver_failures_are_classified_into_actionable_reasons(message, expected):
    pyodbc = pytest.importorskip("pyodbc")
    assert expected in _classify(pyodbc.Error("HYT00", message))


def test_a_bug_in_the_reader_is_never_reported_as_a_blocked_port():
    """The exact defect this guards against.

    ``cursor.timeout = N`` raises AttributeError (``timeout`` belongs to the
    Connection, not the Cursor). Its message contains the word "timeout", so a
    keyword-matching classifier reported a *successful* connection as a blocked
    port - and sent the whole diagnosis in the wrong direction.
    """
    exc = AttributeError("'pyodbc.Cursor' object has no attribute 'timeout'")
    reason = _classify(exc)
    assert "internal error" in reason
    assert "1433" not in reason
    assert "AttributeError" in reason


# --- type rendering ----------------------------------------------------------

@pytest.mark.parametrize("parts,expected", [
    (("varchar", 8000, None, None), "varchar(8000)"),
    (("varchar", -1, None, None), "varchar(max)"),
    (("decimal", None, 18, 2), "decimal(18,2)"),
    (("int", None, 10, 0), "int"),
    (("datetime2", None, None, None), "datetime2"),
])
def test_render_type(parts, expected):
    assert _render_type(*parts) == expected


# --- the Lakehouse varchar(8000) trap ----------------------------------------

@pytest.mark.parametrize("kind,ctype,expected,why", [
    ("Lakehouse", "varchar(8000)", True,
     "a Lakehouse forces every Delta string to varchar(8000) - not a design choice"),
    ("Warehouse", "varchar(8000)", False,
     "a Warehouse author picked that width, so it is assessable"),
    ("Lakehouse", "varchar(4000)", False,
     "any other Lakehouse width had to be declared deliberately"),
    ("Lakehouse", "varchar(max)", False, "max is always a real choice"),
    ("Lakehouse", "int", False, "not a text type at all"),
])
def test_lakehouse_default_text_detector(kind, ctype, expected, why):
    assert _is_lakehouse_default_text({"source_kind": kind}, ctype) is expected, why


def _tables_ctx(tables: dict) -> CheckContext:
    ws = WorkspaceContext(id="w", tables=tables)
    return CheckContext(workspace=ws, settings={}, obj_name="w", obj=ws)


def _cols(*cols) -> dict:
    return {"type": "Managed", "format": "delta", "columns": list(cols)}


def test_a_lakehouse_of_default_widths_is_na_not_a_wall_of_failures():
    """The real shape of a crawled lakehouse: every string is varchar(8000)."""
    tables = {"badges": _cols(
        {"name": "Name", "type": "varchar(8000)", "source_kind": "Lakehouse"},
        {"name": "HEX", "type": "varchar(8000)", "source_kind": "Lakehouse"},
    )}
    verdict = table_data_types(_tables_ctx(tables))
    assert verdict.status is Status.NA
    assert "platform" in verdict.evidence or "cannot be judged" in verdict.evidence


def test_a_warehouse_oversized_column_is_still_a_finding():
    tables = {"dim": _cols(
        {"name": "Notes", "type": "varchar(8000)", "source_kind": "Warehouse"},
        {"name": "Code", "type": "varchar(50)", "source_kind": "Warehouse"},
    )}
    verdict = table_data_types(_tables_ctx(tables))
    assert verdict.score is not None          # scored, not skipped
    assert "1 of 2" in verdict.evidence


def test_a_stringly_typed_date_is_a_finding_on_either_surface():
    tables = {"fact": _cols(
        {"name": "order_date", "type": "varchar(8000)", "source_kind": "Lakehouse"},
    )}
    verdict = table_data_types(_tables_ctx(tables))
    assert verdict.score == 0
    assert "date column(s) typed as text" in verdict.evidence


def test_no_columns_at_all_is_na():
    """The pre-SQL-endpoint state: tables listed, columns never read."""
    verdict = table_data_types(_tables_ctx({"t": {"type": "Managed", "columns": []}}))
    assert verdict.status is Status.NA


# --- WorkspaceContext round trip ---------------------------------------------

def test_warehouse_security_survives_the_kb_cache():
    """Snapshots round-trip through to_dict/from_dict - a lost field is a silent bug."""
    ws = WorkspaceContext(id="w", warehouse_security={"Mart": [{"policy": "p", "enabled": True}]})
    restored = WorkspaceContext.from_dict(ws.to_dict())
    assert restored.warehouse_security == {"Mart": [{"policy": "p", "enabled": True}]}


def test_new_resources_are_distinct_from_table_schemas():
    """Separate resources so a run that reads no columns pays no SQL round trip."""
    assert Resource.TABLE_COLUMNS is not Resource.TABLE_SCHEMAS
    assert Resource.WAREHOUSE_SECURITY.value == "warehouseSecurity"
