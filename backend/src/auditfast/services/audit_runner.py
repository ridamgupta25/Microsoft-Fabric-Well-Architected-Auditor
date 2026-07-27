"""Background execution of audits.

An audit against a real tenant issues many sequential HTTP calls and can take
minutes. Three consequences shape this module:

1. **The API must not block.** ``submit()`` records the job, schedules the work,
   and returns an id immediately. Clients poll rather than holding a connection.
2. **The event loop must not block.** :func:`audit_service.run_audit` is
   synchronous and I/O-bound, so it runs in a worker thread via
   :func:`asyncio.to_thread`. Calling it directly from an async handler would
   stall every other request in the process.
3. **Concurrency must be bounded.** A semaphore caps simultaneous audits so a
   burst degrades into a queue instead of exhausting threads and Fabric rate
   limits.

Swapping this for a distributed queue (Celery, Azure Service Bus) later means
reimplementing :meth:`AuditRunner.submit` only — the API and services are
unaffected, because they already treat execution as fire-and-poll.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ..config.logging import correlation_id, get_logger
from ..database.models import AuditJob
from ..database.repositories.base import AuditJobRepository
from ..schemas.audit import JobStatus
from . import audit_service

logger = get_logger(__name__)

#: How many audits may execute at once in this process.
DEFAULT_MAX_CONCURRENT_AUDITS = 4


class AuditRunner:
    """Submits audits, tracks their state, and serves their history."""

    def __init__(
        self,
        repository: AuditJobRepository,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_AUDITS,
    ) -> None:
        self._repository = repository
        self._semaphore = asyncio.Semaphore(max_concurrent)
        #: Strong references to in-flight tasks. Without these, asyncio may
        #: garbage-collect a running task mid-audit.
        self._tasks: set[asyncio.Task] = set()

    # -- submission -----------------------------------------------------------
    async def submit(
        self,
        *,
        project_path: str,
        pillars: list[str] | None = None,
        workspaces: list[dict] | None = None,
        out_dir: str | None = None,
        token: str | None = None,
        organization_id: str | None = None,
    ) -> AuditJob:
        """Accept an audit and start it in the background."""
        job = AuditJob(
            id=uuid.uuid4().hex[:16],
            status=JobStatus.QUEUED,
            organization_id=organization_id,
            request={
                "project": project_path,
                "pillars": pillars or [],
                "workspaces": workspaces or [],
            },
        )
        await self._repository.add(job)

        task = asyncio.create_task(
            self._execute(
                job,
                project_path=project_path,
                pillars=pillars,
                workspaces=workspaces,
                out_dir=out_dir,
                token=token,
                parent_correlation_id=correlation_id.get(),
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _execute(
        self,
        job: AuditJob,
        *,
        project_path: str,
        pillars: list[str] | None,
        workspaces: list[dict] | None,
        out_dir: str | None,
        token: str | None,
        parent_correlation_id: str = "-",
    ) -> None:
        """Run one audit to completion, recording success or failure."""
        # Background tasks get a fresh context, so carry the request's id across
        # to keep the audit's log lines traceable to whoever asked for it.
        correlation_id.set(parent_correlation_id)

        async with self._semaphore:
            job.mark_running()
            await self._repository.update(job)
            logger.info("audit started", extra={"audit_id": job.id})

            try:
                run = await asyncio.to_thread(
                    audit_service.run_audit,
                    project_path,
                    pillars,
                    workspaces,
                    out_dir,
                    token,
                )
                report: dict[str, Any] = audit_service.to_json(run)
                report["audit_id"] = job.id
                job.mark_succeeded(report)
                logger.info(
                    "audit finished",
                    extra={
                        "audit_id": job.id,
                        "overall": report.get("overall"),
                        "duration_seconds": job.duration_seconds,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - failure must be recorded, not raised
                job.mark_failed(str(exc))
                logger.exception("audit failed", extra={"audit_id": job.id})
            finally:
                await self._repository.update(job)

    # -- reading --------------------------------------------------------------
    async def get(self, job_id: str, organization_id: str | None = None) -> AuditJob | None:
        return await self._repository.get(job_id, organization_id)

    async def history(
        self,
        limit: int = 25,
        offset: int = 0,
        organization_id: str | None = None,
    ) -> tuple[list[AuditJob], int]:
        return await self._repository.list(limit, offset, organization_id)

    async def shutdown(self) -> None:
        """Wait for in-flight audits during application shutdown."""
        if not self._tasks:
            return
        logger.info("waiting for %d in-flight audit(s)", len(self._tasks))
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
