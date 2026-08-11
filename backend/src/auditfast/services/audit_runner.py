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
from functools import partial
from typing import Any

from ..config.logging import correlation_id, get_logger
from ..database.models import AuditJob
from ..database.repositories.base import AuditJobRepository
from ..schemas.audit import JobStatus
from . import audit_service, questionnaire_service

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
        auth_session: str | None = None,
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
            questionnaire=questionnaire_service.build_questionnaire(pillars, workspaces),
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
                auth_session=auth_session,
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
        auth_session: str | None = None,
        parent_correlation_id: str = "-",
    ) -> None:
        """Run one audit to completion, recording success or failure."""
        # Background tasks get a fresh context, so carry the request's id across
        # to keep the audit's log lines traceable to whoever asked for it.
        correlation_id.set(parent_correlation_id)
        from . import auth_service
        token_refresher = auth_service.make_token_refresher(auth_session)
        powerbi_token = auth_service.powerbi_token_for(auth_session)
        sql_token = auth_service.sql_token_for(auth_session)
        onelake_token = auth_service.onelake_token_for(auth_session)

        async with self._semaphore:
            job.mark_running()
            await self._repository.update(job)
            logger.info("audit started", extra={"audit_id": job.id})

            def _on_progress(partial_report: dict[str, Any]) -> None:
                # Store the partial report as each workspace lands. The in-memory
                # store holds jobs by reference, so a concurrent poll sees it at
                # once; the job stays RUNNING until the whole run finishes.
                partial_report["audit_id"] = job.id
                job.report = partial_report

            try:
                run = await asyncio.to_thread(
                    audit_service.run_audit,
                    project_path,
                    pillars,
                    workspaces,
                    out_dir,
                    token,
                    on_progress=_on_progress,
                    token_refresher=token_refresher,
                    powerbi_token=powerbi_token,
                    sql_token=sql_token,
                    onelake_token=onelake_token,
                )
                report: dict[str, Any] = audit_service.to_json(run)
                report["audit_id"] = job.id
                report = self._merge_answers(job, report)
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

        # The report was served from the knowledge base — kick a background live
        # crawl to refresh the KB and replace the report in place, so the user
        # sees results at once and fresh numbers land shortly after.
        if (report := job.report) and report.get("kb", {}).get("served_from_cache"):
            refresh_task = asyncio.create_task(
                self._refresh_in_background(
                    job,
                    project_path=project_path,
                    pillars=pillars,
                    workspaces=workspaces,
                    out_dir=out_dir,
                    token=token,
                    auth_session=auth_session,
                    parent_correlation_id=parent_correlation_id,
                )
            )
            self._tasks.add(refresh_task)
            refresh_task.add_done_callback(self._tasks.discard)

    async def _refresh_in_background(
        self,
        job: AuditJob,
        *,
        project_path: str,
        pillars: list[str] | None,
        workspaces: list[dict] | None,
        out_dir: str | None,
        token: str | None,
        auth_session: str | None = None,
        parent_correlation_id: str = "-",
    ) -> None:
        """Re-crawl the tenant live, rebuild the KB, and update the report.

        Runs after a cache-served audit has already been returned. The job stays
        succeeded; only its ``report`` is replaced, so a client that re-reads the
        report sees the freshened numbers.
        """
        correlation_id.set(parent_correlation_id)
        from . import auth_service
        token_refresher = auth_service.make_token_refresher(auth_session)
        powerbi_token = auth_service.powerbi_token_for(auth_session)
        sql_token = auth_service.sql_token_for(auth_session)
        onelake_token = auth_service.onelake_token_for(auth_session)
        async with self._semaphore:
            try:
                run = await asyncio.to_thread(
                    partial(
                        audit_service.run_audit,
                        project_path,
                        pillars,
                        workspaces,
                        out_dir,
                        token,
                        refresh=True,
                        token_refresher=token_refresher,
                        powerbi_token=powerbi_token,
                        sql_token=sql_token,
                        onelake_token=onelake_token,
                    )
                )
                report = audit_service.to_json(run)
                report["audit_id"] = job.id
                report = self._merge_answers(job, report)
                job.report = report
                await self._repository.update(job)
                logger.info(
                    "audit knowledge base refreshed",
                    extra={"audit_id": job.id, "overall": report.get("overall")},
                )
            except Exception:  # noqa: BLE001 - a failed refresh must not surface
                logger.exception("audit KB refresh failed", extra={"audit_id": job.id})

    # -- interactive questionnaire -------------------------------------------
    async def submit_answers(
        self,
        job_id: str,
        answers: dict[str, str],
        organization_id: str | None = None,
    ) -> AuditJob | None:
        """Record the reviewer's questionnaire answers and score them in.

        Idempotent: only ids that belong to this run's questionnaire are kept,
        and the merge strips any prior interactive results first, so re-submitting
        (or a later KB refresh) always lands the same numbers. If the report has
        not been produced yet, the answers are stored and applied when the audit
        finishes.
        """
        job = await self._repository.get(job_id, organization_id)
        if job is None:
            return None

        valid_ids = {q.get("id") for q in job.questionnaire}
        job.answers = {k: v for k, v in (answers or {}).items() if k in valid_ids}
        job.answers_submitted = True
        if job.report is not None:
            job.report = self._merge_answers(job, job.report)
        await self._repository.update(job)
        logger.info(
            "audit answers recorded",
            extra={"audit_id": job.id, "answers": len(job.answers)},
        )
        return job

    def _merge_answers(self, job: AuditJob, report: dict[str, Any]) -> dict[str, Any]:
        """Fold the reviewer's self-assessed answers into a report."""
        if not job.answers:
            return report
        question_ids = [q["id"] for q in job.questionnaire if q.get("id")]
        return questionnaire_service.merge_answers_into_report(
            report, job.answers, question_ids
        )

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
