"""Shared layer-role inference for workspace and group checks."""
from __future__ import annotations

from auditfast.core.enums import Layer
from auditfast.core.models import WorkspaceContext

_LAYER_NAME_HINTS: tuple[tuple[str, Layer], ...] = (
    ("dataprep", Layer.PREP),
    ("data_prep", Layer.PREP),
    ("datastore", Layer.STORAGE),
    ("data_store", Layer.STORAGE),
    ("storage", Layer.STORAGE),
    ("datalog", Layer.LOGS),
    ("data_log", Layer.LOGS),
    ("dataops", Layer.OPERATIONS),
    ("data_ops", Layer.OPERATIONS),
    ("report", Layer.REPORTING),
    ("semantic", Layer.REPORTING),
    ("consumption", Layer.REPORTING),
)


def effective_layer(workspace: WorkspaceContext) -> Layer:
    """Return the assigned layer, or infer one from an untagged workspace name."""
    if workspace.layer is not Layer.MIXED:
        return workspace.layer
    name = workspace.name.lower()
    for fragment, hinted in _LAYER_NAME_HINTS:
        if fragment in name:
            return hinted
    return Layer.MIXED
