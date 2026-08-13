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
    spark_partition_pruning,
    spark_pool,
    spark_profile,
    spark_repartition,
    spark_runtime,
    spark_select,
    spark_ui_review,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _nb(code: str) -> dict:
    """A minimal ipynb-style notebook definition with one code cell."""
    return {"cells": [{"cell_type": "code", "source": code}]}


def _ctx(obj, settings=None) -> CheckContext:
    return CheckContext(
        workspace=WorkspaceContext(id="w"), settings=settings or {}, obj_name="nb", obj=obj,
    )


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


def test_merge_ignores_sql_commented_delete():
    # A real one-time INSERT plus a SQL ``--`` commented-out DELETE is not the
    # sequential DELETE+INSERT upsert anti-pattern — the DELETE is disabled.
    v = delta_merge(_ctx(_nb(
        "spark.sql('''\nINSERT INTO ctl SELECT * FROM src;\n-- DELETE from ctl;\n''')"
    )))
    assert v.status is Status.NA


def test_merge_ignores_python_commented_dml():
    v = delta_merge(_ctx(_nb(
        "# spark.sql('DELETE FROM t')\n# spark.sql('INSERT INTO t SELECT * FROM s')"
    )))
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


# -- SPARK-REPARTITION --------------------------------------------------------

def test_write_with_repartition_passes():
    v = spark_repartition(_ctx(_nb(
        "df.repartition(16, 'load_date').write.format('delta').saveAsTable('sales')"
    )))
    assert v.score == 3
    assert "repartition" in v.evidence


def test_write_from_assigned_repartitioned_dataframe_passes():
    v = spark_repartition(_ctx(_nb(
        "df_balanced = df.repartition(200, 'year', 'month')\n"
        "(df_balanced\n"
        " .write\n"
        " .format('delta')\n"
        " .saveAsTable('rides_delta'))"
    )))
    assert v.score == 3
    assert "repartition" in v.evidence


def test_optimize_write_with_explicit_bin_size_passes():
    v = spark_repartition(_ctx(_nb(
        'spark.conf.set("spark.microsoft.delta.optimizeWrite.enabled", "true")\n'
        'spark.conf.set("spark.microsoft.delta.optimizeWrite.binSize", "1073741824")\n'
        'df.write.format("delta").partitionBy("Year", "Quarter").save("Tables/sales")'
    )))
    assert v.score == 3
    assert "1 GiB" in v.evidence
    assert "partitionBy(Year, Quarter)" in v.evidence


def test_partition_by_without_file_sizing_is_partial():
    v = spark_repartition(_ctx(_nb(
        'df.write.format("delta").partitionBy("load_date").save("Tables/sales")'
    )))
    assert v.score == 2
    assert "partitionBy(load_date)" in v.evidence
    assert "file-size" in v.evidence


def test_window_partition_by_does_not_count_as_write_partitioning():
    v = spark_repartition(_ctx(_nb(
        'window = Window.partitionBy("customer_key").orderBy("updated_at")\n'
        'df.write.format("delta").mode("overwrite").saveAsTable("customers")'
    )))
    assert v.score == 0
    assert "default partitioning" in v.evidence


def test_optimize_write_without_explicit_bin_size_is_partial():
    v = spark_repartition(_ctx(_nb(
        'spark.conf.set("spark.microsoft.delta.optimizeWrite.enabled", "true")\n'
        'df.write.format("delta").save("Tables/sales")'
    )))
    assert v.score == 2
    assert "Optimize Write is enabled" in v.evidence


def test_column_coalesce_does_not_count_as_output_coalescing():
    v = spark_repartition(_ctx(_nb(
        'df = df.withColumn("name", F.coalesce(F.col("name"), F.lit("unknown")))\n'
        'spark.conf.set("spark.microsoft.delta.optimizeWrite.enabled", "true")\n'
        'spark.conf.set("spark.microsoft.delta.autoOptimize.autoCompact", "true")\n'
        'df.write.format("delta").mode("overwrite").saveAsTable("customers")'
    )))
    assert v.score == 2
    assert "strategy: Optimize Write is enabled" in v.evidence
    assert "Optimize Write is enabled" in v.evidence


def test_write_without_partition_strategy_fails():
    v = spark_repartition(_ctx(_nb(
        "df.write.format('delta').mode('overwrite').saveAsTable('sales')"
    )))
    assert v.score == 0
    assert "default partitioning" in v.evidence
    assert "partitionBy" in v.evidence
    assert "Optimize Write" in v.evidence


def test_repartition_check_is_na_without_a_write():
    v = spark_repartition(_ctx(_nb("df = spark.table('sales')")))
    assert v.status is Status.NA


def test_commented_out_repartition_does_not_pass():
    v = spark_repartition(_ctx(_nb(
        "# df = df.repartition(16, 'load_date')\n"
        "df.write.format('delta').saveAsTable('sales')"
    )))
    assert v.score == 0


