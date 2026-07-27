"""Read-only data providers.

A provider turns an external system into the normalized
:class:`~auditfast.domain.models.WorkspaceContext` the engine consumes. Keeping
it behind one small interface — separate from the pure domain — means a new
source (SQL endpoint, XMLA, Azure DevOps) can be added without the check
library, the engine, or the reports changing at all.

* :class:`LiveFabricProvider` — Fabric REST, read-only, delegated OAuth2 token.
  The only provider the application ships. Deterministic offline test data
  lives entirely inside ``tests/fixtures/`` and is never imported here — it is
  test infrastructure, not a product feature.
"""
from .base import ALL_RESOURCES, Provider
from .errors import ProviderError, WorkspaceAccessError
from .live import LiveFabricProvider

__all__ = [
    "ALL_RESOURCES",
    "LiveFabricProvider",
    "Provider",
    "ProviderError",
    "WorkspaceAccessError",
]
