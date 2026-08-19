"""Regression pins for the checks fixed after the 2026-08-12 validation round.

Three defects were found by running the checks against a real 1,076-item estate
and cross-checking the results against Microsoft's documentation:

* **4.5.6 TB-SURROGATE** reported ``0 of 19`` on an estate where more than half
  the dimensions were correctly modelled. It required an underscore
  (``_sk``/``_key``), so AdventureWorks-style ``CustomerKey`` failed. Microsoft's
  own samples use both spellings, so both must pass - and ``CustomerAlternateKey``
  must not, because AdventureWorks uses that for the *business* key.

* **1.2.3 NB-BRONZE-METADATA** had no Bronze gate at all: every notebook that
  wrote a table was judged by a Bronze-only rule, so Gold and Silver notebooks
  were failed for lacking ingestion metadata they were never meant to carry.

* **1.2.5 NB-SILVER-QUALITY** gated on the word "silver" appearing *anywhere* in
  the code, so a Gold notebook that merely read a silver table was judged as
  though it produced one.

The common rule these pin: layer-specific checks must identify the layer from
what the notebook *writes*, and must report **N/A, never FAIL**, when the layer
cannot be determined - an estate whose layers are named ``raw``/``curated`` is
not assessed rather than failed.
"""
from __future__ import annotations

import pytest

from auditfast.core.check._notebook import medallion_layer, write_targets
from auditfast.core.check._tables import has_surrogate_key, is_surrogate_key_column
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Resource
from auditfast.core.models import CheckContext, WorkspaceContext


def _spec(check_id: str):
    return next(s for s in REGISTRY if s.id == check_id)


def _notebook(code: str, lakehouse: str = "") -> dict:
    """A minimal ipynb definition with one code cell and an optional attached lakehouse."""
    defn: dict = {"cells": [{"cell_type": "code", "source": code}]}
    if lakehouse:
        defn["metadata"] = {
            "dependencies": {"lakehouse": {"default_lakehouse_name": lakehouse}}
        }
    return defn


def _ws(**kwargs) -> WorkspaceContext:
    """A workspace with notebook definitions available unless told otherwise."""
    kwargs.setdefault("id", "ws-id")
    kwargs.setdefault("display_name", "ws")
    return WorkspaceContext(**kwargs)


def _run(check_id: str, workspace: WorkspaceContext, obj_name: str, obj):
    return _spec(check_id).fn(CheckContext(workspace, {}, obj_name, obj))


# -- 4.5.6 surrogate keys -----------------------------------------------------


@pytest.mark.parametrize("column", [
    "customer_sk",          # Fabric dimensional-modelling guidance: Salesperson_SK
    "CustomerSK",
    "CustomerKey",          # AdventureWorksDW: IDENTITY(1,1) surrogate
    "ProductKey",
    "DateKey",
    "customer_key",
    "CUSTOMER_ID",
    "customerId",
])
def test_surrogate_key_spellings_are_all_recognised(column):
    """Naming is a house convention: Microsoft's own samples use several spellings."""
    assert is_surrogate_key_column(column), f"{column} should read as a surrogate key"


@pytest.mark.parametrize("column", [
    "CustomerAlternateKey",  # AdventureWorks: this is the BUSINESS key
    "ProductAlternateKey",
    "customer_alt_key",
    "natural_key",
    "business_key",
    "source_key",
    "monkey",                # a single word - must never look like "…|key"
    "turkey",
    "keyboard",
    "key_lookup_table",      # 'key' is not the trailing word
    "",
])
def test_business_keys_and_lookalikes_are_not_surrogate_keys(column):
    """The point is surrogate *instead of* business key, so an alternate key must fail."""
    assert not is_surrogate_key_column(column), f"{column} must not read as a surrogate key"


