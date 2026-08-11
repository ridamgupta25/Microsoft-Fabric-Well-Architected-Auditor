"""Unit tests for the OneLake Table (Unity-Catalog) column reader and its wiring.

No network: a fake ``get`` callable routes the schemas / tables / table-schema
URLs to canned responses, mirroring the OneLake Table API shape.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from auditfast.clients.onelake import OneLakeTableReader, _parse_columns


class _FakeOneLake:
    """Routes OneLake Table API URLs to canned responses."""

    def __init__(self, schemas, tables_by_schema, columns_by_table,
                 schemas_status=200):
        self.schemas = schemas
        self.tables_by_schema = tables_by_schema
        self.columns_by_table = columns_by_table
        self.schemas_status = schemas_status
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        parsed = urlparse(url)
        path, query = parsed.path, parse_qs(parsed.query)
        if path.endswith("/schemas"):
            if self.schemas_status != 200:
                return self.schemas_status, None
            return 200, {"schemas": [{"name": s} for s in self.schemas]}
        if path.endswith("/tables"):
            schema = (query.get("schema_name") or [""])[0]
            rows = self.tables_by_schema.get(schema, [])
            return 200, {"tables": [{"name": t} for t in rows], "next_page_token": None}
        if "/tables/" in path:
            table = unquote(path.rsplit("/tables/", 1)[1]).split(".")[-1]
            cols = self.columns_by_table.get(table)
            return (200, {"columns": cols}) if cols is not None else (404, None)
        return 404, None


# -- _parse_columns ------------------------------------------------------------

def test_parse_columns_maps_unity_catalog_shape():
    body = {"columns": [
        {"name": "id", "type_text": "bigint", "nullable": False},
        {"name": "Create Date", "type_text": "timestamp"},
        {"name": "amount", "type_name": "DECIMAL", "type_text": "decimal(18,2)"},
        {"no": "name"},
    ]}
    cols = _parse_columns(body)
    assert [c["name"] for c in cols] == ["id", "Create Date", "amount"]
    assert cols[0] == {"name": "id", "type": "bigint",
                       "nullable": False, "source_kind": "Lakehouse"}
    assert cols[1]["type"] == "timestamp" and cols[1]["nullable"] is True
    assert cols[2]["type"] == "decimal(18,2)"


def test_parse_columns_handles_missing_or_bad_columns():
    assert _parse_columns({}) == []
    assert _parse_columns({"columns": "nope"}) == []


# -- OneLakeTableReader --------------------------------------------------------

def test_reader_reads_schemas_tables_and_columns():
    fake = _FakeOneLake(
        schemas=["dbo", "ADAGE"],
        tables_by_schema={"dbo": ["control_table"], "ADAGE": ["ACCT_TMLC"]},
        columns_by_table={
            "control_table": [{"name": "load_type", "type_text": "string"}],
            "ACCT_TMLC": [{"name": "GL_ACCT_KEY", "type_text": "string"}],
        },
    )
    out = OneLakeTableReader(fake.get).columns("ws", "lh", "LH_Bronze")
    assert set(out) == {"control_table", "ACCT_TMLC"}
    assert out["ACCT_TMLC"][0] == {"name": "GL_ACCT_KEY", "type": "string",
                                   "nullable": True, "source_kind": "Lakehouse"}


def test_reader_returns_none_when_schemas_unauthorised():
    reader = OneLakeTableReader(lambda url: (401, None))
    assert reader.columns("ws", "lh", "LH_Bronze") is None
    assert "access" in reader.failures["LH_Bronze"]


def test_reader_400_schemas_falls_back_to_default_schema():
    fake = _FakeOneLake(
        schemas=[], tables_by_schema={"dbo": ["t1"]},
        columns_by_table={"t1": [{"name": "c", "type_text": "int"}]},
        schemas_status=400,
    )
    out = OneLakeTableReader(fake.get).columns("ws", "lh", "LH_Bronze")
    assert set(out) == {"t1"}
    assert out["t1"][0]["type"] == "int"


# -- provider wiring -----------------------------------------------------------

def test_provider_read_onelake_columns_populates_tables():
    from auditfast.clients.live import LiveFabricProvider, _any_table_columns
    from auditfast.core.models import Item, WorkspaceContext

    fake = _FakeOneLake(
        schemas=["dbo"],
        tables_by_schema={"dbo": ["control_table"]},
        columns_by_table={"control_table": [{"name": "load_type", "type_text": "string"}]},
    )
    provider = LiveFabricProvider("tok", onelake_token="tok")
    provider._get_onelake = fake.get
    ctx = WorkspaceContext(id="w")
    ctx.items.append(Item(id="lh1", type="Lakehouse", display_name="LH_Bronze"))

    assert _any_table_columns(ctx) is False
    provider._read_onelake_columns(ctx, "w")

    assert ctx.tables["control_table"]["columns"][0]["name"] == "load_type"
    assert ctx.tables["control_table"]["store"] == "LH_Bronze"
    assert _any_table_columns(ctx) is True


def test_provider_read_onelake_columns_noop_without_lakehouses():
    from auditfast.clients.live import LiveFabricProvider
    from auditfast.core.models import WorkspaceContext

    provider = LiveFabricProvider("tok", onelake_token="tok")
    provider._get_onelake = lambda url: (401, None)
    ctx = WorkspaceContext(id="w")
    provider._read_onelake_columns(ctx, "w")
    assert ctx.tables == {}
    assert not ctx.unavailable


def test_provider_read_onelake_columns_noop_without_token():
    from auditfast.clients.live import LiveFabricProvider
    from auditfast.core.models import Item, WorkspaceContext

    provider = LiveFabricProvider("tok")  # no OneLake Storage token
    ctx = WorkspaceContext(id="w")
    ctx.items.append(Item(id="lh1", type="Lakehouse", display_name="LH_Bronze"))
    provider._read_onelake_columns(ctx, "w")
    assert ctx.tables == {}
