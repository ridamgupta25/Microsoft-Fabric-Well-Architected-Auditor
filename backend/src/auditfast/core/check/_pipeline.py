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