@pytest.mark.parametrize("column,is_any_key", [
    ("order_number", True),      # a key by the broad vocabulary...
    ("product_code", True),
    ("customer_sk", True),
])
def test_the_broad_key_vocabulary_is_not_narrowed_by_the_surrogate_rule(column, is_any_key):
    """``_SURROGATE_KEY_WORDS`` must not shadow the wider ``_KEY_WORDS`` set.

    ``is_key_column`` deliberately counts ``number``/``code``/``no`` as keys - the
    fact-purity and degenerate-dimension checks rely on it. The surrogate rule is
    narrower (``sk``/``key``/``id`` only); naming both sets alike once made the
    broad one shadow the narrow one, so ``order_number`` stopped reading as a key
    at all and the degenerate-dimension check went silent.
    """
    from auditfast.core.check._tables import is_key_column

    assert is_key_column(column) is is_any_key
    # ...but only the sk/key/id spelling is a *surrogate* key.
    assert is_surrogate_key_column("order_number") is False
    assert is_surrogate_key_column("customer_sk") is True


def test_dimension_with_adventureworks_keys_passes_4_5_6():
    """The exact real-world case that scored 0 of 19: DimCustomer with CustomerKey."""
    tables = {
        "DimCustomer": {"columns": [
            {"name": "CustomerKey", "type": "int"},
            {"name": "CustomerAlternateKey", "type": "nvarchar(15)"},
            {"name": "FirstName", "type": "nvarchar(50)"},
        ]},
        "dim_geography": {"columns": [
            {"name": "state", "type": "varchar(50)"},
            {"name": "region", "type": "varchar(50)"},
        ]},
    }
    assert has_surrogate_key(tables["DimCustomer"])
    assert not has_surrogate_key(tables["dim_geography"])

    verdict = _run("TB-SURROGATE", _ws(tables=tables), "ws", None)
    assert verdict.score is not None
    assert "1 of 2" in verdict.evidence
    # The failing dimension is named, so the finding is actionable.
    assert "dim_geography" in verdict.evidence


def test_4_5_6_is_na_when_no_dimension_has_columns():
    """No readable columns is "we could not look", never a finding."""
    verdict = _run("TB-SURROGATE", _ws(tables={"DimCustomer": {"columns": []}}), "ws", None)
    assert verdict.score is None


# -- 1.2.3 / 1.2.5 medallion layer identification -----------------------------


def test_write_target_beats_a_read_reference():
    """The false FAIL found on the real estate: reads bronze, writes silver."""
    code = (
        "df = spark.read.format('delta').load('abfss://ws@onelake/bronze/loans')\n"
        "df.write.format('delta').saveAsTable('silver.fact_loan')\n"
    )
    assert "silver.fact_loan" in write_targets(code)
    layer, how = medallion_layer(_notebook(code, lakehouse="Bronze"), code)
    assert layer == "silver", "the layer produced is what the notebook writes"
    assert "silver.fact_loan" in how


def test_attached_lakehouse_is_the_fallback():
    """With no layer in the write target, the attached lakehouse decides."""
    code = "df.write.saveAsTable('customers')\n"
    layer, how = medallion_layer(_notebook(code, lakehouse="Bronze"), code)
    assert layer == "bronze"
    assert "Bronze" in how


def test_unrecognised_vocabulary_yields_no_layer():
    """An estate naming its lakehouses CELADummyData must not be guessed at."""
    code = "df.write.saveAsTable('customers')\n"
    layer, _how = medallion_layer(_notebook(code, lakehouse="CELADummyData"), code)
    assert layer == "", "an unknown vocabulary must be unknown, not assumed"


@pytest.mark.parametrize("lakehouse,expected", [
    ("Bronze", "bronze"),
    ("raw_zone", "bronze"),
    ("landing", "bronze"),
    ("Silver", "silver"),
    ("curated_data", "silver"),
    ("Gold", "gold"),
    ("serving_layer", "gold"),
])
def test_common_house_vocabularies_are_recognised(lakehouse, expected):
    code = "df.write.saveAsTable('t')\n"
    layer, _how = medallion_layer(_notebook(code, lakehouse=lakehouse), code)
    assert layer == expected


def test_a_path_naming_two_layers_is_unknown_not_guessed():
    """``bronze_to_silver`` belongs to neither: guessing would re-create the false FAIL."""
    code = "df.write.saveAsTable('bronze_to_silver_staging')\n"
    layer, _how = medallion_layer(_notebook(code), code)
    assert layer == ""


