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

import re
from datetime import datetime, timezone

from auditfast.core.check._notebook import notebook_code
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities, walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

from . import _spark
from ._spark import NOTEBOOK_LAYERS, pip_targets, unpinned_targets, writes_delta
import re
import re

# -- Delta table maintenance (3.3.x) ------------------------------------------

@check(
    id="DELTA-MERGE", ref="3.3.1", title="Single `MERGE INTO` handles I/U/D atomically — not separate sequential DELETE/INSERT/UPDATE",
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
    id="DELTA-OPTIMIZE", ref="3.3.2", title="`OPTIMIZE` (bin-compaction) scheduled appropriately (not after every micro-batch)",
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
    id="DELTA-VACUUM", ref="3.3.3", title="`VACUUM` scheduled to clean up old Delta files",
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
    id="DELTA-ZORDER", ref="3.3.4", title="Z-ORDER / liquid clustering applied on high-cardinality filter columns",
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
    id="DELTA-VORDER", ref="3.3.5", title="V-Order enabled where Fabric recommends for read-optimized workloads",
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
    id="DELTA-TBLPROPS", ref="3.3.6", title="Table properties set appropriately (optimizeWrite, autoCompaction)",
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
    id="DELTA-RETENTION", ref="3.3.7", title="Delta table history / log retention configured and monitored",
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
    id="SPARK-ENV", ref="3.4.1", title="Fabric Environments used to manage Spark dependencies",
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
    id="SPARK-LIBPIN", ref="3.4.2", title="Custom library versions pinned (not latest/floating)",
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
    id="SPARK-CONF", ref="3.4.4", title="Spark configuration tuned from defaults where justified (shuffle partitions, memory)",
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
    id="SPARK-SHUFFLE", ref="3.5.2", title="Partition count appropriate (not 200 default for small/medium data)",
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
    id="SPARK-CACHE", ref="3.5.3", title="Caching (`persist`/`cache`) used judiciously, not indiscriminately",
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
    id="SPARK-REPARTITION", ref="3.5.4", title="Write operations use appropriate partition strategy (coalesce vs repartition; right-sized files)",
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
    id="SPARK-SELECT", ref="3.5.8", title="Unnecessary columns eliminated in reads (explicit select, not `SELECT *`)",
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


# -- Workload evidence (3.4.x / 3.5.x) ----------------------------------------

@check(
    id="SPARK-RUNTIME", ref="3.4.5", title="Python/Spark runtime version is current and supported",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.ENVIRONMENT_DEFINITIONS], required=True,
)
def spark_runtime(ctx: CheckContext) -> Verdict:
    """Compare the bound Environment runtime with the configured minimum."""
    environment = ctx.obj.get("_auditfast_environment") if isinstance(ctx.obj, dict) else None
    configured = environment.get("runtime_version") if isinstance(environment, dict) else None
    if configured:
        runtime = _spark.fabric_runtime_to_spark(str(configured))
        minimum = _spark.parse_version(ctx.setting("minimum_spark_version", "3.5"))
        if runtime is None:
            return not_applicable(f"Bound Environment runtime {configured!r} is not recognized")
        if minimum is None:
            return not_applicable("Project minimum_spark_version is invalid; expected major.minor[.patch]")
        actual = ".".join(map(str, runtime))
        required = ".".join(map(str, minimum))
        name = environment.get("name", "bound Environment")
        return binary(
            runtime >= minimum,
            f"Environment {name} uses Fabric runtime {configured} (Spark {actual}); required minimum is {required}",
        )
    versions = _spark.captured_spark_versions(ctx.obj)
    if not versions:
        return not_applicable("No bound Environment runtime or captured Spark runtime version")
    minimum = _spark.parse_version(ctx.setting("minimum_spark_version", "3.5"))
    if minimum is None:
        return not_applicable("Project minimum_spark_version is invalid; expected major.minor[.patch]")
    runtime = versions[-1]
    actual = ".".join(map(str, runtime))
    required = ".".join(map(str, minimum))
    return binary(
        runtime >= minimum,
        f"Captured Spark runtime {actual}; required minimum is {required}",
    )


