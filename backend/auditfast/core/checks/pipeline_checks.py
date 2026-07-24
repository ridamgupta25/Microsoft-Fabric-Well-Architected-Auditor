"""Pipeline best-practice checks (Section 6.2 of the design).

Each check inspects ONE pipeline definition (the JSON returned by getDefinition)
and verifies a best-practice operation is present/configured. It never validates
where data comes from or goes to - only whether the implemented pipeline follows
best practice.
"""
from __future__ import annotations

import json
import re

from ..models import OPEX, RELIABILITY, SECURITY, Severity
from .base import (binary_result, coverage_result, pipeline_check,
                   scored_result)

# Activity timeout values that Fabric/ADF apply by default (i.e. "not explicitly set").
_DEFAULT_TIMEOUTS = {"7.00:00:00", "0.12:00:00", "7.00:00", ""}

_NOTIFY_TYPES = {"Teams", "Office365Outlook", "SendEmail", "WebHook"}
_NOTIFY_NAME_RE = re.compile(r"notif|alert|email|teams", re.IGNORECASE)

# Hardcoded connection / endpoint literals that should be parameterized instead.
_HARDCODED_RE = [
    re.compile(r"\.database\.windows\.net", re.IGNORECASE),
    re.compile(r"Data Source\s*=", re.IGNORECASE),
    re.compile(r"\bServer\s*=\s*tcp:", re.IGNORECASE),
    re.compile(r"\.blob\.core\.windows\.net", re.IGNORECASE),
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # bare IPv4
]

# Literal secret patterns (a value present, not just a parameter name).
_SECRET_RE = [
    re.compile(r"password\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\bpwd\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"AccountKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"SharedAccessKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\"secret\"\s*:\s*\"[^\"]{6,}\"", re.IGNORECASE),
]


def _activities(definition: dict) -> list:
    return (definition.get("properties") or {}).get("activities") or []


@pipeline_check
def pl_naming(ws, name, definition, s):
    pattern = s.get("pipeline_naming_convention")
    ok = bool(pattern) and re.match(pattern, name) is not None
    return binary_result(
        check_id="PL-NAME", ref="2.1.1", title="Pipeline naming convention", pillar=OPEX,
        ok=ok, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""), obj=name,
        evidence=(f"'{name}' matches convention" if ok
                  else f"'{name}' does not match {pattern!r}"),
        fail_severity=Severity.LOW,
    )


@pipeline_check
def pl_descriptions(ws, name, definition, s):
    props = definition.get("properties") or {}
    acts = _activities(definition)
    good = (1 if (props.get("description") or "").strip() else 0)
    good += sum(1 for a in acts if (a.get("description") or "").strip())
    total = 1 + len(acts)
    coverage = good / total if total else 1.0
    return coverage_result(
        check_id="PL-DESC", ref="2.1.6", title="Descriptions / annotations populated",
        pillar=OPEX, coverage=coverage, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence=f"{good} of {total} description slots (pipeline + activities) populated",
        fail_severity=Severity.LOW,
    )


@pipeline_check
def pl_parameterized(ws, name, definition, s):
    blob = json.dumps(definition)
    found = [p.pattern for p in _HARDCODED_RE if p.search(blob)]
    has_params = bool((definition.get("properties") or {}).get("parameters"))
    if found:
        score, evidence = 0, f"Hardcoded endpoint/literal(s) detected: {found}"
    elif has_params:
        score, evidence = 3, "Uses pipeline parameters; no hardcoded endpoints found"
    else:
        score, evidence = 1, "No parameters defined (though no hardcoded endpoints found)"
    return scored_result(
        check_id="PL-PARAM", ref="2.1.2", title="Parameterized (no hardcoded endpoints)",
        pillar=OPEX, score=score, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name, evidence=evidence,
        fail_severity=Severity.MEDIUM,
    )


@pipeline_check
def pl_retry(ws, name, definition, s):
    acts = _activities(definition)
    with_retry = [a for a in acts if (a.get("policy") or {}).get("retry", 0) >= 1]
    coverage = len(with_retry) / len(acts) if acts else 1.0
    return coverage_result(
        check_id="PL-RETRY", ref="2.4.1", title="Retry policy configured on activities",
        pillar=RELIABILITY, coverage=coverage, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence=f"{len(with_retry)} of {len(acts)} activities have a retry policy",
        fail_severity=Severity.HIGH,
    )


@pipeline_check
def pl_failure_path(ws, name, definition, s):
    acts = _activities(definition)
    has_fail = any(
        "Failed" in (dep.get("dependencyConditions") or [])
        for a in acts for dep in (a.get("dependsOn") or [])
    )
    return binary_result(
        check_id="PL-FAILPATH", ref="2.4.3", title="On-failure path defined",
        pillar=RELIABILITY, ok=has_fail, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence="A Failed dependency path exists" if has_fail else "No on-failure path found",
        fail_severity=Severity.MEDIUM,
    )


@pipeline_check
def pl_failure_notification(ws, name, definition, s):
    acts = _activities(definition)
    has_notify = any(
        a.get("type") in _NOTIFY_TYPES or _NOTIFY_NAME_RE.search(a.get("name", ""))
        for a in acts
    )
    return binary_result(
        check_id="PL-NOTIFY", ref="2.4.5", title="Failure notification present",
        pillar=RELIABILITY, ok=has_notify, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence="A notification activity is present" if has_notify else "No notification activity found",
        fail_severity=Severity.MEDIUM,
    )


@pipeline_check
def pl_timeout(ws, name, definition, s):
    acts = _activities(definition)

    def has_timeout(a) -> bool:
        t = (a.get("policy") or {}).get("timeout")
        return bool(t) and str(t) not in _DEFAULT_TIMEOUTS

    with_to = [a for a in acts if has_timeout(a)]
    coverage = len(with_to) / len(acts) if acts else 1.0
    return coverage_result(
        check_id="PL-TIMEOUT", ref="2.4", title="Explicit activity timeouts set",
        pillar=RELIABILITY, coverage=coverage, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence=f"{len(with_to)} of {len(acts)} activities set a non-default timeout",
        fail_severity=Severity.LOW,
    )


@pipeline_check
def pl_no_hardcoded_secrets(ws, name, definition, s):
    blob = json.dumps(definition)
    hits = [p.pattern for p in _SECRET_RE if p.search(blob)]
    ok = not hits
    return binary_result(
        check_id="PL-SECRETS", ref="6.4.2", title="No hardcoded secrets in pipeline",
        pillar=SECURITY, ok=ok, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""), obj=name,
        evidence=("No hardcoded secret patterns detected" if ok
                  else f"Potential hardcoded secret(s) detected ({len(hits)} pattern match)"),
        fail_severity=Severity.CRITICAL,
    )
