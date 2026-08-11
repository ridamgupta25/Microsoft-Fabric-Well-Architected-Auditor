"""Tests for 5.5.5 - NB-CATEGORICAL-DOMAIN.

The point asks whether categorical/enum columns are pinned to an expected set of
values so an invalid code cannot flow to Gold. It sits directly beside 5.5.7
(Boolean / Flag), which is easy to mistake it for, so the dedup pin matters as
much as the detection tests: a ``Y``/``N`` membership test satisfies 5.5.7 and
must **not** satisfy this point.

Like every control check here, a PASS means "an invalid code would be caught",
never "every code is valid" - the data itself is never read.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import (
    nb_categorical_domain,
    nb_flag_domain,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext

_PASS, _FAIL = 3, 0


def _nb_ctx(code: str, **workspace) -> CheckContext:
    definition = {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}
    return CheckContext(workspace=WorkspaceContext(id="w", **workspace), settings={},
                        obj_name="nb", obj=definition)


# -- detection ----------------------------------------------------------------

_LITERAL_CODE_SET = """
valid = df.filter(df.order_status.isin("NEW", "SHIPPED", "CANCELLED"))
valid.write.mode("append").saveAsTable("silver.orders")
"""

_NAMED_ALLOWLIST = """
VALID_STATUSES = {"NEW", "SHIPPED", "CANCELLED"}
clean = df.filter(df.order_status.isin(VALID_STATUSES))
clean.write.mode("append").saveAsTable("silver.orders")
"""

_REFERENCE_JOIN = """
unknown = df.join(dim_status, df.status_code == dim_status.code, "left_anti")
unknown.write.mode("append").saveAsTable("quarantine.bad_status")
df.write.mode("append").saveAsTable("silver.orders")
"""

_NO_VALIDATION = """
df = spark.read.parquet("abfss://x@y/z")
df.write.mode("overwrite").saveAsTable("silver.orders")
"""


def test_passes_on_an_inline_code_list():
    verdict = nb_categorical_domain(_nb_ctx(_LITERAL_CODE_SET))
    assert verdict.score == _PASS
    assert "explicit code list" in verdict.evidence


def test_passes_on_a_named_allowed_value_set():
    verdict = nb_categorical_domain(_nb_ctx(_NAMED_ALLOWLIST))
    assert verdict.score == _PASS


def test_passes_on_a_reference_table_anti_join():
    verdict = nb_categorical_domain(_nb_ctx(_REFERENCE_JOIN))
    assert verdict.score == _PASS
    assert "reference/lookup table" in verdict.evidence


def test_evidence_says_the_data_itself_is_not_read():
    verdict = nb_categorical_domain(_nb_ctx(_REFERENCE_JOIN))
    assert "runtime outcome this check does not read" in verdict.evidence


def test_fails_when_nothing_constrains_the_values():
    assert nb_categorical_domain(_nb_ctx(_NO_VALIDATION)).score == _FAIL


def test_a_commented_out_validation_does_not_pass():
    code = ("# clean = df.filter(df.status.isin('NEW', 'SHIPPED'))\n"
            "df.write.mode('append').saveAsTable('silver.orders')")
    assert nb_categorical_domain(_nb_ctx(code)).score == _FAIL


# -- N/A, never FAIL ----------------------------------------------------------

def test_is_na_when_the_notebook_writes_nothing():
    verdict = nb_categorical_domain(
        _nb_ctx("df = spark.read.table('silver.orders')\ndisplay(df)"))
    assert verdict.status is Status.NA


def test_is_na_when_notebook_definitions_were_unreadable():
    ctx = _nb_ctx(_LITERAL_CODE_SET, unavailable={Resource.NOTEBOOK_DEFINITIONS})
    assert nb_categorical_domain(ctx).status is Status.NA


# -- the 5.5.7 dedup pin ------------------------------------------------------

_FLAG_ONLY = """
df = spark.read.json("abfss://raw@lake/orders")
clean = df.filter(df.is_active.isin("Y", "N"))
clean.write.mode("append").saveAsTable("silver.orders")
"""


def test_a_boolean_flag_test_satisfies_5_5_7():
    assert nb_flag_domain(_nb_ctx(_FLAG_ONLY)).score == _PASS


def test_a_boolean_flag_test_does_not_satisfy_5_5_5():
    """A two-valued Y/N test does not constrain a multi-valued code column.

    Both checks look at ``.isin(...)``; only the literals distinguish them. If
    this pin fails, one line of code is being credited to two checklist points.
    """
    verdict = nb_categorical_domain(_nb_ctx(_FLAG_ONLY))
    assert verdict.score == _FAIL
    assert "scored by 5.5.7" in verdict.evidence


def test_a_categorical_test_does_not_accidentally_report_as_a_flag():
    """The reverse direction: a real code list is not reported as a flag-only find."""
    verdict = nb_categorical_domain(_nb_ctx(_LITERAL_CODE_SET))
    assert "scored by 5.5.7" not in verdict.evidence
