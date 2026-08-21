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

    # -- knowledge-base cache -------------------------------------------------
    # Audits are served from an on-disk snapshot of each workspace (the KB) so a
    # run does not re-crawl Fabric every time. The live API is called only on a
    # cache miss or once a snapshot ages past the hard TTL.
    cache_enabled: bool = Field(
        default=True, description="Serve audits from the on-disk workspace knowledge base."
    )
    cache_dir: str = "kb-cache"
    cache_ttl_seconds: float = Field(
        default=86_400.0,
        description="Hard staleness: a snapshot older than this is re-crawled live.",
    )
    cache_soft_seconds: float = Field(
        default=3_600.0,
        description="Soft staleness: older snapshots are served at once, then refreshed in the background.",
    )
    cache_background_refresh: bool = True

    # -- knowledge-base archive ----------------------------------------------
    # A permanent, timestamped history of every crawled workspace, separate from
    # the single-file cache above. Each audit writes a new dated folder, so the
    # full crawl history is kept on disk rather than overwritten.
    kb_archive_enabled: bool = Field(
        default=True, description="Write a timestamped KB snapshot of each crawl to disk."
    )
    kb_archive_dir: str = Field(
        default="Fabric workspace kb",
        description="Root folder for the permanent, per-run KB archive.",
    )

    # -- SQL analytics endpoint ------------------------------------------------
    # Column schemas and Warehouse security policies are not in the Fabric REST
    # API; they are only readable over TDS (port 1433) against the SQL analytics
    # endpoint Fabric provisions per Lakehouse/Warehouse. The endpoint address is
    # discovered over REST, so nothing is ever asked of the user - but the port
    # must be open outbound and an ODBC driver must be installed. Disable this to
    # skip the SQL reads entirely; column-level checks then report N/A exactly as
    # they did before the endpoint was wired in.
    sql_endpoint_enabled: bool = Field(
        default=True,
        description="Read column schemas and Warehouse RLS over the SQL analytics endpoint.",
    )

    # -- CORS -----------------------------------------------------------------
    # The React dev server runs on a different origin, so the API must allow it
    # explicitly. In production this should be the deployed frontend origin only.
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Origins permitted to call the API.",
    )

    # -- redirect sign-in (Authorization Code flow) ---------------------------
    # A hosted deployment signs remote users in with the standard browser
    # redirect ("Sign in with Microsoft"): the user authenticates on Microsoft's
    # page in *their own* browser and is redirected back. This needs an Entra app
    # registration (the built-in Azure CLI client cannot own a custom redirect
    # URI). Set the client/tenant id to enable it; leave unset to keep only the
    # device-code / local flows. The token is still acquired and kept server-side.
    auth_client_id: str | None = Field(
        default=None, description="Entra app (client) id for redirect sign-in."
    )
    auth_tenant_id: str | None = Field(
        default=None, description="Entra tenant id (a GUID, or 'organizations')."
    )
    auth_client_secret: str | None = Field(
        default=None,
        description="Client secret for the app. Server-side only; never sent to the browser.",
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

    # -- AI (optional; off by default) ----------------------------------------
    # Advisory checks can be re-judged by an LLM. Two providers are supported:
    #   * ``azure``  — Azure OpenAI (endpoint + deployment; key via AZURE_OPENAI_API_KEY)
    #   * ``openai`` — any OpenAI-compatible gateway (MAQ AI, GitHub Models,
    #     OpenAI.com, Ollama, ...): base URL + model + key.
    ai_enabled: bool = False
    ai_provider: str = Field(default="azure", description="azure | openai")
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    openai_base_url: str | None = Field(
        default=None, description="OpenAI-compatible gateway base URL, e.g. https://.../v1"
    )
    openai_api_key: str | None = None
    openai_model: str | None = Field(default=None, description="Model/deployment name to call.")

    # -- custom-checks guardrail (Node 1) -------------------------------------
    # Upper bound on a user-submitted plain-English check, enforced first in the
    # guardrail as a denial-of-service / unbounded-consumption guard.
    guardrail_max_prompt_chars: int = Field(
        default=2_000,
        description="Reject a custom-check prompt longer than this (Node 1 ValidLength).",
    )

    # -- custom-checks semantic router (Node 2) -------------------------------
    # Meaning-based matching is model-specific, so the embedding model is pinned
    # and the thresholds are tuned to it. All optional: with AI off the router
    # uses only the always-on deterministic matcher.
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Pinned local embedding model (FastEmbed). Changing it invalidates the index.",
    )
    router_reuse_threshold: float = Field(
        default=0.45,
        description="Stage 1 deterministic confidence at/above which a check is a duplicate.",
    )
    router_retrieve_threshold: float = Field(
        default=0.70,
        description="Stage 2 cosine floor to gather semantic candidates for the critic.",
    )
    router_semantic_threshold: float = Field(
        default=0.85,
        description="Stage 2 cosine at/above which a candidate is a duplicate when no critic runs.",
    )
    router_top_k: int = Field(
        default=5, description="How many semantic candidates to retrieve for the intent critic."
    )

    # -- custom-checks KB identifier (Node 3a) --------------------------------
    kb_identifier_min_confidence: float = Field(
        default=0.30,
        description="Below this the identified KB field is flagged low-confidence (best guess).",
    )

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @property
    def redirect_sign_in_enabled(self) -> bool:
        """True when the redirect Authorization Code flow is configured."""
        return bool(self.auth_client_id and self.auth_tenant_id)

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