@check(
    id="SPARK-POOL", ref="3.4.3", title="Spark pool size appropriate for workload (not over- or under-provisioned)",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_pool(ctx: CheckContext) -> Verdict:
    """Recent Spark resource usage stays within deterministic utilization limits."""
    usage = _spark.monitoring(ctx.obj).get("resource_usage")
    if not isinstance(usage, dict):
        return not_applicable("Spark resource-usage metrics are unavailable for this notebook")
    duration = _spark.number(usage.get("duration"))
    efficiency = _spark.number(usage.get("coreEfficiency"), -1)
    idle_ratio = _spark.number(usage.get("idleTime")) / duration if duration > 0 else -1
    if efficiency < 0 or idle_ratio < 0:
        return not_applicable("Spark resource-usage metrics lack efficiency or duration data")
    minimum_efficiency = _spark.number(ctx.setting("minimum_spark_core_efficiency", 0.5), -1)
    maximum_idle = _spark.number(ctx.setting("maximum_spark_idle_ratio", 0.3), -1)
    if minimum_efficiency < 0 or maximum_idle < 0:
        return not_applicable("Spark pool utilization thresholds are invalid")
    exceeded = bool(usage.get("capacityExceeded"))
    ok = efficiency >= minimum_efficiency and idle_ratio <= maximum_idle and not exceeded
    return binary(
        ok,
        f"coreEfficiency={efficiency:.2f}, idleRatio={idle_ratio:.2f}, capacityExceeded={exceeded}",
    )


@check(
    id="SPARK-UI", ref="3.5.1", title="Spark UI reviewed for skew, spill, shuffle issues on key jobs",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_ui_review(ctx: CheckContext) -> Verdict:
    """Recent Spark Advisor and stage metrics show no material performance issue."""
    evidence = _spark.monitoring(ctx.obj)
    if "advice" not in evidence and "stages" not in evidence:
        return not_applicable("Spark Advisor and stage metrics are unavailable for this notebook")
    threshold = int(_spark.number(ctx.setting("heavy_shuffle_bytes", 1_073_741_824), -1))
    if threshold < 0:
        return not_applicable("Project heavy_shuffle_bytes threshold is invalid")
    issues = _spark.performance_issues(ctx.obj, threshold)
    return binary(not issues, "No skew, spill, or heavy-shuffle issue detected" if not issues
                  else f"Detected: {', '.join(issues)}")


@check(
    id="SPARK-PARTITION", ref="3.5.5", title="No full-table scans when partition pruning is possible",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_partition_pruning(ctx: CheckContext) -> Verdict:
    """Every SQL read of a configured partitioned table filters a partition column."""
    code = notebook_code(ctx.obj)
    configured = ctx.setting("partition_columns", {})
    if not isinstance(configured, dict) or not configured:
        return not_applicable("No partition-column metadata configured for this project")
    reads = _spark.partitioned_sql_reads(code, configured)
    if not reads:
        return not_applicable("Notebook has no SQL reads of configured partitioned tables")
    unfiltered = sorted({table for table, filtered in reads if not filtered})
    if unfiltered:
        return binary(False, f"Partition predicate missing for: {', '.join(unfiltered)}")
    return binary(True, f"All {len(reads)} configured partitioned-table read(s) filter a partition column")


@check(
    id="SPARK-PROFILE", ref="3.5.6", title="Long-running notebooks profiled and optimized",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def spark_profile(ctx: CheckContext) -> Verdict:
    """Long-running applications carry Spark profiling evidence.

    This check verifies profiling *coverage* for long-running notebooks. The
    quality of that profiling (skew/spill/shuffle issues) is evaluated by
    SPARK-UI (3.5.1) to avoid duplicate issue scoring under two refs.
    """
    evidence = _spark.monitoring(ctx.obj)
    usage = evidence.get("resource_usage")
    if not isinstance(usage, dict):
        return not_applicable("Spark application duration and profiling metrics are unavailable")
    duration = _spark.number(usage.get("duration"))
    if duration <= 0:
        return not_applicable("Spark application duration is unavailable")
    threshold = int(_spark.number(ctx.setting("long_running_notebook_ms", 300_000), -1))
    if threshold < 0:
        return not_applicable("Project long_running_notebook_ms threshold is invalid")
    if duration < threshold:
        return not_applicable(
            f"Latest Spark application ran for {int(duration)} ms; below {threshold} ms threshold"
        )
    if "advice" not in evidence and "stages" not in evidence:
        return binary(False, "Long-running application has no Advisor or stage profiling metrics")
    return binary(True, f"Profiled {int(duration)} ms application; Advisor or stage metrics captured")

@check(
    id="NB-PUSHDOWN",
    ref="3.5.7",
    title="Predicate pushdown verified for shortcut/external reads",
    pillar=Pillar.PERFORMANCE,
    scope=Scope.NOTEBOOK,
    severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS,
    requires=[Resource.NOTEBOOK_DEFINITIONS],
    required=True,
)
def nb_pushdown(ctx: CheckContext) -> Verdict:
    """Notebooks reading shortcut or external data apply filter predicates early."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")

    code = notebook_code(ctx.obj)
    lines = code.splitlines()

    read_idxs = [i for i, l in enumerate(lines) if _spark.EXTERNAL_READ.search(l)]
    if not read_idxs:
        return not_applicable("Notebook does not read from shortcut or external data sources")

    for i in read_idxs:
        window = "\n".join(lines[i : i + 12])  # include read line + next ~11 lines
        if re.search(r"\.filter\s*\(|\.where\s*\(", window, re.IGNORECASE):
            return binary(True, "Filter predicates applied to external/shortcut reads")

    return graded(
        1,
        "Reads from shortcut or external source without applying filter predicates — "
        "all rows are scanned before any selection, preventing predicate pushdown",
    )

# -- Copy activity parallelism (2.6.2) ----------------------------------------
@check(
    id="NB-PUSHDOWN",
    ref="3.5.7",
    title="Predicate pushdown verified for shortcut/external reads",
    pillar=Pillar.PERFORMANCE,
    scope=Scope.NOTEBOOK,
    severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS,
    requires=[Resource.NOTEBOOK_DEFINITIONS],
    required=True,
)
def nb_pushdown(ctx: CheckContext) -> Verdict:
    """Notebooks reading shortcut or external data apply filter predicates early."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")

    code = notebook_code(ctx.obj)
    lines = code.splitlines()

    read_idxs = [i for i, l in enumerate(lines) if _spark.EXTERNAL_READ.search(l)]
    if not read_idxs:
        return not_applicable("Notebook does not read from shortcut or external data sources")

    for i in read_idxs:
        window = "\n".join(lines[i : i + 12])  # include read line + next ~11 lines
        if re.search(r"\.filter\s*\(|\.where\s*\(", window, re.IGNORECASE):
            return binary(True, "Filter predicates applied to external/shortcut reads")

    return graded(
        1,
        "Reads from shortcut or external source without applying filter predicates — "
        "all rows are scanned before any selection, preventing predicate pushdown",
    )

# -- Copy activity parallelism (2.6.2) ----------------------------------------
@check(
    id="PL-COPY-PARALLEL", ref="2.6.2", title="Copy activities use appropriate parallelism (DIU, degree of copy parallelism)",
    pillar=Pillar.PERFORMANCE, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def copy_parallelism(ctx: CheckContext) -> Verdict:
    """Copy activities set ``parallelCopies`` or ``dataIntegrationUnits``.

    A lone Copy activity with no explicit parallelism is N/A, not a finding: DIU
    and parallelCopies default to Auto, and tuning them is only material across
    multiple copies or at a data volume the audit cannot see. An explicitly-tuned
    single copy is still credited.
    """
    copies = [a for a in activities(ctx.obj) if a.get("type") == "Copy"]
    if not copies:
        return not_applicable("Pipeline has no Copy activities")
    tuned = sum(
        1 for a in copies
        if (a.get("typeProperties") or {}).get("parallelCopies")
        or (a.get("typeProperties") or {}).get("dataIntegrationUnits")
    )
    if len(copies) == 1 and tuned == 0:
        return not_applicable(
            "Only 1 Copy activity with no explicit parallelism — DIU / parallelCopies "
            "default to Auto; tuning is material mainly across multiple or large copies"
        )
    return covered(
        tuned, len(copies),
        f"{tuned} of {len(copies)} Copy activities set parallelCopies/DIU",
    )


# -- Relational-source ingestion tuning (2.6.5) -------------------------------

#: A Copy *source* that reads a relational SQL database — Azure SQL DB, SQL
#: Server/MI, Synapse, or an RDS-hosted SQL Server. Matched on the source type
#: name rather than a source-system name, so the rule holds for every relational
#: feed rather than one tenant's.
_SQL_SOURCE_TYPE = re.compile(r"^(?=.*sql).*source$", re.IGNORECASE)

#: A reader query that folds nothing: the whole table, every column.
_UNFOLDED_QUERY = re.compile(r"select\s+\*", re.IGNORECASE)
#: A predicate or a pipeline expression inside the reader query — the projection
#: or restriction being pushed down to the source engine.
_FOLDED_QUERY = re.compile(r"\bwhere\b|\btop\b|@\{|@pipeline\s*\(|@activity\s*\(", re.IGNORECASE)


def _sql_copy_sources(definition: dict) -> list[tuple[str, dict, dict]]:
    """``(activity name, source, sink)`` for every Copy reading a SQL database.

    Uses :func:`walk_activities` so a Copy nested inside a ForEach — the usual
    shape for metadata-driven ingestion — is judged like a top-level one.
    """
    found: list[tuple[str, dict, dict]] = []
    for activity in walk_activities(definition):
        if activity.get("type") != "Copy":
            continue
        props = activity.get("typeProperties") or {}
        source = props.get("source") if isinstance(props.get("source"), dict) else {}
        sink = props.get("sink") if isinstance(props.get("sink"), dict) else {}
        if _SQL_SOURCE_TYPE.match(str(source.get("type") or "")):
            found.append((activity.get("name") or "?", source, sink))
    return found


def _folds_source_read(source: dict) -> bool:
    """The source read pushes projection or restriction into the database.

    A stored procedure always folds — the work happens in the source engine. A
    reader query folds when it names its columns or carries a predicate; a bare
    ``SELECT *`` with no ``WHERE`` reads the whole table and folds nothing.
    """
    if source.get("sqlReaderStoredProcedureName"):
        return True
    query = source.get("sqlReaderQuery") or source.get("query") or ""
    if isinstance(query, dict):  # an expression object — value lives under "value"
        query = query.get("value") or ""
    query = str(query)
    if not query.strip():
        return False
    return bool(_FOLDED_QUERY.search(query)) or not _UNFOLDED_QUERY.search(query)


def _partitions_source_read(source: dict) -> bool:
    """The read is split into partitions rather than pulled as one stream.

    Whether the partition column is *indexed* is not readable — Fabric exposes no
    source-schema metadata — so a configured ``partitionOption`` is the readable
    proxy for an index-friendly ranged read. ``"None"`` is the default and means
    nothing was chosen.
    """
    option = str(source.get("partitionOption") or "").strip()
    return bool(option) and option.lower() != "none"


def _sizes_write_batch(sink: dict) -> bool:
    """The sink writes in explicitly sized batches instead of the default."""
    return bool(sink.get("writeBatchSize") or sink.get("writeBatchTimeout"))


@check(
    id="PL-SQL-INGEST-TUNED", ref="2.6.5",
    title="Relational (Azure SQL DB) ingestion is tuned — query folding, ranged source reads, batch sizing",
    pillar=Pillar.PERFORMANCE, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def sql_ingestion_tuned(ctx: CheckContext) -> Verdict:
    """Copy activities reading a SQL database fold, partition, and batch their I/O.

    Three independent tunings, each read straight off the Copy activity:

    * *query folding* — a reader query or stored procedure that projects columns
      or carries a predicate, so the source engine does the filtering rather than
      the whole table crossing the wire;
    * *ranged source read* — a ``partitionOption`` other than ``None``, so the
      read is split instead of pulled as one long-running stream. Index metadata
      is not exposed by any Fabric API, so this is the readable proxy for an
      index-friendly read, and the docstring says so rather than pretending;
    * *batch sizing* — an explicit ``writeBatchSize``/``writeBatchTimeout`` on the
      sink instead of the conservative default.

    Scored as coverage over all three signals across every SQL-source Copy, so a
    pipeline that folds but never partitions lands in the middle rather than at
    either extreme. A pipeline that reads no SQL database is N/A.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    sql_copies = _sql_copy_sources(ctx.obj)
    if not sql_copies:
        return not_applicable("Pipeline has no Copy activity reading a SQL database")

    folded = [name for name, source, _ in sql_copies if _folds_source_read(source)]
    partitioned = [name for name, source, _ in sql_copies if _partitions_source_read(source)]
    batched = [name for name, _, sink in sql_copies if _sizes_write_batch(sink)]

    total = len(sql_copies)
    met = len(folded) + len(partitioned) + len(batched)
    gaps = [
        label for label, hit in (
            ("query folding", folded), ("ranged source read", partitioned),
            ("sink batch sizing", batched),
        ) if len(hit) < total
    ]
    detail = (f"{total} SQL-source Copy activit(y/ies): {len(folded)} fold the source read, "
              f"{len(partitioned)} set a partitionOption, {len(batched)} set a sink write "
              f"batch size")
    if gaps:
        detail += f" — untuned on {', '.join(gaps)}"
    return covered(met, total * 3, detail)


# -- 2.6.4 — scheduling spread across the capacity -----------------------------
#
# Read entirely from ``Resource.ITEM_RUN_HISTORY``, which retains up to 25 run
# stamps per runnable item. No schedule API is called and no row data is touched.

#: Runnable item types whose starts compete for the same capacity.
_SCHEDULED_TYPES: frozenset[str] = frozenset({
    "DataPipeline", "Notebook", "Dataflow", "SparkJobDefinition",
})

#: The window inside which two runs count as "at the same time". Five minutes is
#: deliberately coarse: a job scheduled on the hour does not fire at exactly
#: :00:00, and a tighter window would score clock jitter instead of contention.
_WINDOW_MINUTES = 5

#: Below this many distinct items, a workspace cannot demonstrate contention no
#: matter how it is scheduled — two items overlapping is a coincidence.
_MIN_ITEMS = 3
#: …and below this many stamps overall there is not enough history to judge.
_MIN_STAMPS = 6

#: Share of items allowed to share the busiest window before it reads as a pile-up.
_SPREAD_GOOD = 0.34
_SPREAD_FAIR = 0.50
_SPREAD_POOR = 0.75


def _run_moment(stamp: str) -> datetime | None:
    """Parse an ISO-8601 UTC run stamp, or ``None`` when it is unreadable."""
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@check(
    id="WS-SCHEDULE-STAGGER", ref="2.6.4",
    title="Pipeline scheduling avoids capacity contention (staggered across domains, not all at once)",
    pillar=Pillar.PERFORMANCE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.PREP, Layer.MIXED),
    requires=[Resource.ITEMS, Resource.ITEM_RUN_HISTORY], required=True,
)
def schedule_stagger(ctx: CheckContext) -> Verdict:
    """Runnable items do not all pile into the same few minutes of the clock.

    **What it can determine.** How the workspace's runs are spread over the
    clock, from the job-run stamps the scheduler history already returns — up to
    25 per item, so no extra call is made. Runs are bucketed into
    5-minute windows; the score is driven by the *busiest* window, measured as
    the share of distinct runnable items that appear in it. A third or fewer is a
    well-staggered estate; more than three quarters of the items landing in one
    window is everything firing at once and competing for the same capacity. The
    classic "everything on the hour" shape is reported alongside, as the share of
    stamps falling within two minutes of the top of an hour.

    **What it cannot — read this before acting on it.**

    * These are **observed run stamps, not the configured schedule.** The Fabric
      job-schedule API is not called. A job whose trigger is staggered but which
      queues behind a busy capacity still shows up clustered here, and that is
      arguably the more useful answer — but it is not the same claim as "the
      schedule is misconfigured".
    * The stamps are each run's **end time**, falling back to its start time for
      a run still in flight, because that is what ``jobs/instances`` yields for
      the one page already fetched. Clustered *completions* are strong evidence
      of concurrent capacity use, but they are a proxy for clustered starts, not
      a measurement of them.
    * It sees **one workspace at a time**. "Staggered across domains" in the
      point's sense — different domains in different workspaces — is only
      partially visible; contention created by a *sibling* workspace on the same
      capacity is invisible here.
    * A workspace with fewer than three runnable items with history, or fewer
      than six readable stamps in total, is **N/A**: it cannot demonstrate
      contention either way. Unreadable run history is N/A, never FAIL.
    """
    if not ctx.workspace.has(Resource.ITEM_RUN_HISTORY):
        return not_applicable(
            "Per-item run history (jobs/instances) could not be read from Fabric, so the "
            "spread of run times over the clock cannot be derived"
        )
    history = ctx.workspace.run_history or {}
    if not history:
        return not_applicable(
            "No item in this workspace has a recorded run history, so there is no "
            "observed schedule to judge for contention"
        )

    by_id = {i.id: i for i in ctx.workspace.items if i.id}

    def _name(item_id: str) -> str:
        item = by_id.get(item_id)
        return (item.display_name or item.id) if item else item_id

    # Only the item types that actually consume capacity when they run.
    moments: dict[str, list[datetime]] = {}
    for item_id, stamps in history.items():
        item = by_id.get(item_id)
        if item is not None and item.type and item.type not in _SCHEDULED_TYPES:
            continue
        parsed = [m for m in (_run_moment(s) for s in stamps) if m is not None]
        if parsed:
            moments[item_id] = parsed

    total_stamps = sum(len(v) for v in moments.values())
    if len(moments) < _MIN_ITEMS or total_stamps < _MIN_STAMPS:
        return not_applicable(
            f"Only {len(moments)} runnable item(s) with {total_stamps} readable run "
            f"stamp(s) — fewer than the {_MIN_ITEMS} items and {_MIN_STAMPS} stamps needed "
            f"before a pile-up can be told apart from coincidence"
        )

    # Bucket every run into a fixed 5-minute window of absolute time, recording
    # which *items* landed there. Counting items rather than runs stops one
    # chatty item's retry storm reading as an estate-wide pile-up.
    windows: dict[int, set[str]] = {}
    on_the_hour = 0
    for item_id, runs in moments.items():
        for moment in runs:
            bucket = int(moment.timestamp()) // (_WINDOW_MINUTES * 60)
            windows.setdefault(bucket, set()).add(item_id)
            if moment.minute <= 2 or moment.minute >= 58:
                on_the_hour += 1

    busiest = max(windows.values(), key=len)
    peak = len(busiest)
    share = peak / len(moments)
    hour_share = on_the_hour / total_stamps

    detail = (
        f"{len(moments)} runnable item(s), {total_stamps} observed run stamp(s): the busiest "
        f"{_WINDOW_MINUTES}-minute window holds {peak} of them ({share:.0%}), across "
        f"{len(windows)} distinct window(s); {hour_share:.0%} of runs land within two "
        f"minutes of the top of an hour"
    )
    if peak > 1:
        detail += f" — concurrent in the busiest window: {', '.join(sorted(_name(i) for i in busiest)[:5])}"
    detail += (
        ". Derived from observed run stamps (each run's end time, or its start time while "
        "still running), not from the configured schedule, and from this workspace only"
    )

    if share <= _SPREAD_GOOD:
        return graded(3, detail + " — well staggered")
    if share <= _SPREAD_FAIR:
        return graded(2, detail + " — partly staggered; a sizeable group still overlaps")
    if share <= _SPREAD_POOR:
        return graded(1, detail + " — most items run in one window and compete for capacity")
    return graded(0, detail + " — effectively everything runs at once")
