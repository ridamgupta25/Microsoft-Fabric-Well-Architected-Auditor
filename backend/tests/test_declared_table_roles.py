"""Declared table roles from semantic-model relationships.

Microsoft's star-schema guidance states that no property marks a table as a fact
or a dimension - *"It's in fact determined by the model relationships"* - and
that in a one-to-many relationship *"the 'one' side is always a dimension table
while the 'many' side is always a fact table"*
(``learn.microsoft.com/power-bi/guidance/star-schema``).

The classifier previously ignored that entirely and inferred every role from
column shape, even on estates whose semantic model declared the answer outright.
On a real workspace that meant 30 roles assigned by guess and 0 by declaration,
while the relationships sat unread in the same snapshot.

These tests pin the new order of evidence, and - just as importantly - pin that
nothing changes for a workspace with no readable model, since most audited
workspaces will not have one.
"""
from __future__ import annotations

from auditfast.core.check._tables import (
    declared_roles,
    dimensions_in,
    facts_in,
    normalise_table_name,
    role_evidence,
    table_roles,
)


def _table(store: str = "LH_Gold", **cols: str) -> dict:
    return {
        "store": store,
        "columns": [{"name": name, "type": type_} for name, type_ in cols.items()],
    }


def _model(*relationships: tuple[str, str], categories: dict | None = None) -> dict:
    return {
        "relationships": [
            {"from_table": many, "from_column": "k", "to_table": one, "to_column": "k"}
            for many, one in relationships
        ],
        "data_categories": categories or {},
    }


# ---------------------------------------------------------------------------
# the core rule: many side = fact, one side = dimension
# ---------------------------------------------------------------------------

def test_relationship_declares_fact_and_dimension():
    tables = {"SALES": _table(), "CUSTOMER": _table()}
    models = {"m": _model(("SALES", "CUSTOMER"))}
    roles = table_roles(tables, models)
    assert roles["SALES"] == "fact"
    assert roles["CUSTOMER"] == "dimension"


def test_declaration_beats_column_shape():
    """The whole point: a stated role outranks an inferred one.

    ``LOOKUP`` is shaped like a fact (keys + measures) but the model says it is
    the 'one' side of a relationship, so it is a dimension.
    """
    tables = {
        "LOOKUP": _table(a_id="int", b_id="int", amount="decimal", qty="int"),
        "TXN": _table(lookup_id="int", amount="decimal"),
    }
    shape_only = table_roles(tables)
    declared = table_roles(tables, {"m": _model(("TXN", "LOOKUP"))})
    assert declared["LOOKUP"] == "dimension"
    assert declared["LOOKUP"] != shape_only.get("LOOKUP") or shape_only["LOOKUP"] == "dimension"


def test_a_table_on_both_sides_is_left_to_other_evidence():
    """A snowflake outrigger is genuinely both; guessing would be worse than not."""
    tables = {"SALES": _table(), "PRODUCT": _table(), "CATEGORY": _table()}
    models = {"m": _model(("SALES", "PRODUCT"), ("PRODUCT", "CATEGORY"))}
    declared = declared_roles(tables, models)
    assert normalise_table_name("PRODUCT") not in declared
    assert declared[normalise_table_name("SALES")] == "fact"
    assert declared[normalise_table_name("CATEGORY")] == "dimension"


# ---------------------------------------------------------------------------
# cross-source name matching
# ---------------------------------------------------------------------------

def test_model_and_sql_names_are_matched_across_separator_styles():
    """A model says 'Sales Order'; the SQL endpoint says 'dbo.sales_order'."""
    tables = {"dbo.sales_order": _table(), "dbo.customer": _table()}
    models = {"m": _model(("Sales Order", "Customer"))}
    roles = table_roles(tables, models)
    assert roles["dbo.sales_order"] == "fact"
    assert roles["dbo.customer"] == "dimension"


