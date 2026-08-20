"""5.1.9 and 5.5.4 - a weak trigger must not produce a confident zero.

Two reviewer comments, one shared defect:

* *"Data-quality result is computed but never raised on … observed that for some
  notebooks data quality was not computed."*
* *"No PII was detected but the check still failed for notebook ApplyStandards."*

Both checks had a loose entry test followed by a hard 0. The entry test decides
"this check applies here"; when it is wrong, the notebook gets an alarming
failure - "bad data flows downstream silently", "raw PII is carried through
unchanged" - about something that is not happening.

5.1.9 matched any identifier containing error/invalid/duplicate, so a variable
named ``error_df`` or the line ``if df.count() > 0:`` counted as a data-quality
evaluation. 5.5.4 wrapped every PII term in ``\\w*``, so any word merely
containing one matched - a notebook standardising column names hits
``account_number`` in a rename list and is judged to be handling personal data.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import (
    notebook_dq_failure_halts_run,
)
from auditfast.core.check.security.data_prep.automated import notebook_pii_is_tokenised
from auditfast.core.enums import Status
from auditfast.core.models import WorkspaceContext

from .fixtures.builders import notebook_ctx


def _dq(source: str):
    return notebook_dq_failure_halts_run(notebook_ctx(source))


def _pii(source: str, tables: dict | None = None):
    workspace = WorkspaceContext(id="w", tables=tables or {})
    return notebook_pii_is_tokenised(notebook_ctx(source, workspace=workspace))


# ===========================================================================
# 5.1.9 - the trigger must be a real data-quality evaluation
# ===========================================================================

def test_a_variable_named_error_df_is_not_a_quality_evaluation():
    """The reviewer's case: no DQ was computed, yet the notebook was failed."""
    verdict = _dq(
        "error_df = spark.read.parquet('Files/errors')\n"
        "error_df.write.mode('overwrite').saveAsTable('bronze_errors')\n"
    )
    assert verdict.status is Status.NA


def test_an_ordinary_row_count_guard_is_not_a_quality_evaluation():
    """``if df.count() > 0`` is how everyone skips an empty batch."""
    verdict = _dq(
        "df = spark.read.parquet('Files/in')\n"
        "if df.count() > 0:\n"
        "    df.write.saveAsTable('silver_orders')\n"
    )
    assert verdict.status is Status.NA


def test_a_function_named_validate_something_is_not_enough():
    verdict = _dq(
        "def validate_schema(df):\n"
        "    return df\n"
        "df = validate_schema(spark.read.parquet('Files/in'))\n"
    )
    assert verdict.status is Status.NA


def test_a_bad_row_count_that_is_never_compared_is_still_an_evaluation():
    """A *count* is a measurement, even before anything is done with it.

    This is the boundary that matters: ``invalid_count = df.filter(...).count()``
    genuinely measures quality, so a notebook that then only prints it is the
    real finding. ``error_df = spark.read.parquet(...)`` is an ordinary
    dataframe and is not.
    """
    verdict = _dq(
        "invalid_count = df.filter(col('id').isNull()).count()\n"
        "print(f'invalid: {invalid_count}')\n"
    )
    assert verdict.score == 0


# --- genuine evaluations, correctly judged ---------------------------------

def test_a_compared_bad_row_count_with_no_halt_is_the_real_finding():
    verdict = _dq(
        "invalid_count = df.filter(col('id').isNull()).count()\n"
        "if invalid_count > 0:\n"
        "    print('problems found')\n"
        "df.write.saveAsTable('silver_orders')\n"
    )
    assert verdict.score == 0
    assert "bad-row count" in verdict.evidence


def test_a_compared_count_that_raises_scores_full():
    verdict = _dq(
        "invalid_count = df.filter(col('id').isNull()).count()\n"
        "if invalid_count > 0:\n"
        "    raise ValueError('bad rows')\n"
    )
    assert verdict.score == 3


def test_great_expectations_result_never_checked_is_a_finding():
    """GE's run() does not raise - the result has to be read.

    learn: validation_definition.run() returns a result whose .success nobody is
    obliged to inspect, which is exactly how this goes wrong in practice.
    """
    verdict = _dq(
        "import great_expectations as gx\n"
        "result = validation_definition.run(batch_parameters=params)\n"
        "print(result)\n"
    )
    assert verdict.score == 0
    assert "validation framework" in verdict.evidence