def test_disabled_or_commented_optimize_write_does_not_pass():
    v = spark_repartition(_ctx(_nb(
        '# spark.conf.set("spark.microsoft.delta.optimizeWrite.enabled", "true")\n'
        'spark.conf.set("spark.microsoft.delta.optimizeWrite.enabled", "false")\n'
        'df.write.format("delta").save("Tables/sales")'
    )))
    assert v.score == 0


def test_repartition_check_is_na_when_definitions_are_unavailable():
    ctx = CheckContext(
        workspace=WorkspaceContext(id="w", unavailable={Resource.NOTEBOOK_DEFINITIONS}),
        settings={},
        obj_name="nb",
        obj=_nb("df.write.saveAsTable('sales')"),
    )
    v = spark_repartition(ctx)
    assert v.status is Status.NA


# -- SPARK-SELECT --------------------------------------------------------------

def test_select_star_fails():
    v = spark_select(_ctx(_nb("spark.sql('SELECT * FROM t')")))
    assert v.score == 0


def test_explicit_projection_passes():
    v = spark_select(_ctx(_nb("spark.sql('SELECT id, name FROM t')")))
    assert v.score == 3


# -- New performance evidence checks -----------------------------------------

def test_partition_pruning_passes_with_filter():
    settings = {"partition_columns": {"sales": ["load_date"]}}
    v = spark_partition_pruning(_ctx(
        _nb("spark.sql('SELECT id FROM sales WHERE load_date = \'2026-01-01\'')"), settings,
    ))
    assert v.score == 3


def test_partition_pruning_fails_for_unfiltered_select_star():
    settings = {"partition_columns": {"sales": ["load_date"]}}
    v = spark_partition_pruning(_ctx(_nb("spark.sql('SELECT * FROM sales')"), settings))
    assert v.score == 0


def test_partition_pruning_is_na_without_partition_metadata():
    v = spark_partition_pruning(_ctx(_nb("print('hello')")))
    assert v.status is Status.NA
    assert "Notebook 'nb'" in v.evidence
    assert "partition_columns" in v.evidence


def test_partition_pruning_checks_each_query_independently():
    settings = {"partition_columns": {"sales": ["load_date"]}}
    code = """
spark.sql('SELECT id FROM sales WHERE load_date = \'2026-01-01\'')
spark.sql('SELECT * FROM sales')
"""
    v = spark_partition_pruning(_ctx(_nb(code), settings))
    assert v.score == 0


def _nb_with_runtime(version: str) -> dict:
    notebook = _nb("print('runtime')")
    notebook["cells"][0]["outputs"] = [{"text": f"Apache-Spark/{version} Delta-Lake/3.2"}]
    return notebook


def _nb_with_printed_spark_version(version: str) -> dict:
    notebook = _nb("print(spark.version)")
    notebook["cells"][0]["outputs"] = [{"text": f"{version}\n"}]
    return notebook


def test_current_runtime_passes():
    assert spark_runtime(_ctx(_nb_with_runtime("3.5.5"))).score == 3


def test_printed_spark_version_passes():
    assert spark_runtime(_ctx(_nb_with_printed_spark_version("3.5.5.5.4.20260403.6"))).score == 3


def test_printed_spark_version_takes_precedence_over_delta_history():
    notebook = _nb_with_printed_spark_version("3.5.5.5.4.20260403.6")
    notebook["cells"].append({
        "cell_type": "code",
        "source": "spark.sql('DESCRIBE HISTORY t')",
        "outputs": [{"text": "Apache-Spark/3.4.1 Delta-Lake/3.2"}],
    })
    v = spark_runtime(_ctx(notebook))
    assert v.score == 3
    assert "3.5.5" in v.evidence


def test_unsupported_runtime_fails():
    assert spark_runtime(_ctx(_nb_with_runtime("3.4.1"))).score == 0


def test_runtime_is_na_without_captured_version():
    assert spark_runtime(_ctx(_nb("print(spark.version)"))).status is Status.NA


def test_bound_environment_runtime_is_primary_evidence():
    notebook = _nb_with_runtime("3.4.1")
    notebook["_auditfast_environment"] = {
        "name": "Validation Environment",
        "runtime_version": "1.3",
    }
    verdict = spark_runtime(_ctx(notebook))
    assert verdict.score == 3
    assert "Validation Environment" in verdict.evidence
    assert "Spark 3.5.0" in verdict.evidence


def test_bound_old_environment_runtime_fails():
    notebook = _nb("print('runtime')")
    notebook["_auditfast_environment"] = {"runtime_version": "1.2"}
    assert spark_runtime(_ctx(notebook)).score == 0


def test_unknown_bound_environment_runtime_is_na():
    notebook = _nb("print('runtime')")
    notebook["_auditfast_environment"] = {"runtime_version": "9.9"}
    assert spark_runtime(_ctx(notebook)).status is Status.NA


