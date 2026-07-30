"""Application services — the orchestration layer.

Every adapter (REST API, CLI, MCP) enters here, which is what guarantees they
produce identical results. Nothing in this package imports a web framework.

* :mod:`.project`          — load and validate a project definition.
* :mod:`.audit_service`    — the one audit path: provider, engine, scoring, reports.
* :mod:`.audit_runner`     — background execution, concurrency limits, history.
* :mod:`.catalog_service`  — questions about the rule library; no I/O.
* :mod:`.auth_service`     — read-only Entra sign-in and the session store.
* :mod:`.fabriciq_service` — native, read-only Power BI FabricIQ tools.
"""
from . import (
    audit_service,
    auth_service,
    catalog_service,
    fabriciq_service,
    project,
)

__all__ = [
    "audit_service",
    "auth_service",
    "catalog_service",
    "fabriciq_service",
    "project",
]
