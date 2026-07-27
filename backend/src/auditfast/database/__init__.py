"""Persistence layer.

Currently in-memory. The structure is here so that adding PostgreSQL means
adding a ``session.py`` and a SQL repository implementation — not reshaping the
services or the API.

* :mod:`.models`       — job/history models (plain dataclasses today, ORM later).
* :mod:`.repositories` — the storage interface plus its implementations.
"""
from .models import AuditJob
from .repositories import AuditJobRepository, InMemoryAuditJobRepository

__all__ = ["AuditJob", "AuditJobRepository", "InMemoryAuditJobRepository"]
