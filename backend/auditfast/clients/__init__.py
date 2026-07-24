"""Data-access clients (adapters).

Read-only clients that turn an external system into the normalized "workspace
context" the core engine consumes. Keeping them here - separate from the pure
domain in :mod:`auditfast.core` - means new sources (SQL endpoint, XMLA, DevOps)
can be added in one place without touching the rule engine.

* :mod:`.fabric_client` - Fabric REST (live) + offline fixture (mock).
"""
from .fabric_client import (
    FabricClient,
    LiveFabricClient,
    MockFabricClient,
    WorkspaceAccessError,
)

__all__ = [
    "FabricClient",
    "LiveFabricClient",
    "MockFabricClient",
    "WorkspaceAccessError",
]
