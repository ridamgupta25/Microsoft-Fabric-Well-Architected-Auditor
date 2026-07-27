"""Pipeline checks — Operational Excellence.

Naming, documentation, and parameterization: can someone other than the author
understand, promote, and re-point this pipeline.
"""
from __future__ import annotations

import json
import re

from ...enums import Pillar, Resource, Scope, Severity
from ...models import CheckContext
from ..helpers import Verdict, binary, covered, graded
from ..registry import check
from .reliability import PIPELINE_LAYERS, activities

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
    pillar=Pillar.OPEX, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
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
    pillar=Pillar.OPEX, scope=Scope.PIPELINE, severity=Severity.LOW,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
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
    pillar=Pillar.OPEX, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
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
    # Nothing hardcoded, but nothing parameterized either — it will not promote
    # cleanly across environments.
    return graded(1, "No parameters defined (though no hardcoded endpoints found)")