def test_a_relationship_naming_an_unknown_table_is_ignored():
    """A model can reference tables from another workspace - not ours to judge."""
    tables = {"SALES": _table()}
    models = {"m": _model(("SALES", "SOMEWHERE_ELSE"))}
    declared = declared_roles(tables, models)
    assert declared == {normalise_table_name("SALES"): "fact"}


# ---------------------------------------------------------------------------
# dataCategory - the weaker declarative signal
# ---------------------------------------------------------------------------

def test_data_category_declares_a_dimension():
    tables = {"CALENDAR": _table()}
    models = {"m": _model(categories={"CALENDAR": "Time"})}
    assert table_roles(tables, models)["CALENDAR"] == "dimension"


def test_relationship_wins_over_data_category():
    """Both are declarative; a relationship is the stronger statement."""
    tables = {"TXN": _table(), "CUST": _table()}
    models = {"m": _model(("TXN", "CUST"), categories={"TXN": "Customers"})}
    assert table_roles(tables, models)["TXN"] == "fact"


def test_unknown_data_category_is_not_a_role():
    tables = {"THING": _table()}
    models = {"m": _model(categories={"THING": "Unknown"})}
    assert not declared_roles(tables, models)


# ---------------------------------------------------------------------------
# backward compatibility - most workspaces have no readable model
# ---------------------------------------------------------------------------

def test_no_model_leaves_the_old_behaviour_untouched():
    tables = {
        "fact_sales": _table(a="int"),
        "dim_customer": _table(a="int"),
        "SOMETHING": _table(a="int"),
    }
    assert table_roles(tables, None) == table_roles(tables)
    assert table_roles(tables, {}) == table_roles(tables)


def test_empty_relationships_change_nothing():
    tables = {"fact_sales": _table(), "dim_customer": _table()}
    models = {"m": {"relationships": [], "data_categories": {}}}
    assert table_roles(tables, models) == table_roles(tables)


def test_helpers_still_work_with_the_old_single_argument_call():
    tables = {"fact_sales": _table(), "dim_customer": _table()}
    assert list(facts_in(tables)) == ["fact_sales"]
    assert list(dimensions_in(tables)) == ["dim_customer"]


def test_platform_tables_are_never_given_a_declared_role():
    """A model naming a platform view must not drag it into the star schema."""
    tables = {"queryinsights.long_running_queries": _table(), "SALES": _table()}
    models = {"m": _model(("SALES", "queryinsights.long_running_queries"))}
    assert table_roles(tables, models)["queryinsights.long_running_queries"] == "unknown"


# ---------------------------------------------------------------------------
# the evidence split - so a check can say how it knows
# ---------------------------------------------------------------------------

def test_role_evidence_separates_declared_from_inferred():
    tables = {
        "SALES": _table(),
        "CUSTOMER": _table(),
        "fact_legacy": _table(),
    }
    models = {"m": _model(("SALES", "CUSTOMER"))}
    evidence = role_evidence(tables, models)
    assert evidence["declared"] == 2
    assert evidence["inferred"] == 1          # fact_legacy, by name
    assert evidence["total"] == 3


def test_role_evidence_with_no_model_is_all_inferred():
    tables = {"fact_sales": _table(), "dim_customer": _table()}
    evidence = role_evidence(tables, None)
    assert evidence["declared"] == 0
    assert evidence["inferred"] == 2


# ---------------------------------------------------------------------------
# a raw store still suppresses inference, but not a declaration
# ---------------------------------------------------------------------------

def test_a_declared_role_survives_the_raw_store_guard():
    """The guard exists to stop *guessing* in a landing zone, not to ignore facts.

    If a modeller built a star schema over Bronze tables and declared the
    relationships, that is a statement of intent and outranks the guard.
    """
    tables = {
        "SALES": _table(store="LH_Bronze", a="int"),
        "CUSTOMER": _table(store="LH_Bronze", a="int"),
    }
    assert table_roles(tables)["SALES"] == "unknown"        # no declaration: guarded
    roles = table_roles(tables, {"m": _model(("SALES", "CUSTOMER"))})
    assert roles["SALES"] == "fact"
    assert roles["CUSTOMER"] == "dimension"
