"""Data Management & Quality · Data Prep — pipeline design & re-usability.

Naming, documentation, and parameterization: can someone other than the author
understand, promote, and re-point this pipeline.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import (
    NOTEBOOK_LAYERS,
    executable_code,
    has_parameters_cell,
    markdown_sources,
    notebook_code,
)
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities, script_sql, walk_activities
from auditfast.core.check._tables import is_dimension, is_fact
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Connection/endpoint literals that belong in a parameter or managed connection.
HARDCODED_PATTERNS = [
    re.compile(r"\.database\.windows\.net", re.IGNORECASE),
    re.compile(r"Data Source\s*=", re.IGNORECASE),
    re.compile(r"\bServer\s*=\s*tcp:", re.IGNORECASE),
    re.compile(r"\.blob\.core\.windows\.net", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # bare IPv4
]


@check(
    id="PL-NAME", ref="2.1.1", title="Pipelines follow consistent naming conventions (including domain prefix/folder alignment)",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def naming_convention(ctx: CheckContext) -> Verdict:
    """The pipeline name matches the convention configured for the project."""
    pattern = ctx.setting("pipeline_naming_convention")
    name = ctx.obj_name
    ok = bool(pattern) and re.match(pattern, name) is not None
    return binary(ok, f"'{name}' matches convention" if ok
                  else f"'{name}' does not match {pattern!r}")


@check(
    id="PL-DESC", ref="2.1.6", title="Pipeline annotations/descriptions populated for pipelines and key activities",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def descriptions(ctx: CheckContext) -> Verdict:
    """The pipeline and each of its activities carry a description."""
    properties = ctx.obj.get("properties") or {}
    acts = activities(ctx.obj)
    populated = 1 if (properties.get("description") or "").strip() else 0
    populated += sum(1 for a in acts if (a.get("description") or "").strip())
    total = 1 + len(acts)  # the pipeline itself, plus every activity
    return covered(
        populated, total,
        f"{populated} of {total} description slots (pipeline + activities) populated",
    )


@check(
    id="PL-PARAM", ref="2.1.2", title="Pipelines are parameterized (no hardcoded sources, targets, dates, or environment values)",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def parameterized(ctx: CheckContext) -> Verdict:
    """Endpoints come from parameters rather than being baked into the definition."""
    blob = json.dumps(ctx.obj)
    found = [p.pattern for p in HARDCODED_PATTERNS if p.search(blob)]
    has_parameters = bool((ctx.obj.get("properties") or {}).get("parameters"))

    if found:
        return graded(0, f"Hardcoded endpoint/literal(s) detected: {found}")
    if has_parameters:
        return graded(3, "Uses pipeline parameters; no hardcoded endpoints found")
    return graded(1, "No parameters defined (though no hardcoded endpoints found)")


# -- notebook checks (scope=NOTEBOOK; read the notebook's code cells) ----------
_NB_SECRETS = [
    re.compile(r"password\s*=\s*[\"'][^\"']{3,}", re.IGNORECASE),
    re.compile(r"AccountKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"SharedAccessKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\.database\.windows\.net", re.IGNORECASE),
]
_WILDCARD_IMPORT = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\*", re.MULTILINE)
# A standalone ``display(...)`` or a ``.show(...)``/``.display(...)`` chained on any
# receiver — including ``spark.sql(\"...\").show()`` where a ``)`` precedes ``.show``.
_DISPLAY_CALL = re.compile(r"(?:^|[^\w.])display\s*\(|\.(?:show|display)\s*\(")
_WIDE_CALL = re.compile(r"\.(?:collect|toPandas)\s*\(|\.count\s*\(\s*\)")


@check(
    id="NB-SECRETS", ref="3.1.3", title="No hardcoded paths, connection strings, secrets, or environment-specific values",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.CRITICAL,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_secrets(ctx: CheckContext) -> Verdict:
    """No credential/endpoint literals baked into the notebook's code cells."""
    hits = [p.pattern for p in _NB_SECRETS if p.search(notebook_code(ctx.obj))]
    return binary(not hits, f"{len(hits)} secret/endpoint pattern match(es)" if hits
                  else "No hardcoded secrets or endpoints found")


@check(
    id="NB-PARAMS", ref="3.1.2", title="Notebooks are parameterized using Fabric notebook parameters or widgets",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_parameterized(ctx: CheckContext) -> Verdict:
    """A parameters cell or the notebook-parameter API drives configuration."""
    code = notebook_code(ctx.obj)
    ok = has_parameters_cell(ctx.obj) or "mssparkutils" in code or "notebookutils" in code
    return binary(ok, "Parameters cell / parameter API present" if ok
                  else "No parameters cell or parameter API found")


@check(
    id="NB-IMPORTS", ref="3.2.7", title="Explicit imports only (no `import *`)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_explicit_imports(ctx: CheckContext) -> Verdict:
    """No ``from x import *`` — wildcard imports hide origins and shadow names."""
    hits = _WILDCARD_IMPORT.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} wildcard import(s)" if hits
                  else "No wildcard imports")


@check(
    id="NB-DISPLAY", ref="3.1.6", title="Notebooks avoid `display()` / `show()` in production execution paths",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_display(ctx: CheckContext) -> Verdict:
    """No inline ``display()``/``.show()`` — they force compute on production runs."""
    hits = _DISPLAY_CALL.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} display()/show() call(s)" if hits
                  else "No display()/show() calls")


@check(
    id="NB-COLLECT", ref="3.2.3", title="No unnecessary `collect()`, `toPandas()`, or `count()` on large datasets",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_wide_calls(ctx: CheckContext) -> Verdict:
    """No ``.collect()``/``.toPandas()``/``.count()`` pulling a dataset to the driver."""
    hits = _WIDE_CALL.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} collect()/toPandas()/count() call(s)" if hits
                  else "No driver-side collect/count calls")


# -- notebook structure (3.1.x) and Spark-usage (3.2.x) checks ----------------
_IMPORT_STMT = re.compile(r"^\s*(?:import|from)\s+[\w.]", re.MULTILINE)
_FUNC_DEF = re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE)
_RDD_API = re.compile(r"\.rdd\b|sc\.parallelize\s*\(|\.mapPartitions\s*\(")
# A Python UDF definition/registration: the ``@udf`` / ``@F.udf`` / ``@pandas_udf``
# decorator, an ``udf(...)`` / ``pandas_udf(...)`` construction, ``F.udf(...)``, or a
# ``spark.udf.register(...)`` / ``sqlContext.udf.register(...)`` registration.
_UDF_DEF = re.compile(
    r"@(?:\w+\.)?(?:pandas_)?udf\b"
    r"|\b(?:pandas_)?udf\s*\("
    r"|\.udf\s*\("
    r"|\budf\.register\s*\(",
    re.IGNORECASE,
)
_SPARK_SQL = re.compile(r"spark\.sql\s*\(|%%?sql\b", re.IGNORECASE)
_DATAFRAME_OP = re.compile(r"spark\.(?:read|table)\b|\.groupBy\s*\(|\.withColumn\s*\(|createDataFrame\s*\(")
_EXTERNAL_READ = re.compile(r"\.read\b[^\n]*(?:csv|json|format\s*\(\s*[\"'](?:csv|json))", re.IGNORECASE)
_SCHEMA_DEFINED = re.compile(r"\.schema\s*\(|StructType\s*\(|inferSchema", re.IGNORECASE)
_BROADCAST = re.compile(r"broadcast\s*\(", re.IGNORECASE)
_NB_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+$")
_NB_NAME_BAD = re.compile(r"^(?:notebook|untitled|test|temp|copy of)\b", re.IGNORECASE)


def _timeout_value_is_positive(value: object) -> bool:
    """A timeout value that actually bounds a run: a positive number or a non-zero
    duration string. ``0`` / ``False`` / ``""`` / an all-zero duration mean "unset"."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return any(ch.isdigit() and ch != "0" for ch in value)
    return False


def _has_positive_timeout(node: object) -> bool:
    """True when any nested metadata key naming a timeout carries a positive value.

    Only dict *keys* named like a timeout are inspected (not free-text values), so
    the word "timeout" appearing in a cell's output or a traceback cannot satisfy it.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (isinstance(key, str) and "timeout" in key.lower()
                    and _timeout_value_is_positive(value)):
                return True
            if _has_positive_timeout(value):
                return True
    elif isinstance(node, list):
        return any(_has_positive_timeout(item) for item in node)
    return False


