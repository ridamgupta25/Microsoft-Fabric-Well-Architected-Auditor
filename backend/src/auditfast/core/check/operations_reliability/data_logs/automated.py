"""Operations & Reliability · Data Logs — audit logging and alerting checks."""
from __future__ import annotations

import re

from auditfast.core.check._notebook import executable_code
from auditfast.core.check._pipeline import walk_activities
from auditfast.core.check.helpers import Verdict, binary, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_AUDIT_LOG_SIGNAL = re.compile(
    r"audit[_ -]?(?:table|log)|quality[_ -]?(?:table|log)|dq[_ -]?(?:table|log)|"
    r"row[_ -]?count|null[_ -]?count|exception[_ -]?count|error[_ -]?count|"
    r"batch[_ -]?id|run[_ -]?id|failure[_ -]?reason",
    re.IGNORECASE,
)
_WRITE_SIGNAL = re.compile(
    r"\.write\b|saveAsTable|INSERT\s+INTO|MERGE\s+INTO|audit[_ -]?(?:table|log)|"
    r"quality[_ -]?(?:table|log)|dq[_ -]?(?:table|log)",
    re.IGNORECASE,
)
_NOTIFY_TYPES = frozenset({"Teams", "Office365Outlook", "Outlook365", "SendEmail", "WebHook"})
_NOTIFY_CALL_TYPES = frozenset({"Web", "WebActivity", "AzureFunctionActivity", "Function"})
_NOTIFY_NAME = re.compile(r"notif|alert|email|teams|activator", re.IGNORECASE)


@check(
    id="NB-AUDIT-LOG", ref="4.6.4",
    title="Audit Tables capture data quality logs, row counts, null checks, and exceptions",
    pillar=Pillar.DATA, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.LOGS,), requires=[Resource.NOTEBOOK_DEFINITIONS], required=True,
)
def audit_tables_capture_quality_logs(ctx: CheckContext) -> Verdict:
    """Workspace notebooks write a repeatable audit log with quality metrics."""
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    if not ctx.workspace.notebooks:
        return not_applicable("No notebook definitions are available for Data Logs")
    candidates = []
    for name, definition in ctx.workspace.notebooks.items():
        code = executable_code(definition)
        if _WRITE_SIGNAL.search(code) and _AUDIT_LOG_SIGNAL.search(code):
            candidates.append(name)
    return binary(bool(candidates),
                  f"Quality audit-log writer found in: {', '.join(sorted(candidates))}"
                  if candidates else
                  "No notebook writes an audit table with row/null/exception quality metrics")


@check(
    id="PL-FAILURE-ALERT", ref="10.1.4",
    title="Alerting on pipeline failure (Data Activator or equivalent)",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def pipeline_failure_alert(ctx: CheckContext) -> Verdict:
    """A pipeline failure dependency leads to a recognizable notification activity."""
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = walk_activities(ctx.obj)
    if not acts:
        return not_applicable("Pipeline has no activities to evaluate for failure alerting")
    failure_names = {
        a.get("name", "") for a in acts
        if any("Failed" in (dep.get("dependencyConditions") or [])
               for dep in (a.get("dependsOn") or []))
    }
    notifiers = {
        a.get("name", "") for a in acts
        if a.get("type") in _NOTIFY_TYPES
        or (a.get("type") in _NOTIFY_CALL_TYPES and _NOTIFY_NAME.search(a.get("name", "")))
    }
    linked = sorted(failure_names & notifiers)
    return binary(bool(linked),
                  f"Failure path is linked to notification activity: {', '.join(linked)}"
                  if linked else
                  "No failure-linked Data Activator, email, Teams, or webhook notification found")
