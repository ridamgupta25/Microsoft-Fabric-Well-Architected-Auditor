"""Regression tests for 4.5.8 (TB-SCD-STRATEGY) - the false PASS.

A reviewer flagged the check as giving the wrong answer: it accepted **any
single** SCD marker as proof that a dimension declared a change-handling
strategy. A dimension carrying only ``is_current`` - no validity dates - scored
as declared, and an estate where every dimension looked like that scored a
full PASS.

That is the broken half-implementation the checklist point warns about. Kimball
Design Tip #107 names three metadata columns for Type 2, and the *validity
pair* is what makes row versioning work: a start date says when a version
began, an end date (or a version number) says when it was superseded. Microsoft's
own ADF/Synapse SCD-2 data flow generates StartDate/EndDate/IsActive; dbt
snapshots generate dbt_valid_from/dbt_valid_to; SQL Server temporal tables
generate SysStartTime/SysEndTime. A lone flag is none of those - ``is_active``
is as often a soft-delete marker.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_storage.automated import (
    scd_strategy_per_dimension,
)
from auditfast.core.enums import Status

from .fixtures.builders import tables_ctx


def _dim(*columns: str) -> dict:
    """A dimension-shaped table: a key, two descriptive attributes, plus extras.

    Extra columns are typed from their name, because the check now requires a
    validity column to carry a temporal type - typing everything ``varchar``
    made every SCD test look like a non-date column.
    """
    base = (("customer_key", "bigint"), ("customer_name", "varchar(100)"),
            ("city", "varchar(50)"))
    extra = tuple(
        (name, "varchar(20)" if any(w in name.lower() for w in
                                    ("current", "active", "latest", "hash", "version"))
         else "timestamp")
        for name in columns
    )
    return {
        "type": "Managed", "format": "Delta", "store": "WH_Gold",
        "columns": [{"name": name, "type": type_} for name, type_ in base + extra],
    }


# ---------------------------------------------------------------------------
# the defect: a lone flag is not a strategy
# ---------------------------------------------------------------------------

def test_a_lone_current_flag_does_not_score_a_pass():
    """The bug: is_current alone scored as a declared SCD strategy."""
    verdict = scd_strategy_per_dimension(tables_ctx(dim_customer=_dim("is_current")))
    assert verdict.score is not None and verdict.score < 3
    assert "partial marker set" in verdict.evidence


def test_a_lone_start_date_is_incomplete():
    """A start date with no end date may just be an insert timestamp."""
    verdict = scd_strategy_per_dimension(tables_ctx(dim_customer=_dim("valid_from")))
    assert verdict.score is not None and verdict.score < 3


def test_a_lone_row_hash_counts_as_a_declared_strategy():
    """A hash is unambiguous SCD machinery, unlike a flag.

    No business column is called ``hash_diff`` or ``row_hash``, so its presence
    is a deliberate change-detection decision. It versions nothing by itself,
    which is why it is not *evidence of Type 2* - but it is evidence that
    somebody chose a change-handling strategy, which is what this check asks.
    An existing test in test_checks_tables_stores.py already pins this.
    """
    verdict = scd_strategy_per_dimension(tables_ctx(dim_customer=_dim("row_hash")))
    assert verdict.score == 3


def test_every_dimension_half_implemented_does_not_pass():
    """The reviewer's case: a whole estate of flags with no dates."""
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("is_current"),
        dim_product=_dim("active_flag"),
        dim_store=_dim("is_active"),
    ))
    assert verdict.score == 1
    assert "None of the 3 dimension(s) declares a complete SCD strategy" in verdict.evidence


# ---------------------------------------------------------------------------
# what a complete implementation looks like
# ---------------------------------------------------------------------------

def test_a_validity_pair_is_a_complete_strategy():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("valid_from", "valid_to", "is_current")))
    assert verdict.score == 3


def test_a_start_date_plus_version_is_complete():
    """A monotonic version is the documented alternative to an end date."""
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("valid_from", "row_version")))
    assert verdict.score == 3