def test_great_expectations_result_asserted_scores_full():
    verdict = _dq(
        "import great_expectations as gx\n"
        "result = validation_definition.run(batch_parameters=params)\n"
        "assert result.success, 'data quality failed'\n"
    )
    assert verdict.score == 3


def test_pydeequ_verification_suite_is_recognised():
    verdict = _dq(
        "from pydeequ.verification import VerificationSuite\n"
        "checkResult = VerificationSuite(spark).onData(df).addCheck(check).run()\n"
        "df_res = VerificationResult.checkResultsAsDataFrame(spark, checkResult)\n"
        "if df_res.filter(\"check_status != 'Success'\").count() > 0:\n"
        "    raise Exception('deequ failed')\n"
    )
    assert verdict.score == 3


def test_soda_scan_without_a_halt_is_a_finding():
    verdict = _dq(
        "scan = Scan()\n"
        "scan.execute()\n"
        "print(scan.get_scan_results())\n"
    )
    assert verdict.score == 0


def test_notebook_exit_is_credited_but_not_a_full_stop():
    """notebookutils.notebook.exit() ends the run as SUCCEEDED.

    The pipeline activity therefore succeeds, and progression stops only if the
    caller inspects the returned value - so it scores 2, not 3.
    """
    verdict = _dq(
        "invalid_count = df.filter(col('id').isNull()).count()\n"
        "if invalid_count > 0:\n"
        "    notebookutils.notebook.exit('FAILED')\n"
    )
    assert verdict.score == 2


def test_an_assertion_alone_is_both_evaluation_and_halt():
    verdict = _dq("assert df.filter(col('id').isNull()).count() == 0\n")
    assert verdict.score == 3


# ===========================================================================
# 5.5.4 - the trigger must be a real column reference
# ===========================================================================

def test_a_pii_word_inside_a_longer_identifier_does_not_trigger():
    """The reviewer's case: ApplyStandards handles no PII but was scored 0."""
    verdict = _pii(
        "renames = {'cust_first_name_raw_staging_col': 'x'}\n"
        "df = df.withColumnRenamed('a', 'b')\n"
    )
    assert verdict.status is Status.NA or verdict.score != 0


def test_a_column_rename_list_is_not_pii_processing():
    verdict = _pii(
        "STANDARD_COLUMNS = ['id', 'created_at', 'updated_at']\n"
        "df = df.select(*STANDARD_COLUMNS)\n"
    )
    assert verdict.status is Status.NA


def test_a_quoted_pii_column_does_trigger():
    verdict = _pii("df = df.select('email_address', 'order_id')\n")
    assert verdict.status is not Status.NA


def test_an_attribute_style_pii_reference_triggers():
    verdict = _pii("df = df.withColumn('x', df.phone_number)\n")
    assert verdict.status is not Status.NA


# --- grading -------------------------------------------------------------

def test_masked_and_validated_scores_full():
    verdict = _pii(
        "df = df.withColumn('email', sha2(col('email'), 256))\n"
        "df = df.filter(col('email').rlike('@'))\n"
    )
    assert verdict.score == 3


def test_unmasked_pii_is_partial_not_zero():
    """The floor is 1: masking may live in a view or procedure we cannot read."""
    verdict = _pii("df = df.select('email', 'ssn').write.saveAsTable('silver_people')\n")
    assert verdict.score == 1
    assert "not readable here" in verdict.evidence


def test_a_warehouse_masked_column_is_credited():
    """Dynamic Data Masking protects the column whatever the notebook does.

    The crawl already reads ``is_masked`` from ``sys.columns``; without
    consulting it, a notebook reading an already-protected column would be
    failed for not masking it a second time.
    """
    tables = {"dbo.customer": {"columns": [
        {"name": "email", "type": "varchar(200)", "is_masked": True},
    ]}}
    verdict = _pii("df = spark.sql('SELECT email FROM customer')\n", tables=tables)
    assert verdict.score == 2
    assert "Dynamic Data Masking" in verdict.evidence


def test_no_masking_metadata_does_not_manufacture_credit():
    """An unreadable SQL endpoint is not evidence that a column is masked."""
    verdict = _pii("df = spark.sql('SELECT email FROM customer')\n", tables={})
    assert verdict.score == 1
