"""Application settings, loaded from the environment.

Everything configurable lives here, read once at startup. Any value can be
overridden with an ``AUDITFAST_``-prefixed environment variable, which is what
makes the same image deployable to local, dev, and production without a code
change — a prerequisite for the container/Azure deployment target.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: backend/src/auditfast/config/settings.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime configuration. Immutable for the lifetime of the process."""

    model_config = SettingsConfigDict(
        env_prefix="AUDITFAST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- identity -------------------------------------------------------------
    app_name: str = "Fabric Well-Architected Auditor"
    api_v1_prefix: str = "/api/v1"
    environment: str = Field(default="local", description="local | dev | staging | prod")
    debug: bool = False

    # -- audit defaults -------------------------------------------------------
    default_project: str = "config/project.example.yaml"
    output_dir: str = "output"

    # -- CORS -----------------------------------------------------------------
    # The React dev server runs on a different origin, so the API must allow it
    # explicitly. In production this should be the deployed frontend origin only.
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Origins permitted to call the API.",
    )

    # -- logging --------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = Field(
        default=False,
        description="Emit structured JSON logs. Enable in any hosted environment.",
    )

    # -- persistence (not yet wired up) ---------------------------------------
    database_url: str | None = Field(
        default=None,
        description="Async SQLAlchemy URL. When unset, audit history is disabled.",
    )

    # -- AI (not yet wired up) ------------------------------------------------
    ai_enabled: bool = False
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    def resolve(self, value: str) -> Path:
        """Resolve a configured relative path against the backend root."""
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT / path

    @property
    def project_path(self) -> Path:
        return self.resolve(self.default_project)

    @property
    def output_path(self) -> Path:
        return self.resolve(self.output_dir)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — the FastAPI dependency for configuration."""
    return Settings()
