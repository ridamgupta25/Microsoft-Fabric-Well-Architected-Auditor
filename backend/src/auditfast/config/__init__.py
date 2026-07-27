"""Configuration and logging.

* :mod:`.settings` — environment-driven settings via pydantic-settings.
* :mod:`.logging`  — structured logging with per-request correlation ids.
"""
from .logging import configure_logging, correlation_id, get_logger
from .settings import Settings, get_settings

__all__ = [
    "Settings",
    "configure_logging",
    "correlation_id",
    "get_logger",
    "get_settings",
]
