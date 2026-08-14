"""``table_role`` - which evidence assigns a fact/dimension role, and where.

The rule these pin down: an *inference* from column shape is only safe where a
table's shape reflects a modelling decision. In a raw/landing store it reflects
whatever the source system had, so only a declared constraint or an explicit
name - both statements of intent - may assign a role there.

The case that forced this is real: an ERP inventory master landed in
``LH_Bronze`` with 20 key columns and 73 numeric columns (lead time, safety
stock, minimum order quantity). Numeric attributes, not additive measures - but
indistinguishable from measures in the schema, so shape read it as a fact. 316
such extracts were classified fact on one production estate, and every
dimensional check then graded a landing zone against star-schema rules.
"""
from __future__ import annotations

from auditfast.core.check._tables import dimensions_in, facts_in, table_role


def _col(name: str, dtype: str = "varchar(50)") -> dict:
    return {"name": name, "type": dtype}


def _erp_master(store: str) -> dict:
    """An ERP item master: many keys, many *numeric attributes*, few text columns.

    Deliberately under :data:`_FACT_MAX_COLUMNS` so these tests exercise the
    *store* gate rather than the width gate - the real table is 125 columns
    wide, which :func:`test_a_very_wide_table_is_never_a_fact` covers separately.
    """
    cols = [_col(f"{part}_KEY", "int") for part in
            ("GL_CMP", "IN_WHS", "IN_ITEM", "UOM", "GL_ACCT", "IN_MOHCD")]
    cols += [_col(f"IN_WHITM_{n}", "decimal(18,2)") for n in
             ("PMCD", "ORDPL", "LEADT", "NDYSP", "SSTK", "MINOQ", "MAXOQ", "RECLC")]
    cols += [_col(f"IN_WHITM_{n}") for n in ("SOURC", "PCKLC", "ACSTEXT")]
    return {"columns": cols, "store": store, "store_kind": "Lakehouse"}


def test_shape_is_not_inferred_in_a_raw_store():
    table = _erp_master("LH_Bronze")
    assert table_role("IN_WHITM_TBL", table, {"IN_WHITM_TBL": table}) == "unknown"


def test_the_same_shape_is_inferred_in_a_curated_store():
    """Outside a landing zone the shape *is* a modelling decision, so it counts."""
    table = _erp_master("LH_Gold")
    assert table_role("IN_WHITM_TBL", table, {"IN_WHITM_TBL": table}) == "fact"


def test_every_raw_store_word_suppresses_inference():
    for store in ("LH_Bronze", "raw_lakehouse", "stg-landing", "Ingestion_LH",
                  "src_lh", "L0_Store"):
        table = _erp_master(store)
        assert table_role("SOME_TBL", table, {"SOME_TBL": table}) == "unknown", store


def test_an_explicit_name_still_wins_inside_a_raw_store():
    """A ``fact_`` prefix is the author's stated intent, not an inference."""
    table = _erp_master("LH_Bronze")
    assert table_role("fact_sales", table, {"fact_sales": table}) == "fact"
    assert table_role("dim_customer", table, {"dim_customer": table}) == "dimension"


def test_a_declared_foreign_key_still_wins_inside_a_raw_store():
    """A constraint is read, not guessed, so a raw store does not suppress it."""
    tables = {
        "orders": {"columns": [_col("id", "int")], "store": "LH_Bronze",
                   "references": ["customers", "products"]},
        "customers": {"columns": [_col("id", "int")], "store": "LH_Bronze"},
        "products": {"columns": [_col("id", "int")], "store": "LH_Bronze"},
    }
    assert table_role("orders", tables["orders"], tables) == "fact"
    assert table_role("customers", tables["customers"], tables) == "dimension"


def test_an_unknown_store_does_not_suppress_inference():
    """Only positive evidence of a raw layer adds caution; unknown is not raw."""
    table = _erp_master("")
    assert table_role("IN_WHITM_TBL", table, {"IN_WHITM_TBL": table}) == "fact"


def test_facts_and_dimensions_exclude_raw_store_guesses():
    tables = {
        "IN_WHITM_TBL": _erp_master("LH_Bronze"),
        "IN_WHS_TBL": _erp_master("LH_Bronze"),
        "curated_sales": _erp_master("LH_Gold"),
    }
    assert list(facts_in(tables)) == ["curated_sales"]
    assert not dimensions_in(tables)


def test_a_very_wide_table_is_never_a_fact():
    """125 columns is a source extract or a report table, not a modelled fact.

    The real one survived the store gate by sitting in ``test_Lakehouse``, whose
    name says nothing about a layer. A fact is narrow by construction, so width
    is evidence in its own right.
    """
    cols = [_col(f"K{n}_KEY", "int") for n in range(20)]
    cols += [_col(f"M{n}", "decimal(18,2)") for n in range(73)]
    cols += [_col(f"D{n}") for n in range(32)]
    wide = {"columns": cols, "store": "test_Lakehouse", "store_kind": "Lakehouse"}
    assert table_role("IN_WHITM_TBL", wide, {"IN_WHITM_TBL": wide}) == "unknown"


def test_operational_tables_are_not_part_of_the_dimensional_model():
    """A run-log and a watermark table are infrastructure, not dimensions."""
    audit = {
        "columns": [_col("event_run_id", "int"), _col("event_activity_run_id", "int"),
                    _col("source_type"), _col("item_name"),
                    _col("event_start_time", "timestamp"),
                    _col("event_end_time", "timestamp"),
                    _col("rows_read", "int"), _col("rows_written", "int")],
        "store": "WH_Gold", "store_kind": "Warehouse",
    }
    control = {
        "columns": [_col("unique_key", "int"), _col("source_type"),
                    _col("source_schema_name"), _col("source_table_name"),
                    _col("source_watermark_column"), _col("target_container_name")],
        "store": "WH_Gold", "store_kind": "Warehouse",
    }
    tables = {"audit_table": audit, "control_table": control}
    assert table_role("audit_table", audit, tables) == "unknown"
    assert table_role("control_table", control, tables) == "unknown"