def test_a_business_date_range_alone_is_not_an_scd_strategy():
    """``start_date``/``end_date`` are ordinary business columns.

    A contract term, a promotion window, an employee's tenure. ``is_audit_column``
    already refuses to read ``start_date`` as lineage metadata, and these
    vocabularies must agree - otherwise every dimension holding a date range
    would silently score as Type 2.
    """
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_city=_dim("start_date", "end_date", "version")))
    assert verdict.score == 1


def test_a_business_date_range_with_a_current_flag_is_an_scd_strategy():
    """The disambiguator: a contract term never carries ``is_current``.

    ``StartDate``/``EndDate``/``IsActive`` is exactly what Microsoft's own
    ADF/Synapse SCD-2 data flow generates, so this combination must be
    recognised even though the date names alone are ambiguous.
    """
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("StartDate", "EndDate", "IsActive")))
    assert verdict.score == 3


def test_dbt_snapshot_columns_are_recognised():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("dbt_valid_from", "dbt_valid_to")))
    assert verdict.score == 3


def test_sql_server_temporal_columns_are_recognised():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("SysStartTime", "SysEndTime")))
    assert verdict.score == 3


def test_data_vault_load_dates_are_recognised():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("load_date", "load_end_date", "hashdiff")))
    assert verdict.score == 3


def test_type3_previous_value_columns_count_as_a_strategy():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("previous_city", "prior_segment")))
    assert verdict.score == 3
    assert "previous values" in verdict.evidence


# ---------------------------------------------------------------------------
# Type 1 is legitimate - never a hard failure
# ---------------------------------------------------------------------------

def test_no_markers_at_all_is_partial_never_fail():
    """Overwrite-in-place is a real strategy; the decision may be recorded elsewhere."""
    verdict = scd_strategy_per_dimension(tables_ctx(dim_customer=_dim()))
    assert verdict.score == 1
    assert "legitimate strategy" in verdict.evidence


def test_a_mixed_estate_scores_partial():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("valid_from", "valid_to", "is_current"),
        dim_product=_dim(),
    ))
    assert verdict.score == 2
    assert "1 of 2" in verdict.evidence


def test_a_complete_dimension_beside_a_half_implemented_one_is_partial():
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer=_dim("valid_from", "valid_to"),
        dim_product=_dim("is_current"),
    ))
    assert verdict.score == 2
    assert "partial marker set" in verdict.evidence


def test_a_non_temporal_validity_column_does_not_count():
    """``valid_from int`` is not a date, whatever it is called.

    A free filter against a false positive: the declared type is already in the
    snapshot, so a column that cannot hold a point in time never establishes a
    validity window.
    """
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer={
            "type": "Managed", "format": "Delta", "store": "WH_Gold",
            "columns": [
                {"name": "customer_key", "type": "bigint"},
                {"name": "customer_name", "type": "varchar(100)"},
                {"name": "valid_from", "type": "int"},
                {"name": "valid_to", "type": "int"},
            ],
        }))
    assert verdict.score == 1


def test_a_column_with_no_readable_type_still_counts():
    """An absent type is not evidence against - a partial crawl must not downgrade."""
    verdict = scd_strategy_per_dimension(tables_ctx(
        dim_customer={
            "type": "Managed", "format": "Delta", "store": "WH_Gold",
            "columns": [
                {"name": "customer_key", "type": "bigint"},
                {"name": "customer_name", "type": "varchar(100)"},
                {"name": "valid_from", "type": ""},
                {"name": "valid_to", "type": ""},
            ],
        }))
    assert verdict.score == 3


def test_no_dimension_is_na():
    verdict = scd_strategy_per_dimension(tables_ctx(
        fact_sales={"type": "Managed", "format": "Delta",
                    "columns": [{"name": "sales_key", "type": "bigint"},
                                {"name": "amount", "type": "decimal"}]}))
    assert verdict.status is Status.NA


def test_no_tables_is_na():
    assert scd_strategy_per_dimension(tables_ctx()).status is Status.NA
