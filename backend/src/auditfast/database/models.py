"""Persistence models.

These are plain dataclasses today and deliberately free of any ORM import. When
a database is introduced, the SQLAlchemy tables live here alongside them and the
repository implementations map between the two — the services never see either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..schemas.audit import JobStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class AuditJob:
    """One submitted audit, from acceptance through to its report.

    Audits can take minutes against a large tenant, so the API accepts the
    request, returns an id immediately, and records progress here. That is what
    lets many audits run concurrently without any client holding a connection
    open for the duration.
    """

    id: str
    mode: str = "mock"
    status: JobStatus = JobStatus.QUEUED
    submitted_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    request: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    error: str | None = None
    #: Reserved for multi-tenancy — every query will filter on this.
    organization_id: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def project_name(self) -> str | None:
        return (self.report or {}).get("project_name")

    @property
    def overall(self) -> float | None:
        return (self.report or {}).get("overall")

    @property
    def workspace_count(self) -> int:
        return len((self.report or {}).get("by_workspace", {}))

    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.started_at = _now()

    def mark_succeeded(self, report: dict[str, Any]) -> None:
        self.status = JobStatus.SUCCEEDED
        self.report = report
        self.finished_at = _now()

    def mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.error = error
        self.finished_at = _now()
