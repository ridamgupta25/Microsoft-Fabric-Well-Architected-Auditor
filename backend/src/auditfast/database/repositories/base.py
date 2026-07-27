"""Repository interfaces.

Services depend on these Protocols, never on a concrete store. Swapping the
in-memory implementation for PostgreSQL is then a wiring change in one provider
function — no service, router, or test has to know.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import AuditJob


@runtime_checkable
class AuditJobRepository(Protocol):
    """Storage for submitted audits and their reports."""

    async def add(self, job: AuditJob) -> AuditJob:
        """Persist a newly submitted job."""
        ...

    async def get(self, job_id: str, organization_id: str | None = None) -> AuditJob | None:
        """Fetch one job, scoped to an organization when multi-tenant."""
        ...

    async def update(self, job: AuditJob) -> AuditJob:
        """Persist a change of status or result."""
        ...

    async def list(
        self,
        limit: int = 25,
        offset: int = 0,
        organization_id: str | None = None,
    ) -> tuple[list[AuditJob], int]:
        """Return a page of jobs, newest first, plus the total count."""
        ...
