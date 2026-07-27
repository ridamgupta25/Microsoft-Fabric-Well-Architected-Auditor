"""Schemas shared across endpoints."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """The single error shape every endpoint returns on failure."""

    detail: str = Field(description="Human-readable message, safe to show a user.")
    code: str = Field(default="error", description="Stable machine-readable code.")
    correlation_id: str | None = Field(
        default=None, description="Ties this response to its server log lines."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": "Sign in first, then run a live audit.",
                "code": "not_authenticated",
                "correlation_id": "3f2a9c14",
            }
        }
    }


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"


class HealthResponse(BaseModel):
    """Liveness/readiness payload for probes and load balancers."""

    status: HealthStatus = HealthStatus.OK
    version: str
    environment: str
    checks_registered: int = Field(description="Size of the loaded rule library.")
    timestamp: datetime


class Page(BaseModel, Generic[T]):
    """A slice of a collection. Used wherever a list can grow unbounded."""

    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    """A simple acknowledgement."""

    message: str
    data: dict[str, Any] | None = None
