"""The KB field catalog (shared by Nodes 3a and 3b).

A curated map from *meaning* to the read-only KB field that answers it. Each entry
says what the field means (for meaning/keyword matching), where in the workspace
snapshot it lives, which read-only resource/endpoint would fetch it if missing, and
how to tell a usable value from junk (the quality validator).

**Non-secret only.** Deliberately excludes credentials, tokens, and connection
secrets - the catalog points a check at auditable metadata, never at a secret.

Field paths address the normalised :class:`WorkspaceContext` snapshot
(``auditfast.core.models``). Presence is resolved either at the top level or one
level down, so both a single-workspace snapshot and a ``{workspace_id: context}``
map work.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: A sentinel distinct from ``None``: the path is absent from the snapshot.
MISSING: Any = object()

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
        "have", "in", "into", "is", "it", "its", "of", "on", "or", "should",
        "that", "the", "their", "to", "use", "used", "using", "via", "with",
        "all", "any", "each", "ensure", "ensures", "enabled", "enable", "make",
        "sure", "set", "check", "checks", "verify", "every", "must", "no",
        "workspace", "workspaces",
    }
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS}


def _non_empty(value: Any) -> bool:
    """A dict/list/str is usable when non-empty; a bool/number is usable as-is."""
    if isinstance(value, (dict, list, str)):
        return bool(value)
    return value is not None


@dataclass(frozen=True, slots=True)
class KbField:
    """One auditable, read-only field the pipeline knows how to reason about."""

    key: str
    meaning_description: str
    path: str
    resource: str
    endpoint: str
    keywords: tuple[str, ...] = ()
    mandatory: bool = True
    validator: Callable[[Any], bool] = _non_empty

    def search_tokens(self) -> set[str]:
        return _tokens(self.meaning_description + " " + " ".join(self.keywords))


#: The curated, non-secret catalog. Extend as new custom-check shapes appear.
KB_FIELD_CATALOG: tuple[KbField, ...] = (
    KbField(
        key="git_integration",
        meaning_description="whether the workspace is connected to Git source control",
        path="git_connected",
        resource="GIT",
        endpoint="GET /v1/workspaces/{id}/git/connection",
        keywords=("git", "source", "control", "version", "repository", "devops", "integration"),
        validator=lambda v: isinstance(v, bool),
    ),
    KbField(
        key="deployment_pipeline",
        meaning_description="whether the workspace uses a deployment pipeline for release stages",
        path="deployment_pipeline",
        resource="DEPLOYMENT_PIPELINE",
        endpoint="GET /v1/deploymentPipelines",
        keywords=("deployment", "pipeline", "release", "stage", "promotion", "cicd"),
        validator=lambda v: isinstance(v, bool),
    ),
    KbField(
        key="role_assignments",
        meaning_description="who has access to the workspace and their roles and permissions",
        path="role_assignments",
        resource="ROLE_ASSIGNMENT",
        endpoint="GET /v1/workspaces/{id}/roleAssignments",
        keywords=("access", "permission", "role", "member", "admin", "viewer", "who", "sharing"),
    ),
    KbField(
        key="refresh_schedule",
        meaning_description="semantic model dataset refresh schedule and incremental refresh policy",
        path="refresh_schedules",
        resource="SEMANTIC_MODEL",
        endpoint="GET /v1/workspaces/{id}/semanticModels/{id}/refreshSchedule",
        keywords=(
            "refresh", "incremental", "reload", "schedule", "dataset", "semantic",
            "model", "cadence",
        ),
    ),
    KbField(
        key="warehouse_audit",
        meaning_description="warehouse SQL audit logging configuration and retention",
        path="warehouse_audit",
        resource="WAREHOUSE",
        endpoint="GET /v1/workspaces/{id}/warehouses/{id}/settings/sqlAudit",
        keywords=("audit", "logging", "warehouse", "sql", "retention", "trail"),
    ),
    KbField(
        key="warehouse_security",
        meaning_description="warehouse row-level security policies",
        path="warehouse_security",
        resource="WAREHOUSE",
        endpoint="SQL sys.security_policies",
        keywords=("row", "level", "security", "rls", "warehouse", "policy", "predicate"),
    ),
    KbField(
        key="warehouse_options",
        meaning_description="warehouse automatic statistics database options",
        path="warehouse_options",
        resource="WAREHOUSE",
        endpoint="SQL sys.databases",
        keywords=("statistics", "stats", "auto", "warehouse", "database", "option", "performance"),
    ),
    KbField(
        key="spark_settings",
        meaning_description="workspace default Spark runtime version and environment",
        path="spark_settings",
        resource="SPARK",
        endpoint="GET /v1/workspaces/{id}/spark/settings",
        keywords=("spark", "runtime", "environment", "notebook", "compute", "version"),
    ),
    KbField(
        key="lakehouse_tables",
        meaning_description="lakehouse Delta table files size and layout summary",
        path="lakehouse_tables_files",
        resource="LAKEHOUSE",
        endpoint="GET /v1/workspaces/{id}/lakehouses/{id}/tables",
        keywords=("lakehouse", "table", "delta", "parquet", "file", "size", "layout", "onelake"),
    ),
    KbField(
        key="shortcuts",
        meaning_description="onelake shortcuts referencing external or internal data",
        path="shortcuts",
        resource="SHORTCUT",
        endpoint="GET /v1/workspaces/{id}/items/{id}/shortcuts",
        keywords=("shortcut", "onelake", "external", "reference", "adls", "s3"),
    ),
    KbField(
        key="reports_binding",
        meaning_description="report to semantic model bindings for dataset reuse",
        path="reports",
        resource="REPORT",
        endpoint="GET /v1/workspaces/{id}/reports",
        keywords=("report", "binding", "dataset", "reuse", "thin", "shared", "model"),
    ),
    KbField(
        key="activators",
        meaning_description="data activator reflex rules and triggers",
        path="activators",
        resource="REFLEX",
        endpoint="GET /v1/workspaces/{id}/reflexes/{id}",
        keywords=("activator", "reflex", "rule", "trigger", "alert", "action"),
    ),
    KbField(
        key="capacity",
        meaning_description="the capacity the workspace is assigned to",
        path="capacity_id",
        resource="CAPACITY",
        endpoint="GET /v1/workspaces/{id}",
        keywords=("capacity", "sku", "fabric", "assignment", "premium"),
        validator=lambda v: bool(v),
    ),
)


def field_value(kb: dict, path: str) -> Any:
    """The value at ``path`` in the snapshot, or :data:`MISSING`.

    Looks at the top level first, then one level down (a ``{workspace_id: ctx}``
    map), returning the first workspace that carries the field.
    """
    value = _resolve(kb, path)
    if value is not MISSING:
        return value
    for nested in kb.values():
        if isinstance(nested, dict):
            value = _resolve(nested, path)
            if value is not MISSING:
                return value
    return MISSING


def _resolve(kb: dict, path: str) -> Any:
    cur: Any = kb
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return MISSING
    return cur


__all__ = ["KbField", "KB_FIELD_CATALOG", "MISSING", "field_value"]
