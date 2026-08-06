"""Shared helpers for pipeline-definition checks.

Named with a leading underscore so the package auto-loader (which imports every
``automated.py`` / ``manual.py``) skips it: it carries no checks of its own, only
constants the pipeline check modules import.
"""
from __future__ import annotations

from auditfast.core.enums import Layer

#: Layers whose workspaces are expected to hold orchestration.
PIPELINE_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)


def activities(definition: dict) -> list[dict]:
    """The activity list of a pipeline definition, tolerating a missing shape."""
    return (definition.get("properties") or {}).get("activities") or []


def script_sql(definition: dict) -> str:
    """Every line of T-SQL a pipeline runs through its Script activities.

    Warehouse load logic frequently lives here rather than in a notebook — a
    Script activity carries its statements inline, so the SQL *is* readable even
    though stored procedures inside the Warehouse are not. Statements are joined
    with newlines so a caller can pattern-match across the whole pipeline.

    Handles both shapes Fabric emits: ``scripts: [{"text": ...}]`` and the older
    single ``script`` string.
    """
    parts: list[str] = []
    for activity in walk_activities(definition):
        if activity.get("type") != "Script":
            continue
        props = activity.get("typeProperties") or {}
        for entry in props.get("scripts") or []:
            if isinstance(entry, dict) and entry.get("text"):
                parts.append(str(entry["text"]))
        if isinstance(props.get("script"), str):
            parts.append(props["script"])
    return "\n".join(parts)


#: Keys (on an activity or its ``typeProperties``) that hold child activity lists.
_CHILD_ACTIVITY_KEYS = ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities")


def walk_activities(definition: dict) -> list[dict]:
    """Every activity in a pipeline, descending into container activities.

    ``If Condition`` / ``ForEach`` / ``Switch`` / ``Until`` nest their child
    activities under ``typeProperties`` — ``ifTrueActivities`` /
    ``ifFalseActivities`` for a condition, ``activities`` for a loop,
    ``defaultActivities`` plus ``cases[].activities`` for a switch. A check that
    asks "does *any* activity do X" must see those, not only the top level.
    """
    found: list[dict] = []

    def _visit(acts: list) -> None:
        for act in acts or []:
            if not isinstance(act, dict):
                continue
            found.append(act)
            for source in (act, act.get("typeProperties") or {}):
                if not isinstance(source, dict):
                    continue
                for key in _CHILD_ACTIVITY_KEYS:
                    child = source.get(key)
                    if isinstance(child, list):
                        _visit(child)
                for case in source.get("cases") or []:
                    if isinstance(case, dict):
                        _visit(case.get("activities") or [])

    _visit(activities(definition))
    return found
