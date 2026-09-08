"""Tenant-administration checks ported from the Fabric audit notebooks.

This module is the drop-in slot for the
tenant family: tenant settings, the scanner API (endorsement / sensitivity
labels), and tenant audit logs — data that genuinely needs a Fabric tenant
administrator.

To populate it, register with ``category=AdminCategory.TENANT``::

    from ...registry import admin_check
    from ....enums import AdminCategory, Pillar, Resource, Scope, Severity
    from ..helpers import Verdict, binary, not_applicable

    @admin_check(
        id="TN-EXPORT", ref="7.2.1",
        title="Tenant export settings are restricted",
        pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
        scope=Scope.WORKSPACE, severity=Severity.HIGH,
        requires=[Resource.TENANT_SETTINGS],
    )
    def export_restricted(ctx) -> Verdict:
        if not ctx.workspace.has(Resource.TENANT_SETTINGS):
            return not_applicable("Tenant settings could not be read")
        ...

Nothing else needs editing: the catalog endpoint, the selection screen and the
run mode are all driven from the registry, so the category lights up with a live
count as soon as a check lands here.

Two rules carry over from the standard library and are not negotiable:

* the check is a **pure function** of its ``CheckContext`` — no clock, no
  randomness, no model call;
* data the token could not read is **N/A, never FAIL**. A tenant-admin API that
  returns 403 means "we could not determine this", which is not the same claim
  as "this is misconfigured".
"""
from __future__ import annotations

from collections import defaultdict

from ...helpers import Verdict, graded, not_applicable
from ...registry import admin_check
from ....enums import AdminCategory, Pillar, Resource, Severity

_EXTERNAL = ("external", "guest", "b2b", "invite", "cross-tenant", "outside", "anonymous")
_EXPORT = ("export", "download", "print", "publish to web", "publishtoweb", "excel", "powerpoint", "csv", "snapshot")
_SHARING = ("external", "share", "publish to web", "b2b", "cross-tenant")
_GUEST = ("guest", "external user")
_ACCESS = ("groupmember", "permission", "share", "role", "access", "user")
_WORKSPACE_CHANGE = ("workspace", "folder")
_DELETE = ("delete", "remove")
_DEPLOY = ("deploy", "deployment", "pipeline")
_ITEM_CHANGE = ("create", "update", "edit", "modify", "publish", "write")


def _text(row: dict) -> str:
  keys = ("settingName", "title", "Setting", "Title", "Activity", "Operation")
  return " ".join(str(row.get(key) or "") for key in keys).lower()


def _enabled(row: dict) -> bool:
  return bool(row.get("enabled", row.get("Enabled", False)))


def _restricted(row: dict) -> bool:
  return bool(row.get("enabledSecurityGroups") or row.get("RestrictedToGroups"))


def _settings(ctx, terms: tuple[str, ...]) -> list[dict]:
  return [row for row in ctx.workspace.tenant_settings
      if any(term in _text(row) for term in terms)]


def _settings_unreadable(ctx) -> Verdict | None:
  if not ctx.workspace.has(Resource.TENANT_SETTINGS):
    return not_applicable(
      "Tenant settings could not be read; Fabric tenant administrator access is required"
    )
  return None


@admin_check(
  id="TN-6-1-6", ref="6.1.6", title="Guest/external user access is explicitly governed",
  pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.TENANT_SETTINGS],
)
def guest_access(ctx) -> Verdict:
  if missing := _settings_unreadable(ctx):
    return missing
  relevant = _settings(ctx, _EXTERNAL)
  open_settings = [row for row in relevant if _enabled(row) and not _restricted(row)]
  score = 3 if not open_settings else 2 if len(open_settings) == 1 else 1 if len(open_settings) <= 3 else 0
  return graded(score, f"{len(open_settings)} of {len(relevant)} enabled outward-facing settings are open to the whole organisation")


@admin_check(
  id="TN-6-1-8", ref="6.1.8", title="Fabric tenant admin settings reviewed and hardened",
  pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.TENANT,
  severity=Severity.CRITICAL, requires=[Resource.TENANT_SETTINGS],
)
def tenant_settings_hardened(ctx) -> Verdict:
  if missing := _settings_unreadable(ctx):
    return missing
  relevant = _settings(ctx, _EXPORT + _SHARING + _GUEST)
  open_rows = [row for row in relevant if _enabled(row) and not _restricted(row)]
  publish_open = any("publish to web" in _text(row) or "publishtoweb" in _text(row) for row in open_rows)
  sharing_open = any(any(term in _text(row) for term in _SHARING + _GUEST) for row in open_rows)
  export_open = any(any(term in _text(row) for term in _EXPORT) for row in open_rows)
  score = 0 if publish_open or len(open_rows) >= 3 else 1 if sharing_open else 2 if export_open else 3
  return graded(score, f"{len(open_rows)} of {len(relevant)} reviewed tenant settings are open to the whole organisation")


@admin_check(
  id="TN-6-2-8", ref="6.2.8", title="Data exfiltration controls configured",
  pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.TENANT,
  severity=Severity.CRITICAL, requires=[Resource.TENANT_SETTINGS],
)
def export_restrictions(ctx) -> Verdict:
  if missing := _settings_unreadable(ctx):
    return missing
  relevant = _settings(ctx, _EXPORT)
  open_rows = [row for row in relevant if _enabled(row) and not _restricted(row)]
  publish_open = any("publish to web" in _text(row) or "publishtoweb" in _text(row) for row in open_rows)
  score = 0 if publish_open else 3 if not open_rows else 2 if len(open_rows) <= 2 else 1
  return graded(score, f"{len(open_rows)} of {len(relevant)} export routes are open; publish-to-web open={publish_open}")


