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
        # Storage/refresh/aggregation metadata degrades to empty on the same terms.
        "storage": {}, "refresh_policies": [], "aggregations": [],
        "direct_lake_behavior": "",
    }
    # A bare model object (no "model" envelope) still parses.
    assert parse_tmsl({"tables": [{"name": "T", "measures": []}]})["tables"] == ["T"]
    # Non-dict input degrades to empty rather than raising.
    assert parse_tmsl("nope")["measures"] == []


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
