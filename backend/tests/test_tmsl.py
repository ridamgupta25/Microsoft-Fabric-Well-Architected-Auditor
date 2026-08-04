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
    assert parse_tmsl({}) == {"tables": [], "measures": [], "relationships": [], "roles": []}
    # A bare model object (no "model" envelope) still parses.
    assert parse_tmsl({"tables": [{"name": "T", "measures": []}]})["tables"] == ["T"]
    # Non-dict input degrades to empty rather than raising.
    assert parse_tmsl("nope")["measures"] == []
