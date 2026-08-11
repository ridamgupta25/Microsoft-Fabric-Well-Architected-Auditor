"""Backward-compatible exports for provider failure types."""
from __future__ import annotations

from auditfast.core.errors import ProviderError, WorkspaceAccessError

__all__ = ["ProviderError", "WorkspaceAccessError"]