@pytest.mark.parametrize("target", [
    "src.customers",        # a source folder, not a medallion layer
    "agg_sales",            # 'agg' collides with ordinary aggregate tables
    "semantic_model_feed",
    "golden_gate_audit",    # substring, not a token
    "sliver_of_data",
])
def test_generic_code_words_do_not_classify_a_layer(target):
    """A vocabulary that collides with ordinary naming must yield unknown."""
    code = f"df.write.saveAsTable('{target}')\n"
    layer, _how = medallion_layer(_notebook(code), code)
    assert layer == "", f"{target} must not be read as a medallion layer"


def test_1_2_3_does_not_judge_a_gold_notebook():
    """The bug: a Gold notebook was failed by the Bronze-only rule."""
    code = "df.write.saveAsTable('gold.sales_summary')\n"
    ws = _ws(notebooks={"nb": _notebook(code)})
    verdict = _run("NB-BRONZE-METADATA", ws, "nb", _notebook(code))
    assert verdict.score is None, "a Gold notebook must be N/A for a Bronze rule"
    assert "gold" in verdict.evidence.lower()


def test_1_2_3_is_na_when_the_layer_cannot_be_determined():
    """The safety property: an unfamiliar vocabulary is not assessed, never failed."""
    code = "df.write.saveAsTable('customers')\n"
    defn = _notebook(code, lakehouse="CELADummyData")
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-BRONZE-METADATA", ws, "nb", defn)
    assert verdict.score is None
    assert "could not be determined" in verdict.evidence


def test_1_2_3_still_fails_a_real_bronze_notebook_without_metadata():
    """The fix must not silence the genuine finding."""
    code = "df.write.saveAsTable('bronze.raw_orders')\n"
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-BRONZE-METADATA", ws, "nb", defn)
    assert verdict.score == 0
    assert "missing: ingestion timestamp, source identity, batch identifier" in verdict.evidence


def test_1_2_3_passes_a_bronze_notebook_with_metadata():
    code = (
        "df = df.withColumn('ingestion_timestamp', current_timestamp())\n"
        "df = df.withColumn('source_system', lit('crm'))\n"
        "df = df.withColumn('batch_id', lit(run_id))\n"
        "df.write.saveAsTable('bronze.raw_orders')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-BRONZE-METADATA", ws, "nb", defn)
    assert verdict.score == 3


def test_1_2_3_awards_partial_credit_for_incomplete_bronze_audit_metadata():
    code = (
        "df = df.withColumn('ingestion_timestamp', current_timestamp())\n"
        "df = df.withColumn('source_system', lit('crm'))\n"
        "df.write.saveAsTable('bronze.raw_orders')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-BRONZE-METADATA", ws, "nb", defn)

    assert verdict.score == 2
    assert "present: ingestion timestamp, source identity" in verdict.evidence
    assert "missing: batch identifier" in verdict.evidence


def test_1_2_5_does_not_judge_a_gold_notebook_that_reads_silver():
    """The word 'silver' in a read path must not make this a silver notebook."""
    code = (
        "df = spark.read.table('silver.fact_loan')\n"
        "df.write.saveAsTable('gold.loan_summary')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-SILVER-QUALITY", ws, "nb", defn)
    assert verdict.score is None
    assert "gold" in verdict.evidence.lower()


def test_1_2_5_still_fails_a_silver_notebook_without_cleansing():
    code = "df.write.saveAsTable('silver.dim_customer')\n"
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-SILVER-QUALITY", ws, "nb", defn)
    assert verdict.score == 0


def test_1_2_5_scores_a_silver_notebook_on_how_many_aspects_it_applies():
    """Dedup, a rename (conforming) and a cast is 3 of the 4 aspects - a partial.

    ``dropna`` is deliberately *not* counted as cleansing: dropping a row is not
    the same as repairing a value, and the cleansing vocabulary names the repair
    operations (trim / regexp_replace / fillna / coalesce).
    """
    code = (
        "df = df.dropDuplicates(['id']).dropna(subset=['id'])\n"
        "df = df.withColumnRenamed('CustomerID', 'customer_id')\n"
        "df = df.withColumn('d', to_date('d'))\n"
        "df.write.saveAsTable('silver.dim_customer')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-SILVER-QUALITY", ws, "nb", defn)
    assert verdict.score is not None and 0 < verdict.score < 3
    assert "3 of 4" in verdict.evidence
    assert "Not found: cleansing" in verdict.evidence


