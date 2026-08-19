"""Shared builders for check tests.

**Why this exists.** Before it, ``_ctx`` was defined in fourteen test files with
four different signatures - notebook-scoped in one, pipeline-scoped in another,
workspace-scoped in a third - and ``_nb``, ``_table`` and ``_pipe`` were
duplicated almost as widely. That made the files impossible to merge (the same
name meant different things) and impossible to read (you had to scroll to the
top of each file to learn what ``_ctx`` was there).

Every builder here is named for the *scope* it builds, so a test reads as what
it does: ``notebook_ctx(code)``, ``pipeline_ctx(activity, ...)``,
``workspace_ctx(tables=...)``. Nothing here touches the engine or the registry -
these are plain constructors for the frozen domain objects, so a test that uses
them exercises the real ``CheckContext`` a crawl would produce.
"""
from __future__ import annotations

from typing import Any

from auditfast.core.models import CheckContext, WorkspaceContext

# ---------------------------------------------------------------------------
# object builders - the raw definitions a provider would have fetched
# ---------------------------------------------------------------------------


def notebook(code: str = "", metadata: dict | None = None) -> dict:
    """A notebook definition holding one code cell."""
    return {
        "cells": [{"cell_type": "code", "source": code}],
        "metadata": metadata or {},
    }


def pipeline(*activities: dict, **properties: Any) -> dict:
    """A pipeline definition wrapping ``activities``."""
    return {"properties": {"activities": list(activities), **properties}}


def activity(name: str, activity_type: str = "Copy", **type_properties: Any) -> dict:
    """One pipeline activity, with anything extra folded into typeProperties."""
    return {
        "name": name,
        "type": activity_type,
        "dependsOn": [],
        "typeProperties": type_properties,
    }


def script_activity(name: str, sql: str) -> dict:
    """A Script activity carrying inline T-SQL - where warehouse load logic lives."""
    return {
        "name": name,
        "type": "Script",
        "dependsOn": [],
        "typeProperties": {"scripts": [{"text": sql}]},
    }


def foreach(name: str, *children: dict) -> dict:
    """A ForEach container - the commonest place a check forgets to look."""
    return {
        "name": name,
        "type": "ForEach",
        "dependsOn": [],
        "typeProperties": {"isSequential": False, "activities": list(children)},
    }


def table(*columns: tuple[str, str], store: str = "", kind: str = "",
          table_type: str = "Managed", fmt: str = "Delta") -> dict:
    """A table schema entry: ``("name", "type")`` pairs plus the owning store."""
    entry: dict[str, Any] = {
        "type": table_type,
        "format": fmt,
        "columns": [{"name": name, "type": column_type}
                    for name, column_type in columns],
    }
    if store:
        entry["store"] = store
    if kind:
        entry["store_kind"] = kind
    return entry


# ---------------------------------------------------------------------------
# context builders - named for the scope the check under test is registered for
# ---------------------------------------------------------------------------


def notebook_ctx(code: str = "", *, settings: dict | None = None,
                 unavailable: set | None = None, name: str = "nb",
                 workspace: WorkspaceContext | None = None,
                 metadata: dict | None = None) -> CheckContext:
    """A notebook-scoped context: ``ctx.obj`` is the notebook definition."""
    return CheckContext(
        workspace=workspace or WorkspaceContext(id="w", unavailable=unavailable or set()),
        settings=settings or {},
        obj_name=name,
        obj=notebook(code, metadata),
    )


def pipeline_ctx(*activities: dict, settings: dict | None = None,
                 unavailable: set | None = None, name: str = "PL",
                 workspace: WorkspaceContext | None = None) -> CheckContext:
    """A pipeline-scoped context: ``ctx.obj`` is the pipeline definition."""
    return CheckContext(
        workspace=workspace or WorkspaceContext(id="w", unavailable=unavailable or set()),
        settings=settings or {},
        obj_name=name,
        obj=pipeline(*activities),
    )


def definition_ctx(definition: dict, *, settings: dict | None = None,
                   unavailable: set | None = None, name: str = "PL",
                   workspace: WorkspaceContext | None = None) -> CheckContext:
    """A pipeline-scoped context for an already-built definition.

    Use when the test needs a shape ``pipeline()`` does not build - a nested
    container, or properties alongside the activity list.
    """
    return CheckContext(
        workspace=workspace or WorkspaceContext(id="w", unavailable=unavailable or set()),
        settings=settings or {},
        obj_name=name,
        obj=definition,
    )


def workspace_ctx(*, settings: dict | None = None, **workspace_fields: Any) -> CheckContext:
    """A workspace-scoped context: ``ctx.obj`` is the workspace itself.

    ``workspace_fields`` are passed straight to :class:`WorkspaceContext`, so a
    test says what the crawl found (``tables=...``, ``items=...``,
    ``semantic_models=...``) rather than assembling the object by hand.
    """
    workspace_fields.setdefault("id", "w")
    workspace = WorkspaceContext(**workspace_fields)
    return CheckContext(
        workspace=workspace,
        settings=settings or {},
        obj_name=workspace.display_name or "w",
        obj=workspace,
    )


def tables_ctx(**tables: dict) -> CheckContext:
    """A workspace-scoped context holding only ``{table name: schema}``."""
    return workspace_ctx(tables=tables)
