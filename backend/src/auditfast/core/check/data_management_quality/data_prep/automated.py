"""Data Management & Quality · Data Prep — pipeline design & re-usability.

Naming, documentation, and parameterization: can someone other than the author
understand, promote, and re-point this pipeline.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import (
    NOTEBOOK_LAYERS,
    has_parameters_cell,
    markdown_sources,
    notebook_code,
)
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
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
    id="PL-NAME", ref="2.1.1", title="Pipeline naming convention",
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
    id="PL-DESC", ref="2.1.6", title="Descriptions / annotations populated",
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
    id="PL-PARAM", ref="2.1.2", title="Parameterized — no hardcoded endpoints",
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
_DISPLAY_CALL = re.compile(r"(?:^|\W)(?:display|\w+\.show)\s*\(")
_WIDE_CALL = re.compile(r"\.(?:collect|toPandas)\s*\(|\.count\s*\(\s*\)")


@check(
    id="NB-SECRETS", ref="3.1.3", title="No hardcoded secrets or endpoints in notebooks",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.CRITICAL,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_secrets(ctx: CheckContext) -> Verdict:
    """No credential/endpoint literals baked into the notebook's code cells."""
    hits = [p.pattern for p in _NB_SECRETS if p.search(notebook_code(ctx.obj))]
    return binary(not hits, f"{len(hits)} secret/endpoint pattern match(es)" if hits
                  else "No hardcoded secrets or endpoints found")


@check(
    id="NB-PARAMS", ref="3.1.2", title="Notebook is parameterized",
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
    id="NB-IMPORTS", ref="3.2.7", title="Explicit imports (no wildcard)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_explicit_imports(ctx: CheckContext) -> Verdict:
    """No ``from x import *`` — wildcard imports hide origins and shadow names."""
    hits = _WILDCARD_IMPORT.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} wildcard import(s)" if hits
                  else "No wildcard imports")


@check(
    id="NB-DISPLAY", ref="3.1.6", title="No display()/show() in production paths",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_display(ctx: CheckContext) -> Verdict:
    """No inline ``display()``/``.show()`` — they force compute on production runs."""
    hits = _DISPLAY_CALL.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} display()/show() call(s)" if hits
                  else "No display()/show() calls")


