"""Regression tests for the seven checks added for refs 4.1.2, 5.2.6, 5.3.1, 5.5.6, 14.1.x.

Every case here is a defect a review found in the first implementation. Each pairs
the misjudged input with the input that must keep working, so a future rewrite of a
detector cannot silently reintroduce the false PASS or false FAIL.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.data_management_quality.data_prep.automated import (
    _DEDUP_PATTERN,
    _KEY_QUALITY,
    _TYPE_CAST,
    nb_dedup,
    nb_key_quality,
    nb_type_cast,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    _normalised,
    complex_measures_use_variables,
    measures_not_duplicated,
    single_direction_relationships,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, WorkspaceContext

_WRITES = 'df.write.saveAsTable("t")\n'


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}


def _nb_ctx(code: str) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="nb", obj=_nb(code))


def _model_ctx(models: dict) -> CheckContext:
    workspace = WorkspaceContext(id="w", semantic_models=models)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


# --- detector precision -----------------------------------------------------

@pytest.mark.parametrize("source,expected,why", [
    ('print(f"Error during deduplication: {e}")', False, "a log message is not a control"),
    ('df.withColumn("UserID", row_number().over(w))', False, "ranking alone removes nothing"),
    ('df.withColumn("rn", row_number().over(w)).filter("rn = 1")', True, "ranking kept to rank 1"),
    ('dim = df.drop_duplicates()', True, "snake_case spelling"),
    ('df_raw.duplicated().sum()', True, "pandas duplicated()"),
    ('df.dropDuplicates()', True, "camelCase spelling"),
])
def test_dedup_detector(source: str, expected: bool, why: str):
    assert bool(_DEDUP_PATTERN.search(source)) is expected, why


@pytest.mark.parametrize("source,expected,why", [
    ("cols = [f.name for f in df.schema.fields if f.dataType in [IntegerType()]]",
     False, "type introspection casts nothing"),
    ("CREATE TABLE IF NOT EXISTS dim (\n  OrgID INT,\n  Name STRING\n)",
     True, "typed SQL DDL is explicit typing"),
    ("CREATE TABLE t AS SELECT * FROM raw -- loaded by DATE",
     False, "untyped CTAS that merely mentions a type name"),
    ('df.selectExpr("CAST(x AS DATE) as d")', True, "SQL cast via selectExpr"),
    ('spark.sql("SELECT CAST(a AS INT) FROM t")', True, "SQL cast via spark.sql"),
    ('StructType([StructField("Name", StringType(), True)])', True, "explicit schema"),
    ('df.withColumn("d", col("d").cast("date"))', True, "an explicit cast"),
])
def test_type_cast_detector(source: str, expected: bool, why: str):
    assert bool(_TYPE_CAST.search(source)) is expected, why


@pytest.mark.parametrize("source,expected,why", [
    ('posts.filter(posts["OwnerUserId"].isNotNull())', True, "isNotNull on a CamelCase Id"),
    ('df.filter(col("CustomerID").isNotNull())', True, "isNotNull on a CamelCase ID"),
    ('df.dropDuplicates(["Id"])', True, "dedup keyed on a column list"),
    ('df.dropna(subset=["user_id"])', True, "dropna on a key subset"),
    ('df.filter(col("valid").isNull())', False, "'valid' ends in 'id' but is not a key"),
    ('df.filter(col("monkey").isNull())', False, "'monkey' ends in 'key' but is not a key"),
    ('assert df.filter(col("CustomerKey").isNull()).count() == 0', True, "key null assertion"),
])
def test_key_quality_detector(source: str, expected: bool, why: str):
    assert bool(_KEY_QUALITY.search(source)) is expected, why


# --- the N/A-not-FAIL gate --------------------------------------------------

@pytest.mark.parametrize("evaluator", [nb_dedup, nb_type_cast, nb_key_quality])
def test_notebook_checks_are_na_when_the_notebook_writes_nothing(evaluator):
    assert evaluator(_nb_ctx("print(1)\n")).status is Status.NA


@pytest.mark.parametrize("evaluator", [nb_dedup, nb_type_cast, nb_key_quality])
def test_notebook_checks_ignore_commented_out_code(evaluator):
    """A commented-out control must not satisfy the check.

    ``binary`` leaves ``status`` for the engine to derive, so the score is what
    carries the verdict here.
    """
    commented = _WRITES + "# df.dropDuplicates()\n# df.cast('date')\n# col('user_id').isNull()\n"
    assert evaluator(_nb_ctx(commented)).score == 0


# --- semantic-model checks --------------------------------------------------

def test_models_without_relationships_are_excluded_from_the_denominator():
    models = {
        "empty": {"relationships": []},
        "single": {"relationships": [{"cross_filter": "oneDirection"}]},
        "bidi": {"relationships": [{"cross_filter": "bothDirections"}]},
    }
    verdict = single_direction_relationships(_model_ctx(models))
    assert "1 of 2" in verdict.evidence


def test_relationship_check_is_na_when_no_model_declares_one():
    verdict = single_direction_relationships(_model_ctx({"m": {"relationships": []}}))
    assert verdict.status is Status.NA


def test_trivial_expressions_are_not_counted_as_duplicated_logic():
    """The constant 0 repeats across models by convention, not by copy-paste."""
    models = {f"m{i}": {"measures": [{"expression": "0"}]} for i in range(5)}
    assert measures_not_duplicated(_model_ctx(models)).status is Status.NA


def test_pretty_printing_does_not_make_a_measure_look_complex():
    """Length is judged on collapsed whitespace, so indentation cannot inflate it."""
    padded = "CALCULATE(\n" + "    \n" * 200 + "    SUM(t[x])\n)"
    assert len(padded) > 400
    assert len(_normalised(padded)) < 400
    models = {"m": {"measures": [{"expression": padded}]}}
    assert complex_measures_use_variables(_model_ctx(models)).status is Status.NA


def test_a_column_named_var_is_not_a_variable_declaration():
    """A loose 'VAR' substring matches a column called 'Var Amount'."""
    long_no_var = "CALCULATE(SUM(t[Var Amount]), " + "FILTER(t, t[a] = 1), " * 25 + "ALL(t))"
    assert len(_normalised(long_no_var)) > 400
    models = {"m": {"measures": [{"expression": long_no_var}]}}
    assert complex_measures_use_variables(_model_ctx(models)).score == 0