def _monitored_nb(*, usage=None, advice=None, stages=None) -> dict:
    notebook = _nb("df = spark.table('sales')")
    monitoring = {}
    if usage is not None:
        monitoring["resource_usage"] = usage
    if advice is not None:
        monitoring["advice"] = advice
    if stages is not None:
        monitoring["stages"] = stages
    notebook["_auditfast_monitoring"] = monitoring
    return notebook


def test_pool_sizing_passes_for_healthy_utilization():
    v = spark_pool(_ctx(_monitored_nb(usage={
        "duration": 600_000, "idleTime": 60_000,
        "coreEfficiency": 0.75, "capacityExceeded": False,
    })))
    assert v.score == 3


def test_pool_sizing_fails_for_underutilization():
    v = spark_pool(_ctx(_monitored_nb(usage={
        "duration": 600_000, "idleTime": 300_000,
        "coreEfficiency": 0.2, "capacityExceeded": False,
    })))
    assert v.score == 0
    assert "Notebook 'nb'" in v.evidence
    assert "minimum=0.5000" in v.evidence
    assert "maximum=0.3000" in v.evidence


def test_pool_sizing_is_na_without_metrics():
    assert spark_pool(_ctx(_nb("print('hello')"))).status is Status.NA


def test_spark_ui_passes_without_detected_issues():
    v = spark_ui_review(_ctx(_monitored_nb(advice=[], stages=[{
        "diskBytesSpilled": 0, "shuffleWriteBytes": 10_000,
    }])))
    assert v.score == 3


def test_spark_ui_fails_on_skew_or_spill():
    v = spark_ui_review(_ctx(_monitored_nb(
        advice=[{"name": "Data skew detected"}],
        stages=[{"diskBytesSpilled": 1, "shuffleWriteBytes": 0}],
    )))
    assert v.score == 0
    assert "skew" in v.evidence and "spill" in v.evidence


def test_spark_ui_is_na_without_monitoring():
    assert spark_ui_review(_ctx(_nb("df.explain()"))).status is Status.NA


def test_profile_passes_for_healthy_long_running_application():
    v = spark_profile(_ctx(_monitored_nb(
        usage={"duration": 600_000}, advice=[], stages=[],
    )))
    assert v.score == 3


def test_profile_passes_for_long_running_application_even_when_ui_has_issues():
    """3.5.11 scores *coverage*; the issues themselves belong to 3.5.1.

    Both checks call the same ``performance_issues()`` helper, so scoring the
    issues here failed one notebook twice under two refs for a single underlying
    problem. Profiling data being *available* is what this one asks.
    """
    v = spark_profile(_ctx(_monitored_nb(
        usage={"duration": 600_000},
        advice=[{"description": "Shuffle skew requires optimization"}], stages=[],
    )))
    assert v.score == 3


def test_profile_fails_for_long_running_application_without_profiling_evidence():
    """No metrics at all is a real, scoreable gap: the job cannot be reviewed."""
    v = spark_profile(_ctx(_monitored_nb(
        usage={"duration": 600_000},
    )))
    assert v.score == 0
    assert "cannot be reviewed" in v.evidence


def test_profile_and_spark_ui_do_not_both_score_the_same_issue():
    """The dedup boundary, pinned in both directions.

    One long-running notebook with a real skew problem: 3.5.1 must fail it (the
    issue is real), 3.5.11 must pass it (the metrics needed to find that issue
    exist, which is all this check claims).
    """
    from auditfast.core.check.performance_capacity.data_prep.automated import spark_ui_review

    nb = _monitored_nb(
        usage={"duration": 600_000},
        advice=[{"description": "Shuffle skew requires optimization"}], stages=[],
    )
    assert spark_ui_review(_ctx(nb)).score == 0, "3.5.1 owns the issue"
    assert spark_profile(_ctx(nb)).score == 3, "3.5.11 owns only the coverage"


def test_profile_is_na_for_short_application():
    v = spark_profile(_ctx(_monitored_nb(
        usage={"duration": 10_000}, advice=[], stages=[],
    )))
    assert v.status is Status.NA


# -- PL-COPY-PARALLEL (pipeline) ----------------------------------------------

def _pipeline(*activities: dict) -> dict:
    return {"properties": {"activities": list(activities)}}


def test_copy_with_parallelism_passes():
    pipe = _pipeline({"type": "Copy", "typeProperties": {"parallelCopies": 8}})
    v = copy_parallelism(_ctx(pipe))
    assert v.score == 3


def test_single_copy_without_parallelism_is_na():
    # A lone untuned Copy relies on Auto DIU/parallelCopies — N/A, not a finding.
    pipe = _pipeline({"type": "Copy", "typeProperties": {}})
    v = copy_parallelism(_ctx(pipe))
    assert v.status is Status.NA


def test_no_copy_activity_is_na():
    pipe = _pipeline({"type": "Notebook", "typeProperties": {}})
    v = copy_parallelism(_ctx(pipe))
    assert v.status is Status.NA