@admin_check(
  id="TN-6-1-9", ref="6.1.9", title="Domain access aligns with domain ownership",
  pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.TENANT_DOMAINS, Resource.ROLE_ASSIGNMENTS],
)
def domain_ownership(ctx) -> Verdict:
  ws = ctx.workspace
  if not ws.has(Resource.TENANT_DOMAINS):
    return not_applicable("Fabric domains could not be read; tenant administrator access is required")
  assigned: set[str] = set()
  principal_domains: dict[str, set[str]] = defaultdict(set)
  for domain in ws.tenant_domains:
    domain_id = str(domain.get("id") or domain.get("domainId") or "")
    for workspace_id in domain.get("workspace_ids", domain.get("workspaceIds", [])) or []:
      assigned.add(str(workspace_id))
    for assignment in domain.get("writers", []) or []:
      principal = str(assignment.get("principal_id") or assignment.get("principalId") or "")
      if principal:
        principal_domains[principal].add(domain_id)
  orphans = 0 if ws.id in assigned else 1
  if not ws.has(Resource.ROLE_ASSIGNMENTS):
    return graded(2 if not orphans else 1, "Domain membership was read, but role assignments were unavailable; score is capped")
  crossing = sum(1 for domains in principal_domains.values() if len(domains) > 1)
  score = 3 if not orphans and not crossing else 2 if not crossing else 1 if crossing <= 2 else 0
  return graded(score, f"orphan workspaces in this context={orphans}; cross-domain writer candidates={crossing}")


def _events(ctx) -> list[dict] | None:
  return ctx.workspace.activity_events if ctx.workspace.has(Resource.ADMIN_ACTIVITY) else None


@admin_check(
  id="TN-7-2-4", ref="7.2.4", title="Access control changes logged and reviewable",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def access_change_logging(ctx) -> Verdict:
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  matches = [event for event in events if any(term in _text(event) for term in _ACCESS)]
  attributed = [event for event in matches if event.get("UserId") or event.get("UserKey")]
  score = 2 if matches and len(attributed) == len(matches) else 1
  if score == 2 and bool(ctx.setting("access_change_review_documented", False)):
    score = 3
  return graded(score, f"{len(matches)} access-change event(s) found; {len(attributed)} identify the actor")


@admin_check(
  id="TN-7-4-1", ref="7.4.1", title="Fabric Activity Log enabled and exported",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def activity_log_export(ctx) -> Verdict:
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  if not events:
    return graded(1, "The readable activity-log window was quiet, so capture is unproven")
  score = 3 if bool(ctx.setting("activity_export_confirmed", False)) else 2
  return graded(score, f"{len(events)} event(s) prove capture; durable export confirmed={score == 3}")


@admin_check(
  id="TN-7-4-2", ref="7.4.2", title="Admin audit log captures important changes",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def audit_log_coverage(ctx) -> Verdict:
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  kinds = {
    "workspace": any(any(term in _text(event) for term in _WORKSPACE_CHANGE) for event in events),
    "permission": any(any(term in _text(event) for term in _ACCESS) for event in events),
    "deletion": any(any(term in _text(event) for term in _DELETE) for event in events),
  }
  seen = sum(kinds.values())
  score = 0 if seen <= 1 else 1 if seen == 2 else 2
  if score == 2 and bool(ctx.setting("audit_review_documented", False)):
    score = 3
  observed = ", ".join(name for name, present in kinds.items() if present) or "none"
  return graded(score, f"Observed audit event kinds: {observed}")


@admin_check(
  id="TN-11-2-3", ref="11.2.3", title="No manual deployments to production",
  pillar=Pillar.DEVOPS, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def no_manual_deployments(ctx) -> Verdict:
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  prod_names = {str(name).strip().lower() for name in ctx.setting("prod_workspaces", []) if str(name).strip()}
  if not prod_names:
    return not_applicable("No production workspaces were configured for this check")
  prod_events = [event for event in events
           if str(event.get("WorkSpaceName") or event.get("WorkspaceName") or "").lower() in prod_names]
  deployments = [event for event in prod_events if any(term in _text(event) for term in _DEPLOY)]
  candidates = [event for event in prod_events if any(term in _text(event) for term in _ITEM_CHANGE)
          and not any(term in _text(event) for term in _DEPLOY)]
  score = 0 if candidates and not deployments else 1 if candidates else 2 if deployments else 0
  if score == 2 and bool(ctx.setting("deployment_controls_confirmed", False)):
    score = 3
  return graded(score, f"Production log has {len(deployments)} deployment event(s) and {len(candidates)} manual-change candidate(s)")


@admin_check(
  id="TN-14-3-6", ref="14.3.6", title="Endorsement applied to trusted models and reports",
  pillar=Pillar.ARCHITECTURE, category=AdminCategory.TENANT,
  severity=Severity.MEDIUM, requires=[Resource.ADMIN_SCANNER],
)
def endorsement(ctx) -> Verdict:
  if not ctx.workspace.has(Resource.ADMIN_SCANNER):
    return not_applicable("Admin scanner metadata could not be read; endorsement badges are unavailable")
  candidates = ctx.workspace.admin_scan.get("items", [])
  if not candidates:
    return not_applicable("The admin scan returned no semantic models or reports to judge")
  endorsed = [row for row in candidates if str(row.get("endorsement") or "").lower() in {"certified", "promoted"}]
  certified = [row for row in candidates if str(row.get("endorsement") or "").lower() == "certified"]
  coverage = len(endorsed) / len(candidates)
  score = 3 if coverage >= .8 and certified else 2 if coverage >= .5 else 1 if endorsed else 0
  return graded(score, f"{len(endorsed)} of {len(candidates)} model(s)/report(s) endorsed; {len(certified)} Certified")
