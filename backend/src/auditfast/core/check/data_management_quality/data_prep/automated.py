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
    notebook_code,
)
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded
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
