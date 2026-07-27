"""FastAPI application factory.

The API is **JSON only** — it renders no HTML and knows nothing about the
frontend. The React app is a separate deployable that talks to it over REST,
which is what allows the two to be scaled, cached, and released independently.

``create_app()`` is a factory rather than a module-level singleton so tests,
the CLI, and a production ASGI server each build an app with their own settings.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import __version__
from .api.errors import register_exception_handlers
from .api.middleware import AccessLogMiddleware, CorrelationIdMiddleware
from .api.v1 import router as v1_router
from .config.logging import configure_logging, get_logger
from .config.settings import Settings, get_settings
from .database.repositories.memory import InMemoryAuditJobRepository
from .services.audit_runner import AuditRunner

logger = get_logger(__name__)

DESCRIPTION = """
Rule-based auditing for Microsoft Fabric workspaces against the Well-Architected
pillars.

**Deterministic.** Every check is a fixed rule with a fixed threshold and
pre-written remediation, so the same input always produces the same score.

**Read-only.** The service never writes to Fabric.

### How an audit works
1. `POST /api/v1/audit` — submit; returns an `audit_id` immediately.
2. `GET /api/v1/audit/{audit_id}` — poll until `succeeded` or `failed`.
3. `GET /api/v1/reports/{audit_id}` — read the scorecard.

Audits run in the background because a tenant-wide run can take minutes.
"""

TAGS_METADATA = [
    {"name": "health", "description": "Liveness, readiness, and service metadata."},
    {"name": "authentication", "description": "Read-only Microsoft Entra sign-in."},
    {"name": "workspaces", "description": "Discover workspaces available to audit."},
    {"name": "catalog", "description": "The rule library — answers without running anything."},
    {"name": "audit", "description": "Submit audits and track their progress."},
    {"name": "reports", "description": "Read scorecards and download report files."},
    {"name": "recommendations", "description": "Remediation guidance for findings."},
    {"name": "history", "description": "Past audit runs."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build shared resources on startup, drain them on shutdown."""
    settings: Settings = get_settings()

    repository = InMemoryAuditJobRepository()
    app.state.audit_repository = repository
    app.state.audit_runner = AuditRunner(repository)
    app.state.settings = settings

    settings.output_path.mkdir(parents=True, exist_ok=True)

    from .core.checks.registry import REGISTRY

    logger.info(
        "application started",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "checks_registered": len(REGISTRY),
        },
    )
    try:
        yield
    finally:
        # Let in-flight audits finish rather than stranding clients mid-poll.
        await app.state.audit_runner.shutdown()
        logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the ASGI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "AuditFAST"},
    )

    # Order matters: correlation ids must be bound before anything logs.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # The frontend is a separate origin, so CORS is required rather than
    # optional. Credentials stay off: the client holds an opaque session id in
    # the request body, never a cookie.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-Id", "X-Response-Time-ms"],
    )

    register_exception_handlers(app)
    app.include_router(v1_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        """Service metadata. Deliberately JSON — this API serves no HTML."""
        return {
            "service": settings.app_name,
            "version": __version__,
            "environment": settings.environment,
            "docs": "/docs",
            "api": settings.api_v1_prefix,
        }

    return app


#: Module-level app for ASGI servers: ``uvicorn auditfast.main:app``.
app = create_app()
