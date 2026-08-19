"""The SQL catalog batch: one connection, many reads, bounded storage.

Fabric's SQL analytics endpoint exposes far more than column schemas, but a
connection is expensive here - each pays a login handshake plus the Azure SQL
gateway redirect, which is why crawl time tracks connection *count*. Reading
eight catalog views as eight queries would multiply the expensive part by eight;
sending one batch and walking ``nextset()`` pays it once.

These pin the two properties that matter:

* the batch is issued as a **single** statement to a **single** connection, and
  its result sets map back onto the right statements;
* what lands in the knowledge base is **bounded** - counts, names and an edge
  list, never row data - so a large estate cannot bloat a snapshot.
"""
from __future__ import annotations

import pytest

from auditfast.clients.sqlendpoint import (
    _METADATA_STATEMENTS,
    _WAREHOUSE_METADATA_STATEMENTS,
    SqlEndpoint,
    SqlEndpointReader,
    _collect_result_sets,
)


class _FakeCursor:
    """A cursor over a fixed list of result sets, like pyodbc's after a batch."""

    def __init__(self, result_sets: list[list[tuple]]):
        self._sets = list(result_sets)
        self._index = 0

    def fetchall(self):
        if self._index >= len(self._sets):
            raise RuntimeError("no results to fetch")
        return self._sets[self._index]

    def nextset(self):
        self._index += 1
        return self._index < len(self._sets)


def test_result_sets_are_collected_in_order():
    cursor = _FakeCursor([[(1, "a")], [(2, "b")], [(3, "c")]])
    assert _collect_result_sets(cursor, 3) == [[(1, "a")], [(2, "b")], [(3, "c")]]


def test_a_short_batch_is_padded_so_names_stay_aligned():
    """An endpoint that stops early must not shift every later statement's rows.

    A Lakehouse SQL endpoint answers fewer statements than a Warehouse. Without
    padding, the zip against the statement table would attribute one view's rows
    to another - silently wrong data rather than missing data.
    """
    cursor = _FakeCursor([[(1,)]])
    assert _collect_result_sets(cursor, 4) == [[(1,)], [], [], []]


def test_collection_stops_at_the_expected_count():
    """A stray extra result set must not shift the mapping either."""
    cursor = _FakeCursor([[(1,)], [(2,)], [(3,)], [(4,)]])
    assert _collect_result_sets(cursor, 2) == [[(1,)], [(2,)]]


def test_statement_names_are_unique_and_sql_is_read_only():
    """Every statement is a catalog read - the auditor must never write."""
    everything = _METADATA_STATEMENTS + _WAREHOUSE_METADATA_STATEMENTS
    names = [name for name, _sql in everything]
    assert len(names) == len(set(names)), f"duplicate statement name: {names}"

    forbidden = ("insert ", "update ", "delete ", "drop ", "alter ", "create ",
                 "truncate ", "merge ", "exec ")
    for name, sql in everything:
        lowered = " ".join(sql.lower().split())
        assert lowered.startswith("select"), f"{name} does not start with SELECT"
        for word in forbidden:
            assert word not in lowered, f"{name} contains '{word.strip()}'"


def test_only_documented_views_are_queried():
    """Fabric supports a subset of T-SQL; asking for the rest wastes a round trip.

    Verified against Microsoft's Fabric surface-area docs: these are documented
    as unavailable on Warehouse / SQL analytics endpoint, so querying them would
    fail every time and (in a batch) abort the statements after them.
    """
    unavailable = (
        "information_schema.table_constraints", "key_column_usage",
        "referential_constraints", "constraint_column_usage",
        "sys.indexes", "sys.index_columns", "sys.views",
        "sys.dm_db_partition_stats", "object_definition(",
    )
    for name, sql in _METADATA_STATEMENTS + _WAREHOUSE_METADATA_STATEMENTS:
        lowered = sql.lower()
        for view in unavailable:
            assert view not in lowered, f"{name} queries unsupported {view}"


def test_foreign_keys_are_warehouse_only():
    """A Lakehouse SQL endpoint does not expose FK metadata.

    Splitting the tables keeps the batch honest: asking anyway would fail on
    every Lakehouse endpoint and abort the statements queued behind it.
    """
    shared = " ".join(sql.lower() for _n, sql in _METADATA_STATEMENTS)
    assert "sys.foreign_keys" not in shared
    assert "sys.foreign_key_columns" not in shared

    warehouse = " ".join(sql.lower() for _n, sql in _WAREHOUSE_METADATA_STATEMENTS)
    assert "sys.foreign_keys" in warehouse
    assert "sys.foreign_key_columns" in warehouse


def test_row_counts_never_scan_rows():
    """Row counts come from partition metadata, never a COUNT(*) over user data."""
    row_counts = dict(_METADATA_STATEMENTS)["row_counts"].lower()
    assert "sys.partitions" in row_counts
    assert "count(*)" not in row_counts
    # index_id 0/1 is the heap or clustered row set; other ids recount the rows.
    assert "index_id in (0, 1)" in row_counts


def test_free_text_definitions_are_capped():
    """A single enormous view or procedure must not bloat the snapshot."""
    statements = dict(_METADATA_STATEMENTS)
    assert "left(view_definition, 4000)" in statements["views"].lower()
    assert "left(routine_definition, 8000)" in statements["routines"].lower()


@pytest.mark.parametrize("kind,expected_extra", [("Warehouse", True), ("Lakehouse", False)])
def test_metadata_batches_the_right_statements_per_kind(monkeypatch, kind, expected_extra):
    """One call, one batch - and Warehouse-only reads are added only there."""
    reader = SqlEndpointReader("token")
    endpoint = SqlEndpoint(kind, "store", "host.datawarehouse.fabric.microsoft.com")

    seen: list[tuple[str, int]] = []

    def fake_query(_endpoint, sql, *, multi=False, expected=1):
        assert multi is True, "the batch must be issued as one multi-set query"
        seen.append((sql, expected))
        return [[] for _ in range(expected)]

    monkeypatch.setattr(reader, "_query", fake_query)
    reader.metadata(endpoint)

    assert len(seen) == 1, "one connection per endpoint, not one per statement"
    sql, expected = seen[0]
    base = len(_METADATA_STATEMENTS)
    assert expected == base + (len(_WAREHOUSE_METADATA_STATEMENTS) if expected_extra else 0)
    assert ("sys.foreign_keys" in sql) is expected_extra


def test_metadata_returns_empty_when_the_connection_fails(monkeypatch):
    """Losing the extra metadata must never cost the column schemas."""
    reader = SqlEndpointReader("token")
    monkeypatch.setattr(reader, "_query", lambda *a, **k: None)
    assert reader.metadata(SqlEndpoint("Warehouse", "s", "h")) == {}
