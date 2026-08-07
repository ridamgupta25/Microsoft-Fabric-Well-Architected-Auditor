"""Branch-matrix regression tests for the MLC Cat-1 data-quality checks (Utkarsh).

Each check gets a PASS input, a FAIL input, and an N/A input so a future rewrite
of a detector cannot silently reintroduce a false PASS/FAIL, and so the
N/A-not-FAIL invariant (no applicable work ⇒ N/A) stays enforced.

Verdict helpers set ``.score`` (3 = pass, 0 = fail); ``.status`` is assigned by
the engine later, except ``not_applicable`` which sets ``status = Status.NA``.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import (
    nb_business_rule,
    nb_completeness,
    nb_date_validation,
    nb_dq_halts,
    nb_dq_standardized,
    nb_enum_domain,
    nb_json_validate,
    nb_null_handling,
    nb_null_propagation,
    nb_numeric_validation,
    nb_sensitive_mask,
    nb_timeliness,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}


def _ctx(code: str) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="nb", obj=_nb(code))


def _ws_ctx(notebooks: dict[str, str]) -> CheckContext:
    ws = WorkspaceContext(id="w", notebooks={n: _nb(c) for n, c in notebooks.items()})
    return CheckContext(workspace=ws, settings={}, obj_name="w", obj=ws)


# -- 5.1.7 · NB-DQ-STANDARD ----------------------------------------------------

def test_dq_standardized_pass():
    ctx = _ws_ctx({"a": "import great_expectations as ge\nge.from_pandas(df)",
                   "b": "import great_expectations as ge\nge.from_pandas(df2)"})
    assert nb_dq_standardized(ctx).score == 3


def test_dq_standardized_mixed_is_not_full_score():
    ctx = _ws_ctx({"a": "import great_expectations as ge\nge.from_pandas(df)",
                   "b": "from pydeequ import VerificationSuite\nVerificationSuite(spark)",
                   "c": "def validate_data(df): return df"})
    assert nb_dq_standardized(ctx).score < 3


def test_dq_standardized_na_when_no_framework():
    assert nb_dq_standardized(_ws_ctx({"a": "df = spark.read.csv('x')"})).status is Status.NA


# -- 5.1.9 · NB-DQ-HALT --------------------------------------------------------

def test_dq_halt_pass():
    code = "result = validator.validate()\nif not result.success:\n    raise ValueError('bad data')"
    assert nb_dq_halts(_ctx(code)).score == 3


def test_dq_halt_fail():
    code = "import great_expectations as ge\nsuite = ge.ExpectationSuite()\nvalidator.validate()"
    assert nb_dq_halts(_ctx(code)).score == 0


def test_dq_halt_na():
    assert nb_dq_halts(_ctx("df = spark.read.csv('x')")).status is Status.NA


# -- 5.2.2 · NB-COMPLETENESS ---------------------------------------------------

def test_completeness_pass():
    code = ("files = mssparkutils.fs.ls('/lake/in')\n"
            "if len(files) != expected_count:\n    raise Exception('missing files')")
    assert nb_completeness(_ctx(code)).score == 3


def test_completeness_fail():
    assert nb_completeness(_ctx("df = spark.read.csv('/lake/in')")).score == 0


def test_completeness_na():
    assert nb_completeness(_ctx("x = 1 + 1")).status is Status.NA


# -- 5.2.3 · NB-TIMELINESS -----------------------------------------------------

def test_timeliness_pass():
    code = ("files = mssparkutils.fs.ls('/lake/in')\n"
            "for f in files:\n    if f.modificationTime < sla_cutoff:\n        raise Exception('stale')")
    assert nb_timeliness(_ctx(code)).score == 3


def test_timeliness_fail():
    assert nb_timeliness(_ctx("df = spark.read.json('/lake/in')")).score == 0


def test_timeliness_na():
    assert nb_timeliness(_ctx("x = 1 + 1")).status is Status.NA


# -- 5.2.7 · NB-NULL-HANDLING --------------------------------------------------

def test_null_handling_pass():
    code = "clean = df.filter(col('id').isNotNull())\nclean.write.saveAsTable('t')"
    assert nb_null_handling(_ctx(code)).score == 3


def test_null_handling_fail():
    assert nb_null_handling(_ctx("df.write.saveAsTable('t')")).score == 0


def test_null_handling_na():
    assert nb_null_handling(_ctx("df = spark.read.csv('x')")).status is Status.NA


# -- 5.3.3 · NB-BUSINESS-RULE --------------------------------------------------

def test_business_rule_pass():
    code = ("valid = df.filter(col('start_date') <= col('end_date'))\n"
            "valid.write.saveAsTable('t')")
    assert nb_business_rule(_ctx(code)).score == 3


def test_business_rule_fail():
    assert nb_business_rule(_ctx("df.write.saveAsTable('t')")).score == 0


def test_business_rule_na():
    assert nb_business_rule(_ctx("df = spark.read.csv('x')")).status is Status.NA


# -- 5.3.10 · NB-NULL-PROPAGATION ----------------------------------------------

def test_null_propagation_pass():
    code = ("j = a.join(b, 'k', 'left')\n"
            "j = j.filter(col('b_val').isNotNull())\nj.write.saveAsTable('t')")
    assert nb_null_propagation(_ctx(code)).score == 3


def test_null_propagation_fail():
    code = "j = a.join(b, 'k', 'left')\nj.write.saveAsTable('t')"
    assert nb_null_propagation(_ctx(code)).score == 0


def test_null_propagation_na_without_join_or_cast():
    assert nb_null_propagation(_ctx("df.write.saveAsTable('t')")).status is Status.NA


def test_null_propagation_na_without_write():
    assert nb_null_propagation(_ctx("j = a.join(b, 'k')")).status is Status.NA


# -- 5.5.1 · NB-DATE-VALIDATION ------------------------------------------------

def test_date_validation_pass():
    code = ("df = df.withColumn('d', to_date('s'))\n"
            "df = df.filter(col('d') <= current_date())")
    assert nb_date_validation(_ctx(code)).score == 3


def test_date_validation_fail():
    assert nb_date_validation(_ctx("df = df.withColumn('d', to_date('s'))")).score == 0


def test_date_validation_na():
    assert nb_date_validation(_ctx("x = 1 + 1")).status is Status.NA


# -- 5.5.2 · NB-NUMERIC-VALIDATION ---------------------------------------------

def test_numeric_validation_pass():
    code = "df = df.withColumn('amount', col('a').cast(DecimalType(18, 2)))"
    assert nb_numeric_validation(_ctx(code)).score == 3


def test_numeric_validation_fail():
    assert nb_numeric_validation(_ctx("total = df.select('amount', 'price')")).score == 0


def test_numeric_validation_na():
    assert nb_numeric_validation(_ctx("x = 1 + 1")).status is Status.NA


# -- 5.5.4 · NB-SENSITIVE-MASK -------------------------------------------------

def test_sensitive_mask_pass():
    code = "df = df.withColumn('email', sha2(col('email'), 256))"
    assert nb_sensitive_mask(_ctx(code)).score == 3


def test_sensitive_mask_fail():
    assert nb_sensitive_mask(_ctx("out = df.select('email', 'ssn')")).score == 0


def test_sensitive_mask_na():
    assert nb_sensitive_mask(_ctx("out = df.select('amount')")).status is Status.NA


# -- 5.5.5 · NB-ENUM-DOMAIN ----------------------------------------------------

def test_enum_domain_pass():
    code = "valid = df.filter(col('status').isin(['A', 'B', 'C']))\nvalid.write.saveAsTable('t')"
    assert nb_enum_domain(_ctx(code)).score == 3


def test_enum_domain_fail():
    assert nb_enum_domain(_ctx("df.write.saveAsTable('t')")).score == 0


def test_enum_domain_na():
    assert nb_enum_domain(_ctx("df = spark.read.csv('x')")).status is Status.NA


# -- 5.5.8 · NB-JSON-VALIDATE --------------------------------------------------

def test_json_validate_pass():
    code = "df = df.withColumn('payload', from_json(col('raw'), payload_schema))"
    assert nb_json_validate(_ctx(code)).score == 3


def test_json_validate_fail():
    assert nb_json_validate(_ctx("data = json.loads(raw_text)")).score == 0


def test_json_validate_na():
    assert nb_json_validate(_ctx("x = 1 + 1")).status is Status.NA
