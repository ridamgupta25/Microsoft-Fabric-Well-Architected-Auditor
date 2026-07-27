"""In-memory job store — the default when no database is configured.

Deliberately bounded and lock-guarded so it behaves correctly under concurrent
audits in a single process. It is **not** a production store: state dies with
the process and is not shared across replicas, so horizontal scaling requires
swapping in the SQL implementation. Nothing outside this file has to change when
that happens.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict

from ..models import AuditJob

#: Keep the process bounded; oldest completed jobs are evicted first.
DEFAULT_MAX_JOBS = 500


class InMemoryAuditJobRepository:
    """Satisfies :class:`AuditJobRepository` using an ordered dict."""

    def __init__(self, max_jobs: int = DEFAULT_MAX_JOBS) -> None:
        self._jobs: OrderedDict[str, AuditJob] = OrderedDict()
        self._max_jobs = max_jobs
        self._lock = asyncio.Lock()

    async def add(self, job: AuditJob) -> AuditJob:
        async with self._lock:
            self._jobs[job.id] = job
            self._evict()
        return job

    async def get(self, job_id: str, organization_id: str | None = None) -> AuditJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        if organization_id is not None and job.organization_id != organization_id:
            return None  # never leak another tenant's audit
        return job

    async def update(self, job: AuditJob) -> AuditJob:
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def list(
        self,
        limit: int = 25,
        offset: int = 0,
        organization_id: str | None = None,
    ) -> tuple[list[AuditJob], int]:
        async with self._lock:
            jobs = list(self._jobs.values())
        if organization_id is not None:
            jobs = [j for j in jobs if j.organization_id == organization_id]
        jobs.sort(key=lambda j: j.submitted_at, reverse=True)
        return jobs[offset: offset + limit], len(jobs)

    def _evict(self) -> None:
        """Drop the oldest finished jobs once the cap is exceeded.

        Running jobs are never evicted — losing one would strand a client that
        is still polling for its result.
        """
        while len(self._jobs) > self._max_jobs:
            for job_id, job in self._jobs.items():
                if job.finished_at is not None:
                    del self._jobs[job_id]
                    break
            else:
                return  # everything still in flight
