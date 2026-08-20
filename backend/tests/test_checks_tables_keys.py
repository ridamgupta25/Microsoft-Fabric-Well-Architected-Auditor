"""4.4.4 / 4.4.5 - findings must name the objects they are about.

A reviewer's comment on both checks: *"Object name not captured"*. Each returned
a single workspace-level verdict carrying only a ratio - "43 of 80 dimension
table(s) include surrogate keys" - so the report's Object column was empty and
the affected tables were invisible. On an estate with 120 fact tables that is a
statistic, not a finding.

Both now follow the ``R-MODEL-HIDDEN-KEYS`` (14.1.8) pattern: a summary verdict
followed by one scored row per offending object, each carrying its name in
``obj``. 4.4.4 also separates its two failure modes, because they are different
problems with different fixes - no surrogate key at all, versus a surrogate key
with no natural key to match incoming rows by.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_storage.automated import (
    table_relationships_declared,
    table_surrogate_generated,
)
from auditfast.core.enums import Status

from .fixtures.builders import workspace_ctx


def _dim(*columns: str) -> dict:
    return {"type": "Managed", "format": "Delta", "store": "WH_Gold",
            "columns": [{"name": n, "type": "varchar(50)"} for n in columns]}


def _fact(*columns: str, references: list | None = None) -> dict:
    table = {"type": "Managed", "format": "Delta", "store": "WH_Gold",
             "columns": [{"name": n, "type": "bigint"} for n in columns]}
    if references:
        table["references"] = references
    return table


def _model(*relationships: tuple[str, str]) -> dict:
    return {"m": {
        "tables": [], "measures": [], "columns": [], "data_categories": {},
        "relationships": [
            {"name": f"r{i}", "from_table": ft, "from_column": "k",
             "to_table": tt, "to_column": "k", "cross_filter": "", "is_active": True}
            for i, (ft, tt) in enumerate(relationships)
        ],
    }}


def _rows(verdicts: list) -> dict[str, str]:
    """``{object name: evidence}`` for the per-object rows, ignoring the summary."""
    return {v.obj: v.evidence for v in verdicts if v.obj}


def test_per_object_rows_are_unscored():
    """They name the affected tables; they do not re-cast the summary's verdict.

    Scoring them let one checklist point dominate the roll-up on a large estate -
    121 scored rows out of ~375 from a single point - and penalised size rather
    than quality: two estates equally bad at surrogate keys scored very
    differently purely because one had more tables. The summary's ratio already
    carries the proportion.
    """
    verdicts = table_surrogate_generated(workspace_ctx(tables={
        f"dim_{i:02d}": _dim("some_id", "some_name") for i in range(40)
    }))
    summary, *rows = verdicts
    assert summary.scored is True
    assert all(not row.scored for row in rows)
    assert all(row.score is None for row in rows)
    assert len(rows) == 40


# ---------------------------------------------------------------------------
# 4.4.4 - a declared IDENTITY column outranks the naming guess
# ---------------------------------------------------------------------------

def test_a_declared_identity_column_is_reported_as_declared():
    """Fabric Warehouse IDENTITY is the documented surrogate-key mechanism.

    learn.microsoft.com/fabric/data-warehouse/identity: "IDENTITY columns enable
    automatic generation of these surrogate keys". Where the crawl reads one from
    sys.identity_columns the check states a fact, rather than inferring from a
    column name.
    """
    table = _dim("customer_sk", "customer_code", "customer_name")
    table["identity_columns"] = ["customer_sk"]
    verdicts = table_surrogate_generated(workspace_ctx(tables={"dim_customer": table}))
    assert verdicts[0].score == 3
    assert "declared IDENTITY column" in verdicts[0].evidence
    assert "dim_customer.customer_sk" in verdicts[0].evidence


def test_an_identity_column_satisfies_the_check_without_a_naming_hint():
    """The engine generates it, so no ``_sk`` suffix is needed to prove it."""
    table = _dim("id", "customer_code", "customer_name")
    table["identity_columns"] = ["id"]
    verdicts = table_surrogate_generated(workspace_ctx(tables={"dim_customer": table}))
    assert verdicts[0].score == 3
    assert not _rows(verdicts)


def test_the_evidence_says_when_it_rests_on_naming_alone():
    """A Lakehouse estate has no IDENTITY concept - the report must not imply more."""
    verdicts = table_surrogate_generated(workspace_ctx(
        tables={"dim_product": _dim("product_sk", "product_code")}))
    assert "rests on column naming" in verdicts[0].evidence
    assert "no flag marking a column as a surrogate key" in verdicts[0].evidence


# ---------------------------------------------------------------------------
# 4.4.4 - every offending dimension is its own named row
# ---------------------------------------------------------------------------

def test_a_dimension_with_no_surrogate_key_gets_its_own_row():
    verdicts = table_surrogate_generated(workspace_ctx(tables={
        "dim_customer": _dim("customer_id", "customer_name"),
        "dim_product": _dim("product_sk", "product_code", "product_name"),
    }))
    rows = _rows(verdicts)
    assert "dim_customer" in rows
    assert "No surrogate key column" in rows["dim_customer"]
    assert "SCD Type 2 is not possible" in rows["dim_customer"]
    assert "dim_product" not in rows


def test_a_dimension_with_no_natural_key_reads_differently():
    """A different problem from a missing surrogate key, with a different fix."""
    verdicts = table_surrogate_generated(workspace_ctx(tables={
        "dim_store": _dim("store_sk", "store_name", "city"),
    }))
    rows = _rows(verdicts)
    assert "dim_store" in rows
    assert "no natural/business key beside it" in rows["dim_store"]
    assert "No surrogate key column" not in rows["dim_store"]


def test_both_failure_modes_appear_as_separate_rows():
    verdicts = table_surrogate_generated(workspace_ctx(tables={
        "dim_customer": _dim("customer_id", "customer_name"),
        "dim_store": _dim("store_sk", "store_name"),
        "dim_product": _dim("product_sk", "product_code"),
    }))
    rows = _rows(verdicts)
    assert set(rows) == {"dim_customer", "dim_store"}
    assert "No surrogate key column" in rows["dim_customer"]
    assert "no natural/business key" in rows["dim_store"]


def test_every_offender_is_named_however_many_there_are():
    """Unlike a bounded evidence string, per-object rows are not truncated.

    A reviewer working through an estate needs every affected table, not the
    first five.
    """
    verdicts = table_surrogate_generated(workspace_ctx(tables={
        f"dim_{i:02d}": _dim("some_id", "some_name") for i in range(40)
    }))
    assert len(_rows(verdicts)) == 40


def test_a_fully_compliant_estate_emits_only_the_summary():
    verdicts = table_surrogate_generated(workspace_ctx(
        tables={"dim_product": _dim("product_sk", "product_code")}))
    assert len(verdicts) == 1
    assert verdicts[0].score == 3


def test_surrogate_check_is_na_without_dimensions():
    verdicts = table_surrogate_generated(workspace_ctx(tables={}))
    assert verdicts[0].status is Status.NA


# ---------------------------------------------------------------------------
# 4.4.5 - undeclared fact tables get their own rows
# ---------------------------------------------------------------------------

def test_a_fact_with_no_declared_relationship_gets_its_own_row():
    verdicts = table_relationships_declared(workspace_ctx(
        tables={"fact_sales": _fact("sales_key", "amount", "quantity"),
                "fact_returns": _fact("return_key", "amount", "quantity"),
                "dim_customer": _dim("customer_key", "customer_name")},
        semantic_models=_model(("fact_sales", "dim_customer")),
    ))
    rows = _rows(verdicts)
    assert "fact_returns" in rows
    assert "No declared relationship" in rows["fact_returns"]
    assert "fact_sales" not in rows


def test_a_warehouse_constraint_satisfies_the_point_outright():
    verdicts = table_relationships_declared(workspace_ctx(
        tables={"fact_sales": _fact("sales_key", "amount", "quantity",
                                    references=[{"column": "customer_key"}]),
                "dim_customer": _dim("customer_key", "customer_name")},
        semantic_models=_model(),
    ))
    assert "Warehouse FK constraint" in verdicts[0].evidence
    assert not _rows(verdicts)


def test_relationships_per_object_rows_are_unscored():
    """Same reasoning as 4.4.4: name the tables, do not re-cast the verdict."""
    verdicts = table_relationships_declared(workspace_ctx(
        tables={"fact_sales": _fact("sales_key", "amount", "quantity"),
                "fact_returns": _fact("return_key", "amount", "quantity"),
                "dim_customer": _dim("customer_key", "customer_name")},
        semantic_models=_model(("fact_sales", "dim_customer")),
    ))
    summary, *rows = verdicts
    assert summary.scored is True
    assert all(not row.scored for row in rows)


def test_relationships_check_is_na_without_facts():
    verdicts = table_relationships_declared(
        workspace_ctx(tables={"dim_customer": _dim("customer_key")}))
    assert verdicts[0].status is Status.NA


def test_relationships_check_is_na_when_nothing_is_readable():
    """No constraints and no models is unreadable, not a failing estate."""
    verdicts = table_relationships_declared(
        workspace_ctx(tables={"fact_sales": _fact("sales_key", "amount", "quantity")}))
    assert verdicts[0].status is Status.NA
    assert not _rows(verdicts)
