"""A per-request AI configuration (a user-supplied key + model).

Lets a caller run the AI steps with *their own* key for a single request, without
touching the process-wide ``settings``. The key lives only in this object for the
life of the request and is never logged, persisted, or returned. When no
``AiConfig`` is supplied, the orchestrator falls back to ``settings`` exactly as
before, so the deterministic-first, AI-optional behaviour is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class AiConfig:
    """One request's AI provider settings. ``api_key`` is never serialised."""

    provider: str  # "openai" | "azure"
    api_key: str = field(repr=False)  # kept out of repr so it can't leak in logs
    model: str = ""
    base_url: str | None = None      # openai-compatible gateways
    endpoint: str | None = None      # azure only
    deployment: str | None = None    # azure only

    def is_configured(self) -> bool:
        """True when this config has everything needed to call the provider."""
        if not self.api_key:
            return False
        if self.provider == "openai":
            return bool(self.base_url and self.model)
        if self.provider == "azure":
            return bool(self.endpoint and self.deployment)
        return False

    def redacted(self) -> dict[str, Any]:
        """A log-safe view: the key is masked, never shown."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "endpoint": self.endpoint,
            "deployment": self.deployment,
            "api_key": "***" if self.api_key else "",
        }

    def without_key(self) -> "AiConfig":
        return replace(self, api_key="")


__all__ = ["AiConfig"]
