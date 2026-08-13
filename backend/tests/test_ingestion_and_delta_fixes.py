"""Regression pins for the 5.2.8 / 3.3.5 / 3.3.6 / 3.3.7 validation round.

Two defects, both of the same family the check library keeps hitting:

* **5.2.8 NB-SOURCE-METADATA** judged *every* notebook that wrote a table, so a
  Gold aggregation reading a lakehouse table was failed for not recording
  "source metadata" it never had. Provenance only means something when the data
  came from outside the lakehouse, so the check is now gated on an external read.

* **3.3.5 / 3.3.6 / 3.3.7** (V-Order, table properties, retention) read the
  notebook with ``notebook_code``, which keeps ``#`` comments. A *commented-out*
  ``spark.conf.set(...vorder...)`` therefore counted as configuration - the
  precise false PASS ``executable_code`` exists to prevent.

Also pinned: the boundary between 5.2.8 and 1.2.3, in both directions, so a
future rewrite of either detector cannot quietly make them the same check.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.registry import REGISTRY
from auditfast.core.models import CheckContext, WorkspaceContext


def _spec(check_id: str):
    return next(s for s in REGISTRY if s.id == check_id)


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}


def _run(check_id: str, code: str):
    defn = _nb(code)
    ws = WorkspaceContext(id="ws", notebooks={"nb": defn})
    return _spec(check_id).fn(CheckContext(ws, {}, "nb", defn))


# -- 5.2.8 is scoped to ingestion ---------------------------------------------

_EXTERNAL = (
    "df = spark.read.csv('abfss://ws@onelake.dfs.fabric.microsoft.com/Files/raw.csv')\n"
    "df.write.saveAsTable('bronze_orders')\n"
)


def test_5_2_8_fails_an_ingestion_notebook_with_no_provenance():
    """The genuine finding the check exists for."""
    verdict = _run("NB-SOURCE-METADATA", _EXTERNAL)
    assert verdict.score == 0
    assert "provenance" in verdict.evidence


def test_5_2_8_passes_when_provenance_is_recorded():
    code = _EXTERNAL.replace(
        "df.write", "df = df.withColumn('ingestion_timestamp', current_timestamp())\ndf.write")
    assert _run("NB-SOURCE-METADATA", code).score == 3


def test_5_2_8_ignores_a_derived_notebook_that_reads_the_lakehouse():
    """A Gold aggregation has no external source, so it must not be failed.

    This is the false FAIL the old rule produced on every non-ingestion notebook
    that happened to write a table.
    """
    code = ("df = spark.read.table('silver.fact_loan')\n"
            "df.groupBy('region').sum('amount').write.saveAsTable('gold.loan_summary')\n")
    verdict = _run("NB-SOURCE-METADATA", code)
    assert verdict.score is None, "a derived notebook must be N/A, not FAIL"
    assert "no external source" in verdict.evidence


def test_5_2_8_ignores_a_notebook_that_writes_nothing():
    assert _run("NB-SOURCE-METADATA", "df = spark.read.csv('abfss://x/y.csv')\n").score is None


@pytest.mark.parametrize("source", [
    "spark.read.csv('abfss://ws@onelake/Files/a.csv')",
    "spark.read.format('jdbc').option('url', u).load()",
    "pd.read_excel('https://example.com/a.xlsx')",
    "requests.get('https://api.example.com/orders')",
])
def test_5_2_8_recognises_the_common_external_sources(source):
    verdict = _run("NB-SOURCE-METADATA", f"df = {source}\ndf.write.saveAsTable('t')\n")
    assert verdict.score == 0, f"{source} should read as an ingestion notebook"


def test_5_2_8_and_1_2_3_stay_distinct():
    """Different populations, different thresholds — pinned in both directions.

    A Bronze notebook that ingests externally is judged by both (intended). A
    Bronze notebook that only moves data *within* the lakehouse is 1.2.3's
    business alone, and a non-Bronze external ingestion is 5.2.8's alone.
    """
    internal_bronze = ("df = spark.read.table('staging.orders')\n"
                       "df.write.saveAsTable('bronze_orders')\n")
    assert _run("NB-BRONZE-METADATA", internal_bronze).score == 0, "1.2.3 still judges it"
    assert _run("NB-SOURCE-METADATA", internal_bronze).score is None, "5.2.8 must not"

    external_gold = ("df = spark.read.csv('https://example.com/rates.csv')\n"
                     "df.write.saveAsTable('gold.fx_rates')\n")
    assert _run("NB-SOURCE-METADATA", external_gold).score == 0, "5.2.8 judges it"
    assert _run("NB-BRONZE-METADATA", external_gold).score is None, "1.2.3 must not"


# -- 3.3.x must not be satisfied by a comment ---------------------------------

_DELTA_WRITE = "df.write.format('delta').mode('overwrite').saveAsTable('t')\n"


@pytest.mark.parametrize("check_id,setting", [
    ("DELTA-VORDER", "spark.conf.set('spark.sql.parquet.vorder.enabled', 'true')"),
    ("DELTA-TBLPROPS", "spark.conf.set('spark.databricks.delta.optimizeWrite.enabled', 'true')"),
    ("DELTA-RETENTION", "spark.conf.set('delta.logRetentionDuration', 'interval 30 days')"),
])
def test_3_3_x_is_not_satisfied_by_a_commented_out_setting(check_id, setting):
    """A control that is commented out is absent — the library's standing rule."""
    live = _run(check_id, f"{setting}\n{_DELTA_WRITE}")
    assert live.score == 3, f"{check_id} should pass on the real setting"

    commented = _run(check_id, f"# {setting}\n{_DELTA_WRITE}")
    assert commented.score != 3, f"{check_id} passed on a commented-out setting"


@pytest.mark.parametrize("check_id", ["DELTA-VORDER", "DELTA-RETENTION"])
def test_unset_delta_options_are_na_not_a_finding(check_id):
    """The effective value is set by Fabric defaults, so absence is unknowable."""
    assert _run(check_id, _DELTA_WRITE).score is None


@pytest.mark.parametrize("check_id", ["DELTA-VORDER", "DELTA-TBLPROPS", "DELTA-RETENTION"])
def test_delta_checks_ignore_a_notebook_that_writes_no_delta(check_id):
    assert _run(check_id, "print('hello')\n").score is None
