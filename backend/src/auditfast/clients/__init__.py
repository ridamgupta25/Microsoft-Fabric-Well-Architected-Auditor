"""Read-only data providers.

A provider turns an external system into the normalized
:class:`~auditfast.domain.models.WorkspaceContext` the engine consumes. Keeping
them behind one small interface — separate from the pure domain — means a new
source (SQL endpoint, XMLA, Azure DevOps) can be added without the check library,
the engine, or the reports changing at all.

* :class:`MockProvider` — offline tenant fixture; powers the tests and the demo.
* :class:`LiveFabricProvider` — Fabric REST, read-only, delegated OAuth2 token.
"""
from .base import ALL_RESOURCES, Provider
from .errors import ProviderError, WorkspaceAccessError
from .live import LiveFabricProvider
from .mock import MockProvider

__all__ = [
    "ALL_RESOURCES",
    "LiveFabricProvider",
    "MockProvider",
    "Provider",
    "ProviderError",
    "WorkspaceAccessError",
]
