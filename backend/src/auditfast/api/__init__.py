"""The HTTP API layer.

Routers are thin: they validate input, call a service, and shape the response.
No auditing logic lives here — that is in :mod:`auditfast.services` and
:mod:`auditfast.core`, which is what lets the CLI and the MCP server produce
identical results without duplicating anything.
"""
from .v1 import router as v1_router

__all__ = ["v1_router"]
