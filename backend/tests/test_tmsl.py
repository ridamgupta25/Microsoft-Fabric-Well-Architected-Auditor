"""Tests for the TMSL semantic-model parser."""
from __future__ import annotations

from auditfast.clients.tmsl import parse_tmsl

_TMSL = {
    "name": "Sales",
    "model": {
        "tables": [
            {"name": "Sales", "measures": [
                {"name": "Total", "expression": "SUM(Sales[Amount])", "description": "Sum"},
                {"name": "Count", "expression": ["COUNTROWS(", "Sales)"]},
            ]},
            {"name": "Date", "measures": []},
        ],
        "relationships": [
            {"name": "r1", "fromTable": "Sales", "fromColumn": "DateKey",
             "toTable": "Date", "toColumn": "DateKey",
             "crossFilteringBehavior": "oneDirection"},
        ],
    },
}


def test_parse_tmsl_extracts_measures_and_relationships():
    parsed = parse_tmsl(_TMSL)
    assert parsed["tables"] == ["Sales", "Date"]
    assert len(parsed["measures"]) == 2

    total = next(m for m in parsed["measures"] if m["name"] == "Total")
    assert total["table"] == "Sales"
    assert total["expression"] == "SUM(Sales[Amount])"
    assert total["description"] == "Sum"

    # An array-valued expression is joined into a single DAX string.
    count = next(m for m in parsed["measures"] if m["name"] == "Count")
    assert count["expression"] == "COUNTROWS(\nSales)"

    assert len(parsed["relationships"]) == 1
    rel = parsed["relationships"][0]
    assert rel["from_table"] == "Sales"
    assert rel["to_table"] == "Date"
    assert rel["cross_filter"] == "oneDirection"


def test_parse_tmsl_tolerates_bare_model_and_garbage():
    assert parse_tmsl({}) == {
        "tables": [], "measures": [], "relationships": [], "roles": [],
        # Storage/refresh/aggregation/column metadata degrades to empty on the same terms.
        "storage": {}, "refresh_policies": [], "aggregations": [],
        "columns": [],
        "direct_lake_behavior": "",
    }
    # A bare model object (no "model" envelope) still parses.
    assert parse_tmsl({"tables": [{"name": "T", "measures": []}]})["tables"] == ["T"]
    # Non-dict input degrades to empty rather than raising.
    assert parse_tmsl("nope")["measures"] == []


def test_parse_tmsl_captures_column_declarations_but_no_row_data():
    """Column *shape* (name + declared types + flags) is metadata; rows are not read."""
    document = {
        "model": {
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "RowGuid", "dataType": "string",
                         "sourceProviderType": "uniqueidentifier"},
                        {"name": "Amount", "dataType": "decimal", "isHidden": True},
                        {"name": "OrderID", "dataType": "int64", "isKey": True,
                         "sourceColumn": "order_id"},
                        "not-a-dict",
                        {"dataType": "string"},  # nameless columns are dropped
                    ],
                },
                {"name": "Date", "columns": [{"name": "Date", "dataType": "dateTime"}]},
            ],
        },
    }
    columns = parse_tmsl(document)["columns"]
    assert [(c["table"], c["name"]) for c in columns] == [
        ("Sales", "RowGuid"), ("Sales", "Amount"), ("Sales", "OrderID"), ("Date", "Date"),
    ]

    guid = columns[0]
    assert guid["data_type"] == "string"
    # The GUID only survives in the source provider type — TMSL has no such type.
    assert guid["source_provider_type"] == "uniqueidentifier"
    assert guid["is_hidden"] is False and guid["is_key"] is False

    assert columns[1]["is_hidden"] is True
    assert columns[2]["is_key"] is True and columns[2]["source_column"] == "order_id"

    # Nothing resembling a value, a count, or a statistic is captured.
    for column in columns:
        assert set(column) == {
            "table", "name", "data_type", "source_provider_type",
            "source_column", "is_hidden", "is_key",
        }


def test_parse_tmsl_columns_skip_auto_date_time_tables():
    document = {
        "model": {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Amount", "dataType": "decimal"}]},
                {"name": "LocalDateTable_14b47659-1b52-4756-8531-72500f6aa194",
                 "columns": [{"name": "Date", "dataType": "dateTime"}]},
            ],
        },
    }
    assert [c["table"] for c in parse_tmsl(document)["columns"]] == ["Sales"]


def test_parse_tmsl_tolerates_a_table_with_no_columns():
    assert parse_tmsl({"tables": [{"name": "T", "measures": []}]})["columns"] == []


