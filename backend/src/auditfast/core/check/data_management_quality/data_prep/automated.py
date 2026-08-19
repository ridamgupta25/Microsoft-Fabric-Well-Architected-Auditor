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
    executable_code_no_strings,
    has_parameters_cell,
    layer_undetermined_evidence,
    layer_words_in,
    markdown_sources,
    medallion_layer,
    notebook_code,
    strip_sql_comments,
    write_targets,
    writes_layer,
)
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities, script_sql, walk_activities
from auditfast.core.check._tables import (
    columns,
    has_timestamp_column,
    is_audit_table,
    is_dimension,
    is_fact,
    is_key_column,
    name_words,
)
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

#: Runtime content that resolves an endpoint/value at execution time instead of
#: baking it in — a declared-parameter reference, a control-table lookup or
#: ForEach item (``@activity(...).output`` / ``@item()``), a variable, or a
#: dataset expression. Their presence, with no hardcoded literal, is evidence the
#: pipeline is parameterised by design even without a declared ``parameters`` block.
_DYNAMIC_CONTENT = re.compile(
    r"@pipeline\(\)\.parameters\.|@activity\(|@item\(|@variables\(|@dataset\(",
    re.IGNORECASE,
)


@check(
    id="PL-NAME", ref="2.1.1", title="Pipelines follow consistent naming conventions (including domain prefix/folder alignment)",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def naming_convention(ctx: CheckContext) -> Verdict:
    """The pipeline name matches the convention configured for the project.

    **N/A when no convention is configured.** ``pipeline_naming_convention`` is a
    project setting with no default, because there is no universal Fabric
    pipeline naming standard to fall back on. With the setting absent, every
    pipeline was previously failed against ``None`` - a finding manufactured from
    the absence of configuration rather than from anything in the estate.
    """
    pattern = ctx.setting("pipeline_naming_convention")
    if not pattern:
        return not_applicable(
            "No pipeline naming convention is configured for this project "
            "(project setting 'pipeline_naming_convention'), so pipeline names "
            "cannot be judged against one"
        )
    name = ctx.obj_name
    if re.match(pattern, name) is not None:
        return binary(True, f"'{name}' matches convention")
    evidence = f"'{name}' does not match {pattern!r}"
    if not activities(ctx.obj):
        evidence += (" - the pipeline is also empty (no activities) and its name looks like a "
                     "leftover test pipeline; delete it or rename it to the naming convention")
    return binary(False, evidence)


@check(
    id="PL-DESC", ref="2.1.6", title="Pipeline annotations/descriptions populated for pipelines and key activities",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.LOW,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def parameterized(ctx: CheckContext) -> Verdict:
    """Sources/targets are resolved by design, not baked into the definition.

    A hardcoded endpoint literal fails. Otherwise the pipeline passes when it
    resolves its endpoints at run time — a declared ``parameters`` block, a
    control-table lookup or ForEach item (``@activity(...).output`` / ``@item()``),
    a variable, or a managed connection reference — and is only *partial* when it
    shows none of those signals (nothing hardcoded, but nothing parameterised
    either), so a metadata-driven framework is credited rather than penalised.
    """
    blob = json.dumps(ctx.obj)
    found = [p.pattern for p in HARDCODED_PATTERNS if p.search(blob)]
    if found:
        return graded(0, f"Hardcoded endpoint/literal(s) detected: {found}")

    if (ctx.obj.get("properties") or {}).get("parameters"):
        return graded(3, "Uses pipeline parameters; no hardcoded endpoints found")

    signals = []
    if _DYNAMIC_CONTENT.search(blob):
        signals.append("runtime expressions (@pipeline().parameters / @activity / @item / @variables)")
    if '"externalReferences"' in blob:
        signals.append("a managed connection reference")
    if signals:
        return graded(
            3,
            "No declared parameters, but endpoints are not hardcoded — resolved via "
            + " and ".join(signals),
        )

    return graded(1, "No parameters or dynamic content (though no hardcoded endpoints found)")


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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.CRITICAL,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_no_secrets(ctx: CheckContext) -> Verdict:
    """No credential/endpoint literals baked into the notebook's code cells."""
    hits = [p.pattern for p in _NB_SECRETS if p.search(notebook_code(ctx.obj))]
    return binary(not hits, f"{len(hits)} secret/endpoint pattern match(es)" if hits
                  else "No hardcoded secrets or endpoints found")


@check(
    id="NB-PARAMS", ref="3.1.2", title="Notebooks are parameterized using Fabric notebook parameters or widgets",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_explicit_imports(ctx: CheckContext) -> Verdict:
    """No ``from x import *`` — wildcard imports hide origins and shadow names."""
    hits = _WILDCARD_IMPORT.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} wildcard import(s)" if hits
                  else "No wildcard imports")


@check(
    id="NB-DISPLAY", ref="3.1.6", title="Notebooks avoid `display()` / `show()` in production execution paths",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_display(ctx: CheckContext) -> Verdict:
    """No inline ``display()``/``.show()`` — they force compute on production runs."""
    hits = _DISPLAY_CALL.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} display()/show() call(s)" if hits
                  else "No display()/show() calls")