def test_1_2_3_and_1_2_5_use_the_highest_layer_a_notebook_writes():
    """A Bronze-to-Silver promotion notebook produces *silver*.

    It writes a bronze staging table first, so taking the first matching write
    target classified every promotion notebook as bronze - on a real estate 8 of
    8 notebooks resolved to bronze, which failed Silver notebooks against the
    Bronze raw-capture rule and left 1.2.5 with nothing to judge.
    """
    code = (
        "raw.write.saveAsTable('bronze.stg_customer')\n"
        "clean = raw.dropDuplicates(['id']).withColumn('d', to_date('d'))\n"
        "clean.write.saveAsTable('silver.dim_customer')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})

    bronze = _run("NB-BRONZE-METADATA", ws, "nb", defn)
    assert bronze.score is None, "a silver producer is not judged by the Bronze rule"

    silver = _run("NB-SILVER-QUALITY", ws, "nb", defn)
    assert silver.score is not None, "the silver branch must be reachable"
    assert "silver.dim_customer" in silver.evidence


def test_1_2_5_awards_partial_credit_when_only_deduplication_is_missing():
    """The MLC Silver mapping conforms and casts, but neither cleans nor deduplicates.

    ``dropna`` is deliberately not cleansing: dropping a row discards data, it
    does not repair a value, and the cleansing vocabulary names the repair
    operations (trim / regexp_replace / fillna / coalesce). So this notebook
    applies 2 of the 4 aspects, not 3.
    """
    code = (
        "df = df.dropna(subset=['customer_id'])\n"
        "df = df.withColumnRenamed('CustomerID', 'customer_id')\n"
        "df = df.withColumn('event_date', to_date('event_date'))\n"
        "df.write.saveAsTable('Silver_MLC_Lakehouse.dim_customer')\n"
    )
    defn = _notebook(code)
    ws = _ws(notebooks={"CC_Mapping_Bronze_to_Silver": defn})
    verdict = _run("NB-SILVER-QUALITY", ws, "CC_Mapping_Bronze_to_Silver", defn)

    assert verdict.score == 1
    assert "2 of 4" in verdict.evidence
    assert "conforming" in verdict.evidence
    assert "type standardization" in verdict.evidence
    assert "Not found: deduplication, cleansing" in verdict.evidence


def test_1_2_5_lists_all_missing_controls_for_an_untreated_silver_write():
    code = "df.write.saveAsTable('silver.dim_customer')\n"
    defn = _notebook(code)
    ws = _ws(notebooks={"nb": defn})
    verdict = _run("NB-SILVER-QUALITY", ws, "nb", defn)

    assert verdict.score == 0
    assert "0 of 4" in verdict.evidence
    assert ("Not found: deduplication, type standardization, cleansing, conforming"
            in verdict.evidence)


@pytest.mark.parametrize("check_id", ["NB-BRONZE-METADATA", "NB-SILVER-QUALITY"])
def test_notebook_checks_are_na_when_definitions_are_unreadable(check_id):
    """Unreadable data is N/A, never FAIL - the library's central invariant."""
    ws = _ws(notebooks={}, unavailable={Resource.NOTEBOOK_DEFINITIONS})
    verdict = _run(check_id, ws, "nb", _notebook("df.write.saveAsTable('x')"))
    assert verdict.score is None


@pytest.mark.parametrize("check_id", ["NB-BRONZE-METADATA", "NB-SILVER-QUALITY"])
def test_notebook_checks_ignore_a_notebook_that_writes_nothing(check_id):
    defn = _notebook("print('hello')")
    ws = _ws(notebooks={"nb": defn})
    verdict = _run(check_id, ws, "nb", defn)
    assert verdict.score is None


# -- 4.2.3 naming consistency / 4.5.1 star schema -----------------------------


def test_4_2_3_accepts_any_convention_provided_it_is_consistent():
    """PascalCase is a valid house style: AdventureWorks uses it throughout.

    The old rule demanded snake_case, so a consistently-PascalCase estate was
    marked down for choosing the other valid convention.
    """
    pascal = {"DimCustomer": {"columns": [
        {"name": "CustomerKey"}, {"name": "FirstName"}, {"name": "LastName"},
    ]}}
    snake = {"dim_customer": {"columns": [
        {"name": "customer_key"}, {"name": "first_name"}, {"name": "last_name"},
    ]}}
    for tables in (pascal, snake):
        verdict = _run("TB-COL-NAMING", _ws(tables=tables), "ws", None)
        assert verdict.score == 3, f"a consistent estate must pass: {verdict.evidence}"


def test_4_2_3_penalises_a_genuinely_inconsistent_estate():
    """Half Pascal, half snake is what the point is actually about."""
    tables = {"t": {"columns": [
        {"name": "CustomerKey"}, {"name": "FirstName"},
        {"name": "last_name"}, {"name": "order_date"},
    ]}}
    verdict = _run("TB-COL-NAMING", _ws(tables=tables), "ws", None)
    assert verdict.score is not None
    assert verdict.score < 3, "a split-convention estate must not score full marks"


def test_4_2_3_reports_names_following_no_convention():
    """``Customer_ID`` and names with spaces follow nothing and can never dominate."""
    tables = {"t": {"columns": [
        {"name": "customer_id"}, {"name": "order_date"},
        {"name": "Customer_ID"}, {"name": "LDP Course Name"},
    ]}}
    verdict = _run("TB-COL-NAMING", _ws(tables=tables), "ws", None)
    assert "no convention at all" in verdict.evidence


def test_4_2_3_is_na_without_column_metadata():
    assert _run("TB-COL-NAMING", _ws(tables={"t": {"columns": []}}), "ws", None).score is None


def test_4_5_1_reports_fact_width_without_scoring_it():
    """Width is context for "not flat wide tables"; 4.5.3 is what scores purity."""
    tables = {
        "fact_sales": {"columns": [{"name": f"c{n}"} for n in range(40)]},
        "dim_customer": {"columns": [{"name": "customer_sk"}]},
    }
    verdict = _run("TB-STARSCHEMA", _ws(tables=tables), "ws", None)
    assert verdict.score == 3, "a wide fact must not turn the structural gate into a FAIL"
    assert "40 columns" in verdict.evidence
    assert "not scored" in verdict.evidence


def test_4_5_1_still_fails_when_no_dimension_exists():
    tables = {"fact_sales": {"columns": [{"name": "sales_sk"}]}}
    verdict = _run("TB-STARSCHEMA", _ws(tables=tables), "ws", None)
    assert verdict.score == 0
    assert "no dimension tables" in verdict.evidence


# -- 13.2.3 sensitivity labels -----------------------------------------------


def test_label_is_read_from_either_documented_spelling():
    """Fabric Core returns ``sensitivityLabel.id``; the scanner returns ``labelId``."""
    from auditfast.core.models import Item

    assert Item.from_api({"sensitivityLabel": {"id": "abc"}}).sensitivity_label == "abc"
    assert Item.from_api({"sensitivityLabel": {"labelId": "xyz"}}).sensitivity_label == "xyz"
    assert Item.from_api({}).sensitivity_label is None


def test_ws_labels_is_na_when_no_item_carries_a_label():
    """The real-estate case: 1,076 items, no label on any - unreadable, not a finding.

    Nothing labelled and labels-not-exposed are indistinguishable here, so this
    must not score 0.
    """
    from auditfast.core.models import Item

    items = [Item(id=str(n), type="Lakehouse", display_name=f"lh{n}") for n in range(5)]
    verdict = _run("WS-LABELS", _ws(items=items), "ws", None)
    assert verdict.score is None, "an all-unlabelled workspace must be N/A, never FAIL"
    assert "unassessed" in verdict.evidence


def test_ws_labels_scores_the_ratio_once_labelling_is_in_use():
    """One labelled item proves labelling works here, so the remainder is scored."""
    from auditfast.core.models import Item

    items = [
        Item(id="1", type="Lakehouse", display_name="a", sensitivity_label="conf"),
        Item(id="2", type="Notebook", display_name="b"),
    ]
    verdict = _run("WS-LABELS", _ws(items=items), "ws", None)
    assert verdict.score is not None
    assert "1 of 2" in verdict.evidence


def test_ws_labels_is_na_when_items_are_unreadable():
    ws = _ws(items=[], unavailable={Resource.ITEMS})
    assert _run("WS-LABELS", ws, "ws", None).score is None


def test_2_1_1_is_na_when_no_naming_convention_is_configured():
    """Failing every pipeline against ``None`` is a finding invented from missing config.

    Flagged by validate_all as one-sided: FAIL on all 50 pipelines of a real
    estate, because the project set no ``pipeline_naming_convention``.
    """
    defn = {"properties": {"activities": [{"name": "Copy", "type": "Copy"}]}}
    ws = _ws(pipelines={"Bronze": defn})
    verdict = _run("PL-NAME", ws, "Bronze", defn)
    assert verdict.score is None, "no configured convention means nothing to judge"
    assert "pipeline_naming_convention" in verdict.evidence


def test_2_1_1_still_judges_when_a_convention_is_configured():
    defn = {"properties": {"activities": [{"name": "Copy", "type": "Copy"}]}}
    ws = _ws(pipelines={"PL_Bronze_Load": defn, "random": defn})
    settings = {"pipeline_naming_convention": r"^PL_"}
    spec = _spec("PL-NAME")

    ok = spec.fn(CheckContext(ws, settings, "PL_Bronze_Load", defn))
    bad = spec.fn(CheckContext(ws, settings, "random", defn))
    assert ok.score == 3
    assert bad.score == 0


# -- 4.4.1 Warehouse schema organisation --------------------------------------


def _wh_table(schema: str, *names: str) -> dict:
    """A Warehouse table whose columns declare ``schema``."""
    return {
        "store": "SalesWarehouse",
        "store_kind": "Warehouse",
        "columns": [
            {"name": n, "type": "varchar(50)", "source_kind": "Warehouse", "schema": schema}
            for n in names
        ],
    }


def test_4_4_1_reads_the_schema_from_column_metadata():
    """The reader now records TABLE_SCHEMA, so schema layout is finally readable.

    Previously the schema was fetched and discarded, leaving the check to guess
    from the table key - which carries the *store*, not the schema. On a real
    estate that meant "none of 252 Warehouse tables carries a schema qualifier"
    and a permanent N/A.
    """
    from auditfast.core.check.data_management_quality.data_storage.automated import (
        _schema_qualifier,
    )

    table = _wh_table("sales", "order_id", "amount")
    assert _schema_qualifier("fact_orders", table) == "sales"


def test_4_4_1_falls_back_to_the_key_for_older_snapshots():
    """A snapshot crawled before the reader captured the schema must still work."""
    from auditfast.core.check.data_management_quality.data_storage.automated import (
        _schema_qualifier,
    )

    legacy = {"store": "SalesWarehouse", "store_kind": "Warehouse",
              "columns": [{"name": "order_id", "type": "int"}]}
    assert _schema_qualifier("staging.fact_orders", legacy) == "staging"
    # The store prefix is not a schema.
    assert _schema_qualifier("SalesWarehouse.fact_orders", legacy) == ""


def test_4_4_1_scores_a_warehouse_once_schemas_are_readable():
    tables = {
        "fact_orders": _wh_table("sales", "order_id", "amount"),
        "dim_customer": _wh_table("sales", "customer_sk", "name"),
        "raw_orders": _wh_table("staging", "order_id"),
    }
    verdict = _run("TB-WH-SCHEMAS", _ws(tables=tables), "ws", None)
    assert verdict.score is not None, f"schema layout is now readable: {verdict.evidence}"


def test_4_4_1_is_na_when_no_table_lives_in_a_warehouse():
    """Schema organisation is a Warehouse question - a Lakehouse-only estate is N/A."""
    tables = {"t": {"store": "LH", "store_kind": "Lakehouse",
                    "columns": [{"name": "a", "type": "int"}]}}
    assert _run("TB-WH-SCHEMAS", _ws(tables=tables), "ws", None).score is None


# -- remediation --------------------------------------------------------------

@pytest.mark.parametrize("ref", ["1.2.3", "1.2.5", "4.2.3", "4.2.4", "4.5.1", "4.5.6", "4.5.9"])
def test_each_reviewed_ref_has_remediation_text(ref):
    """4.5.1 and 4.5.6 had none, so a failing finding carried an empty recommendation."""
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    book = load_remediation(load_project(PROJECT_FILE))
    assert book.get(ref), f"ref {ref} has no remediation text"