def test_parse_tmsl_excludes_auto_date_time_tables():
    """Power BI's hidden Auto date/time tables (and their relationships) are dropped."""
    document = {
        "model": {
            "tables": [
                {"name": "Sales", "measures": []},
                {"name": "DateTableTemplate_56159e93-f8d1-4812-b97e-96e380a002c3", "measures": []},
                {"name": "LocalDateTable_14b47659-1b52-4756-8531-72500f6aa194", "measures": []},
            ],
            "relationships": [
                {"name": "keep", "fromTable": "Sales", "fromColumn": "DateKey",
                 "toTable": "Date", "toColumn": "DateKey"},
                {"name": "drop", "fromTable": "Sales", "fromColumn": "Work Date",
                 "toTable": "LocalDateTable_14b47659-1b52-4756-8531-72500f6aa194", "toColumn": "Date"},
            ],
        },
    }
    parsed = parse_tmsl(document)

    # The two hidden system tables are gone; the real table remains.
    assert parsed["tables"] == ["Sales"]
    # Only the relationship between real tables survives.
    assert [r["name"] for r in parsed["relationships"]] == ["keep"]

    # A user table that merely resembles the prefix (no GUID suffix) is NOT filtered.
    kept = parse_tmsl({"tables": [{"name": "LocalDateTable_Fiscal", "measures": []}]})
    assert kept["tables"] == ["LocalDateTable_Fiscal"]


# =============================================================================
# Regression: storage / refresh_policies / aggregations were initialised and
# returned but NEVER populated - _table_storage() was defined and never called.
# Refs 14.2.1, 14.2.2, 14.2.4, 14.2.6 and 14.5.2 therefore read an empty
# structure and returned N/A on every audit: coverage on the catalog, none in
# the report. The bug was silent, so these tests assert the fields are FILLED.
# =============================================================================

_RICH_TMSL = {
    "name": "Enterprise",
    "model": {
        "tables": [
            {
                "name": "FactSales",
                "partitions": [
                    {"name": "p1", "mode": "import",
                     "source": {"type": "m", "expression": "let Source = Sql.Database(...)"}},
                ],
                "refreshPolicy": {
                    "policyType": "basic",
                    "rollingWindowGranularity": "year",
                    "rollingWindowPeriods": 3,
                    "incrementalGranularity": "day",
                    "incrementalPeriods": 10,
                },
                "columns": [{"name": "Amount"}],
            },
            {
                "name": "AggSales",
                "partitions": [{"name": "p1", "mode": "import", "source": {"type": "calculated"}}],
                "columns": [
                    {"name": "AmountSum", "alternateOf": {
                        "summarization": "sum",
                        "baseColumn": {"table": "FactSales", "column": "Amount"},
                    }},
                ],
            },
            {
                "name": "DirectLakeTable",
                "partitions": [{"name": "p1", "source": {"type": "entity"}}],
                "columns": [],
            },
        ],
    },
}


def test_storage_is_populated_per_table():
    model = parse_tmsl(_RICH_TMSL)
    storage = model["storage"]
    assert set(storage) == {"FactSales", "AggSales", "DirectLakeTable"}, \
        "every table must appear - the loop previously filled nothing"
    assert "import" in storage["FactSales"]["modes"]
    assert storage["FactSales"]["native_query_partitions"] == 1, \
        "an M partition carrying its own query text is an in-model transformation"


def test_direct_lake_mode_is_derived_from_the_source_type():
    """An entity partition carries no explicit mode; the source type implies it."""
    storage = parse_tmsl(_RICH_TMSL)["storage"]
    assert storage["DirectLakeTable"]["modes"] == ["directLake"]


def test_refresh_policies_are_collected():
    policies = parse_tmsl(_RICH_TMSL)["refresh_policies"]
    assert len(policies) == 1
    assert policies[0]["table"] == "FactSales"
    assert policies[0]["policy_type"] == "basic"
    assert policies[0]["incremental_granularity"] == "day"


def test_a_table_without_a_refresh_policy_contributes_nothing():
    policies = parse_tmsl(_RICH_TMSL)["refresh_policies"]
    assert {p["table"] for p in policies} == {"FactSales"}


def test_aggregation_columns_are_collected_with_their_base():
    aggs = parse_tmsl(_RICH_TMSL)["aggregations"]
    assert len(aggs) == 1
    assert aggs[0]["table"] == "AggSales"
    assert aggs[0]["base_table"] == "FactSales"
    assert aggs[0]["base_column"] == "Amount"
    assert aggs[0]["summarization"] == "sum"


def test_a_model_with_no_partitions_still_parses():
    """The original fixture has no partitions at all - it must not raise."""
    model = parse_tmsl(_TMSL)
    assert model["storage"] == {"Sales": {"modes": [], "source_types": [],
                                          "native_query_partitions": 0},
                                "Date": {"modes": [], "source_types": [],
                                         "native_query_partitions": 0}}
    assert model["refresh_policies"] == []
    assert model["aggregations"] == []