@check(
    id="NB-COLLECT", ref="3.2.3", title="No unnecessary `collect()`, `toPandas()`, or `count()` on large datasets",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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


#: Session-timeout values Fabric stamps on a notebook by default (milliseconds).
#: A notebook carrying only a default value has not had a runtime cap deliberately
#: configured, so — like PL-TIMEOUT's default durations — it is treated as "unset":
#: the default alone is a PARTIAL (tune it to the notebook run), never a PASS.
_DEFAULT_NB_TIMEOUTS = frozenset({"600000"})


def _has_positive_timeout(node: object, defaults: frozenset[str] = frozenset()) -> bool:
    """True when any nested metadata key naming a timeout carries a positive,
    *non-default* value.

    Only dict *keys* named like a timeout are inspected (not free-text values), so
    the word "timeout" appearing in a cell's output or a traceback cannot satisfy
    it. A value equal to a known Fabric default (``defaults``) is treated as
    "unset", so the default session timeout Fabric stamps on every notebook is not
    mistaken for a deliberately configured cap.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (isinstance(key, str) and "timeout" in key.lower()
                    and _timeout_value_is_positive(value)
                    and str(value).strip() not in defaults):
                return True
            if _has_positive_timeout(value, defaults):
                return True
    elif isinstance(node, list):
        return any(_has_positive_timeout(item, defaults) for item in node)
    return False


def _has_default_timeout(node: object, defaults: frozenset[str]) -> bool:
    """True when a timeout key carries exactly a known Fabric default value.

    Lets NB-TIMEOUT tell "only Fabric's stamped default" (a PARTIAL to tune) apart
    from "no timeout at all" (N/A).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (isinstance(key, str) and "timeout" in key.lower()
                    and str(value).strip() in defaults):
                return True
            if _has_default_timeout(value, defaults):
                return True
    elif isinstance(node, list):
        return any(_has_default_timeout(item, defaults) for item in node)
    return False


@check(
    id="NB-STRUCTURE", ref="3.1.1", title="Notebooks follow a consistent structure (parameters → imports → config → logic → output)",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_structure(ctx: CheckContext) -> Verdict:
    """Documentation and imports up front — a parameters cell only when the notebook takes inputs."""
    # Import detection runs on executable code with comments *and* string
    # literals stripped, so neither a commented-out ``import`` nor an
    # ``import``/``from`` that only appears inside a docstring counts as structure.
    has_imports = bool(_IMPORT_STMT.search(executable_code_no_strings(ctx.obj)))
    has_markdown = bool(markdown_sources(ctx.obj))
    has_params = has_parameters_cell(ctx.obj)

    # Markdown documentation and explicit imports up front are the structure every
    # notebook should have, so they are scored. A tagged ``parameters`` cell is
    # only expected when the notebook takes inputs, so it is advised, not scored.
    core = {"markdown documentation": has_markdown, "explicit imports": has_imports}
    present = [label for label, ok in core.items() if ok]
    missing = [label for label, ok in core.items() if not ok]

    params_note = (
        "a `parameters` cell is present"
        if has_params
        else "no `parameters` cell — add one at the top (tag a cell 'parameters') if the "
        "notebook takes inputs, otherwise it can be skipped"
    )
    if missing:
        detail = f" ({', '.join(present)})" if present else ""
        return graded(
            1 if present else 0,
            f"{len(present)}/2 core structure signals present{detail}; "
            f"missing {', '.join(missing)}; {params_note}",
        )
    return graded(
        3,
        f"Core structure present (markdown documentation, explicit imports); {params_note}",
    )


@check(
    id="NB-MARKDOWN", ref="3.1.4", title="Cell-level documentation (markdown cells) explains business logic, not just code",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_markdown(ctx: CheckContext) -> Verdict:
    """At least one markdown cell documents what the notebook does."""
    md = markdown_sources(ctx.obj)
    return binary(bool(md), f"{len(md)} markdown documentation cell(s)" if md
                  else "No markdown cells — the logic is undocumented")


@check(
    id="NB-MODULAR", ref="3.1.5", title="Functions are modular and reusable — not monolithic single-cell scripts",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_modular(ctx: CheckContext) -> Verdict:
    """Reusable functions rather than a single monolithic script."""
    defs = _FUNC_DEF.findall(notebook_code(ctx.obj))
    return binary(bool(defs), f"{len(defs)} function definition(s)" if defs
                  else "No functions defined — logic is a monolithic script")


@check(
    id="NB-NAME", ref="3.1.7", title="All notebooks have meaningful, consistent names aligned to domain/layer",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_timeout(ctx: CheckContext) -> Verdict:
    """An explicit, *non-default* execution/session timeout guards runaway Spark sessions.

    Fabric stamps a default session timeout on every notebook
    (``spark.synapse.nbs.session.timeout`` = 600000 ms / 10 min). A notebook that
    carries only that default has not had a cap deliberately set, so it is a
    PARTIAL: the reviewer should replace it with a value tuned to the notebook's
    actual run time. A positive, non-default timeout is a PASS; no timeout at all
    (or ``0``) is N/A.
    """
    metadata = (ctx.obj or {}).get("metadata") or {}
    if _has_positive_timeout(metadata, _DEFAULT_NB_TIMEOUTS):
        return binary(True, "Explicit non-default execution/session timeout configured")
    if _has_default_timeout(metadata, _DEFAULT_NB_TIMEOUTS):
        return graded(
            1,
            "Fabric's default notebook session timeout is set "
            "(spark.synapse.nbs.session.timeout = 600000 ms / 10 min) — replace it "
            "with an explicit timeout tuned to the notebook's actual run time",
        )
    return not_applicable(
        "No execution/session timeout in the notebook definition; "
        "Fabric's default applies and cannot be verified from code"
    )


@check(
    id="NB-LANG", ref="3.2.1", title="Consistent language approach (PySpark vs Spark SQL — one primary, not mixed ad-hoc)",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_dataframe_api(ctx: CheckContext) -> Verdict:
    """The higher-level DataFrame API rather than raw RDD operations."""
    hits = _RDD_API.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} RDD-API usage(s)" if hits
                  else "No RDD API — DataFrame API used")


@check(
    id="NB-BROADCAST", ref="3.2.4", title="Broadcast joins used for small-large table joins where appropriate",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.LOW,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_no_udf(ctx: CheckContext) -> Verdict:
    """No Python UDFs where a native Spark function would do (UDFs block optimization)."""
    hits = _UDF_DEF.findall(notebook_code(ctx.obj))
    return binary(not hits, f"{len(hits)} UDF definition(s) — prefer native Spark functions" if hits
                  else "No UDFs — native Spark functions used")


@check(
    id="NB-SCHEMA", ref="3.2.6", title="Schema explicitly defined at read time for external sources (not inferred on CSV/JSON)",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
#: A pipeline whose *name* declares its load mode is a dedicated full-load /
#: incremental pipeline, so the two modes are separated at the pipeline level
#: rather than by an in-pipeline parameter or branch.
_FULL_LOAD_NAME = re.compile(r"full[_ ]?load", re.IGNORECASE)
_LOAD_MODE_NAME = re.compile(r"full[_ ]?load|incr\w*load|incremental|initial[_ ]?load", re.IGNORECASE)
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
#: Human-readable roll-up of the duplicate-safe writes ``_LATE_SAFE_WRITE`` accepts,
#: listed in a FAIL so the reviewer sees exactly what was searched for.
_SAFE_WRITE_NAMES = (
    "MERGE, upsert, dropDuplicates, row_number(), dedup, "
    "latest/newer-version, or when-matched"
)


def _matched_tokens(pattern: re.Pattern[str], blob: str, limit: int = 6) -> list[str]:
    """Distinct literal substrings ``pattern`` matched in ``blob`` (order-preserving)."""
    seen: list[str] = []
    for m in pattern.finditer(blob):
        tok = re.sub(r"\s+", " ", m.group(0)).strip().lower()
        if tok and tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def _data_move_summary(acts: list[dict]) -> str:
    """A short '1 Copy, 2 Script' tally of the data-movement activities present."""
    tally: dict[str, int] = {}
    for a in acts:
        t = a.get("type") or "?"
        tally[t] = tally.get(t, 0) + 1
    return ", ".join(f"{n} {t}" for t, n in tally.items())


@check(
    id="PL-ORCHESTRATION", ref="2.1.3", title="Master/orchestrator pipeline pattern used for coordinating dependent domain pipelines",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.WORKSPACE, severity=Severity.LOW,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_incremental(ctx: CheckContext) -> Verdict:
    """A watermark / CDC / upsert pattern rather than an unconditional full reload.

    Two cases are N/A rather than FAIL: a dedicated *full-load* pipeline (the name
    declares it, and full-vs-incremental is a design choice on data volume the
    audit cannot read), and a pipeline whose only data-movement is a *notebook*
    (the load logic lives in code this pipeline-scoped check cannot see).
    """
    acts = activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for incremental load")
    if _INCREMENTAL.search(json.dumps(ctx.obj)):
        return binary(True, "Incremental-load pattern detected (watermark / CDC / merge)")
    if _FULL_LOAD_NAME.search(ctx.obj_name):
        return not_applicable(
            "Dedicated full-load pipeline (name declares full load) — incremental not "
            "applicable; confirm the source table's volume warrants a full reload"
        )
    if all((a.get("type") or "") == "TridentNotebook" for a in data_acts):
        return not_applicable(
            "Load runs inside a notebook — the incremental pattern is not visible from "
            "the pipeline definition (assess it in the notebook checks)"
        )
    return binary(False, "No incremental-load pattern detected — full-reload risk")


@check(
    id="PL-LOADMODE", ref="2.2.5", title="Initial load vs. incremental load clearly separated or parameterized",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_load_mode(ctx: CheckContext) -> Verdict:
    """A dedicated per-mode pipeline, a load-mode parameter, or a branch keeps
    first-load and incremental logic apart."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    props = ctx.obj.get("properties") or {}
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable("No data-movement activity to assess for load-mode separation")
    if _LOAD_MODE_NAME.search(ctx.obj_name):
        return binary(True, "Load mode is separated at the pipeline level — the name "
                            "declares a dedicated full-load / incremental pipeline")
    if all((a.get("type") or "") == "TridentNotebook" for a in data_acts):
        return not_applicable(
            "Load runs inside a notebook — initial-vs-incremental separation is not "
            "assessable from the pipeline definition (assess it in the notebook checks)"
        )
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
#: A join, in either the DataFrame or the SQL spelling. ``anti`` and ``semi`` are
#: listed explicitly: ``LEFT ANTI JOIN`` is the standard way to isolate orphan
#: rows, and without it the very notebooks that *do* detect orphans were reported
#: as performing no join at all - N/A instead of the PASS they had earned.
_JOIN_PATTERN = re.compile(
    r"(?is)(?<![\"'])(?<!os\.path)\.join\s*\(|"
    r"\b(?:left|right|full|inner|cross)\s+(?:outer\s+)?join\b|"
    r"\bleft\s+(?:anti|semi)\s+join\b|"
    r"\bjoin\b\s+\w+\s+\bon\b",
    re.IGNORECASE,
)
#: An implicit (comma-style) SQL join: two or more tables listed in one FROM
#: clause, joined by the WHERE predicate — ``FROM a t, b i, c g WHERE ...``. It is
#: an inner join that silently drops unmatched rows, exactly the orphan risk this
#: check exists to catch, so it must count as a join. Anchored on a table
#: identifier (with an optional alias) before the comma, so a ``SELECT a, b``
#: column list or a ``WHERE x IN (1, 2)`` value list is never read as a table list.
_IMPLICIT_JOIN = re.compile(
    r"\bFROM\s+[\w.`\[\]]+(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?\s*,\s*[\w.`\[\]]+",
    re.IGNORECASE,
)
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
_TABLE_READ_ASSIGNMENT = re.compile(
    r"(?P<variable>\w+)\s*=\s*spark\.table\s*\(\s*[\"'](?P<table>[^\"']+)[\"']\s*\)",
    re.IGNORECASE,
)
_FK_ANTI_JOIN = re.compile(
    r"(?P<child>\w+)(?:\.alias\s*\([^)]*\))?\s*\.join\s*\(\s*"
    r"(?P<parent>\w+)(?:\.alias\s*\([^)]*\))?\s*,\s*"
    r"(?P<left_var>\w+)\.(?P<left_key>\w+)\s*==\s*"
    r"(?P<right_var>\w+)\.(?P<right_key>\w+)[\s\S]{0,160}?[\"']left_anti[\"']",
    re.IGNORECASE,
)
_FK_ASSERTION = re.compile(
    r"assert\s+[^\n]*(?:orphan|unmatched|missing_parent|no_parent)[^\n]*(?:==\s*0|not\s+)|"
    r"(?:orphan|unmatched|missing_parent|no_parent)[_\w]*\.count\s*\(\s*\)\s*==\s*0",
    re.IGNORECASE,
)
_FK_QUARANTINE = re.compile(
    r"(?:orphan|unmatched|missing_parent|no_parent)[_\w]*[\s\S]{0,300}?"
    r"(?:\.write\b|saveAsTable\s*\(|insertInto\s*\()[\s\S]{0,160}?"
    r"(?:quarantin|reject|error[_-]?(?:record|table)|ri[_-]?orphan)|"
    r"(?:quarantin|reject|error[_-]?(?:record|table)|ri[_-]?orphan)[\s\S]{0,160}?"
    r"(?:\.write\b|saveAsTable\s*\(|insertInto\s*\()",
    re.IGNORECASE,
)


def _fk_relationship(code: str) -> tuple[str, str, str, str] | None:
    """Return child table/key and parent table/key for a static anti-join."""
    join = _FK_ANTI_JOIN.search(code)
    if not join:
        return None
    tables = {
        match.group("variable"): match.group("table")
        for match in _TABLE_READ_ASSIGNMENT.finditer(code)
    }
    child_var = join.group("child")
    parent_var = join.group("parent")
    left_var = join.group("left_var")
    if left_var == child_var:
        child_key, parent_key = join.group("left_key"), join.group("right_key")
    else:
        child_key, parent_key = join.group("right_key"), join.group("left_key")
    return (
        tables.get(child_var, child_var),
        child_key,
        tables.get(parent_var, parent_var),
        parent_key,
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
_BRONZE_INGESTION = re.compile(
    r"ingest(?:ed|ion)[_ ]?(?:timestamp|time|date)|ingested_at",
    re.IGNORECASE,
)
#: The three audit facts the Bronze point names, kept apart so the verdict can
#: say *which* are present. Lost in a merge, which left the check referencing
#: two undefined names - a NameError on every bronze notebook.
_BRONZE_SOURCE = re.compile(
    r"source[_ ]?(?:system|file|path)|input_file_name\s*\(",
    re.IGNORECASE,
)
_BRONZE_BATCH = re.compile(
    r"batch[_ ]?(?:id|key)",
    re.IGNORECASE,
)
#: The four Silver disciplines the checklist point names, kept separate so the
#: verdict can say *which* are present. A single pattern could only answer
#: "something was found", which reported "applies cleansing, deduplication,
#: conforming and type standardization" on a notebook doing none of the dedup.
_SILVER_ASPECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deduplication", re.compile(
        r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.distinct\s*\(\)|dedup|"
        r"row_number\s*\(\s*\)\s*over|qualify\b",
        re.IGNORECASE)),
    ("type standardization", re.compile(
        r"\.cast\s*\(|to_date\s*\(|to_timestamp\s*\(|astype\s*\(|"
        r"CAST\s*\(|CONVERT\s*\(",
        re.IGNORECASE)),
    ("cleansing", re.compile(
        r"regexp_replace\s*\(|\btrim\s*\(|\blower\s*\(|\bupper\s*\(|"
        r"fillna\s*\(|na\.fill|coalesce\s*\(|cleanse|cleansing",
        re.IGNORECASE)),
    ("conforming", re.compile(
        r"standardi[sz]e|conform|\.withColumnRenamed\s*\(|\balias\s*\(|mapping",
        re.IGNORECASE)),
)

_EXPLICIT_ROW_BY_ROW = re.compile(
    r"row[_ -]?by[_ -]?row|record[_ -]?by[_ -]?record|per[_ -]?(?:row|record)|"
    r"\bCURSOR\b|\bFETCH\s+NEXT\b|\.iterrows\s*\(|\.itertuples\s*\(",
    re.IGNORECASE,
)
_SET_BASED_SCRIPT = re.compile(
    r"\bCOPY\s+INTO\b|\bINSERT\s+INTO\b[\s\S]{0,500}?\bSELECT\b|"
    r"\bCREATE\s+TABLE\b[\s\S]{0,500}?\bAS\s+SELECT\b",
    re.IGNORECASE,
)
_EAM_JSON = re.compile(
    # A real Spark/pandas JSON *data* read. A bare ``json.loads(...)`` /
    # ``json.load(...)`` is deliberately excluded: it is generic stdlib parsing of
    # config, an API response, or a pipeline-definition file — not EAM data
    # ingestion. Treating any ``json.loads`` as EAM ingestion misclassified a
    # deployment-framework notebook (which reads pipeline JSON files) as "EAM JSON".
    r"from_json\s*\(|schema_of_json\s*\(|"
    r"spark\.read(?:Stream)?(?:\s*\.\w+\s*\([^()\n]*\))*\s*\.json\s*\(|"
    r"(?:spark\.read(?:Stream)?|\.read)\.format\s*\(\s*[\"']json[\"']\s*\)|"
    r"\b(?:pd\.)?read_json\s*\(",
    re.IGNORECASE,
)
_EAM_EFFICIENT = re.compile(
    r"readStream|partitionBy\s*\(|repartition\s*\(|multiLine\s*[=:]\s*False|"
    r"maxFilesPerTrigger|badRecordsPath|from_json\s*\(|schema_of_json",
    re.IGNORECASE,
)
#: A read from *outside* the lakehouse: a file, an API, or an external database.
#: This is what makes a notebook an *ingestion* notebook, and therefore what makes
#: recording provenance meaningful. Deliberately excludes ``spark.read.table`` and
#: ``spark.sql`` - reading a table already in the lakehouse carries its own
#: lineage, so a derived/Gold notebook has no external source to record and must
#: not be failed for omitting one.
_EXTERNAL_SOURCE_READ = re.compile(
    # File formats and storage paths.
    r"\.read\s*\.\s*(?:csv|json|parquet|text|xml|orc|avro|excel)\s*\(|"
    r"\.read\s*\.\s*format\s*\(\s*[\"'](?:csv|json|parquet|text|xml|orc|avro|jdbc|"
    r"cosmos\.oltp|sqlserver|mongodb|kafka)[\"']|"
    r"abfss://|wasbs://|https?://|s3a?://|gs://|file:///|dbfs:/|/lakehouse/default/Files|"
    r"\bFiles/|"
    # Pandas-side ingestion.
    r"\bpd\.read_(?:csv|json|excel|parquet|sql|html|xml)\s*\(|"
    # APIs and databases.
    r"\brequests\.(?:get|post)\s*\(|\bhttpx\.(?:get|post)\s*\(|urlopen\s*\(|"
    r"\bjdbc\b|pyodbc\.connect|create_engine\s*\(|"
    # Fabric/Spark connectors to an external system.
    r"mssparkutils\.fs\.(?:cp|mv|ls)\s*\(|notebookutils\.fs\.(?:cp|mv|ls)\s*\(",
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
#: A *categorical* domain restriction - 5.5.5. Deliberately distinct from
#: ``_FLAG_DOMAIN`` (5.5.7), which covers the two-valued boolean/flag case: a
#: membership test whose literals are ``Y``/``N``/``true``/``false``/``0``/``1``
#: is a flag test and is excluded below, so the two checks cannot both claim the
#: same line of code.
#:
#: Three shapes count, because they are the three ways a team actually pins a
#: code list in Spark:
#:   * a membership test against a named allow-list or literal code set;
#:   * an anti/semi join against a reference, lookup or dimension table - the
#:     standard "reject codes absent from the dimension" pattern;
#:   * a declared allowed-value collection (``VALID_STATUSES = {...}``).
_CATEGORICAL_DOMAIN = re.compile(
    # .isin(...) / ~col.isin(...) on a non-boolean literal set of >= 2 codes
    r"\.isin\s*\(\s*\[?\s*[\"'][A-Za-z0-9_\- ]{2,}[\"']\s*,",
    re.IGNORECASE,
)

#: A membership test naming a *variable* allow-list rather than inline literals.
_CATEGORICAL_ALLOWLIST = re.compile(
    r"\.isin\s*\(\s*(?:\*\s*)?[A-Za-z_][A-Za-z0-9_]*\s*\)|"
    r"\b(?:allowed|valid|expected|permitted|accepted|reference|master)[_ ]?"
    r"(?:codes?|categories|category|statuses|status|types?|values|list|set|domain)\b|"
    r"\b(?:code|category|status|type)[_ ]?(?:list|set|domain|lookup|allowlist|whitelist)\b",
    re.IGNORECASE,
)

#: Validation by joining to a reference/lookup/dimension table - the codes that
#: do not match are the invalid ones.
_CATEGORICAL_REFERENCE_JOIN = re.compile(
    r"left_anti|leftanti|left_semi|leftsemi|"
    r"join\s*\([^\n]{0,160}?(?:ref|reference|lookup|dim|dimension|master|codes?)"
    r"[A-Za-z0-9_]*[^\n]{0,80}?\)",
    re.IGNORECASE,
)

#: A boolean/flag membership test - 5.5.7's territory, never 5.5.5's.
_BOOLEAN_LITERALS = re.compile(
    r"\.isin\s*\(\s*\[?\s*(?:True|False|[\"'](?:Y|N|yes|no|true|false|0|1)[\"'])",
    re.IGNORECASE,
)

#: The distinct data-quality disciplines 5.1.2 asks for, kept separate so the
#: verdict can say *which* are codified. Bundling them into one pattern meant a
#: notebook calling ``drop_duplicates`` once scored full marks for "DQ rules
#: codified in code/config" - one line of tidy-up read as a rule framework.
_DQ_ASPECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("assertions / expectations", re.compile(
        r"\bassert\b|expect_|expectation|great_expectations|\bdeequ\b|"
        r"\braise\s+\w*(?:Error|Exception)|pytest\.raises",
        re.IGNORECASE)),
    ("schema validation", re.compile(
        r"StructType|StructField|expected[_ ]?(?:schema|columns|values)|"
        r"\.schema\s*==|enforceSchema|mergeSchema\s*=\s*False",
        re.IGNORECASE)),
    ("null / domain checks", re.compile(
        r"isNull|isNotNull|\bisin\s*\(|\bbetween\s*\(|dropna\s*\(|"
        r"\.filter\s*\([^)]*(?:null|isin)|not\s+in\s*\(",
        re.IGNORECASE)),
    ("quarantine / rejection", re.compile(
        r"quarantine|reject|invalid|bad[_ ]?record|badRecordsPath|"
        r"error[_ ]?table|dead[_ -]?letter|left_anti",
        re.IGNORECASE)),
)


@check(
    id="NB-RECON-COUNT", ref="5.2.5",
    title="Record count reconciliation vs. source system control counts",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_fk_integrity(ctx: CheckContext) -> Verdict:
    """Notebooks that join tables verify FK values exist in dimension/lookup tables."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definition could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _JOIN_PATTERN.search(code):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' performs no join, so no foreign-key relationship "
            "can be assessed",
        )
    if not _FK_INTEGRITY.search(code):
        return binary(
            False,
            f"Notebook '{ctx.obj_name}' joins tables but does not isolate unmatched foreign "
            "keys. Add a left_anti join (or a left join followed by a parent-key null test) "
            "between the child FK and parent key; otherwise orphan fact/child rows can pass "
            "through unnoticed",
        )

    relationship = _fk_relationship(code)
    if relationship:
        child_table, child_key, parent_table, parent_key = relationship
        subject = (
            f"Notebook '{ctx.obj_name}' validates {child_table}.{child_key} -> "
            f"{parent_table}.{parent_key} with a left_anti join"
        )
    else:
        subject = (
            f"Notebook '{ctx.obj_name}' contains an explicit foreign-key integrity check, "
            "but the child table, parent table, or join keys are dynamically resolved"
        )

    enforced = bool(_FK_ASSERTION.search(code))
    quarantined = bool(_FK_QUARANTINE.search(code))
    controls = []
    if enforced:
        controls.append("An assertion fails the run when orphan rows are found")
    if quarantined:
        controls.append("Unmatched rows are persisted to a quarantine/error target")
    if controls:
        return binary(
            True,
            f"{subject}. {'. '.join(controls)}. This verdict verifies the saved notebook "
            "logic, not current table rows; a zero-row orphan result in the notebook output "
            "is separate runtime confirmation that the current data passed",
        )
    return graded(
        1,
        f"{subject}, so unmatched keys are identified. However, the saved code does not "
        "fail the run or persist the orphan set; invalid rows could be detected and then "
        "ignored. Add an assertion or write the unmatched rows to a quarantine table",
    )


# -- 5.3.6 evidence helpers: name *what* was found, not only how many ----------
#: The reconciliation techniques 5.3.6 accepts, split out so the evidence can name
#: the one(s) actually present. The verdict still comes from ``_CROSS_SOURCE_RECON``;
#: these regexes only enrich the message and never change which branch is taken.
_RECON_TECHNIQUES = (
    (re.compile(
        r"\.count\s*\(\s*\)[^\n]{0,120}?(?:==|!=|<=|>=|<|>)[^\n]{0,120}?\.count\s*\(\s*\)|"
        r"\b(?:source|src|left|before|expected)_count\b[^\n]{0,60}"
        r"(?:==|!=|<=|>=|<|>)[^\n]{0,60}\b(?:target|tgt|right|after|actual)_count\b|"
        r"\b(?:target|tgt|right|after|actual)_count\b[^\n]{0,60}"
        r"(?:==|!=|<=|>=|<|>)[^\n]{0,60}\b(?:source|src|left|before|expected)_count\b",
        re.IGNORECASE), "a count-vs-count comparison"),
    (re.compile(
        r"\.subtract\s*\(|\.exceptAll\s*\(|\bEXCEPT\s+(?:ALL\s+)?SELECT\b|\bMINUS\s+SELECT\b",
        re.IGNORECASE), "a set difference (EXCEPT / subtract / exceptAll)"),
    (re.compile(r"left_anti|leftanti|\bleft\s+anti\s+join\b", re.IGNORECASE), "an anti-join"),
    (re.compile(
        r"cross[_\s-]?source[_\s-]?recon|source[_\s-]?to[_\s-]?target|"
        r"\breconcile_sources\b|\bcompare_sources\b",
        re.IGNORECASE), "an explicitly named source-to-target reconciliation"),
)


def _summarize_sources(matches: list[str]) -> str:
    """Tally the read call-sites the multi-source detector matched, grouped by kind."""
    tally: dict[str, int] = {}
    for raw in matches:
        text = raw.strip().lower()
        if "spark.read" in text and "load" in text:
            kind = "spark.read().load()"
        elif "spark.read" in text:
            kind = "spark.read"
        elif "spark.table" in text:
            kind = "spark.table()"
        else:
            kind = ".load()"
        tally[kind] = tally.get(kind, 0) + 1
    return ", ".join(f"{n}\u00d7 {kind}" for kind, n in tally.items())


def _recon_techniques(code: str) -> str:
    """Name the cross-source reconciliation technique(s) actually present in ``code``."""
    found = [label for pattern, label in _RECON_TECHNIQUES if pattern.search(code)]
    return ", ".join(found) if found else "a cross-source comparison"


def _count_reconcile_evidence(code: str) -> str:
    """Name the single-dataset record-count idiom the notebook does have."""
    if re.search(r"\bassert\b[^\n]*\.count\s*\(", code, re.IGNORECASE):
        return "an asserted record count"
    if re.search(r"reconcil|\bcount_check\b|expect_table_row_count", code, re.IGNORECASE):
        return "a named row-count validation"
    if re.search(r"\.count\s*\(\s*\)\s*(?:==|!=|<=|>=|<|>)", code):
        return "a record-count comparison"
    return "a record-count validation"


#: Python-level loaders the bare ``.load(`` arm of ``_MULTI_SOURCE`` also matches.
#: Named so the evidence can say *which* non-Spark loader was seen.
_PY_LOADERS = (
    ("json.load()", re.compile(r"\bjson\s*\.\s*loads?\s*\(", re.IGNORECASE)),
    ("yaml.load()", re.compile(r"\byaml\s*\.\s*(?:safe_load|load)\s*\(", re.IGNORECASE)),
    ("pickle.load()",
     re.compile(r"\b(?:pickle|cpickle|cloudpickle)\s*\.\s*loads?\s*\(", re.IGNORECASE)),
    ("numpy.load()", re.compile(r"\b(?:np|numpy)\s*\.\s*load\s*\(", re.IGNORECASE)),
    ("joblib.load()", re.compile(r"\bjoblib\s*\.\s*load\s*\(", re.IGNORECASE)),
    ("torch.load()", re.compile(r"\btorch\s*\.\s*load\s*\(", re.IGNORECASE)),
)
#: A genuine Spark data-source read. Its absence is what turns a ``.load(`` match
#: into a false "source".
_SPARK_READ = re.compile(r"spark\.read\b|spark\.table\s*\(", re.IGNORECASE)


def _nonspark_load_note(code: str, matches: list[str]) -> str:
    """Caveat when the detected reads are Python ``.load()`` calls, not Spark reads.

    The 5.3.6 source detector also matches a bare ``.load(``, which catches Python
    loaders such as ``json.load()``. When no ``spark.read`` / ``spark.table`` appears
    anywhere, the counted 'sources' are almost certainly configuration/file reads,
    not data - so the finding is flagged as such instead of silently over-counting.
    """
    if _SPARK_READ.search(code):
        return ""
    if not any(".load(" in m.replace(" ", "").lower() for m in matches):
        return ""
    named = [label for label, pattern in _PY_LOADERS if pattern.search(code)]
    which = ", ".join(named) if named else "bare .load()"
    return (
        " \u2014 note: no Spark read (spark.read / spark.table) is present, so these are "
        f"Python {which} calls (configuration/file reads), not Spark data-source reads"
    )


@check(
    id="NB-CROSS-RECON", ref="5.3.6",
    title="Cross-source reconciliation: records from multiple sources reconciled correctly",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    note = _nonspark_load_note(code, sources)
    if len(sources) < 2:
        seen = _summarize_sources(sources)
        detail = f" ({seen})" if seen else ""
        return not_applicable(
            f"Reads from {len(sources)} data-source call-site(s){detail} — cross-source "
            "reconciliation applies only when 2 or more sources are combined in one notebook"
            + note
        )
    breakdown = _summarize_sources(sources)
    if _CROSS_SOURCE_RECON.search(code):
        return binary(
            True,
            f"Reads {len(sources)} source call-sites ({breakdown}) and reconciles "
            f"across them via {_recon_techniques(code)}" + note,
        )
    if _COUNT_RECONCILE.search(code):
        return graded(
            1,
            f"Reads {len(sources)} source call-sites ({breakdown}) and validates a "
            f"single dataset ({_count_reconcile_evidence(code)}), but nothing compares "
            f"the sources against each other — no count-vs-count check, set difference "
            f"(EXCEPT / subtract / exceptAll), or anti-join spanning the sources" + note,
        )
    return binary(
        False,
        f"Reads {len(sources)} source call-sites ({breakdown}) without cross-source "
        f"reconciliation — none of a count-vs-count comparison, set difference "
        f"(EXCEPT / subtract / exceptAll), anti-join, or explicitly named "
        f"source-to-target reconciliation was found across the sources" + note,
    )


@check(
    id="NB-ORPHAN-DETECT", ref="5.3.7",
    title="Orphan detection: child records without matching parent records identified and handled",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    code = strip_sql_comments(executable_code(ctx.obj))
    if not (_JOIN_PATTERN.search(code) or _IMPLICIT_JOIN.search(code)):
        return not_applicable(f"Notebook '{ctx.obj_name}' does not perform joins")
    if not _ORPHAN_DETECT.search(code):
        return binary(
            False,
            f"Notebook '{ctx.obj_name}' joins tables without orphan record detection",
        )
    if _orphan_set_is_handled(code):
        return binary(
            True,
            f"Notebook '{ctx.obj_name}' detects and handles orphan/unmatched records",
        )
    return graded(
        1,
        f"Notebook '{ctx.obj_name}': orphan/unmatched records are detected but not "
        "handled - the unmatched set is computed and then dropped, never quarantined, "
        "raised on or recorded",
    )


@check(
    id="NB-MERGE-VALID", ref="5.3.9",
    title="Merge result validation: post-merge counts reconcile with source I/U/D counts",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_PROCESSING, scope=Scope.PIPELINE, severity=Severity.HIGH,
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
_DIM_REF = re.compile(r"\bdim[_\s.]|\bdimension\b", re.IGNORECASE)
_FACT_REF = re.compile(r"\bfact[_\s.]|\bfct[_\s.]", re.IGNORECASE)
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
#: A real dimensional unknown-member fallback. A generic string value such as
#: ``city = "Unknown"`` is ordinary null cleansing, not a surrogate member.
_UNKNOWN_MEMBER_USE = re.compile(
    r"unknown[_\s]?member|inferred[_\s]?member|is_inferred|"
    r"coalesce\s*\([^\n]{0,80}?-1|fillna\s*\([^\n]{0,60}?-1|"
    r"\.na\.fill\s*\([^\n]{0,60}?-1|otherwise\s*\([^\n]{0,40}?-1|"
    r"when\s*\([^\n]{0,80}?-1",
    re.IGNORECASE,
)

#: A genuine fact→dimension lookup, distinct from a Python ``str.join``. A Spark
#: DataFrame join (``df.join(``) is never written ``"literal".join(``, so a
#: ``.join(`` preceded by a quote is excluded; a SQL ``… JOIN … ON`` also counts.
#: Without this, ``", ".join(cols)`` would read as a fact-to-dimension lookup.
_FACT_DIM_JOIN = re.compile(
    r"(?<![\"'])\.join\s*\(|"
    r"\b(?:left|right|full|inner|cross)?\s*join\b[^\n;]{0,200}?\bon\b",
    re.IGNORECASE | re.DOTALL,
)

#: The inferred row is later updated when the real dimension member arrives.
#: Accept explicit lifecycle names and statically identifiable Delta/SQL merges
#: into a dimension; a generic MERGE elsewhere is not backfill evidence.
_INFERRED_MEMBER_BACKFILL = re.compile(
    r"backfill[_\s]?(?:dimension|member)|"
    r"update[_\s]?(?:inferred|unknown)[_\s]?member|"
    r"DeltaTable\.forName\s*\([^\n]{0,160}?dim[_\s.]?[A-Za-z0-9_]*[^\n]{0,160}?\)"
    r"[\s\S]{0,500}?\.merge\s*\([\s\S]{0,500}?whenMatchedUpdate|"
    r"merge\s+into\s+(?:[`\[\]\w]+\.)*[`\[]?dim[_\s.]?[A-Za-z0-9_`\]]*"
    r"[\s\S]{0,500}?when\s+matched[\s\S]{0,120}?update",
    re.IGNORECASE,
)

_FACT_WRITE_RECEIVER = re.compile(
    r"\b(?:fact|fct)(?:_[A-Za-z0-9]+)*\s*\.write\b",
    re.IGNORECASE,
)


def _fact_write_evidence(code: str) -> str:
    """Describe a provable fact write, or return empty when only reads are seen."""
    targets = write_targets(code)
    fact_targets = [target for target in targets if _FACT_REF.search(target)]
    if fact_targets:
        return f"write target '{fact_targets[0]}'"
    receiver = _FACT_WRITE_RECEIVER.search(code)
    if receiver:
        return f"write receiver '{' '.join(receiver.group(0).split())}'"
    return ""


# NB-LATE-ARRIVING (4.5.10): flags a fact→dimension load with no unknown/inferred-member
# fallback. Was: raw code + any ``.join(`` gate → false-FAILed metadata/DQ notebooks whose
# only "join" was a Python ``str.join`` or whose ``dim``/``fact`` sat in a comment.
# Updated 2026-08-12: strip comments, require a real DataFrame/SQL join (``_FACT_DIM_JOIN``),
# and N/A a dimension-build/upsert that never references a fact (``_DIM_UPSERT`` w/o ``_FACT_REF``).
@check(
    id="NB-LATE-ARRIVING", ref="4.5.10",
    title="Late-arriving dimensions and facts handled (unknown/inferred member pattern)",
    pillar=Pillar.DATA_MODELING, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=(*NOTEBOOK_LAYERS, Layer.STORAGE),
    requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_late_arriving(ctx: CheckContext) -> Verdict:
    """A fact arriving before its dimension gets an inferred member, not dropped.

    Commented-out code is ignored and only a genuine fact→dimension join counts
    (a Spark ``df.join(`` or SQL ``… JOIN … ON`` — never a Python ``str.join``),
    so a metadata/DQ notebook whose only "join" builds a string, or whose only
    ``dim``/``fact`` mention sits in a comment, is N/A rather than FAIL. A notebook
    that merely *builds* a dimension (an SCD upsert) is also N/A — the fallback
    belongs in the fact load. Without the pattern the load either discards the
    fact (silent data loss) or fails outright; the evidence is a stub/unknown
    member: an ``is_inferred`` flag, an explicit "unknown" member, or the
    conventional ``-1`` surrogate key substituted when the lookup misses.
    """
    code = strip_sql_comments(executable_code(ctx.obj))
    fact_write = _fact_write_evidence(code)
    dimensional_lookup = bool(
        _FACT_REF.search(code) and _DIM_REF.search(code) and _FACT_DIM_JOIN.search(code)
    )
    if not (fact_write or dimensional_lookup):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' has no provable dimensional fact load: no "
            f"fact-named write target/receiver or fact-to-dimension lookup. Generic "
            f"JSON-to-Delta ingestion and audit row counts do not establish that "
            f"late-arriving member handling applies"
        )

    fallback = _LATE_ARRIVING.search(code)
    backfill = _INFERRED_MEMBER_BACKFILL.search(code)
    scope = fact_write or "fact-to-dimension lookup"
    if fallback and backfill:
        fallback_signal = " ".join(fallback.group(0).split())
        backfill_signal = " ".join(backfill.group(0).split())
        return binary(
            True,
            f"Notebook '{ctx.obj_name}' has {scope}, routes unresolved dimension "
            f"keys through fallback '{fallback_signal}', and backfills inferred "
            f"members (matched '{backfill_signal}')",
        )

    missing = []
    if not dimensional_lookup:
        missing.append("a dimension-key lookup")
    if not fallback:
        missing.append("an unknown/inferred-member fallback such as surrogate key -1")
    if not backfill:
        missing.append("backfill of inferred members when dimension rows arrive")
    return binary(
        False,
        f"Notebook '{ctx.obj_name}' has {scope} but lacks {', '.join(missing)}; "
        f"late-arriving facts can be dropped, fail the load, or remain permanently "
        f"assigned to an unknown member",
    )


@check(
    id="NB-UNKNOWN-MONITOR", ref="5.4.4",
    title="Completeness: all expected dimension members present; unknown/orphan member usage monitored",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_unknown_monitored(ctx: CheckContext) -> Verdict:
    """Rows landing on the unknown member are counted, not silently accepted.

    Routing unmatched keys to an unknown member keeps the load running, but if
    nobody counts them a broken feed looks healthy for months. Only notebooks
    that actually use an unknown/inferred member are judged — creating one is
    NB-LATE-ARRIVING's job.
    """
    code = strip_sql_comments(executable_code(ctx.obj))
    if not (_DIM_CONTEXT.search(code) and _FACT_DIM_JOIN.search(code)):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' performs no fact-to-dimension lookup with "
            f"an unknown member to monitor"
        )
    member = _UNKNOWN_MEMBER_USE.search(code)
    if not member:
        return not_applicable(
            f"Notebook '{ctx.obj_name}' performs a dimensional lookup but uses no "
            f"deterministic unknown/inferred-member fallback (-1 or an explicitly "
            f"named unknown/inferred member)"
        )
    monitored = _UNKNOWN_MONITORED.search(code)
    fallback = " ".join(member.group(0).split())
    if monitored:
        signal = " ".join(monitored.group(0).split())
        return binary(True, f"Notebook '{ctx.obj_name}' uses unknown-member fallback "
                      f"'{fallback}' and monitors it with '{signal}'")
    return binary(False, f"Notebook '{ctx.obj_name}' uses unknown-member fallback "
                  f"'{fallback}' in a fact-to-dimension lookup but never counts, "
                  f"logs, or audits how many rows use it")


@check(
    id="NB-LAYER-RECON", ref="5.4.6",
    title="Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_layer_recon(ctx: CheckContext) -> Verdict:
    """A notebook promoting Silver to Gold reconciles the row counts across the hop.

    Distinct from NB-CROSS-RECON (ref 3.6.3), which reconciles several *sources*
    in one load. This one is about the medallion hop: rows that entered Silver
    should be accounted for in Gold, allowing for aggregation.
    """
    code = strip_sql_comments(executable_code(ctx.obj))
    # Token-split rather than ``\bsilver\b``: an underscore is a word character,
    # so a boundary regex cannot see ``silver_dim_customer`` - the very naming
    # convention this looks for.
    layers = layer_words_in(code)
    if not ({"silver", "gold"} <= layers):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' has no executable Silver-to-Gold flow"
        )
    if not _WRITE_PATTERN.search(code):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' references Silver and Gold in executable "
            f"code but contains no table write"
        )
    reconciliation = _COUNT_RECONCILE.search(code)
    if reconciliation:
        signal = " ".join(reconciliation.group(0).split())
        return binary(True, f"Notebook '{ctx.obj_name}' promotes Silver to Gold and "
                      f"reconciles row counts (matched '{signal}')")
    return binary(False, f"Notebook '{ctx.obj_name}' promotes data from Silver to "
                  f"Gold and writes output, but has no source/target count comparison "
                  f"or explicitly named reconciliation control")


# -- MLC Cat-1: grain uniqueness (5.4.9) --------------------------------------
#: The load asserts one row per grain: an explicit dedup, or a duplicate probe.
_GRAIN_GUARD = re.compile(
    r"dropDuplicates\s*\(|drop_duplicates\s*\(|\.distinct\s*\(|"
    r"row_number\s*\(\s*\)[^\n]{0,120}?(?:==|=)\s*1|"
    r"groupBy\s*\([^\n]{0,80}?\)[^\n]{0,60}?count\s*\(\s*\)[^\n]{0,60}?(?:>|filter|where)|"
    r"having\s+count\s*\(\s*\*?\s*\)\s*>\s*1|duplicate[_\s]?(?:check|count|test)",
    re.IGNORECASE,
)
#: A ``CREATE TABLE ... AS SELECT`` (CTAS) populates a table, so it is a write
#: even though it uses no ``.saveAsTable``/``.write``/``INSERT``. Without this a
#: pure-CTAS load (``CREATE TABLE lh.bronze.gl_detail AS SELECT ...``) was
#: reported as "writes no table" — a false reason for the N/A.
_CTAS = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b[\s\S]{0,400}?\bAS\s+SELECT\b",
    re.IGNORECASE,
)
@check(
    id="NB-GRAIN-UNIQUE", ref="5.4.9",
    title="No duplicate grain: fact tables contain unique records per defined grain",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_grain_unique(ctx: CheckContext) -> Verdict:
    """A notebook writing a fact table dedupes it, or proves it has no duplicates.

    The grain itself is not declared anywhere machine-readable, so this judges
    whether the load *enforces* uniqueness at all — a dedup, a row-number filter,
    or a duplicate probe. It cannot confirm the surviving rows are unique; that
    needs a GROUP BY against the warehouse.
    """
    code = strip_sql_comments(executable_code(ctx.obj))
    if not (_WRITE_PATTERN.search(code) or _CTAS.search(code)):
        return not_applicable(f"Notebook '{ctx.obj_name}' writes no table")
    fact_write = _fact_write_evidence(code)
    if not fact_write:
        return not_applicable(
            f"Notebook '{ctx.obj_name}' has table-write syntax but no provable fact "
            f"write target or fact-named DataFrame write receiver; reading a fact "
            f"table or writing a dimension does not establish fact-grain scope"
        )
    guard = _GRAIN_GUARD.search(code)
    if guard:
        signal = " ".join(guard.group(0).split())
        return binary(True, f"Notebook '{ctx.obj_name}' has {fact_write} and enforces "
                      f"grain uniqueness (matched '{signal}')")
    return binary(False, f"Notebook '{ctx.obj_name}' has {fact_write} but contains no "
                  f"dropDuplicates/distinct, row-number survivor filter, GROUP BY "
                  f"duplicate probe, or explicitly named duplicate check")


# -- MLC Cat-1: fact-to-dimension referential integrity (4.5.12) --------------
@check(
    id="NB-FACT-DIM-RI", ref="4.5.12",
    title="Referential integrity validated (every FK in fact tables has a matching dimension record)",
    pillar=Pillar.DATA_MODELING, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_MODELING, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_late_arrival(ctx: CheckContext) -> Verdict:
    """Late or out-of-order changes use a version-aware, duplicate-safe write path."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable(
            "No data-movement activity (Copy / Script / Notebook / Stored-proc / Lookup) "
            "to assess for late-arriving changes"
        )
    blob = json.dumps(ctx.obj)
    signals = _matched_tokens(_LATE_ARRIVAL, blob)
    writes = _matched_tokens(_LATE_SAFE_WRITE, blob)
    moved = _data_move_summary(data_acts)
    if not signals:
        return not_applicable(
            f"{moved} present, but no late-arrival / out-of-order signal (watermark, "
            f"event-time, sequence/version id, effective-date, lookback) is referenced, so "
            f"late-change handling does not apply to this pipeline"
        )
    if writes:
        return binary(
            True,
            f"{moved} with a late-arrival signal ({', '.join(signals)}) and a version-aware "
            f"/ duplicate-safe write ({', '.join(writes)}), so out-of-order changes cannot "
            f"corrupt the target",
        )
    return binary(
        False,
        f"{moved} references a late-arrival signal ({', '.join(signals)}) but NO version-aware "
        f"/ duplicate-safe write was found \u2014 none of {_SAFE_WRITE_NAMES} appears, so a "
        f"re-sent or out-of-order record can insert a duplicate or overwrite a newer row",
    )


@check(
    id="NB-DEDUP", ref="5.2.6",
    title="Duplicate detection across batches",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.ARCHITECTURE, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_bronze_metadata(ctx: CheckContext) -> Verdict:
    """Bronze writes retain ingestion timestamp, source identity, and batch metadata.

    **Only Bronze notebooks are judged.** The layer is taken from what the
    notebook *writes to* (or, failing that, the lakehouse it is attached to) -
    never from the word "bronze" appearing somewhere in the file, which matched
    notebooks that merely *read* raw data and failed them for lacking metadata
    they were never meant to carry.

    **What it cannot determine.** Whether the estate uses the medallion pattern
    at all. When no signal identifies a layer the notebook is reported N/A: an
    org whose layers are named ``raw``/``curated``/``publish`` is *not assessed*
    rather than failed.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write a table")
    layer, how = medallion_layer(ctx.obj, code)
    if not layer:
        return not_applicable(layer_undetermined_evidence(ctx.obj, code))
    if layer != "bronze":
        return not_applicable(
            f"Notebook produces the {layer} layer ({how}), so the Bronze "
            "raw-capture rule does not apply"
        )
    controls = {
        "ingestion timestamp": bool(_BRONZE_INGESTION.search(code)),
        "source identity": bool(_BRONZE_SOURCE.search(code)),
        "batch identifier": bool(_BRONZE_BATCH.search(code)),
    }
    present = [name for name, found in controls.items() if found]
    missing = [name for name, found in controls.items() if not found]
    score = {0: 0, 1: 1, 2: 2, 3: 3}[len(present)]
    evidence = f"Bronze write ({how}); present: {', '.join(present) if present else 'none'}"
    if missing:
        evidence += f"; missing: {', '.join(missing)}"
    return graded(score, evidence)


@check(
    id="NB-SILVER-QUALITY", ref="1.2.5",
    title="Silver Lakehouse applies cleansing, deduplication, conforming, and type standardization",
    pillar=Pillar.ARCHITECTURE, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_silver_quality(ctx: CheckContext) -> Verdict:
    """Silver writes apply cleansing, deduplication, conforming and type standardization.

    **Only Silver notebooks are judged**, decided by what the notebook writes to
    rather than by the word "silver" appearing anywhere in it - a Gold notebook
    that *reads* a silver table was previously judged as though it produced one.

    **Scored per aspect.** The point names four disciplines, and a notebook doing
    three of them is not the same as one doing all four. An earlier version
    matched any one pattern and then claimed all four in its evidence, which
    reported deduplication on notebooks that performed none. The verdict now
    names which aspects were found and which were not, and scores the ratio, so
    the evidence can be checked against the code.

    **What it cannot determine.** That a transformation is *correct*, or that an
    absent aspect was unnecessary - a source with a guaranteed-unique key needs
    no dedup. The named gaps are for a reviewer to confirm, and the write target
    is named so the reader knows which lakehouse was judged.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write a table")
    layer, how = medallion_layer(ctx.obj, code)
    if not layer:
        return not_applicable(layer_undetermined_evidence(ctx.obj, code))
    if layer != "silver":
        return not_applicable(
            f"Notebook produces the {layer} layer ({how}), so the Silver "
            "cleansing rule does not apply"
        )

    present = [name for name, pattern in _SILVER_ASPECTS if pattern.search(code)]
    missing = [name for name, _ in _SILVER_ASPECTS if name not in present]
    return covered(
        len(present), len(_SILVER_ASPECTS),
        f"Silver write ({how}) applies {len(present)} of {len(_SILVER_ASPECTS)} "
        f"quality aspect(s): {', '.join(present) if present else 'none'}"
        + (f". Not found: {', '.join(missing)}" if missing else ""),
    )


#: Activity types that actually move or transform *bulk data inside the pipeline*.
#: A notebook run (``TridentNotebook``), a control-table ``Lookup``, or an
#: ``ExecutePipeline`` orchestration moves no bulk data itself — any movement
#: happens in the notebook or child pipeline and is judged there — so a pipeline
#: built only from those is N/A for this check rather than a failure.
_BULK_MOVE_TYPES = {"Copy", "Script", "SqlServerStoredProcedure"}


@check(
    id="PL-BULK-MOVE", ref="2.6.3",
    title="Large data movements use bulk/batch patterns, not row-by-row",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pl_bulk_move(ctx: CheckContext) -> Verdict:
    """A data-moving pipeline uses a bulk/batch pattern, not row-by-row execution.

    Only an in-pipeline data mover — a Copy activity, a Script, or a stored
    procedure — is judged. A pipeline that merely runs a notebook, reads a
    control table with a Lookup, or invokes child pipelines moves no bulk data
    itself, so it is N/A here (the movement, if any, is judged where it happens).
    The evidence names the data-movement activities found and the exact
    bulk-pattern or row-by-row signals that decided the verdict.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    movers = [a for a in walk_activities(ctx.obj)
              if (a.get("type") or "") in _BULK_MOVE_TYPES]
    if not movers:
        return not_applicable(
            "Pipeline moves no bulk data itself — it only runs notebooks, reads "
            "control tables (Lookup), or invokes child pipelines, so any data "
            "movement is judged where it happens, not in this pipeline"
        )
    moved = _data_move_summary(movers)

    def configured(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
        return bool(value)

    def copy_signals(activity: dict) -> list[str]:
        props = activity.get("typeProperties") or {}
        sink = props.get("sink") or {}
        signals: list[str] = []
        if props.get("enableStaging") is True:
            signals.append("enableStaging=true")
        if sink.get("allowCopyCommand") is True:
            signals.append("allowCopyCommand=true")
        if configured(props.get("parallelCopies")):
            signals.append(f"parallelCopies={props['parallelCopies']}")
        for key in ("writeBatchSize", "batchSize"):
            if configured(sink.get(key)):
                signals.append(f"{key}={sink[key]}")
        return signals

    row_by_row = [
        str(activity.get("name") or "?")
        for activity in walk_activities(ctx.obj)
        if _EXPLICIT_ROW_BY_ROW.search(json.dumps(activity))
    ]
    if row_by_row:
        return binary(
            False,
            f"{moved}: explicit row-by-row / record-by-record logic found in "
            f"{', '.join(row_by_row[:5])} — replace it with a set-based Copy, COPY INTO, "
            "or INSERT ... SELECT operation",
        )

    tuned: list[str] = []
    untuned: list[str] = []
    for activity in movers:
        name = str(activity.get("name") or "?")
        activity_type = activity.get("type") or "?"
        if activity_type == "Copy":
            signals = copy_signals(activity)
            if signals:
                tuned.append(f"{name} ({', '.join(signals)})")
            else:
                untuned.append(f"{name} (default Copy settings)")
        elif activity_type == "Script" and _SET_BASED_SCRIPT.search(json.dumps(activity)):
            tuned.append(f"{name} (set-based SQL)")
        else:
            untuned.append(f"{name} ({activity_type} internals not provable)")

    if tuned and not untuned:
        return graded(3, f"{moved}: every data movement activity has a genuine bulk/"
                         f"set-based signal: {'; '.join(tuned)}")
    if tuned:
        return graded(2, f"{moved}: bulk controls are present for {'; '.join(tuned)}, but "
                         f"not for {'; '.join(untuned)}")
    return graded(
        1,
        f"{moved}: warning — no explicit row-by-row logic was found, but the movement "
        f"uses default or unverifiable settings: {'; '.join(untuned)}. Configure Copy "
        "staging, Copy Command, parallelCopies, or a sink write batch size where the "
        "connector and workload support it",
    )



@check(
    id="NB-EAM-INGEST", ref="2.6.6",
    title="JSON ingestion (EAM) is efficient (streaming/partitioned parse, no oversized single-file bottlenecks)",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_eam_ingest(ctx: CheckContext) -> Verdict:
    """EAM/JSON ingestion uses streaming, partitioning, or bounded-file parsing.

    Commented-out code is ignored: ``executable_code`` removes Python ``#``
    comments and ``strip_sql_comments`` removes SQL ``--`` / ``/* */`` comments,
    so a table name or technique that appears only in a comment does not open the
    gate. The evidence names the EAM/JSON signal that opened the gate and the
    efficiency pattern found, or every pattern searched for on a failure.
    """
    code = strip_sql_comments(executable_code(ctx.obj))
    signals = _matched_tokens(_EAM_JSON, code)
    if not signals:
        return not_applicable(
            "Notebook has no EAM/JSON ingestion once commented-out code is ignored "
            "(no spark.read.json / from_json / readStream JSON / EAM signal in live code)"
        )
    efficient = _matched_tokens(_EAM_EFFICIENT, code)
    if efficient:
        return binary(
            True,
            f"EAM/JSON ingestion ({', '.join(signals)}) parses efficiently "
            f"({', '.join(efficient)})",
        )
    return binary(
        False,
        f"EAM/JSON ingestion ({', '.join(signals)}) has no streaming/partitioning/"
        f"bounded-file pattern — none of readStream, partitionBy, repartition, "
        f"maxFilesPerTrigger, multiLine=False, badRecordsPath, from_json or "
        f"schema_of_json is present to bound file size or parallelize the parse",
    )


@check(
    id="NB-SOURCE-METADATA", ref="5.2.8",
    title="Source metadata captured: ingestion timestamp",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_source_metadata(ctx: CheckContext) -> Verdict:
    """Notebooks that ingest from *outside* the lakehouse record where the data came from.

    **Scoped to ingestion.** The point is about provenance: a load that reads an
    external file, an API or a database must record its source. A notebook that
    reads a table already inside the lakehouse and writes a derived table has no
    external source to capture - failing it for missing "source metadata" is a
    false finding, and judging every writing notebook did exactly that.

    **Sibling.** ``NB-BRONZE-METADATA`` (ref 1.2.3) asks a *narrower* question of
    a *narrower* population: it requires ingestion timestamp **and** batch/source
    identity, and only of notebooks writing the Bronze layer. This one applies
    wherever an external read happens - Bronze or not - and asks only that the
    provenance of that read is recorded. A Bronze ingestion notebook is judged by
    both, which is intended: they are different thresholds on the same practice.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable("Notebook does not write data to a table")
    if not _EXTERNAL_SOURCE_READ.search(code):
        return not_applicable(
            "Notebook reads no external source (file, API or database) - it "
            "transforms data already in the lakehouse, so there is no ingestion "
            "provenance to capture"
        )
    ok = bool(_SOURCE_METADATA.search(code))
    return binary(ok, "Source metadata and ingestion timestamp are captured" if ok
                  else "Ingests from an external source without recording its "
                       "provenance (source system/file or an ingestion timestamp)")


@check(
    id="NB-DEDUP-VERIFY", ref="5.3.4",
    title="Deduplication verification: no duplicate business records",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    id="NB-CATEGORICAL-DOMAIN", ref="5.5.5",
    title="**Categorical / Enum**: Values within expected domain; no invalid codes flowing to Gold",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_categorical_domain(ctx: CheckContext) -> Verdict:
    """The notebook pins categorical/code columns to an expected set of values.

    **What it verifies: the control, not the data.** Whether any particular row
    carries a valid code is a runtime fact in the data, which this tool never
    reads. A PASS means "an invalid code would be caught", never "every code is
    valid".

    **What it can determine.** Three shapes, because they are the three ways a
    team actually pins a code list in Spark: a membership test against literal
    codes or a named allow-list (``status.isin(VALID_STATUSES)``), an anti/semi
    join against a reference, lookup or dimension table (the standard "reject
    codes absent from the dimension" pattern), or a declared allowed-value
    collection. Read with ``executable_code`` so a commented-out validation
    cannot pass.

    **What it cannot.** Confirm the code list is the *correct* one, see a domain
    enforced by a Delta ``CHECK`` constraint or a warehouse foreign key, or see a
    rule applied downstream in a pipeline. A notebook that writes no table has no
    codes flowing to Gold and is N/A.

    **Sibling - deliberately not satisfied by it.** ``NB-FLAG-DOMAIN`` (5.5.7)
    covers the two-valued boolean/flag case. A membership test whose literals are
    ``Y``/``N``/``true``/``false``/``0``/``1`` is a *flag* test and is excluded
    here, so one line of code cannot satisfy both points.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable(
            "Notebook writes no table, so there is no categorical value flowing "
            "onward to constrain"
        )

    if _CATEGORICAL_REFERENCE_JOIN.search(code):
        return binary(True, (
            "Categorical values are validated against a reference/lookup table - a "
            "code absent from the reference is separated rather than written on. "
            "Whether any given run's codes were valid is a runtime outcome this "
            "check does not read."
        ))
    literal_set = _CATEGORICAL_DOMAIN.search(code)
    allow_list = _CATEGORICAL_ALLOWLIST.search(code)
    if literal_set or allow_list:
        how = ("an explicit code list" if literal_set
               else "a named allowed-value set")
        return binary(True, (
            f"Categorical/enum columns are restricted to {how}, so an unexpected "
            f"code is detectable before it reaches Gold. Whether the list itself is "
            f"correct is not machine-checkable."
        ))
    if _BOOLEAN_LITERALS.search(code):
        return binary(False, (
            "The only value restriction found is a boolean/flag test (Y/N, true/false), "
            "which is scored by 5.5.7 - no categorical or code column is pinned to an "
            "expected domain, so an invalid code would flow through unchallenged"
        ))
    return binary(False, (
        "Notebook writes data with no categorical domain validation - no allowed-value "
        "test and no reference/lookup join appears, so an invalid or retired code is "
        "indistinguishable from a valid one"
    ))


@check(
    id="NB-DQ-RULES", ref="5.1.2",
    title="DQ rules codified in code/config (not ad-hoc manual checks)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def nb_dq_rules(ctx: CheckContext) -> Verdict:
    """Notebook data-quality rules are expressed as executable, repeatable logic.

    **Scored per discipline, not on a single token.** An earlier version passed
    on any one match from a bundled pattern, so a notebook whose only quality
    logic was ``drop_duplicates`` scored full marks for "DQ rules codified in
    code/config" - and said so in its evidence. Deduplication is housekeeping;
    it is not a rule that decides whether a record is fit to use.

    Four disciplines are looked for separately - assertions/expectations, schema
    validation, null/domain checks, and quarantine of rejected rows - and the
    ratio is scored, so the verdict distinguishes a notebook with one incidental
    call from one that actually validates what it loads. The evidence names both
    what was found and what was not, so it can be checked against the code.

    Comments are stripped first: a rule that exists only in a comment is not a
    codified rule.

    **What it cannot determine.** Whether the rules are *correct* or cover the
    fields that matter - only that they are executable and repeatable rather
    than performed by hand.
    """
    code = executable_code(ctx.obj)
    if not (_INPUT_READ.search(code) or _WRITE_PATTERN.search(code)):
        return not_applicable("Notebook has no recognizable data-ingestion or write operation")

    present: list[str] = []
    missing: list[str] = []
    for label, pattern in _DQ_ASPECTS:
        (present if pattern.search(code) else missing).append(label)

    return covered(
        len(present), len(_DQ_ASPECTS),
        f"{len(present)} of {len(_DQ_ASPECTS)} data-quality discipline(s) are codified "
        f"in executable notebook code: {', '.join(present) if present else 'none'}"
        + (f". Not found: {', '.join(missing)}" if missing else ""),
    )



# =============================================================================
# MLC Cat-1 · ingestion framework (2.1.5, 2.2.2-2.2.4, 2.3.2-2.3.4, 2.5.1, 2.5.3)
# =============================================================================

# -- 2.1.5 parallel execution -------------------------------------------------
_FOREACH = "ForEach"


@check(
    id="PL-PARALLEL", ref="2.1.5",
    title="Parallel execution used where possible (no unnecessary sequential execution)",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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

#: ``tableActionOption`` values that replace a Lakehouse table's contents.
#: ``Append`` and ``Upsert`` add to it; ``Overwrite``/``OverwriteSchema`` do not.
_OVERWRITE_TABLE_ACTIONS = frozenset({"overwrite", "overwriteschema"})


def _copy_sinks(definition: dict) -> list[dict]:
    """Every Copy activity's ``sink`` block, including those inside containers."""
    sinks: list[dict] = []
    for activity in walk_activities(definition):
        if (activity.get("type") or "") != "Copy":
            continue
        sink = (activity.get("typeProperties") or {}).get("sink")
        if isinstance(sink, dict):
            sinks.append(sink)
    return sinks


def _sink_table(sink: dict) -> str:
    """The table a Copy sink writes to, or "" when it is not statically known.

    Fabric embeds the target inline rather than in a separate dataset artifact,
    but one level deeper than the activity's own ``typeProperties``:
    ``sink.datasetSettings.typeProperties.table``. Reading only the activity
    level - which this check used to do - found nothing and reported every
    Copy-driven reload as an unnamed target.

    Returns "" when the name is a pipeline *expression*
    (``{"value": "@{item().TABLE_NAME}", "type": "Expression"}``), because a
    ForEach-driven copy genuinely has no single target until run time.
    """
    props = (sink.get("datasetSettings") or {}).get("typeProperties") or {}
    table = props.get("table")
    if isinstance(table, str):
        return table
    return ""      # expression object, or absent


def _sink_overwrites(sink: dict) -> bool:
    """True when this sink replaces the table's contents rather than adding to it."""
    action = str(sink.get("tableActionOption") or "").strip().lower()
    if action in _OVERWRITE_TABLE_ACTIONS:
        return True
    if str(sink.get("writeBehavior") or "").strip().lower() == "overwrite":
        return True
    # A pre-copy TRUNCATE/DELETE is a full reload however the write is spelled.
    pre_copy = sink.get("preCopyScript")
    return bool(isinstance(pre_copy, str)
                and re.search(r"\b(?:TRUNCATE|DELETE)\b", pre_copy, re.IGNORECASE))


def _bare_table(name: str) -> str:
    """The table name without schema qualifier, brackets, or quotes."""
    return (name or "").replace("[", "").replace("]", "").replace('"', "").split(".")[-1]


#: Signals that a full reload is a one-time / first-time load rather than the
#: routine run — the case the checklist explicitly permits for fact tables.
_INITIAL_LOAD = re.compile(
    r"initial[_ -]?load|first[_ -]?load|full[_ -]?load|is[_ -]?initial|one[_ -]?time|"
    r"back[_ -]?fill|backfill|historical|seed|bootstrap|reload[_ -]?history",
    re.IGNORECASE,
)


def _is_initial_load_context(ctx: CheckContext) -> bool:
    """True when the pipeline declares itself an initial / one-time load.

    Read from the pipeline name, an initial-load parameter, or a Switch/If branch
    that selects the load mode — the three ways a first-load is kept off the
    routine incremental path.
    """
    if _INITIAL_LOAD.search(ctx.obj_name):
        return True
    params = (ctx.obj.get("properties") or {}).get("parameters") or {}
    if any(_INITIAL_LOAD.search(p) for p in params):
        return True
    return any(
        (a.get("type") or "") in {"Switch", "IfCondition"} and _INITIAL_LOAD.search(json.dumps(a))
        for a in walk_activities(ctx.obj)
    )


@check(
    id="PL-FULLLOAD", ref="2.2.2",
    title="Full load reserved only for small reference/dimension tables or initial loads",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_full_load(ctx: CheckContext) -> Verdict:
    """Wholesale reloads target lookup/dimension tables, or are a one-time initial load.

    **Three sources of a reload target**, all read from the definition itself:
    inline T-SQL (``TRUNCATE`` / ``INSERT OVERWRITE`` / ``DROP TABLE``) in a
    Script activity, and - for a Copy activity - the sink's own declared
    behaviour and target. Fabric embeds the sink inline rather than in a separate
    dataset artifact, so ``tableActionOption`` (``Overwrite`` / ``OverwriteSchema``
    vs ``Append``), ``writeBehavior``, ``preCopyScript`` and the target
    ``schema``/``table`` are all in the pipeline JSON. Reading only the SQL - which
    this check used to do - reported every Copy-driven reload as an unnamed target.

    **What it cannot determine.** The target of a Copy whose table name is a
    pipeline expression (``@{item().TABLE_NAME}`` in a ForEach), because it has no
    single target until run time. That is N/A: an unresolvable name is a gap in
    what the definition exposes, not evidence that a fact table is reloaded.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    sql = script_sql(ctx.obj)

    targets: list[str] = []
    for pattern in _FULL_LOAD_TARGETS:
        targets.extend(_bare_table(m) for m in pattern.findall(sql))

    # Copy activities: the sink states whether it overwrites, and what it writes to.
    overwriting_sinks = [s for s in _copy_sinks(ctx.obj) if _sink_overwrites(s)]
    dynamic_targets = 0
    for sink in overwriting_sinks:
        table = _sink_table(sink)
        if table:
            targets.append(_bare_table(table))
        else:
            dynamic_targets += 1

    # The old catch-all: an overwrite signal somewhere in the JSON that the sink
    # parse did not attribute to a table. Kept so a sink shape we do not model
    # still counts as a reload rather than vanishing.
    overwrite_copy = bool(overwriting_sinks) or bool(_OVERWRITE_BEHAVIOUR.search(json.dumps(ctx.obj)))

    if not targets and not overwrite_copy:
        return not_applicable("Pipeline runs no full-reload statement "
                              "(TRUNCATE / INSERT OVERWRITE / overwrite sink)")

    # An initial / one-time load may reload anything, fact tables included - the
    # checklist reserves full loads for small tables *or* initial loads.
    initial_load = _is_initial_load_context(ctx)
    if not targets:
        if initial_load:
            return binary(True, "A Copy activity overwrites its sink, but this is a dedicated "
                                "initial/one-time load, which the standard permits")
        # Undetermined is N/A, never a partial failure: scoring here marked a
        # pipeline down for this tool's blind spot rather than for anything the
        # team did.
        if dynamic_targets:
            return not_applicable(
                f"{dynamic_targets} Copy activity/activities overwrite a table whose name "
                f"is a pipeline expression resolved at run time (a metadata-driven "
                f"ForEach), so which table is reloaded cannot be determined from the "
                f"definition"
            )
        return not_applicable(
            "A Copy activity overwrites its sink, but the target table is not named "
            "in the definition, so whether the reload targets a small "
            "reference/dimension table cannot be determined"
        )

    unresolved = (f". A further {dynamic_targets} overwrite target(s) are named by a "
                  f"run-time expression and are not judged") if dynamic_targets else ""

    facts = sorted({t for t in targets if is_fact(t)})
    safe = sorted({t for t in targets if not is_fact(t)})
    if facts:
        if initial_load:
            return binary(True, f"Full reload targets fact table(s): {', '.join(facts)}, but "
                                f"this is a dedicated initial/one-time load, which the standard "
                                f"permits for fact tables" + unresolved)
        return binary(False, f"Full reload targets fact table(s): {', '.join(facts)} — "
                             f"facts should load incrementally, not be replaced wholesale"
                             + unresolved)
    kind = "dimension/reference" if any(is_dimension(t) for t in safe) else "reference"
    return binary(True, f"Full reload targets only {kind} table(s): {', '.join(safe)}" + unresolved)


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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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
    acts = walk_activities(ctx.obj)
    historical_in_name = bool(_HISTORICAL.search(ctx.obj_name))
    historical_in_definition = bool(_HISTORICAL.search(blob))
    if not historical_in_definition and not historical_in_name:
        return not_applicable(
            f"Pipeline '{ctx.obj_name}' contains no historical/backfill load signal"
        )
    if not acts:
        return not_applicable(
            f"Pipeline '{ctx.obj_name}' is named as a historical/backfill load but contains "
            "no activities, so no implemented load or separation can be verified"
        )

    gating_branches = [
        a for a in acts
        if (a.get("type") or "") in {"Switch", "IfCondition"}
        and (_HISTORICAL.search(json.dumps(a)) or _LOAD_MODE.search(json.dumps(a)))
    ]
    if gating_branches:
        gate = gating_branches[0]
        return binary(
            True,
            f"Pipeline '{ctx.obj_name}' gates its historical/backfill path behind "
            f"{gate.get('type')} activity '{gate.get('name') or '?'}'; the routine "
            "incremental path cannot enter it without the branch condition",
        )

    if _ONGOING.search(blob):
        return binary(
            False,
            f"Pipeline '{ctx.obj_name}' contains both historical/backfill and ongoing "
            "incremental signals on an ungated activity path; no Switch or IfCondition "
            "prevents a routine run from replaying history",
        )

    if historical_in_name:
        activity_names = ", ".join(str(a.get("name") or "?") for a in acts[:3])
        return binary(
            True,
            f"Pipeline '{ctx.obj_name}' is a dedicated historical/backfill artifact with "
            f"{len(acts)} activity(ies) ({activity_names}) and no ongoing incremental "
            "activity in its saved definition",
        )

    return graded(
        1,
        f"Pipeline '{ctx.obj_name}' contains historical/backfill logic but no ongoing "
        "incremental path or explicit Switch/IfCondition separation to evaluate",
    )


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
_WATERMARK_READ_TARGET = re.compile(
    r"(?:FROM|JOIN)\s+(?P<table>[\w.\[\]\"]*"
    r"(?:watermark|control|metadata|etl_?config|load_?log)[\w.\[\]\"]*)",
    re.IGNORECASE,
)
_WATERMARK_WRITE_TARGET = re.compile(
    r"(?:UPDATE|INSERT\s+INTO|MERGE\s+INTO)\s+(?P<table>[\w.\[\]\"]*"
    r"(?:watermark|control|metadata|etl_?config|load_?log)[\w.\[\]\"]*)",
    re.IGNORECASE,
)


def _activity_sql(activity: dict) -> str:
    """Return SQL embedded in Lookup or Script activities."""
    props = activity.get("typeProperties") or {}
    parts: list[str] = []
    source = props.get("source") or {}
    for key in ("sqlReaderQuery", "query"):
        value = source.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict) and value.get("value"):
            parts.append(str(value["value"]))
    for entry in props.get("scripts") or []:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, dict) and text.get("value"):
            parts.append(str(text["value"]))
    if isinstance(props.get("script"), str):
        parts.append(props["script"])
    return "\n".join(parts)


def _activity_connection(activity: dict) -> str | None:
    """Return the Fabric connection/item name attached to an activity."""
    props = activity.get("typeProperties") or {}
    dataset = props.get("datasetSettings") or {}
    for source in (activity, props, dataset):
        linked = source.get("linkedService") or {}
        if linked.get("name"):
            return str(linked["name"])
    return None


def _normalise_table(name: str) -> str:
    return name.replace("[", "").replace("]", "").replace('"', "").lower()


def _succeeds_after(activity: dict, predecessor: str) -> bool:
    return any(
        dependency.get("activity") == predecessor
        and "Succeeded" in (dependency.get("dependencyConditions") or [])
        for dependency in activity.get("dependsOn") or []
    )


@check(
    id="PL-WATERMARK-STORE", ref="2.2.4",
    title="Watermark / control values persisted reliably in the Metadata DB (not volatile locations)",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
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

    acts = walk_activities(ctx.obj)
    reads: list[tuple[dict, str]] = []
    writes: list[tuple[dict, str]] = []
    procedures: list[dict] = []
    for activity in acts:
        activity_type = activity.get("type") or ""
        activity_sql = _activity_sql(activity)
        if activity_type == "Lookup":
            reads.extend((activity, match.group("table"))
                         for match in _WATERMARK_READ_TARGET.finditer(activity_sql))
        if activity_type == "Script":
            writes.extend((activity, match.group("table"))
                          for match in _WATERMARK_WRITE_TARGET.finditer(activity_sql))
        if activity_type == "SqlServerStoredProcedure" and _WATERMARK.search(json.dumps(activity)):
            procedures.append(activity)

    for read_activity, read_table in reads:
        for write_activity, write_table in writes:
            if _normalise_table(read_table) != _normalise_table(write_table):
                continue
            read_name = read_activity.get("name") or "unnamed Lookup"
            write_name = write_activity.get("name") or "unnamed Script"
            connection = _activity_connection(read_activity) or _activity_connection(write_activity)
            location = f" in Warehouse/connection '{connection}'" if connection else ""
            if _succeeds_after(write_activity, read_name):
                return binary(
                    True,
                    f"Durable watermark pattern confirmed: Lookup '{read_name}' reads "
                    f"{read_table}{location}; Script '{write_name}' updates the same "
                    "table after the Lookup succeeds",
                )
            return graded(
                2,
                f"Lookup '{read_name}' reads {read_table}{location} and Script "
                f"'{write_name}' writes the same table, but the write is not explicitly "
                "dependent on Lookup success",
            )

    if reads and procedures:
        read_activity, read_table = reads[0]
        procedure = procedures[0]
        read_name = read_activity.get("name") or "unnamed Lookup"
        procedure_name = procedure.get("name") or "unnamed stored procedure"
        if _succeeds_after(procedure, read_name):
            return binary(
                True,
                f"Durable watermark pattern confirmed: Lookup '{read_name}' reads "
                f"{read_table}; stored-procedure activity '{procedure_name}' persists "
                "the watermark after the Lookup succeeds",
            )

    if writes or procedures:
        writer = writes[0][0] if writes else procedures[0]
        writer_name = writer.get("name") or "unnamed write activity"
        target = f" {writes[0][1]}" if writes else " a durable watermark store"
        if reads:
            read_name = reads[0][0].get("name") or "unnamed Lookup"
            read_table = reads[0][1]
            return graded(
                2,
                f"Lookup '{read_name}' reads {read_table}, while '{writer_name}' writes"
                f"{target}; no safely sequenced same-table read/write pair was found",
            )
        return graded(
            2,
            f"Activity '{writer_name}' writes{target}, but no Lookup reads a durable "
            "watermark table — confirm that each run retrieves the persisted value",
        )
    if reads:
        reader, table = reads[0]
        reader_name = reader.get("name") or "unnamed Lookup"
        return graded(
            1,
            f"Lookup '{reader_name}' reads {table}, but no Script or stored procedure "
            "writes the new watermark back — the persisted value can become stale",
        )
    return binary(False, "Watermark exists only in pipeline variables/expressions, not "
                         "in a durable control table — it is lost when the run ends")


# -- 2.3.2 operation type flag preserved in Bronze ----------------------------
_OP_TYPE_COLUMN = re.compile(
    r"(?P<column>operation[_ -]?type|op[_ -]?type|change[_ -]?type|_change_type|"
    r"__\$operation|cdc[_ -]?operation|dml[_ -]?action|record[_ -]?type|\bopcode\b|"
    r"sys[_ -]?change[_ -]?operation)",
    re.IGNORECASE,
)
_CDC_SOURCE_SIGNAL = re.compile(
    r"change[_ -]?data[_ -]?capture|\bcdc\b|change[_ -]?(?:feed|stream|events?|records?)|"
    r"source[_ -]?changes?|readChangeFeed|readChangeData|startingVersion|startingTimestamp|"
    r"_change_type|__\$operation|sys_change_operation|dml[_ -]?action",
    re.IGNORECASE,
)


@check(
    id="NB-OPTYPE", ref="2.3.2",
    title="Operation type column/flag preserved in Bronze for auditability where the source provides it",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def nb_operation_type(ctx: CheckContext) -> Verdict:
    """Bronze keeps the source's I/U/D flag so downstream layers can replay it.

    Read from the notebook that writes Bronze rather than from table columns:
    the Fabric REST API returns table metadata without columns, so a
    column-based test would be N/A on every live run.

    **The Bronze test is on the write target, not a keyword.** An earlier version
    searched the whole notebook for ``\\bbronze\\b``, which cannot match
    ``bronze_raw_orders`` - the character after ``bronze`` is ``_``, a word
    character, so there is no word boundary. The convention the check exists to
    find was the one convention it could not see, and it reported "does not write
    a Bronze/raw table" on a notebook that plainly did. Matching the write target
    also stops a notebook that merely *reads* Bronze being judged as producing it.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    writes_bronze, bronze_target = writes_layer(code, "bronze")
    if not writes_bronze:
        return not_applicable("Notebook does not write a Bronze/raw table")
    targets = [bronze_target]
    flags = list(dict.fromkeys(
        match.group("column") for match in _OP_TYPE_COLUMN.finditer(code)
    ))
    target_evidence = ", ".join(targets) if targets else "target not statically resolved"
    if flags:
        return binary(
            True,
            f"Notebook '{ctx.obj_name}' writes Bronze/raw target(s) {target_evidence} "
            f"and preserves recognized operation flag column(s): {', '.join(flags)}. "
            "This verdict verifies the saved write logic, not the operation values in "
            "current table rows",
        )
    if not _CDC_SOURCE_SIGNAL.search(code):
        return not_applicable(
            f"Notebook '{ctx.obj_name}' writes Bronze/raw target(s) {target_evidence}, "
            "but the saved definition does not show that its source provides CDC/change "
            "operation metadata; the 'where the source provides it' condition cannot be "
            "determined",
        )
    return binary(
        False,
        f"Notebook '{ctx.obj_name}' writes Bronze/raw target(s) {target_evidence}, but "
        "no operation flag column is preserved (expected operation_type, change_type, "
        "opcode, or an equivalent CDC operation field) — updates cannot be distinguished "
        "from inserts and deletes are unrecoverable",
    )


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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
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
_LOOKUP_OUTPUT_REF = re.compile(
    r"activity\s*\(\s*(['\"])(?P<name>[^'\"]+)\1\s*\)\s*\.output",
    re.IGNORECASE,
)
_METADATA_FIELDS = {
    "source list": re.compile(
        r"source[_\s-]?(?:name|object|table|path|list)|required[_\s-]?source",
        re.IGNORECASE,
    ),
    "load type": re.compile(r"load[_\s-]?(?:type|mode|strategy)", re.IGNORECASE),
    "schedule": re.compile(
        r"schedule(?:[_\s-]?name)?|cadence|frequency", re.IGNORECASE
    ),
    "target mapping": re.compile(
        r"target[_\s-]?(?:schema|table|object|path|mapping)", re.IGNORECASE
    ),
}


def _metadata_lookup_signal(activity: dict) -> str:
    """Return user-authored Lookup signals, excluding connection metadata."""
    type_properties = activity.get("typeProperties") or {}
    return json.dumps({
        "name": activity.get("name") or "",
        "source": type_properties.get("source") or {},
    })


def _lookup_reference(activity: dict) -> str | None:
    items = (activity.get("typeProperties") or {}).get("items") or ""
    match = _LOOKUP_OUTPUT_REF.search(json.dumps(items))
    return match.group("name") if match else None


@check(
    id="PL-METADATA-DRIVEN", ref="2.5.1",
    title="Metadata DB drives ingestion (source list, load type, schedule, target mapping) rather than hardcoded pipelines",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pl_metadata_driven(ctx: CheckContext) -> Verdict:
    """A metadata/config Lookup supplies all ingestion dimensions to a ForEach."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    data_acts = [a for a in acts if (a.get("type") or "") in _DATA_MOVE_TYPES]
    if not data_acts:
        return not_applicable(
            f"Pipeline '{ctx.obj_name}' moves no data — nothing to drive from metadata"
        )

    lookups = [a for a in acts if (a.get("type") or "") == "Lookup"]
    metadata_lookups = {
        str(activity.get("name") or ""): (activity, _metadata_lookup_signal(activity))
        for activity in lookups
        if _METADATA_SOURCE.search(_metadata_lookup_signal(activity))
    }
    loops = [a for a in acts if (a.get("type") or "") == _FOREACH]
    driven_loops = [(activity, _lookup_reference(activity)) for activity in loops]
    driven_loops = [(activity, source) for activity, source in driven_loops if source]
    linked = [
        (metadata_lookups[source], loop)
        for loop, source in driven_loops
        if source in metadata_lookups
    ]

    if linked:
        (lookup, signal), loop = linked[0]
        present = [label for label, pattern in _METADATA_FIELDS.items()
                   if pattern.search(signal)]
        missing = [label for label in _METADATA_FIELDS if label not in present]
        if not missing:
            return binary(
                True,
                f"Pipeline '{ctx.obj_name}' is metadata-driven: Lookup "
                f"'{lookup.get('name')}' selects source list, load type, schedule and "
                f"target mapping fields, and ForEach '{loop.get('name')}' iterates that "
                "Lookup's output",
            )
        return graded(
            2,
            f"Pipeline '{ctx.obj_name}' links metadata Lookup '{lookup.get('name')}' to "
            f"ForEach '{loop.get('name')}', but the saved Lookup does not expose "
            f"{', '.join(missing)}",
        )
    if metadata_lookups:
        lookup_name = next(iter(metadata_lookups))
        if driven_loops:
            loop, source = driven_loops[0]
            return graded(
                1,
                f"Pipeline '{ctx.obj_name}' has metadata Lookup '{lookup_name}', but "
                f"ForEach '{loop.get('name')}' iterates '{source}' instead — the metadata "
                "source does not drive ingestion",
            )
        return graded(
            1,
            f"Pipeline '{ctx.obj_name}' has metadata Lookup '{lookup_name}', but no "
            "ForEach iterates its output — the source list is still fixed",
        )
    if driven_loops:
        loop, source = driven_loops[0]
        return graded(
            1,
            f"Pipeline '{ctx.obj_name}' has ForEach '{loop.get('name')}' iterating "
            f"'{source}', but that Lookup is not a metadata/config source",
        )
    return binary(
        False,
        f"Pipeline '{ctx.obj_name}' has {len(data_acts)} data activity(ies) with no "
        "metadata-driven Lookup+ForEach — adding a source means editing the pipeline",
    )


# -- 2.5.3 run control tables capture batch id, status, counts, timestamps ----
_RUN_CONTROL_WRITE = re.compile(
    r"(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE)\s+[\w.\[\]\"]*"
    r"(?:run[_ -]?(?:control|log|history)|batch[_ -]?(?:control|log)|"
    r"audit[_ -]?(?:log|table)|etl[_ -]?log|load[_ -]?log|process[_ -]?log)|"
    r"(?:saveAsTable|\.write)[\s\S]{0,80}?"
    r"(?:run_?control|run_?log|batch_?log|audit_?log|etl_?log|load_?log)",
    re.IGNORECASE,
)
_RUN_CONTROL_SQL_TARGET = re.compile(
    r"(?:INSERT\s+INTO|MERGE\s+INTO|UPDATE)\s+(?P<target>[\w.\[\]\"]*"
    r"(?:run[_ -]?(?:control|log|history)|batch[_ -]?(?:control|log)|"
    r"audit[_ -]?(?:log|table)|etl[_ -]?log|load[_ -]?log|process[_ -]?log)"
    r"[\w.\[\]\"]*)",
    re.IGNORECASE,
)
_RUN_CONTROL_NOTEBOOK_TARGET = re.compile(
    r"saveAsTable\s*\(\s*[\"'`](?P<target>[^\"'`]+)[\"'`]",
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
}
_RUN_CONTROL_START = re.compile(r"start[_ -]?(?:time|date|utc)", re.IGNORECASE)
_RUN_CONTROL_END = re.compile(
    r"end[_ -]?(?:time|date|utc)|finish[_ -]?time", re.IGNORECASE)


def _run_control_target(text: str) -> str:
    for pattern in (_RUN_CONTROL_SQL_TARGET, _RUN_CONTROL_NOTEBOOK_TARGET):
        match = pattern.search(text)
        if match:
            return match.group("target").replace("[", "").replace("]", "").replace('"', "")
    return "target not statically resolved"


def _run_control_fields(text: str) -> tuple[dict[str, str], list[str]]:
    fields = {
        label: match.group(0)
        for label, pattern in _RUN_CONTROL_ELEMENTS.items()
        if (match := pattern.search(text))
    }
    start = _RUN_CONTROL_START.search(text)
    end = _RUN_CONTROL_END.search(text)
    if start and end:
        fields["start/end timestamps"] = f"{start.group(0)}, {end.group(0)}"

    missing = [label for label in _RUN_CONTROL_ELEMENTS if label not in fields]
    if not start:
        missing.append("start timestamp (expected start_time/start_utc or equivalent)")
    if not end:
        missing.append("end timestamp (expected end_time/end_utc/finish_time or equivalent)")
    return fields, missing


@check(
    id="WS-RUNCONTROL", ref="2.5.3",
    title="Run control tables capture batch ID, status, row counts, start/end timestamps",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pipeline_available = ctx.workspace.has(Resource.PIPELINE_DEFINITIONS)
    notebook_available = ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS)
    if not pipeline_available and not notebook_available:
        return not_applicable("Pipeline and notebook definitions could not be read from Fabric")

    pipelines = ctx.workspace.pipelines if pipeline_available else {}
    notebooks = ctx.workspace.notebooks if notebook_available else {}
    if not pipelines and not notebooks:
        return not_applicable("No pipeline or notebook definitions available to "
                              "inspect for run-control logging")

    writers: list[tuple[str, str, str, str]] = []
    for name, defn in pipelines.items():
        text = json.dumps(defn)
        if _RUN_CONTROL_WRITE.search(text):
            writers.append(("Pipeline", name, _run_control_target(text), text))
    for name, defn in notebooks.items():
        text = executable_code(defn)
        if _RUN_CONTROL_WRITE.search(text):
            writers.append(("Notebook", name, _run_control_target(text), text))

    if not writers:
        return binary(False, f"None of {len(pipelines)} pipeline(s) and "
                             f"{len(notebooks)} notebook(s) writes a run-control / "
                             f"batch-log row. Expected a write to a table such as "
                             f"run_control, run_log, batch_log, audit_log, or etl_log; "
                             f"without one, failed and partial loads leave no durable audit trail")

    assessments = []
    for kind, name, target, text in writers:
        fields, missing = _run_control_fields(text)
        assessments.append((len(fields), kind, name, target, fields, missing))
    _, kind, name, target, fields, missing = max(
        assessments, key=lambda assessment: assessment[0])

    detected = "; ".join(f"{label} '{value}'" for label, value in fields.items())
    location = f"{kind} '{name}' writes run-control target '{target}'"
    if not missing:
        return graded(
            3,
            f"{location}. Detected fields: {detected}. Each run can be traced by batch, "
            f"outcome, processed volume, and start/end time",
        )
    return graded(
        max(0, len(fields) - 1),
        f"{location}. Detected fields: {detected or 'none of the required fields'}. "
        f"Missing: {'; '.join(missing)}. The run history cannot fully identify the "
        f"batch, outcome, processed volume, and execution window until these fields "
        f"are persisted by the same writer",
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
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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


# =============================================================================
# 5.1.4 — DQ scores computed per table/dataset and trended over time
# =============================================================================

#: Column words that name a *measurement* of quality rather than the data itself.
_DQ_METRIC_WORDS: frozenset[str] = frozenset({
    "score", "scores", "metric", "metrics", "pct", "percent", "percentage",
    "ratio", "rate", "quality", "completeness", "accuracy", "validity",
    "conformity", "consistency", "freshness", "dqscore", "qualityscore",
    "passrate", "failrate", "nullpct", "nullpercent", "threshold",
})


def _has_dq_metric_column(table: dict) -> bool:
    """True when a column names a quality measurement (score / rate / % / metric)."""
    return any(name_words(c.get("name") or "") & _DQ_METRIC_WORDS for c in columns(table))


@check(
    id="TB-DQ-TREND", ref="5.1.4",
    title="DQ scores computed per table/dataset and trended over time (via Audit Lakehouse)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.PREP,), requires=[Resource.TABLE_SCHEMAS, Resource.TABLE_COLUMNS],
    required=False,
)
def dq_scores_are_trended(ctx: CheckContext) -> Verdict:
    """A DQ/audit table stores a quality measurement alongside the run timestamp.

    **What it can determine.** Whether any audit/DQ-shaped table carries both a
    score/metric-shaped column *and* a date or run-timestamp column. The
    timestamp is the whole point: a score column on its own records the latest
    reading and overwrites the trend, so it cannot answer "is quality getting
    worse".

    **What it cannot.** Whether rows are actually written, whether the score is
    computed per table or per dataset, or whether anyone looks at the trend.
    Column metadata is all this reads — no row data is fetched.
    """
    tables = ctx.workspace.tables
    if not tables:
        return not_applicable("No lakehouse/warehouse tables were read for this workspace")
    audit = {n: t for n, t in tables.items() if is_audit_table(n, t) and columns(t)}
    if not audit:
        return not_applicable(
            "No audit/DQ-shaped table with readable column metadata was found, so there "
            "is nowhere for a DQ score to be stored"
        )

    trended = sorted(n for n, t in audit.items()
                     if _has_dq_metric_column(t) and has_timestamp_column(t))
    if trended:
        return binary(
            True,
            f"DQ scores are stored with a timestamp in {len(trended)} of {len(audit)} audit "
            f"table(s): {', '.join(trended[:4])}",
        )

    scored_only = sorted(n for n, t in audit.items() if _has_dq_metric_column(t))
    if scored_only:
        return graded(
            1,
            f"{len(scored_only)} audit table(s) carry a DQ score/metric column "
            f"({', '.join(scored_only[:4])}) but no date/run timestamp — a point-in-time "
            f"reading that cannot be trended",
        )
    return binary(
        False,
        f"None of the {len(audit)} audit table(s) carries a DQ score/metric column, so no "
        f"quality score is computed per table or trended over time",
    )


# =============================================================================
# 5.1.7 — one DQ tool/library across the solution
# =============================================================================
#
# A consistency question, so it cannot be judged one notebook at a time: a
# notebook using pandera is not wrong on its own; it is wrong when the notebook
# next to it uses great_expectations. Hence Scope.WORKSPACE.

#: The DQ frameworks a Fabric notebook realistically uses, each matched by an
#: import *or* by an API call that only that framework has — a bare mention of
#: the name in prose cannot satisfy it because the code is read with
#: ``executable_code``.
_DQ_LIBRARY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("great_expectations", re.compile(
        r"\b(?:import|from)\s+great_expectations\b|great_expectations\s*\.|"
        r"\bgx\s*\.\s*(?:get_context|DataContext)\b|\bexpect_[a-z_]+\s*\(")),
    ("soda", re.compile(
        r"\b(?:import|from)\s+soda(?:core)?\b|\bsodacl\b|\badd_sodacl_yaml_str\s*\(")),
    ("pydeequ/deequ", re.compile(
        r"\b(?:import|from)\s+pydeequ\b|\bVerificationSuite\s*\(|com\.amazon\.deequ")),
    ("pandera", re.compile(
        r"\b(?:import|from)\s+pandera\b|\bDataFrameSchema\s*\(")),
    ("cuallee", re.compile(
        r"\b(?:import|from)\s+cuallee\b|\bCheckLevel\s*\.")),
    ("chispa", re.compile(
        r"\b(?:import|from)\s+chispa\b|\bassert_df_equality\s*\(")),
)

#: No framework at all — the rules are written by hand. Still a DQ *approach*,
#: and the one most likely to differ from notebook to notebook.
_HAND_ROLLED_DQ = re.compile(
    r"^\s*assert\s+\w|\braise\s+(?:ValueError|AssertionError|Exception|RuntimeError)\s*\(",
    re.MULTILINE,
)


def _dq_approach(code: str) -> str | None:
    """Which DQ library this notebook uses, or ``None`` when it does no DQ.

    One approach per notebook, chosen in a fixed order so the answer does not
    depend on dict iteration. A notebook that imports a framework *and* also
    writes bare asserts is counted as using the framework — the asserts are how
    people use these libraries, not a second tool.
    """
    for name, pattern in _DQ_LIBRARY_PATTERNS:
        if pattern.search(code):
            return name
    return "hand-rolled asserts" if _HAND_ROLLED_DQ.search(code) else None


@check(
    id="WS-DQ-LIBRARY", ref="5.1.7",
    title="DQ tool/library standardized across the solution",
    pillar=Pillar.DATA_QUALITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.PREP,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=False,
)
def dq_library_is_standardized(ctx: CheckContext) -> Verdict:
    """Every notebook that does data quality does it with the same library.

    **What it can determine.** Which DQ framework each notebook uses —
    great_expectations, Soda, (py)deequ, pandera, cuallee, chispa, or
    hand-rolled asserts — and whether the notebooks that do DQ agree on one.
    Three notebooks with three frameworks is the defect: three rule dialects,
    three failure formats, and no shared DQ report.

    **What it cannot.** Judge which library is the right one, or read the
    library list of a Fabric **Spark Environment**: the Environment definition
    returns only ``Sparkcompute.yml``, so a framework installed there but never
    imported is invisible. It also says nothing about rule *coverage* — that is
    ``NB-DQ-RULES`` (ref 5.1.2), which asks whether a notebook codifies rules at
    all, one notebook at a time.

    N/A when fewer than two notebooks perform DQ: one notebook cannot be
    inconsistent with itself.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    notebooks = ctx.workspace.notebooks or {}
    if not notebooks:
        return not_applicable("Workspace has no notebook definitions to inspect")

    approaches: dict[str, str] = {}
    for name, definition in notebooks.items():
        approach = _dq_approach(executable_code(definition))
        if approach:
            approaches[name] = approach

    if len(approaches) < 2:
        return not_applicable(
            f"{len(approaches)} of {len(notebooks)} notebook(s) perform data quality — "
            f"fewer than two, so there is no cross-notebook standard to judge"
        )

    used: dict[str, list[str]] = {}
    for notebook, approach in approaches.items():
        used.setdefault(approach, []).append(notebook)
    standard, users = max(used.items(), key=lambda kv: (len(kv[1]), kv[0]))
    detail = "; ".join(f"{lib}: {len(names)}" for lib, names in sorted(used.items()))

    if len(used) == 1:
        return covered(
            len(users), len(approaches),
            f"All {len(approaches)} DQ-performing notebook(s) use one approach — {standard}",
        )
    return covered(
        len(users), len(approaches),
        f"{len(approaches)} DQ-performing notebook(s) use {len(used)} different "
        f"approaches ({detail}); the most common is {standard}",
    )


# =============================================================================
# 5.1.9 — a DQ failure has to stop the run
# =============================================================================

#: An activity whose *name* says it validates something. Bare "check" is
#: deliberately excluded — "Check Watermark" and "Check File Exists" are
#: control-flow lookups, not data quality — so ``check`` counts only when it is
#: qualified (``row_count_check``, ``schema check``).
_DQ_ACTIVITY_NAME = re.compile(
    r"\bdq\b|data[_\s-]?quality|\bvalidat\w*|\bverif(?:y|ies|ication)\w*|\bassert\w*|"
    r"\breconcil\w*|\bintegrity\b|\bsanity\b|"
    r"(?:quality|null|count|row|record|schema|duplicate)[_\s-]?check",
    re.IGNORECASE,
)


def _dependents(acts: list[dict]) -> dict[str, list[tuple[dict, set[str]]]]:
    """Map activity name -> the activities that depend on it, with their conditions."""
    out: dict[str, list[tuple[dict, set[str]]]] = {}
    for act in acts:
        for dep in act.get("dependsOn") or []:
            if not isinstance(dep, dict):
                continue
            upstream = dep.get("activity")
            if not isinstance(upstream, str):
                continue
            conditions = {str(c) for c in (dep.get("dependencyConditions") or [])}
            out.setdefault(upstream, []).append((act, conditions))
    return out


@check(
    id="PL-DQ-GATE", ref="5.1.9",
    title="DQ failures halt pipeline progression where critical (bad data does not silently flow downstream)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.PIPELINE, severity=Severity.HIGH,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_dq_failure_halts_run(ctx: CheckContext) -> Verdict:
    """A validation activity's failure actually stops what runs after it.

    Severity is High rather than the checklist's Medium because the failure mode
    is silent: the run goes green and bad data reaches the consumers, so nobody
    is told to look.

    **What it can determine.** Which activities validate (by name), and how each
    is wired: a downstream activity depending on it with ``Succeeded`` — or a
    ``Fail`` activity on its ``Failed`` edge — means a DQ failure stops
    progression. A dependent wired on ``Completed`` or ``Skipped`` explicitly
    does *not*: the load runs whether validation passed or failed. Activities
    nested in ForEach / If / Switch are included (``walk_activities``).

    **What it cannot.** Judge whether the validation is any good, or whether an
    unnamed activity is secretly a validation — the name is the only readable
    signal of intent. A pipeline with no validation activity is N/A, not a
    failure: whether this pipeline *should* validate is ``NB-DQ-RULES`` /
    ``PL-DEADLETTER`` territory, not this point's.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    acts = walk_activities(ctx.obj)
    if not acts:
        return not_applicable("Pipeline has no activities to evaluate")

    dq_acts = [a for a in acts if _DQ_ACTIVITY_NAME.search(str(a.get("name") or ""))]
    if not dq_acts:
        return not_applicable(
            f"None of the {len(acts)} activity(ies) is a validation/DQ step, so there is "
            f"no DQ outcome to gate progression on"
        )

    dependents = _dependents(acts)
    gating: list[str] = []
    ungated: list[str] = []
    for act in dq_acts:
        name = str(act.get("name") or "")
        downstream = dependents.get(name, [])
        halts = any(
            "Succeeded" in conditions
            or (child.get("type") == "Fail" and "Failed" in conditions)
            for child, conditions in downstream
        )
        if halts:
            gating.append(name)
        elif not downstream:
            ungated.append(f"'{name}' (nothing depends on it)")
        else:
            wiring = sorted({c for _, conds in downstream for c in conds}) or ["no condition"]
            ungated.append(f"'{name}' (downstream runs on {'/'.join(wiring)})")

    return covered(
        len(gating), len(dq_acts),
        f"{len(gating)} of {len(dq_acts)} validation activity(ies) gate progression on success"
        + (f"; not gating: {'; '.join(ungated[:3])}" if ungated else ""),
    )


#: A notebook has actually *evaluated* data quality — it holds a count of bad
#: rows, an expectation result, or a comparison — rather than merely mentioning
#: quality.
_DQ_EVALUATION = re.compile(
    r"\b(?:invalid|bad|error|reject|rejected|failed|violation|mismatch|orphan|duplicate|null)"
    r"[_\s]?(?:count|cnt|rows?|records?|df)\b|"
    r"^\s*assert\s+\w|\bexpect_[a-z_]+\s*\(|\bVerificationSuite\s*\(|"
    r"\b(?:validate|validation|dq)_\w*\s*[\(=]|"
    r"\.count\s*\(\s*\)\s*(?:==|!=|>=|<=|>|<)",
    re.IGNORECASE | re.MULTILINE,
)
#: The notebook stops. ``raise``/``assert``/``sys.exit`` fail the notebook, and
#: therefore the pipeline activity that ran it.
_DQ_HARD_STOP = re.compile(
    r"^\s*assert\s+\w|\braise\s+\w+|\bsys\.exit\s*\(",
    re.MULTILINE,
)
#: ``notebookutils.notebook.exit(...)`` ends the notebook *successfully* and
#: returns a value, so it only halts the pipeline if the caller inspects that
#: value — credited, but not as a full stop.
_DQ_SOFT_EXIT = re.compile(
    r"(?:notebookutils|mssparkutils|dbutils)\s*\.\s*notebook\s*\.\s*exit\s*\(",
    re.IGNORECASE,
)


@check(
    id="NB-DQ-HALT", ref="5.1.9",
    title="DQ failures halt pipeline progression where critical (bad data does not silently flow downstream)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_dq_failure_halts_run(ctx: CheckContext) -> Verdict:
    """A notebook that finds bad data stops, rather than printing and carrying on.

    The sibling of ``PL-DQ-GATE`` under the same ref, and a different signal:
    the pipeline check reads *wiring*, this one reads what the notebook does
    with its own result. A notebook that computes ``invalid_count`` and only
    prints it returns success, so the orchestrator has nothing to gate on — the
    pipeline can be wired perfectly and bad data still flows.

    **What it can determine.** Whether a notebook that evaluates data quality
    also raises, asserts, or exits on the result. ``raise``/``assert``/
    ``sys.exit`` fail the run; ``notebookutils.notebook.exit`` ends it
    *successfully* with a value, which only halts the caller if the caller looks
    — so it scores 2, not 3.

    **What it cannot.** Tell whether the stop is on the right condition, or
    whether the caller inspects an exit value. Distinct from ``NB-DQ-RULES``
    (5.1.2), which asks whether rules exist, and from ``NB-DEADLETTER`` (5.1.10),
    which asks whether rejects are retained — retaining rejects and halting are
    different controls, and a solution can want both.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _DQ_EVALUATION.search(code):
        return not_applicable(
            "Notebook evaluates no data-quality result (no bad-row count, expectation, "
            "or assertion), so there is nothing here to halt on"
        )
    if _DQ_HARD_STOP.search(code):
        return graded(3, "Data-quality failure raises/asserts and fails the notebook, "
                         "so the calling pipeline cannot continue")
    if _DQ_SOFT_EXIT.search(code):
        return graded(2, "Data-quality result ends the notebook through notebook.exit() — "
                         "the run still reports success, so progression stops only if the "
                         "caller inspects the returned value")
    return graded(0, "Data-quality result is computed but never raised on — the notebook "
                     "succeeds regardless, so bad data flows downstream silently")


# =============================================================================
# 5.2.7 — nulls in the columns 5.5.6 does not look at
# =============================================================================
#
# ``NB-KEY-QUALITY`` (ref 5.5.6) already covers nulls in *key* columns. This
# point is about the rest of the row: which attributes are allowed to be null,
# and whether an unexpected null anywhere else is noticed. The detector is
# therefore built the other way round — it resolves the *column name* each null
# construct refers to and keeps only the non-key ones, so evidence that satisfies
# 5.5.6 cannot satisfy this check as well.

#: Null constructs that name the column(s) they act on. Group 1 is either a
#: single column name or a list/dict fragment holding several.
_NULL_COLUMN_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"fillna\s*\(\s*\{([^}]{0,400})\}", re.IGNORECASE),
    re.compile(r"(?:fillna|dropna|fill)\s*\([^)]{0,200}?subset\s*=\s*[\[\(]([^\]\)]{0,400})",
               re.IGNORECASE),
    re.compile(r"\.na\s*\.\s*(?:fill|drop)\s*\([^)]{0,200}?\{([^}]{0,400})\}", re.IGNORECASE),
    re.compile(r"(?:col|column)\s*\(\s*[\"']([\w.$]+)[\"']\s*\)\s*\.\s*is(?:Not)?Null\s*\(",
               re.IGNORECASE),
    re.compile(r"[\"']([\w.$]+)[\"']\s*\]\s*\.\s*is(?:Not)?Null\s*\(", re.IGNORECASE),
    re.compile(r"\b([\w$]+)\s+IS\s+(?:NOT\s+)?NULL\b", re.IGNORECASE),
    re.compile(r"COALESCE\s*\(\s*[`\"\[]?([\w.$]+)", re.IGNORECASE),
    # An explicit schema declares, per column, whether null is expected — the
    # "known nullable fields documented" half of the point.
    re.compile(r"StructField\s*\(\s*[\"']([\w.$]+)[\"'][^)]{0,80}?,\s*(?:True|False)\s*\)"),
)

#: Profiling every column at once — by construction this covers the non-key
#: columns, so no name has to be resolved.
_NULL_PROFILE_ALL = re.compile(
    r"for\s+\w+\s+in\s+\w+\.columns[\s\S]{0,240}?is(?:Not)?Null|"
    r"is(?:Not)?Null[\s\S]{0,240}?for\s+\w+\s+in\s+\w+\.columns|"
    r"\bnull[_\s]?(?:count|counts|profile|profiling|summary)\b|"
    r"\bcount[_\s]?nulls?\b",
    re.IGNORECASE,
)

_QUOTED_NAME = re.compile(r"[\"']([\w.$]+)[\"']")
#: A quoted dict *key* — ``fillna({"region": "UNKNOWN"})`` names one column and
#: one replacement value; only the key is a column.
_QUOTED_KEY = re.compile(r"[\"']([\w.$]+)[\"']\s*:")


def _null_handled_columns(code: str) -> set[str]:
    """Column names that some null construct explicitly names."""
    names: set[str] = set()
    for pattern in _NULL_COLUMN_PATTERNS:
        for match in pattern.finditer(code):
            fragment = match.group(1) or ""
            found = _QUOTED_KEY.findall(fragment) or _QUOTED_NAME.findall(fragment)
            names.update(found or [fragment])
    return {n.rsplit(".", 1)[-1] for n in names if n}


@check(
    id="NB-NULL-HANDLING", ref="5.2.7",
    title="Null/empty handling: known nullable fields documented; unexpected nulls flagged",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_handles_non_key_nulls(ctx: CheckContext) -> Verdict:
    """Nulls in ordinary attributes are declared, filled, or flagged — not just tolerated.

    **Deliberately not the same evidence as ``NB-KEY-QUALITY`` (ref 5.5.6).**
    That check passes on a null test against a *key* column. This one resolves
    the column name each null construct names and keeps only the columns
    ``is_key_column`` rejects, so a notebook whose only null handling is
    ``df.filter(col("customer_id").isNotNull())`` scores 1 here, not 3 — the
    business attributes are still unexamined.

    **What it can determine.** Whether the notebook names non-key columns in a
    ``fillna`` / ``dropna(subset=…)`` / ``isNull`` / ``COALESCE`` / explicit
    ``StructField(..., nullable)`` construct, or profiles nulls across every
    column at once (a loop over ``df.columns``, a ``null_count``).

    **What it cannot.** Tell whether the nullable fields were documented
    *correctly*, or resolve a column name held in a variable — an unresolvable
    name is simply not counted, never counted against.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not (_WRITE_PATTERN.search(code) or _INPUT_READ.search(code)):
        return not_applicable("Notebook neither reads nor writes data, so it handles no nulls")

    if _NULL_PROFILE_ALL.search(code):
        return graded(3, "Nulls are profiled across every column (column loop / null-count "
                         "summary), so unexpected nulls in non-key fields are visible")

    named = _null_handled_columns(code)
    non_key = sorted(n for n in named if not is_key_column(n))
    if non_key:
        return graded(3, f"Null handling names {len(non_key)} non-key column(s): "
                         f"{', '.join(non_key[:5])}")
    if named:
        return graded(1, f"Null handling covers only key column(s) "
                         f"({', '.join(sorted(named)[:5])}) — already credited by "
                         f"NB-KEY-QUALITY (5.5.6); nulls in business attributes are "
                         f"neither declared nor flagged")
    return graded(0, "No null handling names any column and no null profiling is performed — "
                     "an unexpected null in a business attribute passes through unnoticed")


# =============================================================================
# 5.5.1 — dates: ranges validated, timezones handled deliberately
# =============================================================================

#: The notebook works with dates at all. ``\bdate\b`` is word-bounded so
#: ``validate`` and ``update`` (both of which contain "date") do not match.
_DATE_HANDLING = re.compile(
    r"to_date\s*\(|to_timestamp\s*\(|DateType\s*\(|TimestampType\s*\(|"
    r"current_date\s*\(|current_timestamp\s*\(|date_format\s*\(|datediff\s*\(|"
    r"\bCAST\s*\([^)]{0,60}\bAS\s+(?:DATE|TIMESTAMP|DATETIME)\b|"
    r"\b(?:date|dates|datetime|timestamp)\b|_date\b|_dt\b|\bdob\b",
    re.IGNORECASE,
)
_CREATE_TABLE_DDL = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b", re.IGNORECASE)
#: A date is held against a bound — a literal, another date, or "now". The
#: comparison may be separated from the column by a closing quote/paren, which is
#: why a short run of ``")]`` characters is allowed before the operator.
_DATE_RANGE_CHECK = re.compile(
    r"\.between\s*\(|\bBETWEEN\b[^\n]{0,80}\bAND\b|"
    r"(?:date|timestamp|dt)\w*[\"'\)\]\s]{0,6}(?:<=|>=|<|>)|"
    r"(?:<=|>=|<|>)[\s\(]{0,6}(?:current_date|current_timestamp|to_date|to_timestamp|"
    r"lit\s*\(|[\"']\d{4}-\d{2})|"
    r"\bfuture[_\s]?date\w*|\bmin_date\b|\bmax_date\b|\bdate_range\b|"
    r"\bvalid_(?:from|to|date)\b|\bdate[_\s]?(?:range|bounds?)[_\s]?(?:check|validation)\b",
    re.IGNORECASE,
)
#: The timezone is a decision rather than whatever the session happened to use.
_TIMEZONE_AWARE = re.compile(
    r"to_utc_timestamp\s*\(|from_utc_timestamp\s*\(|"
    r"spark\.sql\.session\.timeZone|session\.timeZone|"
    r"\btz\s*=|\btimezone\s*=|ZoneInfo\s*\(|\bpytz\b|\btzinfo\b|astimezone\s*\(|"
    r"\bAT\s+TIME\s+ZONE\b|utcnow\s*\(|timezone\.utc|\butc\b",
    re.IGNORECASE,
)


@check(
    id="NB-DATE-QUALITY", ref="5.5.1",
    title="**Dates**: Valid date ranges; consistent timezone handling; no invalid future dates where prohibited",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_date_quality(ctx: CheckContext) -> Verdict:
    """Dates are bounded and their timezone is chosen, not inherited.

    **What it can determine.** Two independent halves. *Range*: a date is
    compared against a bound — a literal, ``current_date()``, a min/max, or a
    ``between`` — which is what catches a 1900 default or a date in the future.
    *Timezone*: ``to_utc_timestamp`` / ``from_utc_timestamp`` / an explicit
    ``tz=`` / ``ZoneInfo`` / a session timezone setting / a UTC-suffixed column,
    rather than parsing into whatever the Spark session's timezone happens to be
    — the source of the classic one-day-off defect across regions.

    **What it cannot.** Tell whether the bound is the *right* bound, or which
    dates a business prohibits from being in the future. It also cannot see
    dates handled entirely inside a stored procedure. Distinct from
    ``NB-TYPE-CAST`` (5.3.1), which only asks that a date be cast to a date at
    all; casting correctly and bounding sensibly are different failures.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = strip_sql_comments(executable_code(ctx.obj))
    if (_CREATE_TABLE_DDL.search(code)
            and not (_INPUT_READ.search(code) or _WRITE_PATTERN.search(code))):
        return not_applicable(
            "Notebook only declares a table schema; it does not read, transform, or write "
            "date values"
        )
    if not _DATE_HANDLING.search(code):
        return not_applicable("Notebook handles no date or timestamp data")

    ranged = bool(_DATE_RANGE_CHECK.search(code))
    zoned = bool(_TIMEZONE_AWARE.search(code))
    if ranged and zoned:
        return graded(3, "Date values are held against a range/bound and timezone handling "
                         "is explicit (UTC conversion or a declared timezone)")
    if ranged:
        return graded(2, "Date values are held against a range/bound, but timezone handling "
                         "is implicit — parsing inherits the Spark session timezone, which "
                         "differs between environments")
    if zoned:
        return graded(1, "Timezone handling is explicit, but no date is validated against a "
                         "range or bound — an out-of-range or future date is loaded as-is")
    return graded(0, "Dates are parsed with neither a range/bound validation nor explicit "
                     "timezone handling")


# =============================================================================
# 5.5.2 — money keeps its precision, and currency codes are real
# =============================================================================

#: Column-name words that mean money. ``total`` and ``rate`` are excluded on
#: purpose: ``total_rows`` and ``error_rate`` are counters, not currency.
_MONEY_WORD = (
    r"(?:amount|amt|price|unitprice|cost|revenue|salary|payment|balance|invoice|"
    r"fee|charge|tax|discount|margin|profit|currency|money)"
)
_MONEY_CONTEXT = re.compile(r"\w*" + _MONEY_WORD + r"\w*", re.IGNORECASE)
#: Precision preserved: a fixed-point type, in any of its spellings.
_DECIMAL_TYPING = re.compile(
    r"DecimalType\s*\(|\bDecimal\s*\(|"
    r"cast\s*\(\s*[\"']decimal|\bAS\s+(?:DECIMAL|NUMERIC|MONEY)\b|"
    r"\bdecimal\s*\(\s*\d+\s*,\s*\d+\s*\)",
    re.IGNORECASE,
)
#: Binary floating point applied to a money-named value — the classic defect:
#: 0.1 + 0.2 never equals 0.3, and the error compounds over a sum.
_FLOAT_FOR_MONEY = re.compile(
    r"\w*" + _MONEY_WORD + r"\w*[\"']?[^\n]{0,80}?"
    r"(?:DoubleType\s*\(|FloatType\s*\(|cast\s*\(\s*[\"'](?:double|float)|"
    r"\bAS\s+(?:FLOAT|DOUBLE|REAL)\b|\bfloat\s*\()|"
    r"(?:DoubleType\s*\(|FloatType\s*\(|cast\s*\(\s*[\"'](?:double|float)|"
    r"\bAS\s+(?:FLOAT|DOUBLE|REAL)\b)[^\n]{0,80}?\w*" + _MONEY_WORD,
    re.IGNORECASE,
)
_CURRENCY_COLUMN = re.compile(r"\bcurrency\w*\b|\bcurr[_\s]?cd\b|\biso[_\s]?currency\b",
                              re.IGNORECASE)
_CURRENCY_VALIDATED = re.compile(
    r"currency\w*[\"']?[^\n]{0,120}?(?:\.isin\s*\(|\brlike\s*\(|\bIN\s*\(\s*[\"'][A-Za-z]{3}[\"']|"
    r"\bjoin\s*\()|"
    r"(?:allowed|valid|expected|known)[_\s]?currenc\w*|"
    r"[\"']\^?\[A-Z\]\{3\}\$?[\"']",
    re.IGNORECASE,
)


@check(
    id="NB-MONEY-PRECISION", ref="5.5.2",
    title="**Numeric / Financial**: Precision preserved; no rounding errors; currency codes valid",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_money_precision(ctx: CheckContext) -> Verdict:
    """Monetary values are fixed-point, and currency codes are checked against a set.

    **What it can determine.** Whether a notebook that handles money-named
    values (amount / price / cost / revenue / invoice / tax …) types them as
    ``DecimalType`` — or instead casts them to ``double``/``float``, where
    binary floating point loses cents and the error compounds across a sum — and
    whether a currency column is validated against an allowed set or an ISO
    3-letter pattern.

    **What it cannot.** See the precision of a column it never casts (the source
    type is not in the notebook), judge whether the chosen scale is right, or
    detect rounding done inside a stored procedure or the Warehouse. Distinct
    from ``NB-TYPE-CAST`` (5.3.1), which is satisfied by *any* explicit cast:
    ``cast("double")`` passes there and fails here, which is exactly the point.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = strip_sql_comments(executable_code(ctx.obj))
    if not _MONEY_CONTEXT.search(code):
        return not_applicable("Notebook handles no monetary or financial value")

    decimal = bool(_DECIMAL_TYPING.search(code))
    floating = bool(_FLOAT_FOR_MONEY.search(code))
    has_currency = bool(_CURRENCY_COLUMN.search(code))
    currency_ok = bool(_CURRENCY_VALIDATED.search(code))

    if floating and not decimal:
        return graded(0, "Monetary values are cast to float/double — binary floating point "
                         "cannot hold a decimal amount exactly, so cents are lost and the "
                         "error compounds across sums; use DecimalType(p, s)")
    if not decimal:
        return graded(1, "No explicit numeric typing for monetary values — precision is "
                         "whatever the source or inference produced, so it is neither "
                         "chosen nor guaranteed")
    if has_currency and not currency_ok:
        return graded(2, "Monetary values use fixed-point decimal typing, but the currency "
                         "code is never validated against an allowed set or ISO pattern")
    if has_currency:
        return graded(3, "Monetary values use fixed-point decimal typing and currency codes "
                         "are validated against an allowed set/pattern")
    return graded(3, "Monetary values use fixed-point decimal typing (no currency-code "
                     "column is handled in this notebook)")


# =============================================================================
# 5.2.2 — completeness control, and 5.2.3 — pipeline timeliness timeout
#
# Both points describe a *runtime outcome*: whether every expected batch arrived,
# and whether it arrived on time. Neither is a property of the code, and neither
# can be answered without row data or run telemetry this tool must not fetch.
#
# What a definition *does* answer is whether the safeguard exists — notebook
# code that would notice a missing partition, or a pipeline activity timeout
# that bounds refresh execution. Both checks below score exactly that and say so
# in their evidence, so nobody reads a PASS as "today's load was complete / on
# time".
# =============================================================================

#: Reading a source: files, a table, or a directory listing. The gate for both
#: checks — a notebook that reads nothing has no arrival to police.
_SOURCE_READ = re.compile(
    r"spark\.read\b|\.read\.(?:csv|json|parquet|text|format|table|load)|spark\.table\s*\(|"
    r"spark\.sql\s*\(|"
    r"read_csv\s*\(|read_json\s*\(|read_parquet\s*\(|"
    r"(?:notebookutils|mssparkutils)\s*\.\s*fs\s*\.\s*ls\s*\(|"
    r"\bdbutils\s*\.\s*fs\s*\.\s*ls\s*\(",
    re.IGNORECASE,
)

#: An *expectation* about the set of inputs: a named list/count of the files,
#: partitions, batches or source tables that should be there, or the manifest /
#: control file that carries it.
_EXPECTED_INPUT_SET = re.compile(
    r"\bexpected[_\s-]?(?:file|files|filename|filenames|file_?count|partition|partitions|"
    r"batch|batches|batch_?count|table|tables|source|sources|date|dates|day|days|list|set|"
    r"count)\b|"
    r"\b(?:file|partition|batch|source)[_\s-]?manifest\b|\bmanifest[_\s-]?(?:file|df|list)\b|"
    r"\bcontrol[_\s-]?file\b|\brequired[_\s-]?(?:files?|partitions?|tables?|sources?)\b",
    re.IGNORECASE,
)

#: The *unmatched* half, assigned as an executable value. Requiring assignment
#: prevents mode names and paths such as ``"missing_file"`` from masquerading
#: as a completeness calculation.
_MISSING_INPUT_COMPUTATION = re.compile(
    r"(?:"
    r"\bmissing[_\s-]?(?:file|files|partition|partitions|batch|batches|table|tables|"
    r"source|sources|date|dates|day|days)\b|"
    r"\b(?:absent|unreceived|not_?received)[_\s-]?(?:file|files|batch|batches|"
    r"partition|partitions)\b"
    r")"
    r"\s*(?::[^=\n]+)?=",
    re.IGNORECASE,
)

#: Acting on the expectation: differencing the two sets, or failing/raising when
#: they disagree. Alone these mean nothing — they are only consulted alongside
#: ``_EXPECTED_INPUT_SET``, so a bare ``assert`` cannot satisfy the check.
_COMPLETENESS_ACTION = re.compile(
    r"\.difference\s*\(|\bset\s*\([^\n]{0,120}?\)\s*-\s*set\s*\(|"
    r"\.subtract\s*\(|\.exceptAll\s*\(|\bEXCEPT\s+(?:ALL\s+)?SELECT\b|"
    r"left_anti|leftanti|\bleft\s+anti\s+join\b|"
    r"\bnot\s+in\b|\bnotin\s*\(|~\w+\.isin\s*\(|"
    r"\braise\b|\bassert\b|\bFail\b|"
    r"(?:notebookutils|mssparkutils)\s*\.\s*notebook\s*\.\s*exit\s*\(|sys\s*\.\s*exit\s*\(",
    re.IGNORECASE,
)


@check(
    id="NB-COMPLETENESS-CONTROL", ref="5.2.2",
    title="Completeness: all expected source files/batches received (no missing partitions or source tables)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.HIGH,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_has_an_arrival_completeness_control(ctx: CheckContext) -> Verdict:
    """The notebook implements a control that would notice a missing file, partition or source.

    **What it verifies: the safeguard, not the outcome.** Whether *this run's*
    batches all arrived is a runtime fact — it lives in the data and in the run
    log, not in the code, and this tool reads neither row data nor run telemetry.
    A PASS here means "the notebook would notice an absent input", never "the
    load was complete". A FAIL means nothing in the code would notice.

    **What it can determine.** Two readable shapes of the control. Either the
    unmatched set is named outright (``missing_partitions``, ``missing_files``),
    or an expectation about the inputs is declared (``expected_files``,
    ``expected_partitions``, a manifest or control file) **and** something acts
    on it: a set difference, an anti-join, a ``not in``, or a raise/assert/exit.
    An expectation with nothing acting on it scores in the middle — the list is
    there, but nothing fails when reality differs from it.

    **What it cannot.** Resolve an expectation that lives in a config table or a
    pipeline parameter rather than the notebook, see a completeness gate
    implemented in a stored procedure, or judge whether the expected set is the
    *right* one. Read with ``executable_code`` so a commented-out check cannot
    pass.

    **Siblings.** ``NB-RECON-COUNT`` (5.2.5) compares *row* counts of what was
    written against a source count; this is about the set of *inputs* that should
    have arrived. ``NB-UNKNOWN-MONITOR`` (5.4.4) is completeness of dimension
    *members*, inside data already loaded.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    reads_source = bool(_SOURCE_READ.search(code))
    moves_data = reads_source or bool(_WRITE_PATTERN.search(code))
    if not moves_data:
        return not_applicable(
            "Notebook neither reads nor writes data, so there is no source arrival "
            "completeness to control here"
        )

    if _MISSING_INPUT_COMPUTATION.search(code):
        return graded(
            3,
            "Notebook computes the missing/unreceived inputs explicitly — a completeness "
            "control is present. Whether any particular load was complete is a runtime "
            "outcome this check does not read.",
        )
    expectation = bool(_EXPECTED_INPUT_SET.search(code))
    if expectation and _COMPLETENESS_ACTION.search(code):
        return graded(
            3,
            "Notebook declares the expected set/count of inputs and compares or asserts "
            "against it — a completeness control is present. Whether any particular load "
            "was complete is a runtime outcome this check does not read.",
        )
    if expectation:
        return graded(
            1,
            "Notebook names an expected set/count of inputs but nothing compares or "
            "asserts against it — a missing file, partition or source table would pass "
            "through unnoticed",
        )
    source_evidence = (
        "Notebook reads source data" if reads_source else
        "Notebook moves data but its definition contains no recognisable source read"
    )
    return graded(
        0,
        f"{source_evidence} with no completeness control — nothing declares which "
        "files/partitions/source tables were expected, and nothing computes what is "
        "missing, so an absent batch would be silently loaded as a short one",
    )


# -- 5.2.3 --------------------------------------------------------------------

# Fabric stamps a long timeout on many activity definitions even when the author
# did not configure an SLA. Those defaults are evidence that the activity is
# bounded eventually, but not that its expected refresh window was chosen.
_DEFAULT_PIPELINE_TIMEOUTS = frozenset({"7.00:00:00", "0.12:00:00", "7.00:00", ""})
_TIMELINESS_ACTIVITY_TYPES = frozenset({
    "Copy", "Dataflow", "ExecutePipeline", "Lookup", "Notebook",
    "RefreshDataflow", "Script", "SparkJobDefinition", "SqlServerStoredProcedure",
    "TridentNotebook",
})


@check(
    id="NB-TIMELINESS-CONTROL", ref="5.2.3",
    title="Timeliness: data arrives within expected SLA window",
    pillar=Pillar.DATA_QUALITY, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_has_a_timeliness_control(ctx: CheckContext) -> Verdict:
    """Pipeline refresh/data activities have an explicit timeout chosen for their SLA.

    This verifies the saved timeout safeguard, not whether a particular run or
    batch arrived on time. Fabric's stamped multi-hour/day defaults are PARTIAL:
    they eventually stop a run, but do not prove that the business SLA was used.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    relevant = [
        activity for activity in walk_activities(ctx.obj)
        if (activity.get("type") or "") in _TIMELINESS_ACTIVITY_TYPES
    ]
    if not relevant:
        return not_applicable(
            "Pipeline has no refresh or data activity whose execution timeout can be assessed"
        )

    timeouts = [
        str((activity.get("policy") or {}).get("timeout") or "").strip()
        for activity in relevant
    ]
    custom = [value for value in timeouts if value and value not in _DEFAULT_PIPELINE_TIMEOUTS]
    defaults = [value for value in timeouts if value in _DEFAULT_PIPELINE_TIMEOUTS and value]
    missing = len(timeouts) - len(custom) - len(defaults)

    if len(custom) == len(relevant):
        return graded(
            3,
            f"All {len(relevant)} refresh/data activities set a custom timeout — the "
            "pipeline execution window is explicitly bounded for its SLA. Whether a "
            "particular batch arrived on time is a runtime outcome this check does not read.",
        )
    if custom:
        return graded(
            2,
            f"{len(custom)} of {len(relevant)} refresh/data activities set a custom timeout; "
            f"{len(defaults)} keep a Fabric default and {missing} have none — the SLA "
            "execution bound is only partially configured",
        )
    if defaults:
        values = ", ".join(sorted(set(defaults)))
        return graded(
            1,
            f"All assessed timeout values are Fabric defaults ({values}); no refresh/data "
            "activity has a custom timeout chosen for the expected SLA window",
        )
    return graded(
        0,
        f"None of the {len(relevant)} refresh/data activities sets an execution timeout — "
        "the pipeline has no configured bound for its expected SLA window",
    )


# =============================================================================
# 5.3.3 — business rules: two fields of the same row held against each other
# =============================================================================

#: One column reference in the DataFrame API: ``col("x")``, ``F.col("x")``,
#: ``sf.col("x")``.
_COL_REF = r"(?:[A-Za-z_]\w*\s*\.\s*)?col\s*\(\s*[\"'][\w.$ ]+[\"']\s*\)"
#: A relational operator. Two-character forms are listed first so ``<=`` is never
#: consumed as a bare ``<``.
_REL_OP = r"(?:<=|>=|==|!=|<>|<|>)"

#: A comparison whose **both** sides are columns of the same row — the shape a
#: business rule takes (``start_date <= end_date``, ``net <= gross``). A column
#: compared to a *literal* is deliberately absent: that is a range check and
#: belongs to ``NB-DATE-QUALITY`` (5.5.1).
_COLUMN_PAIR_COMPARISON = re.compile(
    # col("start_date") <= col("end_date")
    rf"{_COL_REF}\s*{_REL_OP}\s*{_COL_REF}|"
    # df.start_date <= df.end_date. Neither side may be a call, so
    # ``df.count() > other.count()`` (a reconciliation, ref 5.2.5) does not match.
    rf"\b[A-Za-z_]\w*\s*\.\s*[A-Za-z_]\w*(?!\s*\()\s*{_REL_OP}\s*"
    rf"[A-Za-z_]\w*\s*\.\s*[A-Za-z_]\w*(?!\s*\()",
    re.IGNORECASE,
)

#: A SQL identifier, optionally backtick/bracket-quoted and optionally
#: table-qualified. Double quotes are **not** accepted: in Spark SQL they delimit
#: a string literal, and a column compared to a literal is a range check
#: (ref 5.5.1), not a cross-field business rule.
_SQL_COL = r"[`\[]?[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)?[`\]]?"
#: Values that look like an identifier but are not another column of the row, so
#: comparing against them is a range/null check rather than a business rule.
_SQL_NOT_A_COLUMN = r"(?:NULL|CURRENT_DATE|CURRENT_TIMESTAMP|GETDATE|SYSDATE|NOW|TRUE|FALSE)\b"

#: The SQL spelling of the same shape: two column identifiers compared inside a
#: ``WHERE`` / ``CASE WHEN`` / ``CHECK`` / ``HAVING``. ``AND`` and ``OR`` are not
#: accepted as the leading keyword on purpose — Python's own ``and``/``or`` would
#: then turn any two-variable comparison into a "business rule". Neither side may
#: sit inside a quoted literal.
_SQL_COLUMN_PAIR = re.compile(
    rf"\b(?:WHERE|WHEN|CHECK|HAVING)\b[^\n]{{0,120}}?"
    rf"(?<![\w'\"`]){_SQL_COL}\s*{_REL_OP}\s*"
    rf"(?!{_SQL_NOT_A_COLUMN})(?!['\"]){_SQL_COL}(?!\s*\()",
    re.IGNORECASE,
)

#: A rule expressed through a named construct rather than an inline comparison.
#: Each must be a call or an assignment: a bare word also matches a comment or a
#: column called ``rule_id``.
_NAMED_BUSINESS_RULE = re.compile(
    r"\b(?:business_rules?|rule_check\w*|check_rules?|validate_rules?|rule_set|ruleset|"
    r"rule_engine)\s*[\(=\[]|"
    r"expect_column_pair_values\w*\s*\(|expect_multicolumn\w*\s*\(",
    re.IGNORECASE,
)


def _business_rule_present(code: str) -> bool:
    """True when the code holds two fields of the same row against each other."""
    return bool(
        _COLUMN_PAIR_COMPARISON.search(code)
        or _SQL_COLUMN_PAIR.search(code)
        or _NAMED_BUSINESS_RULE.search(code)
    )


@check(
    id="NB-BUSINESS-RULE", ref="5.3.3",
    title="Business rule validation: domain-specific rules applied (e.g., start_date <= end_date)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_applies_business_rules(ctx: CheckContext) -> Verdict:
    """A domain rule relates two fields of the same row — not one field to a constant.

    **What it verifies: the safeguard, not the data.** Whether any row actually
    violates a rule is a runtime fact held in the rows, which this tool never
    reads. A PASS means "the notebook applies at least one cross-field rule",
    never "the data obeys the business rules".

    **What it can determine.** That a comparison exists whose *both* sides are
    columns of the same row — ``col("start_date") <= col("end_date")``,
    ``df.net <= df.gross``, a SQL ``WHERE``/``CASE WHEN``/``CHECK`` comparing two
    column identifiers — or that a named rule construct is invoked
    (``business_rules(...)``, ``rule_check(...)``, ``validate_rule(...)``,
    ``expect_column_pair_values_*``).

    **What it cannot.** Tell whether these are the *right* rules for the domain,
    how many of the domain's rules are covered, or read a rule that lives in a
    config table, a stored procedure or a downstream constraint. It also cannot
    resolve a rule assembled from variables. Read with ``executable_code`` so a
    commented-out rule cannot satisfy it.

    **Siblings — deliberately different evidence.** ``NB-TYPE-CAST`` (5.3.1) asks
    whether a column has the right *type*; ``NB-NULL-HANDLING`` (5.2.7) whether
    it is *null*; ``NB-DATE-QUALITY`` (5.5.1) whether a value falls in a valid
    *range* — a column against a literal or against ``current_date()``. A
    literal-sided comparison is excluded here on purpose, so a pure range check
    such as ``col("d") > "2020-01-01"`` scores 0 here and is credited only by
    5.5.1. ``NB-DQ-RULES`` (5.1.2) asks the broader question of whether *any* DQ
    logic is codified at all.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not (_SOURCE_READ.search(code) or _WRITE_PATTERN.search(code)):
        return not_applicable(
            "Notebook neither reads nor writes data, so it carries no business data to "
            "apply domain rules to"
        )
    ok = _business_rule_present(code)
    return binary(
        ok,
        "Notebook applies at least one cross-field business rule (two columns of the same "
        "row compared, or a named rule construct invoked). Whether these are the right "
        "rules for the domain is not readable from code."
        if ok else
        "No cross-field business rule found — no comparison relates two columns of the "
        "same row and no named rule construct is invoked, so a row whose end_date "
        "precedes its start_date would load unremarked",
    )


# =============================================================================
# 5.3.10 — nulls introduced *by* a join or a cast, checked where they appear
# =============================================================================

#: A construct that probes for, fills, or drops nulls.
_NULL_PROBE = re.compile(
    r"\.is(?:Not)?Null\s*\(|\bIS\s+(?:NOT\s+)?NULL\b|"
    r"\.fillna\s*\(|\.na\s*\.\s*(?:fill|drop)\s*\(|\.dropna\s*\(|\bCOALESCE\s*\(|"
    r"\bnull[_\s]?(?:count|counts|check|checks|profile)\b|\bcount[_\s]?nulls?\b|"
    r"\bnullif\s*\(",
    re.IGNORECASE,
)
#: Counting the nulls that an operation produced, rather than merely testing one
#: value — evidence that the *result* of the operation is what is being examined.
_NULL_COUNT = re.compile(
    r"is(?:Not)?Null[^\n]{0,120}\.count\s*\(|"
    r"\bnull[_\s]?counts?\b|\bcount[_\s]?nulls?\b|"
    r"\bIS\s+NULL\b[^\n]{0,120}\bcount\s*\(",
    re.IGNORECASE,
)
#: The control named outright. Nothing computes ``post_join_nulls`` by accident.
_NULL_PROPAGATION_NAMED = re.compile(
    r"\bnull[_\s]?propagation\b|\bpost[_\s-]?(?:join|cast)[_\s-]?null\w*|"
    r"\b(?:unmatched|failed)[_\s-]?(?:join|cast)\w*|"
    r"\bcast[_\s]?(?:failure|failures|error|errors)\b",
    re.IGNORECASE,
)

#: How far after a join/cast a null check still counts as checking *that*
#: operation. Wide enough to span the statement and the few that follow it,
#: narrow enough that a fillna in a later section of the notebook is not credited.
_NULL_PROP_WINDOW = 400

#: Words that appear in almost every join/cast statement, so sharing one with a
#: later null check proves no connection between them.
_GENERIC_CODE_WORDS = frozenset({
    "join", "left", "right", "outer", "inner", "anti", "semi", "cross", "cast",
    "select", "selectexpr", "filter", "where", "when", "otherwise", "with",
    "withcolumn", "withcolumnrenamed", "alias", "spark", "read", "table", "load",
    "from", "into", "using", "true", "false", "none", "null", "lit", "expr",
    "string", "integer", "bigint", "double", "float", "boolean", "decimal",
    "date", "timestamp", "struct", "structtype", "structfield", "to_date",
    "to_timestamp", "count", "show", "display", "print", "data", "temp",
})
_CODE_IDENTIFIER = re.compile(r"[A-Za-z_]\w{3,}")


def _null_introducing_ops(code: str) -> list[re.Match]:
    """Every join and every cast, in source order — the two null producers."""
    found = list(_JOIN_PATTERN.finditer(code)) + list(_TYPE_CAST.finditer(code))
    return sorted(found, key=lambda m: m.start())


def _statement_words(code: str, match: re.Match) -> set[str]:
    """Distinctive identifiers on the source line the match sits on."""
    start = code.rfind("\n", 0, match.start()) + 1
    end = code.find("\n", match.end())
    stmt = code[start:end if end != -1 else len(code)]
    return {w.lower() for w in _CODE_IDENTIFIER.findall(stmt)} - _GENERIC_CODE_WORDS


def _null_check_after_null_introducing_op(code: str) -> tuple[bool, bool]:
    """``(a null check follows a join/cast, that check is bound to the operation)``.

    *Positional* means a null construct appears in the window **after** a join or
    a cast — the point of ref 5.3.10, and what a ``fillna`` at the top of the
    notebook (which satisfies ref 5.2.7) can never be.

    *Bound* additionally means the check names something from the operation's own
    statement — the joined frame, the cast column — or counts the nulls in its
    result.
    """
    positional = bound = False
    for match in _null_introducing_ops(code):
        window = code[match.end():match.end() + _NULL_PROP_WINDOW]
        probe = _NULL_PROBE.search(window)
        if not probe:
            continue
        positional = True
        # The remainder of the operation's *own* line is excluded from the
        # name test: every join statement names its own frames, so matching a
        # word there would bind every join to whatever null check follows it.
        line_end = code.find("\n", match.end())
        after_line = code[line_end if line_end != -1 else len(code):
                          match.end() + _NULL_PROP_WINDOW]
        probe_context = window[max(0, probe.start() - 120):probe.end() + 120]
        words = _statement_words(code, match)
        names_the_subject = any(
            re.search(rf"\b{re.escape(w)}\b", after_line, re.IGNORECASE) for w in words
        )
        bound = bound or names_the_subject or bool(_NULL_COUNT.search(probe_context))
    return positional, bound


@check(
    id="NB-NULL-PROPAGATION", ref="5.3.10",
    title="Null propagation check: no nulls introduced by failed joins or type casts",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_checks_null_propagation(ctx: CheckContext) -> Verdict:
    """Nulls are looked for *where they can appear* — right after a join or a cast.

    **What it verifies: the guard, not the data.** Whether nulls actually
    appeared is a property of the rows, which this tool never reads (the source
    checklist lists a SQL-endpoint read for this point; proving nulls *exist*
    would need rows, proving the *code guards* against them needs only the
    notebook, and it is the guard that is scored here).

    **What it can determine.** That the notebook performs a null-producing
    operation — a DataFrame/SQL join, or a cast — and that a null construct
    (``isNull``, ``IS NULL``, ``fillna``, ``dropna``, ``COALESCE``, a null count)
    appears **after** it, within the same window. It scores highest when that
    check is *bound* to the operation: it names the joined frame or the cast
    column, or counts the nulls in the operation's result.

    **What it cannot.** Tell whether the check covers every column the join or
    cast could null, whether the handling is correct, or see a guard applied in a
    stored procedure or a downstream constraint. A frame name held only in a
    variable it cannot resolve is simply not credited, never counted against.

    **Sibling — deliberately narrower than ``NB-NULL-HANDLING`` (ref 5.2.7).**
    That check asks whether nulls are handled *anywhere* in the notebook, across
    non-key columns. This one is **positional**: a notebook whose only null
    handling is a ``df.fillna(0)`` at the top, followed later by a join, scores 0
    here — the nulls the join introduces are never looked at — while still
    passing 5.2.7. The two therefore never score off one line of code.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not (_JOIN_PATTERN.search(code) or _TYPE_CAST.search(code)):
        return not_applicable(
            "Notebook performs neither a join nor a cast, so there is no null propagation "
            "to guard against"
        )

    positional, bound = _null_check_after_null_introducing_op(code)
    if _NULL_PROPAGATION_NAMED.search(code):
        return graded(
            3,
            "Notebook runs a named null-propagation control (post-join / failed-cast null "
            "check) over the operations that can introduce nulls",
        )
    if bound:
        return graded(
            3,
            "A null check follows a join/cast and is bound to it — it names the joined or "
            "cast subject, or counts the nulls in the result, so a non-matching join key or "
            "a failed cast would be noticed",
        )
    if positional:
        return graded(
            2,
            "A null construct appears after a join/cast but is not tied to it — it names "
            "neither the joined frame nor the cast column and counts no nulls in the "
            "result, so it is unclear that the introduced nulls are the ones examined",
        )
    return graded(
        0,
        "Notebook joins or casts but no null check follows either operation — a join key "
        "that does not match and a cast that fails both produce nulls that pass through "
        "unnoticed. Null handling elsewhere in the notebook (credited by NB-NULL-HANDLING, "
        "5.2.7) does not cover the nulls these operations introduce.",
    )


# =============================================================================
# 5.5.8 — EAM/JSON ingestion: structure, required elements, coerced types
# =============================================================================

#: The JSON structure is *declared* rather than inferred. ``inferSchema`` is not
#: accepted — ``\bschema\s*=`` cannot match inside ``inferSchema=True``.
_JSON_EXPLICIT_SCHEMA = re.compile(
    r"StructType\s*\(|StructField\s*\(|MapType\s*\(|ArrayType\s*\(|"
    r"\.schema\s*\(|\bschema\s*=\s*(?!True\b|False\b|None\b)[A-Za-z_]\w*|"
    r"from_json\s*\([^,()\n]{0,80},|schema_of_json\s*\(|\bDDL\b|"
    r"\bexpected[_\s]?schema\b",
    re.IGNORECASE,
)

#: The expected keys/fields are asserted to be present.
_JSON_REQUIRED_ELEMENTS = re.compile(
    r"\b(?:required|expected|mandatory)[_\s]?"
    r"(?:field|fields|column|columns|key|keys|element|elements|attribute|attributes)\b|"
    r"\bmissing[_\s]?(?:field|fields|column|columns|key|keys|element|elements)\b|"
    r"\bset\s*\([^\n]{0,120}?\)\s*-\s*set\s*\(|\.issubset\s*\(|\.issuperset\s*\(|"
    r"\bin\s+\w+\s*\.\s*columns\b|\bnot\s+in\s+\w+\s*\.\s*columns\b|"
    r"assert[^\n]{0,100}\b(?:columns|keys|fields)\b|"
    r"\bhasField\s*\(|\.has_key\s*\(",
    re.IGNORECASE,
)

#: The coercion is *checked* rather than assumed: a parser mode that surfaces bad
#: records, a corrupt-record column, or a quarantine path for malformed input.
_JSON_COERCION_GUARD = re.compile(
    r"badRecordsPath|columnNameOfCorruptRecord|_corrupt_record|"
    r"\bmode\s*[=,]\s*[\"'](?:FAILFAST|PERMISSIVE|DROPMALFORMED)[\"']|"
    r"[\"']mode[\"']\s*,\s*[\"'](?:FAILFAST|PERMISSIVE|DROPMALFORMED)[\"']",
    re.IGNORECASE,
)
#: A coercion wrapped in exception handling — the Python spelling of the same
#: guard.
_TRY_AROUND_COERCION = re.compile(
    r"\btry\s*:[\s\S]{0,400}?"
    r"(?:\.cast\s*\(|to_date\s*\(|to_timestamp\s*\(|from_json\s*\(|json\.loads\s*\()"
    r"[\s\S]{0,400}?\bexcept\b"
)


def _coercion_is_verified(code: str) -> bool:
    """True when a cast/parse is checked rather than assumed."""
    if _JSON_COERCION_GUARD.search(code) or _TRY_AROUND_COERCION.search(code):
        return True
    for match in _TYPE_CAST.finditer(code):
        if _NULL_PROBE.search(code[match.end():match.end() + _NULL_PROP_WINDOW]):
            return True
    return False


@check(
    id="NB-JSON-VALIDATION", ref="5.5.8",
    title="**JSON (EAM)**: Structure validated; required elements present; type coercion verified",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_validates_json_payloads(ctx: CheckContext) -> Verdict:
    """EAM/JSON ingestion declares its structure, asserts required elements, and checks coercion.

    **What it verifies: the guard, not the payload.** Whether any incoming
    document is actually malformed is a runtime fact in the data (the source
    checklist lists a SQL-endpoint read for this point; that would only show the
    landed result). What is scored here is whether the *code* would notice —
    readable from the notebook alone.

    **What it can determine.** Three sub-practices, each scored independently:
    *structure* — an explicit schema on the JSON read (``StructType``,
    ``.schema(...)``, a schema passed to ``from_json``) rather than inference;
    *required elements* — an expected/required field list, a column-presence
    assertion, or a set difference against ``df.columns``; *type coercion
    verified* — a parser mode that surfaces bad records, a corrupt-record column,
    a ``try``/``except`` around the coercion, or a cast followed by a null check.

    **What it cannot.** Tell whether the declared schema matches the contract,
    whether the required-field list is complete, or validate a payload shape
    enforced outside the notebook.

    **Sibling — same population, different question.** ``NB-EAM-INGEST``
    (ref 2.6.6) gates on the same ``_EAM_JSON`` detector, deliberately reused
    here so the two agree on what counts as EAM/JSON ingestion, and asks whether
    that ingestion is *efficient* (streaming, partitioned, bounded parsing). This
    check asks whether it is *correct*.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = strip_sql_comments(executable_code(ctx.obj))
    if not _EAM_JSON.search(code):
        return not_applicable("Notebook has no recognizable EAM/JSON ingestion")

    facets = {
        "an explicit schema on the JSON read": bool(_JSON_EXPLICIT_SCHEMA.search(code)),
        "a required-element presence check": bool(_JSON_REQUIRED_ELEMENTS.search(code)),
        "verification that type coercion succeeded": _coercion_is_verified(code),
    }
    present = [name for name, ok in facets.items() if ok]
    missing = [name for name, ok in facets.items() if not ok]
    if not present:
        return graded(
            0,
            "EAM/JSON ingestion validates nothing: no explicit schema, no required-element "
            "check and no verification that type coercion succeeded — a renamed element or "
            "an uncoercible value lands silently as null",
        )
    return graded(
        len(present),
        f"EAM/JSON validation covers {len(present)} of 3 sub-practices "
        f"({'; '.join(present)})"
        + (f"; missing: {'; '.join(missing)}" if missing else "")
        + ". Whether the declared structure matches the source contract is not readable "
          "from code.",
    )


# =============================================================================
# 5.3.8 — historical consistency: this run's volume held against a previous run
#
# The point describes a *runtime outcome* — did the row count move as expected.
# Row data and run telemetry are never fetched, so what is scored is whether the
# code carries the run-over-run control that would notice an unexplained
# shrinkage. The evidence says so, exactly as 5.2.2 / 5.2.3 do.
# =============================================================================

#: A count belonging to an **earlier run**. This is the whole distinction from
#: ``NB-RECON-COUNT`` (5.2.5), which compares this run's output against *this
#: run's source*: ``source_count``, ``expected_count`` and a bare
#: ``df.count() == src.count()`` are all same-run reconciliation and must not
#: match here. Only a token that names a *prior* run does.
_PREVIOUS_RUN_COUNT = re.compile(
    r"\b(?:prev|previous|prior|last|lastrun|yester(?:day)?|historic(?:al)?|baseline|"
    r"benchmark)[_\s-]?(?:run[_\s-]?)?(?:row[_\s-]?|record[_\s-]?)?"
    r"(?:count|rowcount|rowcounts|rows|volume)\b|"
    r"\b(?:row|record)[_\s-]?count[_\s-]?"
    r"(?:prev|previous|prior|last|yesterday|baseline|history|historical)\b",
    re.IGNORECASE,
)

#: The run-over-run *movement* named directly — a delta, a percentage change, or
#: a shrinkage. Nothing computes ``pct_change`` on a row count by accident.
_VOLUME_MOVEMENT = re.compile(
    r"\b(?:row|record|count|volume)[_\s-]?"
    r"(?:delta|diff|difference|change|drop|variance|swing)\b|"
    r"\b(?:delta|diff|difference|change|drop|variance)[_\s-]?"
    r"(?:row|record|count|volume)s?\b|"
    r"\bpct[_\s-]?(?:change|diff|drop|delta|variance)\b|"
    r"\bpercent(?:age)?[_\s-]?(?:change|diff|drop|delta|variance)\b|"
    r"\bshrink(?:age|ing)?\b|\bunexpected[_\s-]?drop\b|\bvolume[_\s-]?drop\b",
    re.IGNORECASE,
)

#: An explicitly named run-over-run guard, as a *call or assignment* — a bare
#: word also matches a comment or a column name.
_NAMED_TREND_GUARD = re.compile(
    r"\b(?:volume_check|check_volume|trend_check|check_trend|shrinkage_check|"
    r"check_shrinkage|variance_check|check_variance|count_trend|row_count_trend|"
    r"historical_check|check_historical|drift_check|check_drift)\s*[\(=]",
    re.IGNORECASE,
)

#: A relational comparison. A previous count merely *read* judges nothing.
_TREND_COMPARISON = re.compile(r"(?:<=|>=|==|!=|<|>)|\bbetween\b", re.IGNORECASE)

#: A row count is persisted somewhere a later run could read it back. That is the
#: raw material for a trend, without the trend itself.
#: The separator deliberately excludes whitespace: ``row_count`` / ``rowcount``
#: is an identifier or a column, while ``"row count mismatch"`` is prose inside
#: an assertion message. Allowing a space made a same-run reconciliation whose
#: *error text* mentioned a row count read as a persisted count.
_COUNT_PERSISTED = re.compile(
    r"(?:row|record)[_-]?count[\s\S]{0,200}?"
    r"(?:INSERT\s+INTO|\.saveAsTable\s*\(|\.write\b)|"
    r"(?:INSERT\s+INTO|\.saveAsTable\s*\(|\.write\b)[\s\S]{0,200}?"
    r"(?:row|record)[_-]?count",
    re.IGNORECASE,
)

#: How far from the previous-run token the comparison may sit: one statement,
#: pretty-printed over a couple of lines — not the whole notebook.
_TREND_WINDOW = 200


def _run_over_run_control(code: str) -> bool:
    """True when a previous-run count or a volume movement is actually compared."""
    if _NAMED_TREND_GUARD.search(code):
        return True
    for pattern in (_PREVIOUS_RUN_COUNT, _VOLUME_MOVEMENT):
        for match in pattern.finditer(code):
            window = code[max(0, match.start() - _TREND_WINDOW):match.end() + _TREND_WINDOW]
            if _TREND_COMPARISON.search(window):
                return True
    return False


@check(
    id="NB-VOLUME-TREND", ref="5.3.8",
    title="Historical consistency: row counts change as expected (no unexplained shrinkage)",
    pillar=Pillar.DATA_QUALITY, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
    layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def notebook_has_a_run_over_run_volume_control(ctx: CheckContext) -> Verdict:
    """The notebook holds this load's row count against a **previous run's**.

    **What it verifies: the control, not the outcome.** Whether today's count
    actually moved as expected is a runtime fact that lives in the data and the
    run history; this tool reads neither. A PASS means "the code would notice an
    unexplained shrinkage", never "the volume was correct".

    **What it can determine.** A comparison against a count from an *earlier
    run* — a ``previous_count`` / ``last_run_row_count`` / ``baseline_count``
    read back and compared, a named movement (``row_delta``, ``pct_change``,
    ``shrinkage``) held against a bound, or a named guard
    (``volume_check(...)``, ``check_drift(...)``). A row count that is merely
    *persisted* for a later run to read scores in the middle: the history is
    being accumulated, but nothing compares it.

    **What it cannot.** Resolve a threshold that lives in a config table, see a
    volume rule enforced by a Data Activator alert or a monitoring dashboard, or
    judge whether the tolerance is the right one. Read with ``executable_code``
    so a commented-out check cannot pass.

    **Sibling — deliberately not satisfied by it.** ``NB-RECON-COUNT`` (5.2.5)
    compares this run's output against *this run's source* (``source_count``,
    ``expected_count``, ``df.count() == src.count()``). That is reconciliation
    across a hop, in one moment. This point is reconciliation across **time**, so
    none of those tokens match here; only a count naming a prior run does.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = executable_code(ctx.obj)
    if not _WRITE_PATTERN.search(code):
        return not_applicable(
            "Notebook writes no table, so there is no load volume to compare against "
            "a previous run"
        )

    if _run_over_run_control(code):
        return graded(
            3,
            "Notebook compares its row count against a previous run's count or against a "
            "named delta / percentage-change bound — a run-over-run consistency control is "
            "present. Whether any particular run's volume was correct is a runtime outcome "
            "this check does not read.",
        )
    if _COUNT_PERSISTED.search(code):
        return graded(
            1,
            "Notebook records its row count to a table, so the history exists, but nothing "
            "reads a previous run's count back and compares it — a load that silently "
            "halves would still be written and logged as normal",
        )
    return graded(
        0,
        "Notebook writes data with no run-over-run volume control — no previous-run count, "
        "delta or percentage-change test appears anywhere, so an unexplained shrinkage is "
        "indistinguishable from a normal load",
    )


# =============================================================================
# 2.1.4 — activities are logically grouped and self-documenting
# =============================================================================

#: A name Fabric generates when an activity is dropped on the canvas — the
#: activity type, optionally with a trailing number. It documents nothing.
#: ``Copy Sales To Bronze`` does not match: the tail after the type word must be
#: empty or a number.
_DEFAULT_ACTIVITY_NAME = re.compile(
    r"^(?:copy(?:\s*data)?|notebook|script|lookup|get\s*metadata|for\s*each|foreach|"
    r"if\s*condition|switch|until|wait|web(?:hook)?|set\s*variable|append\s*variable|"
    r"execute\s*pipeline|invoke\s*pipeline|fail|filter|delete(?:\s*data)?|"
    r"stored\s*procedure|dataflow|activity|new\s*activity|untitled|test|temp|tmp|"
    r"office\s*365\s*outlook|teams|semantic\s*model\s*refresh|refresh|new)"
    r"[\s_-]*\d*$",
    re.IGNORECASE,
)

#: Activity types that *group* work: their children are a labelled unit rather
#: than one more row in a flat list.
_CONTAINER_TYPES = frozenset({"ForEach", "IfCondition", "Switch", "Until"})

#: Above this many top-level activities with no container at all, the pipeline is
#: a flat wall of steps — readable as a list, not as a structure.
_FLAT_ACTIVITY_LIMIT = 10

#: Shortest name that can carry a subject and a verb.
_MIN_NAME_CHARS = 8

_NAME_WORD_SPLIT = re.compile(r"[\s_\-]+|(?<=[a-z0-9])(?=[A-Z])")


def _is_self_documenting(name: str) -> bool:
    """True when an activity name says what the step does, not what type it is.

    Two conditions, both cheap and both stable across tenants: the name is not a
    Fabric-generated default (``Copy data1``, ``Notebook3``, ``Untitled``), and
    it carries at least two words — ``LoadDimCustomer`` and ``Copy Sales To
    Bronze`` qualify, ``Stage`` does not.
    """
    text = (name or "").strip()
    if not text or _DEFAULT_ACTIVITY_NAME.match(text):
        return False
    words = [w for w in _NAME_WORD_SPLIT.split(text) if w]
    return len(words) >= 2 and len(text) >= _MIN_NAME_CHARS


@check(
    id="PL-ACTIVITY-SELFDOC", ref="2.1.4",
    title="Pipeline activities are logically grouped, annotated, and self-documenting",
    pillar=Pillar.DATA_INTEGRATION, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=False,
)
def pipeline_activities_are_self_documenting(ctx: CheckContext) -> Verdict:
    """Activity *names* say what each step does, and related steps sit inside a container.

    **What it can determine.** Two readable properties of the definition. First,
    naming: a Fabric-generated default (``Copy data1``, ``Notebook3``,
    ``Untitled``, or a bare type word) documents nothing, while a name with two
    or more words (``Copy Sales To Bronze``, ``LoadDimCustomer``) does. Second,
    grouping: whether the pipeline uses container activities — ForEach, If
    Condition, Switch, Until — or presents more than
    ``10`` steps as one flat top-level list. A flat, uncontained wall of steps
    costs one band.

    **What it cannot.** Judge whether a well-formed name is *accurate*, see
    logical grouping expressed by a naming prefix rather than a container, or
    know that a short pipeline was deliberately kept flat.

    **Sibling — deliberately disjoint.** ``PL-DESC`` (2.1.6) scores whether the
    ``description`` fields are populated. This check never reads a description:
    a pipeline whose every activity carries a description but is still called
    ``Copy data1`` passes 2.1.6 and fails here, which is the whole point — an
    annotation you have to open the properties pane to see is not the same thing
    as a canvas that reads itself.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    every = walk_activities(ctx.obj)
    if not every:
        return not_applicable("Pipeline has no activities to name or group")

    named, defaults = [], []
    for activity in every:
        name = str(activity.get("name") or "")
        (named if _is_self_documenting(name) else defaults).append(name or "?")
    defaults = sorted(defaults)
    top_level = activities(ctx.obj)
    containers = [a for a in every if a.get("type") in _CONTAINER_TYPES]
    flat = not containers and len(top_level) > _FLAT_ACTIVITY_LIMIT

    detail = (
        f"{len(named)} of {len(every)} activity name(s) are self-documenting "
        f"(two or more words, not a Fabric default)"
    )
    if defaults:
        detail += f"; default/one-word names: {', '.join(defaults[:5])}"
    detail += (
        f". {len(containers)} container activity(ies) (ForEach/If/Switch/Until) group "
        f"{len(top_level)} top-level step(s)."
        if containers else
        f". No container activity groups the {len(top_level)} top-level step(s)."
    )

    verdict = covered(len(named), len(every), detail)
    if not flat:
        return verdict
    return graded(
        max(0, (verdict.score or 0) - 1),
        detail + f" More than {_FLAT_ACTIVITY_LIMIT} steps sit in one flat list with no "
                 f"grouping container, which costs a band.",
    )