@check(
    id="NB-COLLECT", ref="3.2.3", title="No collect()/toPandas()/count() on datasets",
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
_UDF_DEF = re.compile(r"@udf\b|\budf\s*\(|\.udf\s*\(", re.IGNORECASE)
_SPARK_SQL = re.compile(r"spark\.sql\s*\(|%%?sql\b", re.IGNORECASE)
_DATAFRAME_OP = re.compile(r"spark\.(?:read|table)\b|\.groupBy\s*\(|\.withColumn\s*\(|createDataFrame\s*\(")
_EXTERNAL_READ = re.compile(r"\.read\b[^\n]*(?:csv|json|format\s*\(\s*[\"'](?:csv|json))", re.IGNORECASE)
_SCHEMA_DEFINED = re.compile(r"\.schema\s*\(|StructType\s*\(|inferSchema", re.IGNORECASE)
_JOIN_CALL = re.compile(r"\.join\s*\(")
_BROADCAST = re.compile(r"broadcast\s*\(", re.IGNORECASE)
_TIMEOUT_HINT = re.compile(r"timeout|max_?runtime|session\.?timeout", re.IGNORECASE)
_NB_NAME_OK = re.compile(r"^[A-Za-z][A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)+$")
_NB_NAME_BAD = re.compile(r"^(?:notebook|untitled|test|temp|copy of)\b", re.IGNORECASE)


@check(
    id="NB-STRUCTURE", ref="3.1.1", title="Notebook follows a consistent structure",
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
    id="NB-MARKDOWN", ref="3.1.4", title="Cell-level documentation (markdown) explains the logic",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_markdown(ctx: CheckContext) -> Verdict:
    """At least one markdown cell documents what the notebook does."""
    md = markdown_sources(ctx.obj)
    return binary(bool(md), f"{len(md)} markdown documentation cell(s)" if md
                  else "No markdown cells — the logic is undocumented")


@check(
    id="NB-MODULAR", ref="3.1.5", title="Logic is modular (uses functions)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_modular(ctx: CheckContext) -> Verdict:
    """Reusable functions rather than a single monolithic script."""
    defs = _FUNC_DEF.findall(notebook_code(ctx.obj))
    return binary(bool(defs), f"{len(defs)} function definition(s)" if defs
                  else "No functions defined — logic is a monolithic script")


@check(
    id="NB-NAME", ref="3.1.7", title="Notebook naming is meaningful and consistent",
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
    id="NB-TIMEOUT", ref="3.1.8", title="Execution timeout / max runtime configured",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_timeout(ctx: CheckContext) -> Verdict:
    """A timeout / max-runtime guards against runaway Spark sessions."""
    ok = bool(_TIMEOUT_HINT.search(json.dumps(ctx.obj)))
    return binary(ok, "Timeout / max-runtime setting present" if ok
                  else "No execution timeout configured (runaway-session risk)")


@check(
    id="NB-LANG", ref="3.2.1", title="Consistent language approach (not mixed PySpark / Spark SQL)",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_language(ctx: CheckContext) -> Verdict:
    """One primary language rather than a mix of PySpark and Spark SQL."""
    code = notebook_code(ctx.obj)
    mixed = bool(_SPARK_SQL.search(code)) and bool(_DATAFRAME_OP.search(code))
    if mixed:
        return graded(1, "Mixes PySpark and Spark SQL — pick one primary approach")
    return binary(True, "Consistent language approach")


@check(
    id="NB-DATAFRAME", ref="3.2.2", title="DataFrame API used over the RDD API",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_dataframe_api(ctx: CheckContext) -> Verdict:
    """The higher-level DataFrame API rather than raw RDD operations."""
    hits = _RDD_API.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} RDD-API usage(s)" if hits
                  else "No RDD API — DataFrame API used")


@check(
    id="NB-BROADCAST", ref="3.2.4", title="Broadcast joins used for small-large joins",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_broadcast(ctx: CheckContext) -> Verdict:
    """Joins carry a broadcast() hint where a small dimension meets a large fact."""
    code = notebook_code(ctx.obj)
    joins = _JOIN_CALL.findall(code)
    if not joins:
        return not_applicable("No DataFrame joins present to evaluate for broadcast hints")
    ok = bool(_BROADCAST.search(code))
    return binary(ok, "broadcast() hint present on join(s)" if ok
                  else f"{len(joins)} join(s) without a broadcast() hint")


@check(
    id="NB-NO-UDF", ref="3.2.5", title="UDFs avoided where native functions exist",
    pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_udf(ctx: CheckContext) -> Verdict:
    """No Python UDFs where a native Spark function would do (UDFs block optimization)."""
    hits = _UDF_DEF.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} UDF definition(s) — prefer native Spark functions" if hits
                  else "No UDFs — native Spark functions used")


@check(
    id="NB-SCHEMA", ref="3.2.6", title="Schema explicitly defined for external file reads",
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


# -- pipeline load-pattern checks (2.1.3 orchestration, 2.2.x incremental) -----
_INVOKE_TYPES = {"ExecutePipeline", "InvokePipeline"}
_DATA_MOVE_TYPES = {"Copy", "Script", "TridentNotebook", "SqlServerStoredProcedure", "Lookup"}
_INCREMENTAL = re.compile(
    r"watermark|last_?modified|last_?load|high_?water|incrementalstart|"
    r"\bcdc\b|change[_\s]?tracking|change[_\s]?data|upsert|merge\s+into|delta[_\s]?detect",
    re.IGNORECASE,
)
_LOAD_MODE = re.compile(r"load_?type|load_?mode|is_?initial|full_?load|incremental", re.IGNORECASE)


@check(
    id="PL-ORCHESTRATION", ref="2.1.3", title="Orchestration coordinates dependent pipelines",
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
    id="PL-INCREMENTAL", ref="2.2.1", title="Incremental load pattern used",
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
    id="PL-LOADMODE", ref="2.2.4", title="Initial vs incremental load separated or parameterized",
    pillar=Pillar.DATA, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_load_mode(ctx: CheckContext) -> Verdict:
    """A load-mode parameter or a branch keeps first-load and incremental logic apart."""
    props = ctx.obj.get("properties") or {}
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for load-mode separation")
    param_mode = any(_LOAD_MODE.search(p) for p in (props.get("parameters") or {}))
    branch = any((a.get("type") or "") in {"Switch", "IfCondition"} for a in acts)
    ok = param_mode or branch
    return binary(ok, "Load mode is parameterized / branched (initial vs incremental)" if ok
                  else "No initial-vs-incremental separation (load-mode parameter or branch) found")
