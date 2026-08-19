"""Tests for the three row-level data-rule notebook checks (refs 5.3.3, 5.3.10, 5.5.8).

All three read the notebook definition only — never rows, counts or column
values — so each pins the *guard* rather than the data, plus the dedup boundary
that keeps it from re-scoring a sibling's evidence:

* 5.3.3 (``NB-BUSINESS-RULE``) must be satisfied only by a comparison relating
  **two columns of the same row**. A pure range check against a literal belongs
  to ``NB-DATE-QUALITY`` (5.5.1) and must not pass here.
* 5.3.10 (``NB-NULL-PROPAGATION``) is **positional**: the null check has to come
  *after* a join or a cast. The evidence that satisfies ``NB-NULL-HANDLING``
  (5.2.7) — a ``fillna`` at the top of the notebook — must not pass here.
* 5.5.8 (``NB-JSON-VALIDATION``) reuses the ``_EAM_JSON`` gate of its sibling
  ``NB-EAM-INGEST`` (2.6.6) so both agree on what EAM/JSON ingestion is, and
  grades three sub-practices rather than all-or-nothing.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import (
    nb_eam_ingest,
    notebook_applies_business_rules,
    notebook_checks_null_propagation,
    notebook_handles_non_key_nulls,
    notebook_validates_json_payloads,
)
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Pillar, Resource, Scope, Severity, Status
from auditfast.core.models import CheckContext, WorkspaceContext

_PASS, _PARTIAL_HIGH, _PARTIAL_LOW, _FAIL = 3, 2, 1, 0


def _nb_ctx(code: str, *, unavailable: set | None = None) -> CheckContext:
    definition = {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}
    workspace = WorkspaceContext(id="w", unavailable=unavailable or set())
    return CheckContext(workspace=workspace, settings={}, obj_name="nb", obj=definition)


# =============================================================================
# 5.3.3 — NB-BUSINESS-RULE
# =============================================================================

def test_business_rule_passes_on_a_dataframe_column_pair_comparison():
    ctx = _nb_ctx(
        'df = spark.read.json("/lake/eam/work_orders")\n'
        'bad = df.filter(col("start_date") > col("end_date"))\n'
        'bad.write.saveAsTable("quarantine_work_orders")\n'
    )
    verdict = notebook_applies_business_rules(ctx)
    assert verdict.score == _PASS
    assert "cross-field business rule" in verdict.evidence


def test_business_rule_passes_on_a_sql_comparison_of_two_columns():
    ctx = _nb_ctx(
        'spark.sql("SELECT * FROM work_orders WHERE actual_end < actual_start")\n'
    )
    assert notebook_applies_business_rules(ctx).score == _PASS


def test_business_rule_passes_on_a_named_rule_construct():
    ctx = _nb_ctx(
        'df = spark.read.json("/lake/eam/assets")\n'
        "violations = business_rules(df)\n"
        'violations.write.saveAsTable("dq_violations")\n'
    )
    assert notebook_applies_business_rules(ctx).score == _PASS


def test_business_rule_is_not_satisfied_by_a_pure_range_check():
    """Dedup with NB-DATE-QUALITY (5.5.1): a column against a literal is a range check."""
    ctx = _nb_ctx(
        'df = spark.read.json("/lake/eam/work_orders")\n'
        'recent = df.filter(col("event_date") > "2020-01-01")\n'
        'recent.write.saveAsTable("silver_work_orders")\n'
    )
    verdict = notebook_applies_business_rules(ctx)
    assert verdict.score == _FAIL
    assert "No cross-field business rule" in verdict.evidence


def test_business_rule_is_not_satisfied_by_a_commented_out_rule():
    ctx = _nb_ctx(
        'df = spark.read.json("/lake/eam/work_orders")\n'
        '# df.filter(col("start_date") <= col("end_date"))\n'
        'df.write.saveAsTable("silver_work_orders")\n'
    )
    assert notebook_applies_business_rules(ctx).score == _FAIL


def test_business_rule_is_na_when_the_notebook_moves_no_data():
    ctx = _nb_ctx("threshold = 10\nprint(threshold)\n")
    verdict = notebook_applies_business_rules(ctx)
    assert verdict.status is Status.NA
    assert verdict.score is None


def test_business_rule_is_na_when_notebook_definitions_were_unreadable():
    ctx = _nb_ctx("", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    verdict = notebook_applies_business_rules(ctx)
    assert verdict.status is Status.NA
    assert "could not be read" in verdict.evidence


# =============================================================================
# 5.3.10 — NB-NULL-PROPAGATION
# =============================================================================

def test_null_propagation_passes_when_a_null_count_follows_a_join():
    ctx = _nb_ctx(
        'joined = orders.join(customers, "customer_id", "left")\n'
        'unresolved = joined.filter(col("customer_name").isNull()).count()\n'
        "assert unresolved == 0\n"
    )
    verdict = notebook_checks_null_propagation(ctx)
    assert verdict.score == _PASS
    assert "bound" in verdict.evidence


def test_null_propagation_passes_when_a_null_check_follows_a_cast():
    ctx = _nb_ctx(
        'typed = raw.withColumn("quantity", col("quantity").cast("int"))\n'
        'failed_rows = typed.filter(col("quantity").isNull()).count()\n'
    )
    assert notebook_checks_null_propagation(ctx).score == _PASS


def test_null_propagation_scores_in_the_middle_when_the_check_is_not_bound():
    ctx = _nb_ctx(
        'result = alpha.join(beta, "k", "left")\n'
        "gamma.fillna(0)\n"
    )
    verdict = notebook_checks_null_propagation(ctx)
    assert verdict.score == _PARTIAL_HIGH
    assert "not tied to it" in verdict.evidence


def test_null_propagation_is_not_satisfied_by_the_null_handling_that_satisfies_5_2_7():
    """Dedup with NB-NULL-HANDLING (5.2.7): a top-of-notebook fillna is not positional.

    The same notebook passes 5.2.7 — nulls *are* handled — while failing 5.3.10,
    because nothing looks at the nulls the later join introduces.
    """
    code = (
        'df = spark.read.json("/lake/orders")\n'
        'df = df.fillna({"region": "UNKNOWN"})\n'
        'enriched = df.join(dim_customer, "customer_id", "left")\n'
        'enriched.write.saveAsTable("silver_orders")\n'
    )
    ctx = _nb_ctx(code)
    assert notebook_handles_non_key_nulls(ctx).score == _PASS   # 5.2.7 is satisfied
    verdict = notebook_checks_null_propagation(ctx)             # 5.3.10 is not
    assert verdict.score == _FAIL
    assert "NB-NULL-HANDLING" in verdict.evidence


def test_null_propagation_is_na_when_the_notebook_neither_joins_nor_casts():
    ctx = _nb_ctx(
        'df = spark.read.json("/lake/orders")\n'
        'df = df.fillna({"region": "UNKNOWN"})\n'
        'df.write.saveAsTable("silver_orders")\n'
    )
    verdict = notebook_checks_null_propagation(ctx)
    assert verdict.status is Status.NA
    assert "neither a join nor a cast" in verdict.evidence


def test_null_propagation_is_na_when_notebook_definitions_were_unreadable():
    ctx = _nb_ctx("", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    assert notebook_checks_null_propagation(ctx).status is Status.NA


# =============================================================================
# 5.5.8 — NB-JSON-VALIDATION
# =============================================================================

_FULLY_VALIDATED_JSON = (
    'schema = StructType([StructField("asset_id", StringType()), '
    'StructField("quantity", StringType())])\n'
    'raw = spark.read.schema(schema).option("badRecordsPath", "/bad").json("/eam/assets")\n'
    'required_fields = ["asset_id", "quantity"]\n'
    "absent = set(required_fields) - set(raw.columns)\n"
    "assert not absent\n"
)


def test_json_validation_scores_all_three_sub_practices():
    verdict = notebook_validates_json_payloads(_nb_ctx(_FULLY_VALIDATED_JSON))
    assert verdict.score == _PASS
    assert "3 of 3 sub-practices" in verdict.evidence


def test_json_validation_names_the_missing_sub_practices():
    ctx = _nb_ctx(
        'schema = StructType([StructField("asset_id", StringType())])\n'
        'raw = spark.read.schema(schema).json("/eam/assets")\n'
        'raw.write.saveAsTable("bronze_eam_assets")\n'
    )
    verdict = notebook_validates_json_payloads(ctx)
    assert verdict.score == _PARTIAL_LOW
    assert "1 of 3 sub-practices" in verdict.evidence
    assert "required-element presence check" in verdict.evidence


def test_json_validation_fails_when_nothing_is_validated():
    ctx = _nb_ctx(
        'raw = spark.read.json("/eam/assets")\n'
        'raw.write.saveAsTable("bronze_eam_assets")\n'
    )
    verdict = notebook_validates_json_payloads(ctx)
    assert verdict.score == _FAIL
    assert "validates nothing" in verdict.evidence


def test_json_validation_shares_the_eam_gate_with_its_sibling():
    """5.5.8 and 2.6.6 must agree on what counts as EAM/JSON ingestion."""
    ctx = _nb_ctx(
        'df = spark.read.parquet("/lake/orders")\n'
        'df.write.saveAsTable("silver_orders")\n'
    )
    assert notebook_validates_json_payloads(ctx).status is Status.NA
    assert nb_eam_ingest(ctx).status is Status.NA


def test_json_validation_is_na_for_a_non_json_eam_table_name():
    ctx = _nb_ctx(
        'df = spark.read.table("eam_work_orders")\n'
        'df.write.saveAsTable("silver_eam_work_orders")\n'
    )
    assert notebook_validates_json_payloads(ctx).status is Status.NA
    assert nb_eam_ingest(ctx).status is Status.NA


def test_json_validation_is_na_when_notebook_definitions_were_unreadable():
    ctx = _nb_ctx("", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    assert notebook_validates_json_payloads(ctx).status is Status.NA


def test_json_validation_is_na_for_stdlib_json_config_parsing():
    """A deployment notebook that reads pipeline JSON files with stdlib
    ``json.load``/``json.loads`` is not EAM *data* ingestion - the shared gate
    must not open on bare stdlib parsing (both siblings agree)."""
    ctx = _nb_ctx(
        "import json\n"
        "with open(path) as f:\n"
        "    cfg = json.load(f)\n"
        "payload = json.dumps(cfg)\n"
        "parsed = json.loads(response.text)\n"
    )
    assert notebook_validates_json_payloads(ctx).status is Status.NA
    assert nb_eam_ingest(ctx).status is Status.NA


# =============================================================================
# registration
# =============================================================================

def test_the_three_refs_are_registered_once_each_with_the_agreed_metadata():
    by_ref = {spec.ref: spec for spec in REGISTRY if spec.ref in {"5.3.3", "5.3.10", "5.5.8"}}
    assert set(by_ref) == {"5.3.3", "5.3.10", "5.5.8"}
    assert [spec.ref for spec in REGISTRY].count("5.3.3") == 1
    assert [spec.ref for spec in REGISTRY].count("5.3.10") == 1
    assert [spec.ref for spec in REGISTRY].count("5.5.8") == 1
    assert {spec.id for spec in by_ref.values()} == {
        "NB-BUSINESS-RULE", "NB-NULL-PROPAGATION", "NB-JSON-VALIDATION",
    }
    for spec in by_ref.values():
        assert spec.pillar is Pillar.DATA_QUALITY
        assert spec.scope is Scope.NOTEBOOK
        assert spec.severity is Severity.MEDIUM
        assert spec.requires == frozenset({Resource.NOTEBOOK_DEFINITIONS})
