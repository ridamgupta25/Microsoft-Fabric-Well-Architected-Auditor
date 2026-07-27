"""Pipeline checks — Security."""
from __future__ import annotations

import json
import re

from ...enums import Pillar, Resource, Scope, Severity
from ...models import CheckContext
from ..helpers import Verdict, binary
from ..registry import check
from .reliability import PIPELINE_LAYERS

#: Literal secret patterns — a *value* being present, not merely a parameter name.
SECRET_PATTERNS = [
    re.compile(r"password\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\bpwd\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"AccountKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"SharedAccessKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\"secret\"\s*:\s*\"[^\"]{6,}\"", re.IGNORECASE),
]


@check(
    id="PL-SECRETS", ref="6.4.2", title="No hardcoded secrets in pipeline",
    pillar=Pillar.SECURITY, scope=Scope.PIPELINE, severity=Severity.CRITICAL,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS],
)
def no_hardcoded_secrets(ctx: CheckContext) -> Verdict:
    """No credential literal appears anywhere in the pipeline definition.

    Evidence deliberately reports only the number of matching patterns, never the
    matched text — an audit report must not become a second copy of the secret.
    """
    blob = json.dumps(ctx.obj)
    hits = [p.pattern for p in SECRET_PATTERNS if p.search(blob)]
    return binary(
        not hits,
        "No hardcoded secret patterns detected" if not hits
        else f"Potential hardcoded secret(s) detected ({len(hits)} pattern match)",
    )
