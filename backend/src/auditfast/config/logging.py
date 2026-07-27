"""Logging setup.

Plain text locally, single-line JSON everywhere else — hosted log collectors
(Azure Monitor, Container Apps) index structured records and mangle multi-line
ones. Every record carries the correlation id of the request that produced it,
so one audit's log lines can be pulled out of a busy multi-tenant stream.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

#: Correlation id for the in-flight request. A ContextVar (not a global) so
#: concurrent audits cannot read each other's value.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "taskName",
}


class CorrelationFilter(logging.Filter):
    """Attach the current correlation id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including any extra=... fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "correlation_id":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    """Install handlers on the root logger. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(
        JsonFormatter() if as_json
        else logging.Formatter("%(asctime)s %(levelname)-8s [%(correlation_id)s] %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
