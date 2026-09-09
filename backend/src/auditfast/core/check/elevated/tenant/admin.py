"""Tenant-administration checks — ``AdminCategory.TENANT``.

Ported from the reviewed ``Setup1-Tenant`` notebooks, which a Fabric tenant
administrator runs by hand today. The scoring ladders here are the notebooks'
ladders; where this file deviates, the docstring says so and why.

Everything here needs **Fabric tenant administrator** — ``/admin/tenantsettings``,
``/admin/activityevents``, the scanner API and ``/admin/domains``. A workspace
Admin cannot read any of it, and a refusal is **N/A, never FAIL**: "we could not
determine this" is not "this is misconfigured".

**Ceilings are deliberate.** Several checks cannot reach 3 from the API alone —
a log recording a change is not a log anyone reviews, and same-window correlation
is not proof a change came from the pipeline. Those caps come from the notebooks
and are documented per check rather than papered over with a settings flag.

**Settings are matched by keyword, not by name.** Fabric's tenant-setting titles
change between releases and there is no stable identifier, so this searches the
title text for terms like "export" or "guest". A renamed setting silently drops
out of the population, which is why every verdict reports the population it
judged.
"""
from __future__ import annotations

from collections.abc import Sequence

from ....enums import AdminCategory, Pillar, Resource, Severity
from ...helpers import Verdict, graded, not_applicable
from ...registry import admin_check

_EXTERNAL = ("external", "guest", "b2b", "invite", "cross-tenant", "outside", "anonymous")
_EXPORT = ("export", "download", "print", "publish to web", "publishtoweb", "excel", "powerpoint", "csv", "snapshot")
_SHARING = ("external", "share", "publish to web", "b2b", "cross-tenant")
_GUEST = ("guest", "external user")
_ACCESS = ("groupmember", "permission", "share", "role", "access", "user")
_WORKSPACE_CHANGE = ("workspace", "folder")
_DELETE = ("delete", "remove")
_DEPLOY = ("deploy", "deployment", "pipeline")
_ITEM_CHANGE = ("create", "update", "edit", "modify", "publish", "write")


def _names(ctx, key: str) -> set[str]:
  """A project-config list of names, lowercased for comparison.

  Shared shape with the workspace tier: tolerates a comma-separated string as
  well as a list, because a YAML written by hand often carries one name without
  brackets.
  """
  raw = ctx.setting(key, []) or []
  if isinstance(raw, str):
    raw = raw.split(",")
  if not isinstance(raw, Sequence):
    return set()
  return {str(item).strip().lower() for item in raw if str(item).strip()}


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
  severity=Severity.HIGH, requires=[Resource.TENANT_DOMAINS],
)
def domain_ownership(ctx) -> Verdict:
  """This workspace belongs to a domain, so the platform records who owns it.

  A workspace in no domain has no recorded owner: nobody is accountable for who
  may read its data, and it appears in no domain-scoped governance view.

  Ladder: no domain ⇒ 1, in a domain ⇒ 2.

  **Deviation from the notebook — a deliberate ceiling of 2.** The notebook's
  rungs 3 and 0 turn on *cross-domain writers*: one principal holding a write
  role in workspaces belonging to two different domains. It finds them by
  reading the role assignments of **every in-scope workspace at once** and
  joining each to its domain. This check sees one workspace per call, so it can
  see a principal's role here but never their access to a workspace in another
  domain — the spanning set cannot be computed from this context at all.

  Rather than invent a rung, this reports the notebook's own degraded ladder for
  exactly this gap: when the notebook cannot assess crossings it scores
  ``2 if not orphans else 1``, which is what this returns. Recovering rungs 3
  and 0 needs a cross-workspace ``@group_check``, which the elevated runner does
  not dispatch yet.

  **What it cannot determine.** Whether one principal holds write access across
  two domains, and whether the domain a workspace sits in is the *right* one.
  """
  ws = ctx.workspace
  if not ws.has(Resource.TENANT_DOMAINS):
    return not_applicable("Fabric domains could not be read; tenant administrator access is required")

  domains = list(ws.tenant_domains)
  if not domains:
    return not_applicable(
      "No Fabric domain is defined in this tenant, so there is no domain "
      "ownership to align against"
    )

  owning = [
    str(domain.get("name") or domain.get("id") or "")
    for domain in domains
    if ws.id in {
      str(workspace_id)
      for workspace_id in (domain.get("workspace_ids", domain.get("workspaceIds", [])) or [])
    }
  ]
  if not owning:
    return graded(
      1,
      f"This workspace belongs to none of the {len(domains)} Fabric domain(s), so "
      f"the platform records no owner for it. Cross-domain write access spans "
      f"workspaces and is not assessable from a single workspace",
    )
  return graded(
    2,
    f"This workspace belongs to the '{owning[0]}' domain, so ownership is "
    f"recorded. Capped at 2: confirming nobody holds write access across two "
    f"domains needs every workspace's role assignments at once",
  )


