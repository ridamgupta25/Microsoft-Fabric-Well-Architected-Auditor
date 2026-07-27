"""Repository implementations.

Import the Protocol from :mod:`.base`; pick a concrete class here.
"""
from .base import AuditJobRepository
from .memory import InMemoryAuditJobRepository

__all__ = ["AuditJobRepository", "InMemoryAuditJobRepository"]
