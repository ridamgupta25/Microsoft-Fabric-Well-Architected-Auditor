"""Regression tests for two checks that could not judge a modern workspace.

**3.4.5 SPARK-RUNTIME** knew runtimes 1.1-1.3 only, so a workspace on Runtime
2.0 - the current recommendation - resolved to "not recognized" and reported
N/A. The best-configured estate was the one it could not judge. It also read
the runtime only from a *bound Environment*, so a notebook using the workspace
default (the commonest setup) had no runtime at all.

**3.6.2 NB-NO-CURSOR** searched notebook code for T-SQL cursor syntax. A Fabric
notebook is Spark, so ``DECLARE ... CURSOR`` cannot appear in one and three of
its five patterns could never fire. It now looks for the Spark equivalent:
pulling a distributed DataFrame onto the driver and looping over it.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_storage.automated import nb_no_cursor
from auditfast.core.check.performance_capacity.data_prep import _spark
from auditfast.core.check.performance_capacity.data_prep.automated import spark_runtime
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _nb(code: str, environment: dict | None = None) -> dict:
    definition = {"cells": [{"cell_type": "code", "source": code.splitlines(keepends=True)}]}
    if environment is not None:
        definition["_auditfast_environment"] = environment
    return definition


def _ctx(obj, *, spark_settings=None, settings=None, unavailable=None) -> CheckContext:
    workspace = WorkspaceContext(
        id="w",
        spark_settings=spark_settings or {},
        unavailable=unavailable or set(),
    )
    return CheckContext(workspace=workspace, settings=settings or {},
                        obj_name="nb", obj=obj)


# ---------------------------------------------------------------------------
# 3.4.5 - the runtime table, and the workspace-default fallback
# ---------------------------------------------------------------------------

def test_runtime_2_0_is_recognised():
    """The bug: Runtime 2.0 was absent, so the newest workspaces reported N/A."""
    assert _spark.fabric_runtime_to_spark("2.0") == (4, 1, 0)


def test_known_runtimes_map_to_their_documented_spark_version():
    assert _spark.fabric_runtime_to_spark("1.1") == (3, 3, 0)
    assert _spark.fabric_runtime_to_spark("1.2") == (3, 4, 0)
    assert _spark.fabric_runtime_to_spark("1.3") == (3, 5, 5)


def test_an_unknown_runtime_stays_unmapped():
    """A runtime newer than this code must not be guessed at."""
    assert _spark.fabric_runtime_to_spark("9.9") is None


def test_runtime_2_0_passes_the_minimum():
    verdict = spark_runtime(_ctx(_nb("df.show()", {"name": "Env", "runtime_version": "2.0"})))
    assert verdict.score == 3
    assert "Spark 4.1.0" in verdict.evidence


def test_retired_runtime_fails_the_minimum():
    verdict = spark_runtime(_ctx(_nb("df.show()", {"name": "Env", "runtime_version": "1.1"})))
    assert verdict.score == 0


def test_workspace_default_runtime_is_used_when_no_environment_is_bound():
    """The commonest setup: a notebook that binds to no named Environment."""
    verdict = spark_runtime(_ctx(
        _nb("df.show()"),
        spark_settings={"runtime_version": "1.3", "default_environment": ""},
    ))
    assert verdict.score == 3
    assert "workspace default runtime" in verdict.evidence


def test_a_bound_environment_outranks_the_workspace_default():
    verdict = spark_runtime(_ctx(
        _nb("df.show()", {"name": "Legacy", "runtime_version": "1.1"}),
        spark_settings={"runtime_version": "2.0"},
    ))
    assert verdict.score == 0          # judged on the Environment, not the default
    assert "Legacy" in verdict.evidence


def test_an_environment_without_a_runtime_falls_back_to_the_workspace_default():
    verdict = spark_runtime(_ctx(
        _nb("df.show()", {"name": "Env", "runtime_version": None}),
        spark_settings={"runtime_version": "2.0"},
    ))
    assert verdict.score == 3
    assert "workspace default" in verdict.evidence


def test_an_unrecognised_runtime_is_na_not_a_failure():
    verdict = spark_runtime(_ctx(
        _nb("df.show()"), spark_settings={"runtime_version": "9.9"}))
    assert verdict.status is Status.NA
    assert "must not read as out of date" in verdict.evidence


def test_no_runtime_anywhere_is_na():
    verdict = spark_runtime(_ctx(_nb("df.show()")))
    assert verdict.status is Status.NA
    assert "not readable" in verdict.evidence


# ---------------------------------------------------------------------------
# 3.6.2 - Spark row-by-row patterns, not T-SQL cursors
# ---------------------------------------------------------------------------

def test_loop_over_collect_is_flagged():
    code = """
rows = spark.table('silver.sales').collect()
for row in rows:
    process(row)
"""
    verdict = nb_no_cursor(_ctx(_nb(code)))
    assert verdict.score == 0
    assert "collected DataFrame" in verdict.evidence


def test_inline_loop_over_collect_is_flagged():
    verdict = nb_no_cursor(_ctx(_nb("for row in df.collect():\n    process(row)\n")))
    assert verdict.score == 0
    assert "loop over collect()" in verdict.evidence


def test_iterrows_is_flagged():
    verdict = nb_no_cursor(_ctx(_nb("for i, r in pdf.iterrows():\n    process(r)\n")))
    assert verdict.score == 0
    assert "iterrows" in verdict.evidence


def test_rdd_map_is_flagged():
    verdict = nb_no_cursor(_ctx(_nb("out = df.rdd.map(lambda r: transform(r))\n")))
    assert verdict.score == 0
    assert "rdd row map" in verdict.evidence


def test_set_based_code_passes():
    code = """
silver = spark.table('bronze.sales')
gold = silver.groupBy('customer_id').agg(sum('amount').alias('total'))
gold.write.saveAsTable('gold.customer_totals')
"""
    verdict = nb_no_cursor(_ctx(_nb(code)))
    assert verdict.score == 3
    assert "set-based" in verdict.evidence


def test_a_bare_collect_is_not_flagged():
    """collect() alone is legitimate for a small result - NB-COLLECT judges that.

    The signal here is the *iteration*, not the materialisation.
    """
    verdict = nb_no_cursor(_ctx(_nb("count = df.collect()[0][0]\nprint(count)\n")))
    assert verdict.score == 3


def test_a_commented_out_loop_does_not_count():
    """executable_code strips comments, so a disabled anti-pattern cannot fail."""
    verdict = nb_no_cursor(_ctx(_nb("# for row in df.collect():\n#     process(row)\ndf.show()\n")))
    assert verdict.score == 3


def test_unreadable_definitions_are_na():
    verdict = nb_no_cursor(_ctx(_nb("for row in df.collect():\n    x(row)\n"),
                                unavailable={Resource.NOTEBOOK_DEFINITIONS}))
    assert verdict.status is Status.NA


def test_an_empty_notebook_is_na():
    verdict = nb_no_cursor(_ctx(_nb("")))
    assert verdict.status is Status.NA