def _events(ctx) -> list[dict] | None:
  return ctx.workspace.activity_events if ctx.workspace.has(Resource.ADMIN_ACTIVITY) else None


@admin_check(
  id="TN-7-2-4", ref="7.2.4", title="Access control changes logged and reviewable",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def access_change_logging(ctx) -> Verdict:
  """Access-control changes appear in the tenant activity log.

  Ladder (from the notebook): a readable log with at least one access change
  scores 2; a readable log with none scores 1 — the log works, but nothing
  proves *this* kind of change is captured.

  **It never scores 3**, and that is deliberate: the log recording a change is
  not the same as anyone reviewing it, and the review is not readable from any
  API. The notebook caps here for the same reason.
  """
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  matches = [event for event in events if any(term in _text(event) for term in _ACCESS)]
  if matches:
    attributed = [event for event in matches if event.get("UserId") or event.get("UserKey")]
    return graded(
      2,
      f"{len(matches)} access-change event(s) recorded, {len(attributed)} naming the "
      f"actor. Whether anyone reviews the log is not readable from the API",
    )
  return graded(
    1,
    "The activity log is readable but recorded no access change in the window, so "
    "capture of this event kind is unproven — not evidence that none occurred",
  )


@admin_check(
  id="TN-7-4-1", ref="7.4.1", title="Fabric Activity Log enabled and exported",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def activity_log_export(ctx) -> Verdict:
  """The activity log captures events for every workspace in scope.

  Ladder (from the notebook): no events at all across the whole window scores
  **0** — the log is readable but proving nothing; partial coverage scores 1;
  every workspace in scope proven scores 2.

  **It never scores 3.** The Fabric activity log retains a limited window, so
  full marks need a durable export to Log Analytics, an Event Hub or storage —
  a destination no Fabric API reports.
  """
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  if not events:
    return graded(
      0,
      "The activity log returned no events at all across the readable window, so "
      "nothing proves the log is capturing activity",
    )
  seen = {
    str(event.get("WorkSpaceName") or event.get("WorkspaceName") or "").strip().lower()
    for event in events
  }
  seen.discard("")
  this = (ctx.workspace.name or "").strip().lower()
  if this and this not in seen:
    return graded(
      1,
      f"{len(events)} event(s) prove the log is capturing, but none is from "
      f"'{ctx.workspace.name}', so logging is unproven for this workspace",
    )
  return graded(
    2,
    f"{len(events)} event(s) prove capture for this workspace. Full marks need a "
    f"durable export beyond the log's retention window, which no API reports",
  )


@admin_check(
  id="TN-7-4-2", ref="7.4.2", title="Admin audit log captures important changes",
  pillar=Pillar.COMPLIANCE, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def audit_log_coverage(ctx) -> Verdict:
  """All three kinds of important change appear in the log.

  Ladder (from the notebook): **the score is the number of kinds seen** — three
  of workspace changes, permission changes and item deletions gives 3, two gives
  2, and so on. An empty log is N/A rather than 0: nothing could be read, which
  is not the same as nothing being captured.
  """
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  if not events:
    return not_applicable(
      "The activity log returned nothing for this workspace, so the kinds of "
      "change it captures could not be determined"
    )
  kinds = {
    "workspace changes": any(any(term in _text(event) for term in _WORKSPACE_CHANGE) for event in events),
    "permission changes": any(any(term in _text(event) for term in _ACCESS) for event in events),
    "item deletions": any(any(term in _text(event) for term in _DELETE) for event in events),
  }
  seen = sum(kinds.values())
  present = ", ".join(name for name, found in kinds.items() if found) or "none"
  missing = ", ".join(name for name, found in kinds.items() if not found)
  return graded(
    seen,
    f"{seen} of 3 change kinds appeared in the window ({present})"
    + (f"; not seen: {missing}" if missing else ""),
  )


@admin_check(
  id="TN-11-2-3", ref="11.2.3", title="No manual deployments to production",
  pillar=Pillar.DEVOPS, category=AdminCategory.TENANT,
  severity=Severity.HIGH, requires=[Resource.ADMIN_ACTIVITY],
)
def no_manual_deployments(ctx) -> Verdict:
  """Production changes arrive through a deployment, not by hand.

  Ladder (from the notebook): production changes with **no** deployment event at
  all scores 0; more than five unexplained changes scores 0; up to five scores 1;
  every change lining up with a deployment scores 2.

  **It never scores 3.** Same-window correlation is not proof that a specific
  change came *from* the pipeline — only that a deployment happened too.

  **What it cannot determine.** The notebook pairs a change with a deployment on
  the same *day*; the activity events kept in the knowledge base carry no
  timestamp, so this compares populations rather than dates and the evidence
  says so.
  """
  events = _events(ctx)
  if events is None:
    return not_applicable("Tenant activity log could not be read; tenant administrator access is required")
  prod_names = _names(ctx, "production_workspaces")
  if not prod_names:
    return not_applicable(
      "No workspace is named in the project's 'production_workspaces' setting, so "
      "nothing marks a change as being made to production"
    )
  prod_events = [
    event for event in events
    if str(event.get("WorkSpaceName") or event.get("WorkspaceName") or "").strip().lower() in prod_names
  ]
  if not prod_events:
    return not_applicable(
      "The activity log holds no event for the named production workspace(s), so "
      "there is no production change to judge"
    )
  deployments = [event for event in prod_events if any(term in _text(event) for term in _DEPLOY)]
  changes = [
    event for event in prod_events
    if any(term in _text(event) for term in _ITEM_CHANGE)
    and not any(term in _text(event) for term in _DEPLOY)
  ]
  if changes and not deployments:
    return graded(
      0,
      f"{len(changes)} production change(s) and not one deployment event — every "
      f"change reached production by hand",
    )
  if not changes:
    return graded(
      2,
      f"No manual production change in the window; {len(deployments)} deployment "
      f"event(s) recorded. Full marks need the release process confirmed off-API",
    )
  return graded(
    0 if len(changes) > 5 else 1,
    f"{len(changes)} production change(s) alongside {len(deployments)} deployment "
    f"event(s). The knowledge base keeps no event timestamps, so a change cannot be "
    f"paired to a deployment by date — this compares populations only",
  )


@admin_check(
  id="TN-14-3-6", ref="14.3.6", title="Endorsement applied to trusted models and reports",
  pillar=Pillar.ARCHITECTURE, category=AdminCategory.TENANT,
  severity=Severity.MEDIUM, requires=[Resource.ADMIN_SCANNER],
)
def endorsement(ctx) -> Verdict:
  """Trusted models and reports carry a Certified or Promoted badge.

  Without a badge a consumer cannot tell a governed model from someone's ad-hoc
  copy, so they either use the wrong one or rebuild it themselves.

  Ladder (from the notebook): ≥80% endorsed **and** at least one Certified ⇒ 3;
  ≥50% ⇒ 2; anything endorsed ⇒ 1; none ⇒ 0.

  The population excludes the semantic models Fabric creates automatically for a
  Lakehouse or Warehouse, and usage-metrics/template-app content — nobody
  endorses those, and counting them would drag the score down for content never
  meant to be badged. The crawl applies both exclusions (the notebook's
  ``INCLUDE_DEFAULT_MODELS=False`` and ``EXCLUDE_KEYWORDS``) and the evidence
  reports how many were left out.

  **What it cannot determine.** Whether a badge is *deserved* — only that
  someone applied one.
  """
  if not ctx.workspace.has(Resource.ADMIN_SCANNER):
    return not_applicable("Admin scanner metadata could not be read; endorsement badges are unavailable")
  candidates = ctx.workspace.admin_scan.get("items", [])
  left_out = len(ctx.workspace.admin_scan.get("excluded", []))
  if not candidates:
    return not_applicable(
      "The admin scan returned no semantic model or report to judge"
      + (f"; {left_out} item(s) were left out as automatically created or "
         f"usage-metrics content" if left_out else "")
    )
  endorsed = [row for row in candidates if str(row.get("endorsement") or "").lower() in {"certified", "promoted"}]
  certified = [row for row in candidates if str(row.get("endorsement") or "").lower() == "certified"]
  coverage = len(endorsed) / len(candidates)
  score = 3 if coverage >= .8 and certified else 2 if coverage >= .5 else 1 if endorsed else 0
  return graded(score, (
    f"{len(endorsed)} of {len(candidates)} model(s)/report(s) endorsed; "
    f"{len(certified)} Certified"
    + (f"; {left_out} auto-created or usage-metrics item(s) excluded" if left_out else "")
  ))
