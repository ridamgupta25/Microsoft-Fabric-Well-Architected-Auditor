"""Shared helpers for semantic-model (TMSL) checks.

Underscore-prefixed so the package auto-loader skips it: helpers only, no checks.
"""
from __future__ import annotations

#: TMSL spells a denial as ``metadataPermission: "none"``; anything else grants.
_DENIED = "none"


def restricts_objects(model: dict) -> bool:
    """True when a role hides a column or a whole table — object-level security.

    Both shapes count: a ``columnPermissions`` entry set to ``none`` (classic OLS)
    and a ``tablePermissions`` entry whose own ``metadataPermission`` is ``none``
    (the whole table hidden). A permission that *grants* access is not a
    restriction and must not be read as one.
    """
    for role in model.get("roles") or []:
        for table in role.get("table_permissions") or []:
            if _denied(table.get("metadata_permission")):
                return True
            for column in table.get("column_permissions") or []:
                if _denied(column.get("permission")):
                    return True
    return False


def _denied(permission: object) -> bool:
    return str(permission or "").strip().lower() == _DENIED


def rls_roles(model: dict) -> tuple[int, int]:
    """``(roles carrying an RLS filter, roles defined)`` for one model.

    A role with table permissions but no ``filterExpression`` restricts no rows,
    so it is defined but not actually filtering.
    """
    defined = filtering = 0
    for role in model.get("roles") or []:
        defined += 1
        if any((tp.get("filter") or "").strip()
               for tp in role.get("table_permissions") or []):
            filtering += 1
    return filtering, defined


def hidden_columns(model: dict) -> set[str]:
    """Lower-cased column names a role hides via OLS."""
    hidden: set[str] = set()
    for role in model.get("roles") or []:
        for table in role.get("table_permissions") or []:
            for column in table.get("column_permissions") or []:
                if _denied(column.get("permission")):
                    hidden.add(str(column.get("column") or "").lower())
    return hidden
