"""Performance & Capacity · Data Prep — Spark & Delta optimization checks.

Real evaluators (promoted from the roadmap) that read notebook and pipeline
definitions the delegated Fabric token already fetches — no admin access needed.
Each is a conservative, read-only signal over the item's source: it judges what
the code demonstrably does, and returns N/A when a notebook is simply out of
scope for the practice (e.g. it writes no Delta tables) rather than failing it.

Detection patterns live in :mod:`._spark`, encoding the Fabric Spark/Delta
guidance from the vendored ``fabric-skills``.
"""
from __future__ import annotations

from auditfast.core.check._notebook import notebook_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

from . import _spark
from ._spark import NOTEBOOK_LAYERS, pip_targets, unpinned_targets, writes_delta

# -- Delta table maintenance (3.3.x) ------------------------------------------

@check(
    id="DELTA-MERGE", ref="3.3.1", title="Upserts use a single atomic MERGE",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def delta_merge(ctx: CheckContext) -> Verdict:
    """A single ``MERGE INTO`` handles insert/update/delete, not sequential DML."""
    code = notebook_code(ctx.obj)
    if _spark.MERGE.search(code):
        return binary(True, "Uses MERGE INTO for atomic upserts")
    if _spark.SEQ_DELETE.search(code) and _spark.SEQ_INSERT.search(code):
        return graded(0, "Separate DELETE + INSERT detected instead of a single MERGE")
    return not_applicable("Notebook performs no upsert/merge logic")


@check(
    id="DELTA-OPTIMIZE", ref="3.3.2", title="OPTIMIZE runs after write-heavy operations",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def delta_optimize(ctx: CheckContext) -> Verdict:
    """A notebook that writes Delta tables also runs ``OPTIMIZE`` (bin-compaction)."""
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write Delta tables")
    ok = bool(_spark.OPTIMIZE.search(code))
    return binary(ok, "Calls OPTIMIZE after writes" if ok
                  else "Writes Delta tables but never calls OPTIMIZE (bin-compaction)")


@check(
    id="DELTA-VACUUM", ref="3.3.3", title="VACUUM scheduled to clean up old Delta files",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def delta_vacuum(ctx: CheckContext) -> Verdict:
    """A notebook maintaining Delta tables also runs ``VACUUM``."""
    code = notebook_code(ctx.obj)
    if not (writes_delta(code) or _spark.OPTIMIZE.search(code)):
        return not_applicable("Notebook does not write or maintain Delta tables")
    ok = bool(_spark.VACUUM.search(code))
    return binary(ok, "Runs VACUUM to reclaim old files" if ok
                  else "Maintains Delta tables but never runs VACUUM")


@check(
    id="DELTA-ZORDER", ref="3.3.4", title="Z-ORDER applied on high-cardinality filter columns",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def delta_zorder(ctx: CheckContext) -> Verdict:
    """When ``OPTIMIZE`` runs, it uses ``ZORDER BY`` to co-locate filter columns."""
    code = notebook_code(ctx.obj)
    if not _spark.OPTIMIZE.search(code):
        return not_applicable("No OPTIMIZE present; Z-ORDER is an OPTIMIZE option")
    ok = bool(_spark.ZORDER.search(code))
    return binary(ok, "OPTIMIZE uses ZORDER BY on filter columns" if ok
                  else "OPTIMIZE present but without ZORDER BY")


@check(
    id="DELTA-VORDER", ref="3.3.5", title="V-Order enabled for read-optimized workloads",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def delta_vorder(ctx: CheckContext) -> Verdict:
    """Explicit V-Order configuration is present where read-optimization matters."""
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write Delta tables")
    if _spark.VORDER.search(code):
        return binary(True, "Explicit V-Order configuration found")
    return not_applicable(
        "No explicit V-Order config; Fabric enables V-Order by default "
        "(cannot verify the effective setting from code)"
    )


@check(
    id="DELTA-TBLPROPS", ref="3.3.6", title="Delta table optimization properties set",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def delta_tblprops(ctx: CheckContext) -> Verdict:
    """Write-side tuning (autoOptimize / optimizeWrite / autoCompaction) is set."""
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write Delta tables")
    if _spark.TBLPROPS.search(code):
        return binary(True, "Delta table optimization properties configured")
    return graded(
        1, "Writes Delta tables but sets no optimization properties "
        "(autoOptimize / optimizeWrite / autoCompaction)"
    )


@check(
    id="DELTA-RETENTION", ref="3.3.7", title="Delta history retention configured",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def delta_retention(ctx: CheckContext) -> Verdict:
    """Log / deleted-file retention is tuned rather than left at the default."""
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write Delta tables")
    if _spark.RETENTION.search(code):
        return binary(True, "Delta history/log retention explicitly configured")
    return not_applicable(
        "No explicit Delta retention config; Fabric defaults apply "
        "(cannot verify from code)"
    )


# -- Spark environment & tuning (3.4.x / 3.5.x) -------------------------------

@check(
    id="SPARK-ENV", ref="3.4.1", title="Fabric Environments manage Spark dependencies",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_env(ctx: CheckContext) -> Verdict:
    """Dependencies come from a Fabric Environment, not inline ``%pip`` installs."""
    code = notebook_code(ctx.obj)
    if _spark.has_inline_install(code):
        count = len(pip_targets(code))
        what = f"{count} package(s)" if count else "package(s)"
        return graded(
            1, f"Installs {what} inline (%pip/!pip/pip install/wheel URL); prefer a "
            "Fabric Environment for shared, reproducible dependencies"
        )
    return binary(True, "No inline library installs; consistent with Environment-managed dependencies")


@check(
    id="SPARK-LIBPIN", ref="3.4.2", title="Custom library versions pinned (not floating)",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_libpin(ctx: CheckContext) -> Verdict:
    """Every inline-installed package pins an explicit ``==`` version."""
    code = notebook_code(ctx.obj)
    targets = pip_targets(code)
    if not targets:
        # An install is present but no package spec could be parsed — a wheel/VCS
        # URL, a bare-shell/subprocess pip call. None of those pin a reproducible
        # version, so flag rather than skip (SPARK-ENV sees the same installs).
        if _spark.has_inline_install(code):
            return graded(
                0, "Installs libraries inline but not via a pinned '==' version "
                "(wheel URL / VCS / bare-shell or subprocess pip) — pin an explicit "
                "version for reproducible builds"
            )
        return not_applicable("Notebook installs no libraries inline")
    unpinned = unpinned_targets(code)
    return covered(
        len(targets) - len(unpinned), len(targets),
        f"{len(targets) - len(unpinned)} of {len(targets)} installed package(s) pin an explicit version"
        + (f"; unpinned: {', '.join(unpinned[:5])}" if unpinned else ""),
    )


@check(
    id="SPARK-CONF", ref="3.4.4", title="Spark configuration tuned from defaults",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def spark_conf(ctx: CheckContext) -> Verdict:
    """Custom Spark configuration is present where tuning is justified."""
    code = notebook_code(ctx.obj)
    if _spark.SPARK_CONF.search(code):
        return binary(True, "Custom Spark configuration present (tuned from defaults)")
    return not_applicable(
        "No custom Spark configuration; Fabric defaults in use "
        "(appropriateness cannot be judged from code)"
    )


@check(
    id="SPARK-SHUFFLE", ref="3.5.2", title="Shuffle partition count tuned for data size",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_shuffle(ctx: CheckContext) -> Verdict:
    """Wide transformations set ``spark.sql.shuffle.partitions`` off the default 200."""
    code = notebook_code(ctx.obj)
    if not (writes_delta(code) or _spark.WIDE_TRANSFORM.search(code)):
        return not_applicable("Notebook has no shuffles/joins to tune partitions for")
    if _spark.SHUFFLE.search(code):
        return binary(True, "spark.sql.shuffle.partitions explicitly tuned")
    return graded(1, "Wide transformations present but shuffle partitions left at the default (200)")


@check(
    id="SPARK-CACHE", ref="3.5.3", title="Caching used judiciously and released",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def spark_cache(ctx: CheckContext) -> Verdict:
    """A cached/persisted DataFrame is later released with ``unpersist()``."""
    code = notebook_code(ctx.obj)
    if not _spark.CACHE.search(code):
        return not_applicable("Notebook does not cache/persist DataFrames")
    if _spark.UNPERSIST.search(code):
        return binary(True, "Caches and releases with unpersist()")
    return graded(1, "Caches/persists without a matching unpersist() — may hold executor memory")


@check(
    id="SPARK-REPARTITION", ref="3.5.4", title="Write partition strategy is explicit",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def spark_repartition(ctx: CheckContext) -> Verdict:
    """Writes use an explicit ``coalesce``/``repartition`` rather than the default."""
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write tables")
    if _spark.REPARTITION.search(code):
        return binary(True, "Explicit coalesce/repartition before writes")
    return not_applicable("No explicit repartition/coalesce; default partitioning on write")


@check(
    id="SPARK-SELECT", ref="3.5.9", title="Explicit column projection (no SELECT *)",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_select(ctx: CheckContext) -> Verdict:
    """Reads project explicit columns instead of ``SELECT *``."""
    code = notebook_code(ctx.obj)
    if not _spark.SELECT.search(code):
        return not_applicable("Notebook issues no select/projection")
    if _spark.SELECT_STAR.search(code):
        return graded(0, "Uses SELECT * — project explicit columns to avoid reading unused data")
    return binary(True, "Projects explicit columns (no SELECT *)")


# -- Copy activity parallelism (2.6.2) ----------------------------------------

@check(
    id="PL-COPY-PARALLEL", ref="2.6.2", title="Copy activities use appropriate parallelism",
    pillar=Pillar.PERFORMANCE, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def copy_parallelism(ctx: CheckContext) -> Verdict:
    """Copy activities set ``parallelCopies`` or ``dataIntegrationUnits``."""
    copies = [a for a in activities(ctx.obj) if a.get("type") == "Copy"]
    if not copies:
        return not_applicable("Pipeline has no Copy activities")
    tuned = 0
    for activity in copies:
        props = activity.get("typeProperties") or {}
        if props.get("parallelCopies") or props.get("dataIntegrationUnits"):
            tuned += 1
    return covered(
        tuned, len(copies),
        f"{tuned} of {len(copies)} Copy activities set parallelCopies/DIU",
    )
