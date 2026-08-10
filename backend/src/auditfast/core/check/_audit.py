"""Shared helpers for the Warehouse SQL-audit checks.

Underscore-prefixed so the package auto-loader skips it: it registers no checks,
only the vocabulary two checks in different pillars/layers both need.

The provider stores each Warehouse's ``settings/sqlAudit`` **configuration** —
state, action groups, retention — and never the audit rows themselves
(``sys.fn_get_audit_file_v2`` returns runtime data, which is deliberately out of
scope for a configuration auditor). Everything here therefore judges *what the
audit is configured to capture*, not what it has captured.

Action-group names follow the SQL Server / Fabric SQL audit vocabulary. They are
compared case-insensitively because the API echoes back whatever casing was
configured.
"""
from __future__ import annotations

#: Groups that capture executed T-SQL batches — the only way an INSERT / UPDATE /
#: DELETE / MERGE against a table lands in the audit. Without one of these the
#: audit records that someone connected, not that they changed anything.
DML_ACTION_GROUPS: frozenset[str] = frozenset({
    "BATCH_COMPLETED_GROUP",
    "BATCH_STARTED_GROUP",
})

#: Groups that capture *structural* change — DDL against objects and schemas, and
#: changes to principals, roles and permissions. Real audit evidence, but they do
#: not record a row being modified.
OBJECT_CHANGE_ACTION_GROUPS: frozenset[str] = frozenset({
    "DATABASE_OBJECT_CHANGE_GROUP",
    "SCHEMA_OBJECT_CHANGE_GROUP",
    "DATABASE_OBJECT_PERMISSION_CHANGE_GROUP",
    "SCHEMA_OBJECT_PERMISSION_CHANGE_GROUP",
    "DATABASE_PERMISSION_CHANGE_GROUP",
    "DATABASE_PRINCIPAL_CHANGE_GROUP",
    "DATABASE_ROLE_MEMBER_CHANGE_GROUP",
    "DATABASE_OWNERSHIP_CHANGE_GROUP",
    "SCHEMA_OBJECT_OWNERSHIP_CHANGE_GROUP",
})

#: Groups that only record connections. An audit configured with nothing else
#: answers "who signed in", never "what changed".
AUTHENTICATION_ACTION_GROUPS: frozenset[str] = frozenset({
    "SUCCESSFUL_DATABASE_AUTHENTICATION_GROUP",
    "FAILED_DATABASE_AUTHENTICATION_GROUP",
    "DATABASE_LOGOUT_GROUP",
    "USER_CHANGE_PASSWORD_GROUP",
})


def audit_enabled(settings: dict) -> bool:
    """True when the Warehouse's SQL audit is switched on.

    The provider normalises the state, but the raw ``state`` string is re-read
    here so a snapshot written by an older crawl (which carried only ``state``)
    still answers correctly instead of silently reading as disabled.
    """
    if not isinstance(settings, dict):
        return False
    if isinstance(settings.get("enabled"), bool):
        return bool(settings["enabled"])
    return str(settings.get("state") or "").strip().lower() in {"enabled", "enable", "on", "true"}


def action_groups(settings: dict) -> set[str]:
    """The configured audit action groups, upper-cased for comparison."""
    if not isinstance(settings, dict):
        return set()
    raw = settings.get("action_groups") or settings.get("auditActionsAndGroups") or []
    if not isinstance(raw, list):
        return set()
    return {str(group).strip().upper() for group in raw if str(group).strip()}


def captures_data_modifications(settings: dict) -> bool:
    """True when the audit is on *and* configured to record data modifications.

    A modification is only captured by a batch/statement group: the object-change
    groups record DDL, and the authentication groups record connections. Auditing
    that is enabled but configured with neither cannot answer "what changed".
    """
    return bool(audit_enabled(settings) and (action_groups(settings) & DML_ACTION_GROUPS))


def captures_object_changes(settings: dict) -> bool:
    """True when the audit is on and records structural (DDL/permission) change."""
    return bool(audit_enabled(settings) and (action_groups(settings) & OBJECT_CHANGE_ACTION_GROUPS))
