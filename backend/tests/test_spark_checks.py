"""Unit tests for the promoted Spark/Delta notebook & pipeline checks.

Each check is a pure function of a notebook/pipeline definition, so these build
synthetic definitions and assert the verdict directly — no provider, no tenant.
"""
from __future__ import annotations

from auditfast.core.check.performance_capacity.data_prep.automated import (
    copy_parallelism,
    delta_merge,
    delta_optimize,
    delta_zorder,
    spark_env,
    spark_libpin,
    spark_select,
)
from auditfast.core.enums import Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _nb(code: str) -> dict:
    """A minimal ipynb-style notebook definition with one code cell."""
    return {"cells": [{"cell_type": "code", "source": code}]}


def _ctx(obj) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={}, obj_name="nb", obj=obj)


# -- DELTA-MERGE ---------------------------------------------------------------

def test_merge_single_statement_passes():
    v = delta_merge(_ctx(_nb("df = spark.sql('MERGE INTO gold.t USING s ON ...')")))
    assert v.score == 3


def test_merge_sequential_dml_fails():
    v = delta_merge(_ctx(_nb("spark.sql('DELETE FROM t WHERE 1=1')\nspark.sql('INSERT INTO t SELECT * FROM s')")))
    assert v.score == 0


def test_merge_absent_is_na():
    v = delta_merge(_ctx(_nb("df = spark.read.table('t')")))
    assert v.status is Status.NA


# -- DELTA-OPTIMIZE ------------------------------------------------------------

def test_optimize_after_write_passes():
    v = delta_optimize(_ctx(_nb("df.write.saveAsTable('t')\nspark.sql('OPTIMIZE t')")))
    assert v.score == 3


def test_write_without_optimize_fails():
    v = delta_optimize(_ctx(_nb("df.write.saveAsTable('t')")))
    assert v.score == 0


def test_optimize_no_write_is_na():
    v = delta_optimize(_ctx(_nb("df = spark.read.table('t').show()")))
    assert v.status is Status.NA


# -- DELTA-ZORDER --------------------------------------------------------------

def test_zorder_with_optimize_passes():
    v = delta_zorder(_ctx(_nb("spark.sql('OPTIMIZE t ZORDER BY (customer_id)')")))
    assert v.score == 3


def test_optimize_without_zorder_fails():
    v = delta_zorder(_ctx(_nb("spark.sql('OPTIMIZE t')")))
    assert v.score == 0


def test_zorder_no_optimize_is_na():
    v = delta_zorder(_ctx(_nb("df.write.saveAsTable('t')")))
    assert v.status is Status.NA


# -- SPARK-LIBPIN --------------------------------------------------------------

def test_pinned_libraries_full_coverage():
    v = spark_libpin(_ctx(_nb("%pip install pandas==2.2.0 numpy==1.26.0")))
    assert v.score == 3


def test_unpinned_library_partial():
    v = spark_libpin(_ctx(_nb("%pip install pandas==2.2.0 requests")))
    assert v.coverage == 0.5


def test_no_installs_is_na():
    v = spark_libpin(_ctx(_nb("import pandas")))
    assert v.status is Status.NA


def test_bare_pip_install_is_scored_not_na():
    """A bare `pip install` (no magic) must be evaluated for pinning, not skipped as N/A."""
    v = spark_libpin(_ctx(_nb("pip install azure-kusto-data azure-identity")))
    assert v.score == 0  # both unpinned


def test_wheel_url_install_is_flagged_unpinned():
    v = spark_libpin(_ctx(_nb("%pip install https://aka.ms/chat_magics-0.0.0-py3-none-any.whl")))
    assert v.score == 0


def test_subprocess_pip_install_is_flagged():
    v = spark_libpin(_ctx(_nb('subprocess.run(["-m", "pip", "install", "build"])')))
    assert v.score == 0


# -- SPARK-ENV -----------------------------------------------------------------

def test_inline_pip_is_flagged():
    v = spark_env(_ctx(_nb("!pip install some-lib==1.0")))
    assert v.score == 1


def test_no_inline_install_passes():
    v = spark_env(_ctx(_nb("import pandas as pd")))
    assert v.score == 3


# -- SPARK-SELECT --------------------------------------------------------------

def test_select_star_fails():
    v = spark_select(_ctx(_nb("spark.sql('SELECT * FROM t')")))
    assert v.score == 0


def test_explicit_projection_passes():
    v = spark_select(_ctx(_nb("spark.sql('SELECT id, name FROM t')")))
    assert v.score == 3


# -- PL-COPY-PARALLEL (pipeline) ----------------------------------------------

def _pipeline(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


def test_copy_with_parallelism_passes():
    pipe = _pipeline({"type": "Copy", "typeProperties": {"parallelCopies": 8}})
    v = copy_parallelism(_ctx(pipe))
    assert v.score == 3


def test_copy_without_parallelism_fails():
    pipe = _pipeline({"type": "Copy", "typeProperties": {}})
    v = copy_parallelism(_ctx(pipe))
    assert v.score == 0


def test_no_copy_activity_is_na():
    pipe = _pipeline({"type": "Notebook", "typeProperties": {}})
    v = copy_parallelism(_ctx(pipe))
    assert v.status is Status.NA