@check(
    id="NB-STRUCTURE", ref="3.1.1", title="Notebooks follow a consistent structure (parameters → imports → config → logic → output)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_structure(ctx: CheckContext) -> Verdict:
    """Parameters, documentation, and imports up front — not one undifferentiated cell."""
    code = notebook_code(ctx.obj)
    signals = sum([
        has_parameters_cell(ctx.obj),
        bool(markdown_sources(ctx.obj)),
        bool(_IMPORT_STMT.search(code)),
    ])
    return covered(
        signals, 3,
        f"{signals}/3 structure signals present (parameters cell, markdown docs, explicit imports)",
    )


@check(
    id="NB-MARKDOWN", ref="3.1.4", title="Cell-level documentation (markdown cells) explains business logic, not just code",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_markdown(ctx: CheckContext) -> Verdict:
    """At least one markdown cell documents what the notebook does."""
    md = markdown_sources(ctx.obj)
    return binary(bool(md), f"{len(md)} markdown documentation cell(s)" if md
                  else "No markdown cells — the logic is undocumented")


@check(
    id="NB-MODULAR", ref="3.1.5", title="Functions are modular and reusable — not monolithic single-cell scripts",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_modular(ctx: CheckContext) -> Verdict:
    """Reusable functions rather than a single monolithic script."""
    defs = _FUNC_DEF.findall(notebook_code(ctx.obj))
    return binary(bool(defs), f"{len(defs)} function definition(s)" if defs
                  else "No functions defined — logic is a monolithic script")


@check(
    id="NB-NAME", ref="3.1.7", title="All notebooks have meaningful, consistent names aligned to domain/layer",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_name(ctx: CheckContext) -> Verdict:
    """The notebook name matches the project convention, or is at least descriptive."""
    name = ctx.obj_name or ""
    pattern = ctx.setting("notebook_naming_convention")
    if pattern:
        ok = re.match(pattern, name) is not None
        return binary(ok, f"'{name}' matches convention" if ok
                      else f"'{name}' does not match {pattern!r}")
    ok = bool(_NB_NAME_OK.match(name)) and not _NB_NAME_BAD.match(name)
    return binary(ok, f"'{name}' is descriptive and consistent" if ok
                  else f"'{name}' is not a descriptive, consistent name")


@check(
    id="NB-TIMEOUT", ref="3.1.8", title="Notebook execution timeout / max runtime configured to prevent runaway Spark sessions",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_timeout(ctx: CheckContext) -> Verdict:
    """An explicit, positive execution/session timeout guards runaway Spark sessions.

    Fabric always emits ``sessionKeepAliveTimeout`` in notebook metadata and
    applies a *default* session timeout that the definition does not expose, so a
    missing or ``0`` value is neither demonstrably compliant nor a failure — it is
    reported N/A. Only an explicit positive timeout in the metadata is a PASS.
    """
    metadata = (ctx.obj or {}).get("metadata") or {}
    if _has_positive_timeout(metadata):
        return binary(True, "Explicit positive execution/session timeout configured")
    return not_applicable(
        "No explicit positive execution timeout in the notebook definition; "
        "Fabric's default session timeout applies and cannot be verified from code"
    )


@check(
    id="NB-LANG", ref="3.2.1", title="Consistent language approach (PySpark vs Spark SQL — one primary, not mixed ad-hoc)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_language(ctx: CheckContext) -> Verdict:
    """One primary language rather than a mix of PySpark and Spark SQL.

    A notebook using neither — pure Python, pandas, or plain orchestration — has
    no Spark language choice to be consistent about, so it is N/A rather than a
    vacuous pass.
    """
    code = executable_code(ctx.obj)
    sql = bool(_SPARK_SQL.search(code))
    dataframe = bool(_DATAFRAME_OP.search(code))
    if not sql and not dataframe:
        return not_applicable(
            "Notebook uses neither Spark SQL nor the DataFrame API, so it makes no "
            "Spark language choice to be consistent about"
        )
    if sql and dataframe:
        return graded(1, "Mixes PySpark and Spark SQL — pick one primary approach")
    return binary(True, f"Consistent language approach "
                        f"({'Spark SQL' if sql else 'PySpark DataFrame API'} only)")


@check(
    id="NB-DATAFRAME", ref="3.2.2", title="DataFrame API used over RDD API",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_dataframe_api(ctx: CheckContext) -> Verdict:
    """The higher-level DataFrame API rather than raw RDD operations."""
    hits = _RDD_API.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} RDD-API usage(s)" if hits
                  else "No RDD API — DataFrame API used")


@check(
    id="NB-BROADCAST", ref="3.2.4", title="Broadcast joins used for small-large table joins where appropriate",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_broadcast(ctx: CheckContext) -> Verdict:
    """A notebook that joins carries a broadcast() hint somewhere in its code.

    Judged at *notebook* level, not per join: no Fabric API reports table sizes,
    so which join deserves a hint cannot be determined. The evidence says exactly
    that rather than implying each join was inspected.
    """
    code = executable_code(ctx.obj)
    joins = _JOIN_PATTERN.findall(code)
    if not joins:
        return not_applicable("No joins present to evaluate for broadcast hints")
    ok = bool(_BROADCAST.search(code))
    return binary(ok, f"{len(joins)} join(s) and a broadcast() hint is present" if ok
                  else f"{len(joins)} join(s) present; no broadcast() hint anywhere in the "
                       f"notebook — confirm none joins a small table to a large one")


@check(
    id="NB-NO-UDF", ref="3.2.5", title="UDFs avoided where native Spark functions exist",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_udf(ctx: CheckContext) -> Verdict:
    """No Python UDFs where a native Spark function would do (UDFs block optimization)."""
    hits = _UDF_DEF.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} UDF definition(s) — prefer native Spark functions" if hits
                  else "No UDFs — native Spark functions used")


@check(
    id="NB-SCHEMA", ref="3.2.6", title="Schema explicitly defined at read time for external sources (not inferred on CSV/JSON)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_schema(ctx: CheckContext) -> Verdict:
    """External CSV/JSON reads declare a schema instead of inferring it."""
    code = notebook_code(ctx.obj)
    if not _EXTERNAL_READ.search(code):
        return not_applicable("No external CSV/JSON file reads present to evaluate")
    ok = bool(_SCHEMA_DEFINED.search(code))
    return binary(ok, "Explicit schema on external file read(s)" if ok
                  else "External CSV/JSON read without an explicit schema")


@check(
    id="NB-LATE-ARRIVAL", ref="2.3.8",
    title="Out-of-order / late-arriving change records handled without data corruption",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_late_arrival(ctx: CheckContext) -> Verdict:
    """Late or out-of-order changes use version-aware deduplication or MERGE logic."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    late_signal = bool(_LATE_ARRIVAL.search(code))
    safe_write = bool(_LATE_SAFE_WRITE.search(code))
    if not late_signal:
        return not_applicable("No late-arrival or out-of-order handling signal found")
    return binary(
        safe_write,
        "Late/out-of-order handling uses version-aware deduplication or MERGE"
        if safe_write else
        "Late/out-of-order handling is indicated but no version-aware duplicate-safe write was found",
    )
# -- pipeline load-pattern checks (2.1.3 orchestration, 2.2.x incremental) -----
_INVOKE_TYPES = {"ExecutePipeline", "InvokePipeline"}
_DATA_MOVE_TYPES = {"Copy", "Script", "TridentNotebook", "SqlServerStoredProcedure", "Lookup"}
_INCREMENTAL = re.compile(
    r"watermark|last_?modified|last_?load|high_?water|incrementalstart|"
    r"\bcdc\b|change[_\s]?tracking|change[_\s]?data|upsert|merge\s+into|delta[_\s]?detect",
    re.IGNORECASE,
)
_LOAD_MODE = re.compile(r"load_?type|load_?mode|is_?initial|full_?load|incremental", re.IGNORECASE)
_LATE_ARRIVAL = re.compile(
    r"late[_ -]?arriv|out[_ -]?of[_ -]?order|event[_ -]?time|watermark|lookback|"
    r"sequence[_ -]?(?:number|no|id)|version[_ -]?(?:number|no|id)|effective[_ -]?date",
    re.IGNORECASE,
)
# Deliberately excludes bare ``version`` / ``sequence``: those are also how
# ``_LATE_ARRIVAL`` trips (``version_id``, ``sequence_number``), so accepting them
# here made the verdict a tautology — any pipeline that entered the gate via a
# version/sequence token satisfied the safe-write test by the same token and could
# never FAIL. Only an actual duplicate-safe *write* counts.
_LATE_SAFE_WRITE = re.compile(
    r"merge|upsert|dropduplicates|drop_duplicates|row_number|dedup|"
    r"latest[_ -]?version|newer[_ -]?version|when[_ -]?matched",
    re.IGNORECASE,
)


@check(
    id="PL-ORCHESTRATION", ref="2.1.3", title="Master/orchestrator pipeline pattern used for coordinating dependent domain pipelines",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_orchestration(ctx: CheckContext) -> Verdict:
    """When a workspace has several pipelines, a master pipeline coordinates them."""
    pipelines = ctx.workspace.pipelines or {}
    if len(pipelines) < 2:
        return not_applicable("Fewer than two pipelines — no orchestration needed")
    orchestrators = [
        name for name, defn in pipelines.items()
        if any((a.get("type") or "") in _INVOKE_TYPES for a in activities(defn))
    ]
    ok = bool(orchestrators)
    return binary(ok, f"Master/orchestrator pipeline present: {', '.join(orchestrators)}" if ok
                  else f"{len(pipelines)} pipelines but none invoke others (no orchestrator pipeline)")


@check(
    id="PL-INCREMENTAL", ref="2.2.1", title="Incremental load implemented where applicable (watermark, CDC, delta detection) for IFS/EAM/LIMS",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_incremental(ctx: CheckContext) -> Verdict:
    """A watermark / CDC / upsert pattern rather than an unconditional full reload."""
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for incremental load")
    ok = bool(_INCREMENTAL.search(json.dumps(ctx.obj)))
    return binary(ok, "Incremental-load pattern detected (watermark / CDC / merge)" if ok
                  else "No incremental-load pattern detected — full-reload risk")


@check(
    id="PL-LOADMODE", ref="2.2.5", title="Initial load vs. incremental load clearly separated or parameterized",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_load_mode(ctx: CheckContext) -> Verdict:
    """A load-mode parameter or a branch keeps first-load and incremental logic apart."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    props = ctx.obj.get("properties") or {}
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for load-mode separation")
    param_mode = any(_LOAD_MODE.search(p) for p in (props.get("parameters") or {}))
    # A bare Switch/If proves nothing — a condition on file existence or row count
    # is not load-mode separation. The branch must itself mention the load mode.
    branch = any(
        (a.get("type") or "") in {"Switch", "IfCondition"}
        and _LOAD_MODE.search(json.dumps(a))
        for a in acts
    )
    ok = param_mode or branch
    return binary(ok, "Load mode is parameterized / branched (initial vs incremental)" if ok
                  else "No initial-vs-incremental separation (load-mode parameter or branch) found")


# -- data quality framework checks (3.6.x) ------------------------------------
_WRITE_PATTERN = re.compile(
    r"\.saveAsTable\s*\(|\.write\b|INSERT\s+INTO|INSERT\s+OVERWRITE",
    re.IGNORECASE,
)
_COUNT_RECONCILE = re.compile(
    # A count that is actually asserted or compared against an expectation. A count
    # merely assigned, used as a column alias, or compared to 0 (an emptiness guard)
    # reconciles nothing.
    #
    # The assertion arm carries the same zero-guard as the bare-comparison arm, and
    # skips a line that probes for nulls. Without both, a key-quality assertion such
    # as ``assert df.filter(col(k).isNull()).count() == 0`` scores as record-count
    # reconciliation - it counts rows, but it reconciles nothing against a source.
    #
    # The guard must be ``(?!\s*0\b)``, not ``\s*(?!0\b)``: the latter lets ``\s*``
    # backtrack to zero width so the lookahead tests the space rather than the ``0``,
    # and ``assert df.count() > 0`` slips through.
    r"assert(?![^\n]*\.is(?:Not)?Null\s*\()[^\n]*?\.count\s*\([^\n]*?(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"\.count\s*\(\s*\)\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:row|record|source|target|actual|expected|recon)_count\b\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:==|!=|<=|>=|<|>)\s*(?:row|record|source|target|actual|expected|recon)_count\b|"
    # Explicitly named reconciliation / row-count validation.
    r"reconcil|\bcount_check\b|validate[^\n]*count|expect_table_row_count",
    re.IGNORECASE,
)
_RI_PATTERN = re.compile(
    r"""(?isx)
    (?:\.join\s*\(.*?["']left_anti["'])
    |
    (?:\.join\s*\(.*?["']left["'].*?\.(?:where|filter)\s*\(.*?isNull\s*\()
    |
    (?:\bleft\s+join\b.*?\bwhere\b.*?\bis\s+null\b)
    |
    \b(?:referential|integrity|fk_check|integrity_check|orphan|unmatched|missing_parent|no_parent)\b
    """
)
_JOIN_PATTERN = re.compile(r"(?is)\.join\s*\(|\bleft\s+join\b|\binner\s+join\b|\bright\s+join\b|\bfull\s+join\b", re.IGNORECASE)
_FK_INTEGRITY = re.compile(
    # An anti-join - the standard way to isolate FK values with no parent row.
    # ``"anti"`` is quoted so the join-type argument matches but the English word
    # in a comment or a table name does not.
    r"left_anti|leftanti|\bleft\s+anti\s+join\b|['\"]anti['\"]|"
    # A left join whose unmatched rows are then isolated with a null test, in
    # either the DataFrame or the SQL spelling.
    r"\.isNull\s*\(\s*\)[^\n]*\bjoin\b|\bjoin\b[^\n]*\.isNull\s*\(|"
    r"\bIS\s+NULL\b[^\n]*\bjoin\b|\bjoin\b[^\n]*\bIS\s+NULL\b|"
    # An explicitly named integrity check. It must be a call or an assignment:
    # a bare ``referential`` also matches a comment, a column name or a docstring
    # that merely mentions the idea without performing it.
    r"\b(?:referential_integrity|referential_check|fk_check|integrity_check|ri_check)\s*[\(=]",
    re.IGNORECASE,
)
# One physical read. The chained ``spark.read...load(...)`` form is listed first so it
# is consumed whole rather than counted as both a ``spark.read`` and a ``.load(``. The
# chain may be split by line continuations and may nest one level of parentheses.
_MULTI_SOURCE = re.compile(
    r"spark\.read\b(?:[\s\\]*\.\s*\w+\s*\((?:[^()]|\([^()]*\))*\))*?[\s\\]*\.\s*load\s*\(|"
    r"spark\.read\b|"
    r"spark\.table\s*\(|"
    r"\.load\s*\(",
    re.IGNORECASE,
)
_ORPHAN_DETECT = re.compile(
    r"left_anti|leftanti|\bleft\s+anti\s+join\b|"
    r"orphan|unmatched|no_parent|missing_parent|"
    r"anti.*join.*parent|parent.*anti.*join",
    re.IGNORECASE,
)

# -- 5.3.6: reconciliation *across* sources, not just a row-count assertion ----
#: Comparing one source against another. ``_COUNT_RECONCILE`` alone is not
#: enough here: it is satisfied by a single ``assert df.count() == 100``, which
#: validates one dataset against an expectation and reconciles nothing *between*
#: sources. Each alternative below needs two datasets to be meaningful.
_CROSS_SOURCE_RECON = re.compile(
    # Two counts compared against each other in one expression.
    r"\.count\s*\(\s*\)[^\n]{0,120}?(?:==|!=|<=|>=|<|>)[^\n]{0,120}?\.count\s*\(\s*\)|"
    # Named per-side counts compared, either way round.
    r"\b(?:source|src|left|before|expected)_count\b[^\n]{0,60}(?:==|!=|<=|>=|<|>)"
    r"[^\n]{0,60}\b(?:target|tgt|right|after|actual)_count\b|"
    r"\b(?:target|tgt|right|after|actual)_count\b[^\n]{0,60}(?:==|!=|<=|>=|<|>)"
    r"[^\n]{0,60}\b(?:source|src|left|before|expected)_count\b|"
    # A set difference — the primitive that answers "what is in A but not B".
    r"\.subtract\s*\(|\.exceptAll\s*\(|\bEXCEPT\s+(?:ALL\s+)?SELECT\b|\bMINUS\s+SELECT\b|"
    # An anti-join between the two sources, which surfaces the non-matching rows.
    r"left_anti|leftanti|\bleft\s+anti\s+join\b|"
    # Explicitly named cross-source reconciliation.
    r"cross[_\s-]?source[_\s-]?recon|source[_\s-]?to[_\s-]?target|"
    r"\breconcile_sources\b|\bcompare_sources\b",
    re.IGNORECASE,
)

# -- 5.3.7: identified is not the same as handled -----------------------------
#: Turning an identified orphan set into an outcome: persisted, raised on, or
#: recorded. ``display()``/``show()`` are deliberately absent - showing a
#: dataframe is identification, which the check already credits separately.
_ORPHAN_HANDLED = (
    r"\.write\b|saveAsTable\s*\(|\.save\s*\(|insertInto\s*\(|"
    r"\braise\b|\bassert\b|sys\s*\.\s*exit\s*\(|notebook\s*\.\s*exit\s*\(|"
    r"logger\s*\.|logging\s*\."
)
#: Names teams give a quarantined/rejected record set. Only meaningful next to a
#: handling verb - a column called ``rejected_flag`` in unrelated business data
#: must not read as orphan handling.
_QUARANTINE_NAME = (
    r"quarantin|reject|bad[_-]?record|error[_-]?record|error[_-]?table|"
    r"exception[_-]?record|dead[_-]?letter|\bdlq\b"
)
#: ``orphans = <expression containing an anti-join>``. The window spans lines so
#: a multi-line ``spark.sql(\"\"\"... LEFT ANTI JOIN ...\"\"\")`` still binds to
#: its variable; it is lazy and bounded so it cannot run on into a later
#: statement's anti-join.
_ANTI_JOIN_ASSIGN = re.compile(
    r"^[ \t]*(\w+)\s*=[\s\S]{0,300}?(?:left_anti|leftanti|\bleft\s+anti\s+join\b)",
    re.IGNORECASE | re.MULTILINE,
)


def _orphan_set_is_handled(code: str) -> bool:
    """True when an identified orphan set is persisted, raised on, or recorded.

    Two ways to satisfy it. A write whose destination is a quarantine/reject
    target is self-evident wherever it appears. Otherwise each anti-join is bound
    to the variable it is assigned to, and that variable must be used downstream
    in a handling verb - a set that is computed and never referenced again was
    identified but not handled.
    """
    for first, second in ((_ORPHAN_HANDLED, _QUARANTINE_NAME),
                          (_QUARANTINE_NAME, _ORPHAN_HANDLED)):
        if re.search(rf"(?:{first})[^\n]*(?:{second})", code, re.IGNORECASE):
            return True
    for match in _ANTI_JOIN_ASSIGN.finditer(code):
        name = re.escape(match.group(1))
        rest = code[match.end():]
        if re.search(rf"\b{name}\b[^\n]*?(?:{_ORPHAN_HANDLED})", rest, re.IGNORECASE):
            return True
        if re.search(rf"(?:{_ORPHAN_HANDLED})[^\n]*?\b{name}\b", rest, re.IGNORECASE):
            return True
    return False


# -- 5.3.9: the validated table must be the merged table ----------------------
#: A table identifier, optionally schema-qualified and optionally quoted.
_TABLE_IDENT = r"[`\"\[]?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)[`\"\]]?"

_MERGE_TARGET = re.compile(rf"MERGE\s+INTO\s+{_TABLE_IDENT}", re.IGNORECASE)
#: ``tgt = DeltaTable.forName(spark, "gold.sales")`` - binds a variable to a table.
_DELTA_BINDING = re.compile(
    r"(\w+)\s*=\s*DeltaTable\s*\.\s*(?:forName|forPath)\s*\([^)]*?['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_HISTORY_TABLE = re.compile(rf"DESCRIBE\s+HISTORY\s+{_TABLE_IDENT}", re.IGNORECASE)
_HISTORY_VAR = re.compile(r"(\w+)\s*\.\s*history\s*\(")
#: Validation read off the merge itself. These describe the write that just
#: happened, so they carry no table name and need no cross-check.
_MERGE_SELF_VALIDATE = re.compile(
    r"operationMetrics|rows_(?:inserted|updated|deleted)|num_(?:inserted|updated|deleted)|"
    r"merge[_\s]*count\b|merge_result|merge_valid|post.?merge.*count",
    re.IGNORECASE,
)


def _table_leaf(name: str) -> str:
    """Bare table name from a qualified name, a path, or a quoted identifier."""
    text = str(name or "").strip().strip("`\"'[]")
    if "/" in text:
        text = text.rstrip("/").split("/")[-1]
    return text.split(".")[-1].strip().lower()


def _merge_targets(code: str) -> set[str]:
    """Tables this notebook merges into."""
    targets = {_table_leaf(m.group(1)) for m in _MERGE_TARGET.finditer(code)}
    targets |= {_table_leaf(m.group(2)) for m in _DELTA_BINDING.finditer(code)}
    return {t for t in targets if t}


def _validated_tables(code: str) -> set[str]:
    """Tables whose history this notebook inspects after the merge."""
    tables = {_table_leaf(m.group(1)) for m in _HISTORY_TABLE.finditer(code)}
    bound = {m.group(1): _table_leaf(m.group(2)) for m in _DELTA_BINDING.finditer(code)}
    for match in _HISTORY_VAR.finditer(code):
        if match.group(1) in bound:
            tables.add(bound[match.group(1)])
    return {t for t in tables if t}

# A Delta merge is often issued through a variable bound to DeltaTable.forName/forPath,
# so the ``.merge(`` call site alone does not carry the DeltaTable name.
_MERGE_PATTERN = re.compile(
    r"MERGE\s+INTO|"
    r"DeltaTable\s*\.\s*merge\s*\(|"
    r"DeltaTable\s*\.\s*(?:forName|forPath)\s*\([\s\S]*?\.\s*merge\s*\(|"
    r"\.\s*merge\s*\([\s\S]{0,400}?\.\s*whenMatched",
    re.IGNORECASE,
)
_MERGE_VALIDATE = re.compile(
    # ``merge[_ ]count`` must be a real token: a bare ``merge.*count`` also matches
    # the target name in "MERGE INTO gold_customer_country".
    r"operationMetrics|DESCRIBE\s+HISTORY|merge[_\s]*count\b|"
    r"rows_inserted|rows_updated|rows_deleted|"
    r"num_inserted|num_updated|num_deleted|"
    r"merge_result|merge_valid|post.?merge.*count",
    re.IGNORECASE,
)
_DEDUP_PATTERN = re.compile(
    # Real dedup calls only. A bare ``row_number()`` ranks rows without removing
    # any, so it counts only when a later filter keeps rank 1. The word "dedup"
    # must be a call: "Error during deduplication" is a message, not a control.
    r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.duplicated\s*\(|"
    r"\.distinct\s*\(|"
    r"row_number\s*\(\s*\)[\s\S]{0,120}?\bover\b[\s\S]{0,400}?(?:==|=)\s*1\b|"
    r"\bdedup\w*\s*\(|"
    # The group-by-then-keep-count-above-one idiom, the SQL/PySpark way of
    # surfacing duplicate keys without calling any dedup API.
    r"groupBy[^\n]*\.count\s*\(\s*\)[^\n]*?count[^\n]*?>\s*1",
    re.IGNORECASE,
)
_TYPE_CAST = re.compile(
    # An explicit cast, an explicit schema, or typed DDL. A bare ``IntegerType()``
    # is excluded: it appears in type *introspection* as often as in a schema.
    r"\.cast\s*\(|to_date\s*\(|to_timestamp\s*\(|\.astype\s*\(|"
    r"StructField\s*\(|"
    # SQL casting, written in selectExpr / spark.sql / a %%sql cell.
    r"\bCAST\s*\(\s*[\w.`\"\[\]]+\s+AS\s+\w+|"
    # Typed DDL. The type must sit inside the column-definition parentheses, so a
    # CREATE TABLE ... AS SELECT that merely mentions DATE later does not count.
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE[^\n(]*\([^)]{0,400}?"
    r"\b\w+\s+(?:INT|BIGINT|SMALLINT|STRING|VARCHAR|CHAR|DECIMAL|NUMERIC|TIMESTAMP|DATE|"
    r"BOOLEAN|DOUBLE|FLOAT)\b",
    re.IGNORECASE,
)
# Case matters here: ``Id``/``ID``/``_id`` are key suffixes, but a case-insensitive
# ``id`` also matches the tail of ordinary words such as "valid", and a lower-case
# ``key`` matches "monkey". Each alternative therefore carries its own boundary.
_KEY_NAME = r"(?:\bkey\b|\w*_(?:key|KEY|id|ID)\b|\w+(?:Key|KEY|Id|ID)\b)"
_KEY_QUALITY = re.compile(
    # A null test on a key-named column, either way round, or a dedup keyed on one.
    rf"{_KEY_NAME}[^\n]{{0,40}}\.is(?:Not)?Null\s*\(|"
    rf"\.is(?:Not)?Null\s*\([^\n]{{0,60}}{_KEY_NAME}|"
    r"drop[Dd]uplicates\s*\(\s*(?:subset\s*=\s*)?\[|"
    r"drop_duplicates\s*\(\s*(?:subset\s*=\s*)?\[|"
    r"dropna\s*\([^\n]*subset",
)
_BRONZE_METADATA = re.compile(
    r"ingest(?:ed|ion)[_ ]?(?:timestamp|time|date)|ingested_at|"
    r"source[_ ]?(?:system|file|path)|input_file_name\s*\(|batch[_ ]?(?:id|key)|"
    r"current_timestamp\s*\(",
    re.IGNORECASE,
)
_SILVER_QUALITY = re.compile(
    r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.distinct\s*\(\)|"
    r"\.cast\s*\(|to_date\s*\(|to_timestamp\s*\(|regexp_replace\s*\(|"
    r"trim\s*\(|standardize|conform|cleanse|cleansing|dedup",
    re.IGNORECASE,
)
_BULK_ACTIVITY = re.compile(
    r"parallelCopies|batchCount|batch[_ -]?size|bulk|copy activity|"
    r"COPY\s+INTO|write\.mode|saveAsTable|repartition|coalesce",
    re.IGNORECASE,
)
_ROW_BY_ROW = re.compile(
    r"ForEach|foreach|row[_ -]?by[_ -]?row|insert\s+into|execute\s+query|"
    r"SqlServerStoredProcedure|Script",
    re.IGNORECASE,
)
_EAM_JSON = re.compile(r"EAM|JSON|json\.loads|from_json\s*\(|spark\.read\.json", re.IGNORECASE)
_EAM_EFFICIENT = re.compile(
    r"readStream|partitionBy\s*\(|repartition\s*\(|multiLine\s*[=:]\s*False|"
    r"maxFilesPerTrigger|badRecordsPath|from_json\s*\(|schema_of_json",
    re.IGNORECASE,
)
_SOURCE_METADATA = re.compile(
    r"ingest(?:ed|ion)[_ ]?(?:timestamp|time|date)|ingested_at|"
    r"source[_ ]?(?:metadata|system|file|path)|input_file_name\s*\(|"
    r"current_timestamp\s*\(|_metadata|batch[_ ]?timestamp",
    re.IGNORECASE,
)
_INPUT_READ = re.compile(
    r"spark\.read|\.read\.(?:csv|json|text|format)|json\.loads|from_json\s*\(|"
    r"read_json|read_csv|EAM\s+JSON|EAM_JSON",
    re.IGNORECASE,
)
_DUPLICATE_VERIFICATION = re.compile(
    r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.duplicated\s*\(|"
    r"groupBy[\s\S]{0,160}?\.count\s*\(\s*\)[\s\S]{0,160}?(?:>|duplicate)|"
    r"count\s*\(\s*\)\s*!=\s*count\s*\(\s*distinct|"
    r"assert[_ ]?(?:unique|distinct|no[_ ]?duplicates)|duplicate[_ ]?(?:check|count|rows?)|"
    r"dropDuplicates|distinct\s*\(",
    re.IGNORECASE,
)
_TEXT_ENCODING = re.compile(
    r"encoding\s*[=:]\s*[\"']?utf[-_]?8|option\s*\(\s*[\"']encoding[\"']\s*,\s*[\"']utf[-_]?8|"
    r"decode\s*\(\s*[\"']utf[-_]?8|StringType\s*\(\)|utf[-_]?8",
    re.IGNORECASE,
)
_FLAG_DOMAIN = re.compile(
    r"(?:flag|boolean|is_[A-Za-z0-9_]+|active|enabled|valid)[^\n]{0,120}?\.isin\s*\(|"
    r"\.isin\s*\(\s*(?:True|False|[\"'](?:Y|N|true|false|0|1)[\"'])|"
    r"(?:allowed|valid)[_ ]?(?:values|flags)|expected[_ ]?(?:values|flags)|"
    r"BooleanType\s*\(\)|when\s*\([^\n]{0,120}?\)\.otherwise\s*\(",
    re.IGNORECASE,
)
_DQ_RULE = re.compile(
    r"assert\b|validation|validate|quality|quarantine|reject|invalid|"
    r"dropDuplicates|drop_duplicates|left_anti|isNull|isin\s*\(|"
    r"StructType|StructField|expected[_ ]?(?:schema|columns|values)",
    re.IGNORECASE,
)


@check(
    id="NB-RECON-COUNT", ref="5.2.5",
    title="Record count reconciliation vs. source system control counts",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_recon_count(ctx: CheckContext) -> Verdict:
    """Notebooks that write data validate row counts against source expectations."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_COUNT_RECONCILE.search(code))
    return binary(ok, "Record count reconciliation present" if ok
                  else "Writes data without record count validation")


@check(
    id="NB-FK-INTEGRITY", ref="5.3.2",
    title="Referential integrity: FK values exist in corresponding dimension/lookup tables",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_fk_integrity(ctx: CheckContext) -> Verdict:
    """Notebooks that join tables verify FK values exist in dimension/lookup tables."""
    code = executable_code(ctx.obj)
    if not _JOIN_PATTERN.search(code):
        return not_applicable("Notebook does not perform joins")
    ok = bool(_FK_INTEGRITY.search(code))
    return binary(ok, "Referential integrity check present" if ok
                  else "Joins tables without FK/referential integrity validation")


@check(
    id="NB-CROSS-RECON", ref="5.3.6",
    title="Cross-source reconciliation: records from multiple sources reconciled correctly",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_cross_recon(ctx: CheckContext) -> Verdict:
    """Notebooks reading multiple sources reconcile records **across** them.

    A single row-count assertion validates one dataset against an expectation; it
    says nothing about whether two sources agree. This therefore looks for a
    comparison that needs both sides — two counts compared with each other, a set
    difference, an anti-join, or an explicitly named cross-source reconciliation.

    A notebook that validates counts but never compares the sources scores in the
    middle: the discipline is there, the cross-source part is not.
    """
    code = executable_code(ctx.obj)
    sources = _MULTI_SOURCE.findall(code)
    if len(sources) < 2:
        return not_applicable("Notebook reads from fewer than 2 sources")
    if _CROSS_SOURCE_RECON.search(code):
        return binary(True, f"Reads {len(sources)} sources and reconciles across them")
    if _COUNT_RECONCILE.search(code):
        return graded(
            1,
            f"Reads {len(sources)} sources and validates a record count, but nothing "
            f"compares the sources against each other — no count-vs-count check, set "
            f"difference, or anti-join",
        )
    return binary(False, f"Reads {len(sources)} sources without cross-source reconciliation")


@check(
    id="NB-ORPHAN-DETECT", ref="5.3.7",
    title="Orphan detection: child records without matching parent records identified and handled",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_orphan_detect(ctx: CheckContext) -> Verdict:
    """Notebooks with joins detect orphan/unmatched child records **and handle them**.

    The point asks for two things. Identification is an anti-join or an equivalent
    unmatched-row probe. Handling is what happens next: the unmatched set is
    persisted (a quarantine/error table), raised on, or recorded. A set that is
    computed and then dropped satisfies half the point, so it scores in the middle
    rather than passing outright.
    """
    code = executable_code(ctx.obj)
    if not _JOIN_PATTERN.search(code):
        return not_applicable("Notebook does not perform joins")
    if not _ORPHAN_DETECT.search(code):
        return binary(False, "Joins tables without orphan record detection")
    if _orphan_set_is_handled(code):
        return binary(True, "Orphan/unmatched records are detected and handled")
    return graded(
        1,
        "Orphan/unmatched records are detected but not handled - the unmatched set "
        "is computed and then dropped, never quarantined, raised on or recorded",
    )


@check(
    id="NB-MERGE-VALID", ref="5.3.9",
    title="Merge result validation: post-merge counts reconcile with source I/U/D counts",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_merge_valid(ctx: CheckContext) -> Verdict:
    """Notebooks performing MERGE validate post-merge counts against I/U/D expectations.

    Where the validation names a table, that table must be one the notebook merged
    into - reading ``DESCRIBE HISTORY`` on some *other* table proves nothing about
    the merge. Metrics read off the merge itself (``operationMetrics``, I/U/D row
    counts) describe the write that just happened and need no cross-check. When
    neither the target nor the validated table can be resolved from the code, the
    result is N/A rather than a guess.
    """
    code = executable_code(ctx.obj)
    if not _MERGE_PATTERN.search(code):
        return not_applicable("Notebook does not perform MERGE operations")
    if not _MERGE_VALIDATE.search(code):
        return binary(False, "MERGE without post-merge count/result validation")

    if _MERGE_SELF_VALIDATE.search(code):
        return binary(True, "Post-merge result validation reads the merge's own metrics")

    targets = _merge_targets(code)
    validated = _validated_tables(code)
    if not targets or not validated:
        return not_applicable(
            "Post-merge validation is present, but the merged table could not be "
            "resolved from the notebook, so it cannot be confirmed to validate the "
            "table that was merged"
        )
    matched = targets & validated
    if matched:
        return binary(True, f"Post-merge validation covers the merged table(s): "
                            f"{', '.join(sorted(matched))}")
    return binary(
        False,
        f"Post-merge validation inspects {', '.join(sorted(validated))} but the "
        f"MERGE targets {', '.join(sorted(targets))} - a different table is validated",
    )


# -- MLC Cat-1: warehouse load idempotency (3.6.4) ----------------------------
#: Re-runnable write patterns. MERGE upserts on a key; TRUNCATE/DELETE-then-INSERT
#: clears the target slice first. All three leave the same rows after a re-run.
_IDEMPOTENT_SQL = (
    (re.compile(r"\bMERGE\s+INTO\b", re.IGNORECASE), "MERGE INTO"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE), "TRUNCATE then INSERT"),
    (re.compile(r"\bDELETE\s+FROM\b[\s\S]{0,400}?\bWHERE\b", re.IGNORECASE),
     "DELETE ... WHERE then INSERT"),
)
#: A bare append. Run it twice and the target has the rows twice.
_PLAIN_INSERT = re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE)


@check(
    id="PL-IDEMPOTENT-LOAD", ref="3.6.4",
    title="Warehouse load procedures are idempotent and re-runnable",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_idempotent_load(ctx: CheckContext) -> Verdict:
    """Re-running the load leaves the same rows — no duplicates, no double counting.

    Reads the T-SQL a pipeline runs through its Script activities, which is where
    warehouse loads usually live. A load is re-runnable when it MERGEs on a key,
    or clears the target slice (TRUNCATE, or DELETE ... WHERE) before inserting.
    A bare ``INSERT INTO`` is the failing case: run it twice, get the rows twice.

    A Copy activity whose sink upserts counts too. Stored procedures defined
    inside the Warehouse are not readable, so a pipeline that only calls one is
    reported as N/A rather than guessed at.
    """
    sql = script_sql(ctx.obj)
    upserting_copy = [
        a for a in walk_activities(ctx.obj)
        if a.get("type") == "Copy"
        and "upsert" in str(((a.get("typeProperties") or {}).get("sink") or {})
                            .get("writeBehavior", "")).lower()
    ]
    if upserting_copy:
        names = ", ".join(sorted(a.get("name", "?") for a in upserting_copy))
        return binary(True, f"Copy activity writes with upsert behaviour: {names}")

    if not sql:
        proc_calls = [a for a in walk_activities(ctx.obj)
                      if a.get("type") in ("SqlServerStoredProcedure", "StoredProcedure")]
        if proc_calls:
            names = ", ".join(sorted(a.get("name", "?") for a in proc_calls))
            return not_applicable(
                f"Load runs a stored procedure ({names}); its body lives in the "
                f"Warehouse and cannot be read")
        return not_applicable("Pipeline runs no SQL load logic")

    found = [label for pattern, label in _IDEMPOTENT_SQL if pattern.search(sql)]
    if found:
        return binary(True, f"Load is re-runnable — uses {', '.join(found)}")
    if _PLAIN_INSERT.search(sql):
        return binary(False, "Load uses a bare INSERT INTO with no MERGE, TRUNCATE or "
                             "DELETE guard — re-running duplicates rows")
    return not_applicable("Script activities run no INSERT/MERGE load statement")


# -- MLC Cat-1: dimensional load quality (4.5.10, 5.4.4, 5.4.6) ---------------
#: The notebook is doing dimensional work at all — otherwise these checks have
#: nothing to judge and must report N/A rather than fail a Bronze ingest.
_DIM_CONTEXT = re.compile(r"\bdim[_\s.]|\bdimension\b|\bfact[_\s.]|\bfct[_\s.]", re.IGNORECASE)

#: A late-arriving fact is given an inferred / unknown member instead of being
#: dropped: a stub dimension row, or the conventional -1 surrogate key.
#: The -1 forms all allow a nested call — ``coalesce(col("x"), lit(-1))`` is the
#: common Spark idiom — so match "-1 appears shortly after the call", not a
#: bracket-exact shape.
_LATE_ARRIVING = re.compile(
    r"inferred[_\s]?member|late[_\s]?arriv|unknown[_\s]?member|is_inferred|"
    r"coalesce\s*\([^\n]{0,80}?-1|"
    r"fillna\s*\([^\n]{0,60}?-1|"
    r"\.na\.fill\s*\([^\n]{0,60}?-1|"
    r"otherwise\s*\([^\n]{0,40}?-1|"
    r"when\s*\([^\n]{0,80}?-1|"
    r"['\"]unknown['\"]",
    re.IGNORECASE,
)
#: Unknown/orphan member usage is counted or logged, not just silently allowed.
_UNKNOWN_MONITORED = re.compile(
    r"unknown[_\s]?(?:count|rate|pct|percent|usage)|orphan[_\s]?(?:count|rate|pct)|"
    r"(?:count|sum)\s*\([^)]{0,60}\)[^\n]{0,100}?(?:unknown|inferred|orphan|-1)|"
    r"(?:log|insert\s+into|write)[^\n]{0,80}?(?:unknown|orphan|inferred)[^\n]{0,40}?(?:count|log|audit)",
    re.IGNORECASE,
)
#: Names of the medallion layers, to spot a notebook that spans two of them.
_SILVER_REF = re.compile(r"\bsilver\b", re.IGNORECASE)
_GOLD_REF = re.compile(r"\bgold\b", re.IGNORECASE)


@check(
    id="NB-LATE-ARRIVING", ref="4.5.10",
    title="Late-arriving dimensions and facts handled (unknown/inferred member pattern)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_late_arriving(ctx: CheckContext) -> Verdict:
    """A fact arriving before its dimension gets an inferred member, not dropped.

    Without the pattern the load either discards the fact — silent data loss —
    or fails outright. The evidence is a stub/unknown member: an ``is_inferred``
    flag, an explicit "unknown" member, or the conventional ``-1`` surrogate key
    substituted when the lookup misses.
    """
    code = notebook_code(ctx.obj)
    if not _DIM_CONTEXT.search(code):
        return not_applicable("Notebook does not load or join dimensional tables")
    if not _JOIN_PATTERN.search(code):
        return not_applicable("Notebook performs no fact-to-dimension lookup")
    ok = bool(_LATE_ARRIVING.search(code))
    return binary(ok, "Late-arriving rows get an inferred/unknown member" if ok
                  else "Joins dimensions with no inferred/unknown member fallback — "
                       "late-arriving facts are dropped or fail the load")


@check(
    id="NB-UNKNOWN-MONITOR", ref="5.4.4",
    title="Completeness: all expected dimension members present; unknown/orphan member usage monitored",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_unknown_monitored(ctx: CheckContext) -> Verdict:
    """Rows landing on the unknown member are counted, not silently accepted.

    Routing unmatched keys to an unknown member keeps the load running, but if
    nobody counts them a broken feed looks healthy for months. Only notebooks
    that actually use an unknown/inferred member are judged — creating one is
    NB-LATE-ARRIVING's job.
    """
    code = notebook_code(ctx.obj)
    if not _LATE_ARRIVING.search(code):
        return not_applicable("Notebook uses no unknown/inferred member to monitor")
    ok = bool(_UNKNOWN_MONITORED.search(code))
    return binary(ok, "Unknown/orphan member usage is counted or logged" if ok
                  else "Uses an unknown member but never counts how many rows land "
                       "on it — a broken feed would go unnoticed")


@check(
    id="NB-LAYER-RECON", ref="5.4.6",
    title="Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_layer_recon(ctx: CheckContext) -> Verdict:
    """A notebook promoting Silver to Gold reconciles the row counts across the hop.

    Distinct from NB-CROSS-RECON (ref 3.6.3), which reconciles several *sources*
    in one load. This one is about the medallion hop: rows that entered Silver
    should be accounted for in Gold, allowing for aggregation.
    """
    code = notebook_code(ctx.obj)
    if not (_SILVER_REF.search(code) and _GOLD_REF.search(code)):
        return not_applicable("Notebook does not span the Silver and Gold layers")
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook reads both layers but writes neither")
    ok = bool(_COUNT_RECONCILE.search(code))
    return binary(ok, "Cross-layer row counts are reconciled" if ok
                  else "Promotes Silver to Gold without reconciling row counts "
                       "across the hop")


# -- MLC Cat-1: grain uniqueness (5.4.9) --------------------------------------
#: The load asserts one row per grain: an explicit dedup, or a duplicate probe.
_GRAIN_GUARD = re.compile(
    r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.distinct\s*\(|"
    r"row_number\s*\(\s*\)[^\n]{0,120}?(?:==|=)\s*1|"
    r"groupBy\s*\([^\n]{0,80}?\)[^\n]{0,60}?count\s*\(\s*\)[^\n]{0,60}?(?:>|filter|where)|"
    r"having\s+count\s*\(\s*\*?\s*\)\s*>\s*1|duplicate[_\s]?(?:check|count|test)",
    re.IGNORECASE,
)


@check(
    id="NB-GRAIN-UNIQUE", ref="5.4.9",
    title="No duplicate grain: fact tables contain unique records per defined grain",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_grain_unique(ctx: CheckContext) -> Verdict:
    """A notebook writing a fact table dedupes it, or proves it has no duplicates.

    The grain itself is not declared anywhere machine-readable, so this judges
    whether the load *enforces* uniqueness at all — a dedup, a row-number filter,
    or a duplicate probe. It cannot confirm the surviving rows are unique; that
    needs a GROUP BY against the warehouse.
    """
    code = notebook_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook writes no table")
    if not _DIM_CONTEXT.search(code):
        return not_applicable("Notebook does not write a fact or dimension table")
    ok = bool(_GRAIN_GUARD.search(code))
    return binary(ok, "Load enforces grain uniqueness (dedup / duplicate check)" if ok
                  else "Writes a fact/dimension table with no dedup or duplicate "
                       "check — re-runs and late replays can duplicate the grain")


# -- MLC Cat-1: fact-to-dimension referential integrity (4.5.12) --------------
@check(
    id="NB-FACT-DIM-RI", ref="4.5.12",
    title="Referential integrity validated (every FK in fact tables has a matching dimension record)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_fact_dim_ri(ctx: CheckContext) -> Verdict:
    """A fact load proves its FKs resolve to real dimension rows.

    Narrower than NB-FK-INTEGRITY (ref 3.6.2), which accepts any lookup
    validation on any join. This one only judges notebooks doing *dimensional*
    work, because Fabric Warehouse declares foreign keys but does not enforce
    them — nothing stops an unmatched key being written except this validation.

    Detects the anti-join idiom (``left_anti``), an explicit null-check after a
    left join, or a named referential/integrity check.
    """
    code = notebook_code(ctx.obj)
    if not _DIM_CONTEXT.search(code):
        return not_applicable("Notebook does not load or join dimensional tables")
    if not _JOIN_PATTERN.search(code):
        return not_applicable("Notebook performs no fact-to-dimension join")
    ok = bool(_RI_PATTERN.search(code))
    ok = bool(_RI_PATTERN.search(code))
    return binary(ok, "Fact FKs are validated against the dimension (anti-join / "
                      "null check)" if ok
                  else "Joins facts to dimensions without validating that every FK "
                       "resolves — Fabric Warehouse does not enforce FK constraints")


# -- MLC Cat-1: orphaned file cleanup (4.3.4) ---------------------------------
#: A deliberate purge / archive routine over the Files section. Distinct from
#: VACUUM, which only reclaims Delta table files (that is DELTA-VACUUM's job).
_FILE_PURGE = re.compile(
    r"(?:mssparkutils|notebookutils)\s*\.\s*fs\s*\.\s*rm\s*\(|"
    r"\bshutil\s*\.\s*rmtree\s*\(|\bos\s*\.\s*remove\s*\(|"
    r"archive[_\s]?(?:old|file|policy)|purge[_\s]?(?:old|file)|"
    r"retention[_\s]?(?:policy|days|cutoff)|cleanup[_\s]?(?:old|file)",
    re.IGNORECASE,
)


@check(
    id="WS-FILE-PURGE", ref="4.3.4",
    title="Orphaned files cleaned up periodically (archiving/purging policy)",
    pillar=Pillar.PERFORMANCE, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.STORAGE, Layer.PREP, Layer.MIXED),
    requires=[Resource.ITEMS, Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def ws_file_purge(ctx: CheckContext) -> Verdict:
    """Somewhere in the solution, stale Files-section data is archived or purged.

    Scoped to the workspace rather than to each notebook: a purge routine is a
    housekeeping job that exists once, so failing every notebook that is not it
    would be noise. Asks only whether the routine exists at all.

    The Files listing itself is not readable through the Fabric REST API, so
    this cannot confirm files were actually removed — only that a routine is
    implemented.
    """
    lakehouses = [i for i in ctx.workspace.items if i.type in ("Lakehouse", "Warehouse")]
    if not lakehouses:
        return not_applicable("Workspace holds no lakehouse or warehouse")
    if not ctx.workspace.notebooks:
        return not_applicable("No notebook definitions available to inspect for a "
                              "purge routine")
    purging = [name for name, nb in ctx.workspace.notebooks.items()
               if _FILE_PURGE.search(notebook_code(nb))]
    if purging:
        return binary(True, f"File archive/purge routine found in: {', '.join(sorted(purging))}")
    return binary(False, f"{len(lakehouses)} lakehouse/warehouse item(s) but no notebook "
                         f"implements a file archive or purge routine — stale Files "
                         f"data accumulates indefinitely")


@check(
    id="PL-LATE-ARRIVAL", ref="2.3.8",
    title="Out-of-order / late-arriving change records handled without data corruption",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_late_arrival(ctx: CheckContext) -> Verdict:
    """Late or out-of-order changes use a version-aware, duplicate-safe write path."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for late changes")
    blob = json.dumps(ctx.obj)
    late_signal = bool(_LATE_ARRIVAL.search(blob))
    safe_write = bool(_LATE_SAFE_WRITE.search(blob))
    if not late_signal:
        return not_applicable("No late-arrival or out-of-order handling signal found")
    return binary(
        safe_write,
        "Late/out-of-order handling uses a version-aware or duplicate-safe write pattern"
        if safe_write else
        "Late/out-of-order handling is indicated but no version-aware duplicate-safe write was found",
    )


@check(
    id="NB-DEDUP", ref="5.2.6",
    title="Duplicate detection across batches",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_dedup(ctx: CheckContext) -> Verdict:
    """Notebooks that write data de-duplicate, so a re-run cannot double-load rows."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_DEDUP_PATTERN.search(code))
    return binary(ok, "Duplicate detection present" if ok
                  else "Writes data without duplicate detection")


@check(
    id="NB-TYPE-CAST", ref="5.3.1",
    title="Data type conformance: all columns cast to standard types (dates as DATE, correct numeric precision)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_type_cast(ctx: CheckContext) -> Verdict:
    """Notebooks that write data cast columns explicitly instead of trusting inference."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_TYPE_CAST.search(code))
    return binary(ok, "Explicit type conformance present" if ok
                  else "Writes data without explicit type casting")


@check(
    id="NB-KEY-QUALITY", ref="5.5.6",
    title="**Identifiers / Keys**: Uniqueness verified; format consistent; no nulls in key columns",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_key_quality(ctx: CheckContext) -> Verdict:
    """Notebooks that write data validate key columns for nulls and duplicates."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_KEY_QUALITY.search(code))
    return binary(ok, "Key uniqueness / null validation present" if ok
                  else "Writes data without key uniqueness or null validation")


@check(
    id="NB-BRONZE-METADATA", ref="1.2.3",
    title="Bronze Lakehouse captures raw data with audit metadata (ingestion timestamp, source system, batch ID)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_bronze_metadata(ctx: CheckContext) -> Verdict:
    """Bronze writes retain ingestion timestamp, source identity, and batch metadata."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write a raw/Bronze table")
    if not _BRONZE_METADATA.search(code):
        return binary(False, "Bronze write has no ingestion/source/batch audit metadata")
    return binary(True, "Bronze write captures ingestion/source/batch audit metadata")


@check(
    id="NB-SILVER-QUALITY", ref="1.2.5",
    title="Silver Lakehouse applies cleansing, deduplication, conforming, and type standardization",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_silver_quality(ctx: CheckContext) -> Verdict:
    """Silver writes apply at least one explicit cleansing, deduplication, or type-conformance transformation."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write a Silver table")
    if not re.search(r"silver", code, re.IGNORECASE):
        return not_applicable("Notebook does not identify a Silver-layer write")
    ok = bool(_SILVER_QUALITY.search(code))
    return binary(ok, "Silver write applies cleansing/deduplication/conformance/type standardization" if ok
                  else "Silver write has no recognizable quality transformation")


@check(
    id="PL-BULK-MOVE", ref="2.6.3",
    title="Large data movements use bulk/batch patterns, not row-by-row",
    pillar=Pillar.PERFORMANCE, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_bulk_move(ctx: CheckContext) -> Verdict:
    """Data-moving pipelines use bulk or batch movement rather than row-by-row execution."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    if not any((a.get("type") or "") in _DATA_MOVE_TYPES for a in acts):
        return not_applicable("Pipeline has no data-movement activity")
    blob = json.dumps(ctx.obj)
    if _ROW_BY_ROW.search(blob) and not _BULK_ACTIVITY.search(blob):
        return binary(False, "Data movement shows row-by-row or serial execution without a bulk/batch pattern")
    ok = bool(_BULK_ACTIVITY.search(blob))
    return binary(ok, "Bulk/batch data-movement pattern detected" if ok
                  else "No bulk/batch data-movement pattern detected")


@check(
    id="NB-EAM-INGEST", ref="2.6.6",
    title="JSON ingestion (EAM) is efficient (streaming/partitioned parse, no oversized single-file bottlenecks)",
    pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_eam_ingest(ctx: CheckContext) -> Verdict:
    """EAM/JSON ingestion uses streaming, partitioning, or bounded-file parsing."""
    code = executable_code(ctx.obj)
    if not _EAM_JSON.search(code):
        return not_applicable("Notebook has no recognizable EAM/JSON ingestion")
    ok = bool(_EAM_EFFICIENT.search(code))
    return binary(ok, "EAM/JSON ingestion uses streaming, partitioning, or bounded parsing" if ok
                  else "EAM/JSON ingestion has no streaming/partitioning/bounded-file pattern")


@check(
    id="NB-SOURCE-METADATA", ref="5.2.8",
    title="Source metadata captured: ingestion timestamp",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_source_metadata(ctx: CheckContext) -> Verdict:
    """Notebook writes retain source metadata including an ingestion timestamp."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_SOURCE_METADATA.search(code))
    return binary(ok, "Source metadata and ingestion timestamp are captured" if ok
                  else "Writes data without source metadata or an ingestion timestamp")


@check(
    id="NB-DEDUP-VERIFY", ref="5.3.4",
    title="Deduplication verification: no duplicate business records",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_dedup_verify(ctx: CheckContext) -> Verdict:
    """Notebook logic verifies that duplicate business records are not loaded."""
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    ok = bool(_DUPLICATE_VERIFICATION.search(code))
    return binary(ok, "Duplicate records are verified and handled" if ok
                  else "Writes data without duplicate-record verification")


@check(
    id="NB-UTF8", ref="5.5.3",
    title="String / Text: Encoding validated (UTF-8)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_utf8_encoding(ctx: CheckContext) -> Verdict:
    """Notebook input handling explicitly validates or declares UTF-8 text encoding."""
    code = executable_code(ctx.obj)
    if not _INPUT_READ.search(code):
        return not_applicable("Notebook has no recognizable incoming file or JSON read")
    ok = bool(_TEXT_ENCODING.search(code))
    return binary(ok, "String/text input encoding is explicitly UTF-8" if ok
                  else "Incoming string/text data has no explicit UTF-8 encoding validation")


@check(
    id="NB-FLAG-DOMAIN", ref="5.5.7",
    title="Boolean / Flag: Only expected values permitted",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_flag_domain(ctx: CheckContext) -> Verdict:
    """Notebook logic restricts Boolean and Flag fields to an approved value set."""
    code = executable_code(ctx.obj)
    if not _INPUT_READ.search(code):
        return not_applicable("Notebook has no recognizable incoming file or JSON read")
    ok = bool(_FLAG_DOMAIN.search(code))
    return binary(ok, "Boolean/Flag fields are restricted to expected values" if ok
                  else "Boolean/Flag fields have no explicit allowed-value validation")


@check(
    id="NB-DQ-RULES", ref="5.1.2",
    title="DQ rules codified in code/config (not ad-hoc manual checks)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_dq_rules(ctx: CheckContext) -> Verdict:
    """Notebook data-quality rules are expressed as executable, repeatable logic."""
    code = executable_code(ctx.obj)
    if not (_INPUT_READ.search(code) or _WRITE_PATTERN.search(code)):
        return not_applicable("Notebook has no recognizable data-ingestion or write operation")
    ok = bool(_DQ_RULE.search(code))
    return binary(ok, "Data-quality rule logic is codified in notebook code/config" if ok
                  else "Data movement has no recognizable codified data-quality rules")



# =============================================================================
# MLC Cat-1 · ingestion framework (2.1.5, 2.2.2-2.2.4, 2.3.2-2.3.4, 2.5.1, 2.5.3)
# =============================================================================

# -- 2.1.5 parallel execution -------------------------------------------------
_FOREACH = "ForEach"


@check(
    id="PL-PARALLEL", ref="2.1.5",
    title="Parallel execution used where possible (no unnecessary sequential execution)",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_parallel(ctx: CheckContext) -> Verdict:
    """Independent work fans out instead of running one item at a time."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    loops = [a for a in acts if (a.get("type") or "") == _FOREACH]

    if loops:
        # ``isSequential`` forces one iteration at a time — the single most common
        # cause of an ingestion pipeline running far longer than it needs to.
        serial = sorted((a.get("name") or "?") for a in loops
                        if (a.get("typeProperties") or {}).get("isSequential"))
        parallel_count = len(loops) - len(serial)
        evidence = f"{parallel_count} of {len(loops)} ForEach loop(s) iterate in parallel"
        if serial:
            evidence += f" — forced sequential: {', '.join(serial)}"
        return covered(parallel_count, len(loops), evidence)

    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if len(data_acts) < 2:
        return not_applicable("Fewer than two data activities and no ForEach — "
                              "nothing to parallelize")
    # More than one activity with no upstream dependency means at least two
    # branches start together; a single root is a strict serial chain.
    roots = [a for a in data_acts if not a.get("dependsOn")]
    ok = len(roots) > 1
    return binary(ok, f"{len(roots)} of {len(data_acts)} data activities start "
                      f"independently (work fans out)" if ok
                  else f"All {len(data_acts)} data activities run in a single "
                       f"serial chain with no parallel branch")


# -- 2.2.2 full load reserved for small reference/dimension tables -------------
#: A statement that replaces a table wholesale, capturing the target it hits.
_FULL_LOAD_TARGETS = (
    re.compile(r"TRUNCATE\s+TABLE\s+([\w.\[\]\"]+)", re.IGNORECASE),
    re.compile(r"INSERT\s+OVERWRITE\s+(?:TABLE\s+)?([\w.\[\]\"]+)", re.IGNORECASE),
    re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.\[\]\"]+)", re.IGNORECASE),
)
#: Copy-activity sinks that overwrite rather than append/upsert.
_OVERWRITE_BEHAVIOUR = re.compile(
    r"\"(?:writeBehavior|tableOption)\"\s*:\s*\"(?:overwrite|autoCreate)\"|"
    r"\"preCopyScript\"\s*:\s*\"[^\"]*TRUNCATE",
    re.IGNORECASE,
)


def _bare_table(name: str) -> str:
    """The table name without schema qualifier, brackets, or quotes."""
    return (name or "").replace("[", "").replace("]", "").replace('"', "").split(".")[-1]


@check(
    id="PL-FULLLOAD", ref="2.2.2",
    title="Full load reserved only for small reference/dimension tables or initial loads",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_full_load(ctx: CheckContext) -> Verdict:
    """Wholesale reloads target lookup/dimension tables, never fact tables."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    sql = script_sql(ctx.obj)
    blob = json.dumps(ctx.obj)

    targets: list[str] = []
    for pattern in _FULL_LOAD_TARGETS:
        targets.extend(_bare_table(m) for m in pattern.findall(sql))
    overwrite_copy = bool(_OVERWRITE_BEHAVIOUR.search(blob))

    if not targets and not overwrite_copy:
        return not_applicable("Pipeline runs no full-reload statement "
                              "(TRUNCATE / INSERT OVERWRITE / overwrite sink)")
    if not targets:
        return graded(1, "A Copy activity overwrites its sink, but the target table "
                         "is not named in the definition — cannot confirm it is a "
                         "small reference/dimension table")

    facts = sorted({t for t in targets if is_fact(t)})
    safe = sorted({t for t in targets if not is_fact(t)})
    if facts:
        return binary(False, f"Full reload targets fact table(s): {', '.join(facts)} — "
                             f"facts should load incrementally, not be replaced wholesale")
    kind = "dimension/reference" if any(is_dimension(t) for t in safe) else "reference"
    return binary(True, f"Full reload targets only {kind} table(s): {', '.join(safe)}")


# -- 2.2.3 historical (Adage) load separated from ongoing incremental ----------
_HISTORICAL = re.compile(
    r"historical|back[_ -]?fill|backfill|full[_ -]?history|one[_ -]?time[_ -]?load|"
    r"initial[_ -]?load|adage|reload[_ -]?history",
    re.IGNORECASE,
)
_ONGOING = re.compile(
    r"incremental|daily|hourly|delta[_ -]?load|watermark|cdc|ongoing|scheduled",
    re.IGNORECASE,
)


@check(
    id="PL-HIST-SEPARATION", ref="2.2.3",
    title="Adage historical load clearly separated from ongoing incremental patterns",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_historical_separation(ctx: CheckContext) -> Verdict:
    """A one-off historical backfill cannot be triggered by the routine daily run.

    Narrower than ``PL-LOADMODE`` (2.2.5), which asks whether *any* initial /
    incremental separation exists. This fires only when a historical or backfill
    load is actually present, and asks whether it is kept off the routine path.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    blob = json.dumps(ctx.obj)
    if not _HISTORICAL.search(blob) and not _HISTORICAL.search(ctx.obj_name):
        return not_applicable("No historical / backfill load present in this pipeline")

    # A pipeline whose own name marks it as the historical load is separated by
    # construction — it is a different artifact from the incremental one.
    if _HISTORICAL.search(ctx.obj_name):
        return binary(True, f"'{ctx.obj_name}' is a dedicated historical/backfill "
                            f"pipeline, separate from the ongoing incremental load")

    acts = walk_activities(ctx.obj)
    gated_by_branch = any(
        (a.get("type") or "") in {"Switch", "IfCondition"}
        and (_HISTORICAL.search(json.dumps(a)) or _LOAD_MODE.search(json.dumps(a)))
        for a in acts
    )
    param_gated = any(
        _HISTORICAL.search(p) or _LOAD_MODE.search(p)
        for p in ((ctx.obj.get("properties") or {}).get("parameters") or {})
    )
    if gated_by_branch or param_gated:
        return binary(True, "Historical load is gated behind a parameter or branch, "
                            "so the routine run does not replay history")
    if _ONGOING.search(blob):
        return binary(False, "Historical/backfill logic sits inline with the ongoing "
                             "incremental logic with no parameter or branch separating "
                             "them — a routine run can replay the full history")
    return graded(1, "Historical/backfill logic present but no ongoing incremental "
                     "path found to separate it from")


# -- 2.2.4 watermark / control values persisted durably -----------------------
_WATERMARK = re.compile(r"watermark|high[_ -]?water|last[_ -]?(?:load|run|modified|extract)",
                        re.IGNORECASE)
#: The watermark is written back to a durable store.
_WATERMARK_DURABLE = re.compile(
    r"(?:UPDATE|INSERT\s+INTO|MERGE\s+INTO)\s+[\w.\[\]\"]*"
    r"(?:watermark|control|metadata|etl_?config|load_?log)|"
    r"EXEC(?:UTE)?\s+[\w.\[\]]*(?:set|update|save)_?watermark",
    re.IGNORECASE,
)
_WATERMARK_TABLE_READ = re.compile(
    r"(?:FROM|JOIN)\s+[\w.\[\]\"]*(?:watermark|control|metadata|etl_?config|load_?log)",
    re.IGNORECASE,
)


@check(
    id="PL-WATERMARK-STORE", ref="2.2.4",
    title="Watermark / control values persisted reliably in the Metadata DB (not volatile locations)",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_watermark_store(ctx: CheckContext) -> Verdict:
    """The incremental watermark survives a failure because it lives in a table.

    A watermark held only in a pipeline variable is lost the moment the run ends,
    so the next run either reprocesses everything or silently skips rows.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    blob = json.dumps(ctx.obj)
    if not _WATERMARK.search(blob):
        return not_applicable("Pipeline uses no watermark / control value")

    sql = script_sql(ctx.obj)
    acts = walk_activities(ctx.obj)
    lookup_sql = json.dumps([a for a in acts if (a.get("type") or "") == "Lookup"])
    proc = any((a.get("type") or "") == "SqlServerStoredProcedure"
               and _WATERMARK.search(json.dumps(a)) for a in acts)

    persists = bool(_WATERMARK_DURABLE.search(sql)) or proc
    reads_table = bool(_WATERMARK_TABLE_READ.search(sql)
                       or _WATERMARK_TABLE_READ.search(lookup_sql))

    if persists and reads_table:
        return binary(True, "Watermark is read from and written back to a durable "
                            "control/metadata table")
    if persists:
        return graded(2, "Watermark is written back to a durable table, but no read "
                         "of it was found — confirm the next run picks it up")
    if reads_table:
        return graded(1, "Watermark is read from a control/metadata table but never "
                         "written back — the stored value goes stale")
    return binary(False, "Watermark exists only in pipeline variables/expressions, not "
                         "in a durable control table — it is lost when the run ends")


# -- 2.3.2 operation type flag preserved in Bronze ----------------------------
_BRONZE = re.compile(r"\bbronze\b|\braw\b|\blanding\b|pre[_ -]?bronze", re.IGNORECASE)
_OP_TYPE_COLUMN = re.compile(
    r"operation[_ -]?type|op[_ -]?type|change[_ -]?type|_change_type|__\$operation|"
    r"cdc[_ -]?operation|dml[_ -]?action|record[_ -]?type|\bopcode\b|"
    r"sys[_ -]?change[_ -]?operation",
    re.IGNORECASE,
)


@check(
    id="NB-OPTYPE", ref="2.3.2",
    title="Operation type column/flag preserved in Bronze for auditability where the source provides it",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_operation_type(ctx: CheckContext) -> Verdict:
    """Bronze keeps the source's I/U/D flag so downstream layers can replay it.

    Read from the notebook that writes Bronze rather than from table columns:
    the Fabric REST API returns table metadata without columns, so a
    column-based test would be N/A on every live run.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _WRITE_PATTERN.search(code) or not _BRONZE.search(code):
        return not_applicable("Notebook does not write a Bronze/raw table")
    ok = bool(_OP_TYPE_COLUMN.search(code))
    return binary(ok, "Bronze write carries the source operation type / change flag"
                  if ok else
                  "Bronze write drops the source operation type — an update cannot "
                  "be told from an insert, and deletes are unrecoverable")


# -- 2.3.3 all applicable operation types (I/U/D) handled in the merge --------
_MERGE_STMT = re.compile(r"MERGE\s+INTO|\.merge\s*\(|DeltaTable\s*\.", re.IGNORECASE)
_MERGE_UPDATE = re.compile(
    r"whenMatchedUpdate|WHEN\s+MATCHED[\s\S]{0,120}?THEN\s+UPDATE", re.IGNORECASE)
_MERGE_INSERT = re.compile(
    r"whenNotMatchedInsert|WHEN\s+NOT\s+MATCHED[\s\S]{0,120}?THEN\s+INSERT", re.IGNORECASE)
_MERGE_DELETE = re.compile(
    r"whenMatchedDelete|WHEN\s+MATCHED[\s\S]{0,120}?THEN\s+DELETE|"
    r"WHEN\s+NOT\s+MATCHED\s+BY\s+SOURCE[\s\S]{0,120}?THEN\s+DELETE|"
    r"is_?deleted|soft[_ -]?delete", re.IGNORECASE)


@check(
    id="NB-IUD-MERGE", ref="2.3.3",
    title="All applicable operation types (I/U/D) handled correctly in the merge strategy",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_iud_merge(ctx: CheckContext) -> Verdict:
    """The merge covers inserts, updates and deletes, not just the easy two.

    Distinct from ``DELTA-MERGE`` (3.3.1), which asks whether the upsert is a
    single atomic MERGE. This asks whether that MERGE is *complete*.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _MERGE_STMT.search(code):
        return not_applicable("Notebook runs no MERGE / upsert to assess")

    handled = {
        "update": bool(_MERGE_UPDATE.search(code)),
        "insert": bool(_MERGE_INSERT.search(code)),
        "delete": bool(_MERGE_DELETE.search(code)),
    }
    present = sorted(k for k, v in handled.items() if v)
    missing = sorted(k for k, v in handled.items() if not v)
    if not missing:
        return graded(3, "MERGE handles inserts, updates and deletes")
    if len(present) == 2:
        return graded(2, f"MERGE handles {' and '.join(present)} but not "
                         f"{missing[0]}s — confirm the source never emits them")
    return graded(0, f"MERGE handles only {', '.join(present) or 'none'} of "
                     f"insert/update/delete — {', '.join(missing)} are silently lost")


# -- 2.3.4 insert records validated for uniqueness before merge ---------------
_DEDUP_BEFORE_MERGE = re.compile(
    r"dropDuplicates|drop_duplicates|\.distinct\s*\(|"
    r"row_number\s*\(\s*\)\s*over[\s\S]{0,160}?(?:=|==)\s*1|"
    r"rank\s*\(\s*\)\s*over[\s\S]{0,160}?(?:=|==)\s*1|"
    r"GROUP\s+BY[\s\S]{0,160}?HAVING\s+COUNT\s*\(|"
    r"duplicate[_ -]?check|unique[_ -]?check|assert[_ -]?unique|business[_ -]?key[_ -]?check",
    re.IGNORECASE,
)


@check(
    id="NB-INSERT-UNIQUE", ref="2.3.4",
    title="Insert records validated for uniqueness / business key before merge into target",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_insert_unique(ctx: CheckContext) -> Verdict:
    """Source rows are deduplicated on the business key before they reach the MERGE.

    A Delta MERGE raises on a duplicate source key rather than picking a winner,
    so an un-deduplicated batch fails the run outright.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    if not _MERGE_STMT.search(code):
        return not_applicable("Notebook runs no MERGE / upsert to assess")
    ok = bool(_DEDUP_BEFORE_MERGE.search(code))
    return binary(ok, "Source rows are deduplicated / uniqueness-checked before the merge"
                  if ok else
                  "MERGE runs on the source batch with no deduplication or business-key "
                  "uniqueness check — duplicate source keys abort the merge")


# -- 2.5.1 metadata DB drives ingestion ---------------------------------------
_METADATA_SOURCE = re.compile(
    r"metadata|etl_?config|control[_ -]?table|source[_ -]?list|ingestion[_ -]?config|"
    r"load[_ -]?config|table[_ -]?list|\bconfig\b|manifest",
    re.IGNORECASE,
)
_ITERATES_LOOKUP = re.compile(r"activity\s*\(\s*'[^']+'\s*\)\s*\.output",
                              re.IGNORECASE)


@check(
    id="PL-METADATA-DRIVEN", ref="2.5.1",
    title="Metadata DB drives ingestion (source list, load type, schedule, target mapping) rather than hardcoded pipelines",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_metadata_driven(ctx: CheckContext) -> Verdict:
    """The source list comes from a metadata table, not from the pipeline body."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("Pipeline moves no data — nothing to drive from metadata")

    lookups = [a for a in acts if (a.get("type") or "") == "Lookup"
               and _METADATA_SOURCE.search(json.dumps(a))]
    loops = [a for a in acts if (a.get("type") or "") == _FOREACH]
    driven_loops = [a for a in loops if _ITERATES_LOOKUP.search(
        json.dumps((a.get("typeProperties") or {}).get("items") or ""))]

    if lookups and driven_loops:
        return binary(True, f"Ingestion is metadata-driven: Lookup "
                            f"'{lookups[0].get('name')}' feeds a ForEach over its rows")
    if lookups:
        return graded(1, f"A metadata Lookup ('{lookups[0].get('name')}') exists but no "
                         f"ForEach iterates its output — the source list is still fixed")
    if driven_loops:
        return graded(1, "A ForEach iterates another activity's output, but that source "
                         "is not a metadata/config table")
    return binary(False, f"{len(data_acts)} data activities with no metadata-driven "
                         f"Lookup+ForEach — adding a source means editing the pipeline")


# -- 2.5.3 run control tables capture batch id, status, counts, timestamps ----
_RUN_CONTROL_WRITE = re.compile(
    r"(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE)\s+[\w.\[\]\"]*"
    r"(?:run[_ -]?(?:control|log|history)|batch[_ -]?(?:control|log)|"
    r"audit[_ -]?(?:log|table)|etl[_ -]?log|load[_ -]?log|process[_ -]?log)|"
    r"(?:saveAsTable|\.write)[\s\S]{0,80}?"
    r"(?:run_?control|run_?log|batch_?log|audit_?log|etl_?log|load_?log)",
    re.IGNORECASE,
)
#: The four things a run-control row has to carry to be useful in an incident.
_RUN_CONTROL_ELEMENTS = {
    "batch id": re.compile(r"batch[_ -]?id|run[_ -]?id|load[_ -]?id|execution[_ -]?id",
                           re.IGNORECASE),
    "status": re.compile(r"\bstatus\b|\bsucceeded\b|\bfailed\b|run[_ -]?state|outcome",
                         re.IGNORECASE),
    "row counts": re.compile(r"row[_ -]?count|record[_ -]?count|rows[_ -]?(?:read|written|"
                             r"inserted|updated)|affected[_ -]?rows", re.IGNORECASE),
    "timestamps": re.compile(r"start[_ -]?(?:time|date|utc)|end[_ -]?(?:time|date|utc)|"
                             r"finish[_ -]?time|duration", re.IGNORECASE),
}


@check(
    id="WS-RUNCONTROL", ref="2.5.3",
    title="Run control tables capture batch ID, status, row counts, start/end timestamps",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS],
    required=False,
)
def ws_run_control(ctx: CheckContext) -> Verdict:
    """Every run leaves a row saying which batch ran, whether it worked, and how big.

    Workspace-scoped because run control is written by one framework component,
    so judging every pipeline against it would be noise. Read from the code that
    *writes* the control rows: the control table's own columns are not returned
    by the Fabric REST API.
    """
    pipelines = ctx.workspace.pipelines or {}
    notebooks = ctx.workspace.notebooks or {}
    if not pipelines and not notebooks:
        return not_applicable("No pipeline or notebook definitions available to "
                              "inspect for run-control logging")

    writers: list[str] = []
    corpus: list[str] = []
    for name, defn in pipelines.items():
        text = json.dumps(defn)
        if _RUN_CONTROL_WRITE.search(text):
            writers.append(name)
            corpus.append(text)
    for name, defn in notebooks.items():
        text = notebook_code(defn)
        if _RUN_CONTROL_WRITE.search(text):
            writers.append(name)
            corpus.append(text)

    if not writers:
        return binary(False, f"None of {len(pipelines)} pipeline(s) and "
                             f"{len(notebooks)} notebook(s) writes a run-control / "
                             f"batch-log row — a failed load leaves no audit trail")

    blob = "\n".join(corpus)
    present = sorted(k for k, p in _RUN_CONTROL_ELEMENTS.items() if p.search(blob))
    missing = sorted(k for k in _RUN_CONTROL_ELEMENTS if k not in present)
    where = ", ".join(sorted(writers))
    if not missing:
        return graded(3, f"Run control in {where} captures batch id, status, "
                         f"row counts and timestamps")
    return graded(
        max(0, len(present) - 1),
        f"Run control in {where} captures {', '.join(present)} but is missing "
        f"{', '.join(missing)}",
    )


# -- 2.6.1 pipeline execution times monitored and baselined -------------------
#: The run's *duration* is measured, not merely its start and end recorded.
#: Distinct from ``WS-RUNCONTROL`` (2.5.3), which only asks that timestamps land
#: in the control row — a stored start/end is data, not monitoring.
_DURATION_CAPTURE = re.compile(
    r"\bduration\b|\belapsed\b|\bruntime\b|run[_ -]?time[_ -]?(?:sec|ms|min)|"
    r"execution[_ -]?time|exec[_ -]?time|time[_ -]?taken|"
    r"DATEDIFF\s*\(\s*(?:second|minute|ms|millisecond)|"
    r"time\s*\.\s*(?:time|perf_counter|monotonic)\s*\(\s*\)",
    re.IGNORECASE,
)
#: The measured duration is compared with something it is expected to beat.
_DURATION_BASELINE = re.compile(
    # ``sla`` must not be followed by a letter (so ``sla_seconds`` counts but
    # ``slack`` does not); ``duration``/``elapsed`` may carry a unit suffix
    # before the comparison, as in ``duration_s > sla_seconds``.
    r"baseline|\bsla(?![a-z])|threshold|expected[_ -]?(?:duration|runtime|time)|"
    r"avg[_ -]?(?:duration|runtime)|average[_ -]?(?:duration|runtime)|"
    r"median[_ -]?duration|\bp9[05]\b|percentile|"
    r"max[_ -]?(?:duration|runtime)|(?:longer|slower|exceed)[a-z_ -]{0,12}than|"
    r"duration\w*\s*[><]=?|elapsed\w*\s*[><]=?",
    re.IGNORECASE,
)


@check(
    id="WS-RUNTIME-BASELINE", ref="2.6.1",
    title="Pipeline execution times monitored and baselined",
    pillar=Pillar.PERFORMANCE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS],
    required=False,
)
def ws_runtime_baseline(ctx: CheckContext) -> Verdict:
    """Run durations are measured and compared against an expected value.

    Reads the *instrumentation*, not the telemetry: Fabric's run history lives
    behind the Activity / monitoring admin API, which this tool does not call.
    What is verifiable from the definitions is whether the solution measures its
    own durations and holds them against a baseline — and a baseline is
    deliberate work that the portal does not do for you, so its absence is a
    real finding rather than an unreadable one.
    """
    pipelines = ctx.workspace.pipelines or {}
    notebooks = ctx.workspace.notebooks or {}
    if not pipelines and not notebooks:
        return not_applicable("No pipeline or notebook definitions available to "
                              "inspect for duration monitoring")

    measured: list[str] = []
    corpus: list[str] = []
    for name, defn in pipelines.items():
        text = json.dumps(defn)
        if _DURATION_CAPTURE.search(text):
            measured.append(name)
            corpus.append(text)
    for name, defn in notebooks.items():
        text = notebook_code(defn)
        if _DURATION_CAPTURE.search(text):
            measured.append(name)
            corpus.append(text)

    total = len(pipelines) + len(notebooks)
    if not measured:
        return graded(0, f"None of {total} pipeline(s)/notebook(s) measures its own run "
                         f"duration, and no baseline or SLA threshold is defined — a "
                         f"run that doubles in length passes unnoticed (Fabric's "
                         f"monitoring hub shows durations but sets no baseline)")

    where = ", ".join(sorted(measured))
    if any(_DURATION_BASELINE.search(text) for text in corpus):
        return graded(3, f"Run duration is measured and compared against a "
                         f"baseline/threshold in: {where}")
    return graded(2, f"Run duration is measured in {where}, but is never compared "
                     f"against a baseline, SLA or expected value — the number is "
                     f"recorded, not monitored")
