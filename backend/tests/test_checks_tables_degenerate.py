"""4.5.11 - declared relationships and column types replace name guessing.

``TB-DEGENERATE-JUNK-DIM`` decided two things from column names alone:

* whether a fact's key column resolves to a dimension - by comparing name
  tokens against dimension table names, which is the unsafe "pair two objects
  because their names look alike" operation;
* whether a column is a junk-dimension candidate - by a broad regex that also
  matches ``rejection_reason``, ``product_type`` and ``country_code``.

Both now defer to something the estate declares. A semantic-model relationship
*states* that a column resolves to another table, and a declared type says
whether a column could plausibly hold a handful of values. Names are consulted
only where neither is available.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_storage.automated import (
    degenerate_and_junk_dimension_candidates,
)
from auditfast.core.enums import Status

from .fixtures.builders import workspace_ctx


def _fact(*columns: tuple[str, str]) -> dict:
    base = (("sales_key", "bigint"), ("amount", "decimal(18,2)"),
            ("quantity", "int"), ("order_date", "date"))
    return {"type": "Managed", "format": "Delta", "store": "WH_Gold",
            "columns": [{"name": n, "type": t} for n, t in base + columns]}


def _dim(*columns: tuple[str, str]) -> dict:
    base = (("customer_key", "bigint"), ("customer_name", "varchar(100)"))
    return {"type": "Managed", "format": "Delta", "store": "WH_Gold",
            "columns": [{"name": n, "type": t} for n, t in base + columns]}


def _model(*relationships: tuple[str, str, str, str]) -> dict:
    """One semantic model declaring ``(from_table, from_column, to_table, to_column)``."""
    return {"m": {
        "tables": [], "measures": [], "columns": [], "data_categories": {},
        "relationships": [
            {"name": f"r{i}", "from_table": ft, "from_column": fc,
             "to_table": tt, "to_column": tc, "cross_filter": "", "is_active": True}
            for i, (ft, fc, tt, tc) in enumerate(relationships)
        ],
    }}


# ---------------------------------------------------------------------------
# the degenerate half: a declared relationship is not a guess
# ---------------------------------------------------------------------------

def test_a_related_key_is_never_reported_as_unmodelled():
    """The modeller declared where this column points, so the name is irrelevant.

    ``ext_ref_id`` resolves to no table by name, but a relationship states that
    it joins to the customer dimension. Without relationships this scored a
    degenerate-dimension finding purely because the names did not match.
    """
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("ext_ref_id", "bigint")),
                "dim_customer": _dim()},
        semantic_models=_model(("fact_sales", "ext_ref_id",
                                "dim_customer", "customer_key")),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.score == 3
    assert "ext_ref_id" not in verdict.evidence


def test_an_unrelated_key_is_still_reported():
    """No relationship, no name match - the finding stands."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("order_number", "varchar(20)")),
                "dim_customer": _dim()},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.score is not None and verdict.score < 3
    assert "order_number" in verdict.evidence


def test_relationships_do_not_mask_a_second_orphan_key():
    """One modelled column must not clear the whole table."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("ext_ref_id", "bigint"),
                                    ("invoice_number", "varchar(20)")),
                "dim_customer": _dim()},
        semantic_models=_model(("fact_sales", "ext_ref_id",
                                "dim_customer", "customer_key")),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "invoice_number" in verdict.evidence
    assert "ext_ref_id" not in verdict.evidence


def test_no_semantic_model_falls_back_to_names_rather_than_passing():
    """A Bronze workspace has no relationships - that is not evidence of health.

    The check must keep the weaker name-based test rather than reporting a clean
    pass simply because nothing was declared.
    """
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("order_number", "varchar(20)")),
                "dim_customer": _dim()},
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert verdict.score is not None and verdict.score < 3


# ---------------------------------------------------------------------------
# the junk half: the declared type has to agree with the name
# ---------------------------------------------------------------------------

def test_wide_text_columns_are_not_junk_dimension_candidates():
    """The bug: the name pattern alone matched free text.

    ``rejection_reason varchar(500)`` matches ``\\w+_reason`` and is prose. A
    junk dimension collapses low-cardinality columns; collapsing a comment field
    buys nothing. These are the *vague* suffixes, so the type must agree.
    """
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("rejection_reason", "varchar(500)"),
                                    ("failure_reason", "varchar(4000)"),
                                    ("comment_category", "varchar(255)"))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "flag column(s)" not in verdict.evidence


def test_an_unbounded_vague_suffix_is_not_a_candidate():
    """``order_status string`` states no width, so narrowness is not shown."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("order_status", "varchar"),
                                    ("trip_type", "string"),
                                    ("payment_type", "varchar(max)"))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "flag column(s)" not in verdict.evidence


def test_unambiguous_flag_names_count_without_a_declared_width():
    """The Lakehouse case: bare ``string`` types must not silence the check.

    Requiring a declared width everywhere removed all 51 findings on a real
    estate - including genuine ones like ``store_and_fwd_flag``. Nobody names a
    comment field ``is_returned``, so these names stand on their own.
    """
    ctx = workspace_ctx(
        tables={"fact_trips": _fact(("store_and_fwd_flag", "string"),
                                    ("is_returned", "string"),
                                    ("has_surcharge", "string"))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "3 flag column(s)" in verdict.evidence


def test_narrow_and_boolean_columns_are_junk_dimension_candidates():
    """The classic junk-dimension inputs: booleans and short coded values."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("is_returned", "bit"),
                                    ("payment_type", "char(1)"),
                                    ("order_status", "varchar(10)"),
                                    ("store_flag", "boolean"))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "4 flag column(s)" in verdict.evidence


def test_columns_with_no_readable_type_stay_eligible():
    """An unreadable type is not evidence against - it must not silently clear a finding."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("is_returned", ""), ("order_status", ""),
                                    ("trip_type", ""))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "3 flag column(s)" in verdict.evidence


def test_below_the_threshold_is_not_a_cluster():
    """Two flags are not worth a junk dimension - the pattern collapses several."""
    ctx = workspace_ctx(
        tables={"fact_sales": _fact(("is_returned", "bit"), ("trip_type", "char(1)"))},
        semantic_models=_model(),
    )
    verdict = degenerate_and_junk_dimension_candidates(ctx)
    assert "flag column(s)" not in verdict.evidence


# ---------------------------------------------------------------------------
# N/A, never FAIL, when nothing is readable
# ---------------------------------------------------------------------------

def test_no_tables_is_na():
    assert degenerate_and_junk_dimension_candidates(
        workspace_ctx(tables={})).status is Status.NA


def test_no_fact_tables_is_na():
    verdict = degenerate_and_junk_dimension_candidates(
        workspace_ctx(tables={"dim_customer": _dim()}))
    assert verdict.status is Status.NA
