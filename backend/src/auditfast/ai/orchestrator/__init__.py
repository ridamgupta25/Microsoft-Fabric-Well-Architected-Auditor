"""Model routing and provider abstraction.

One interface over Azure OpenAI, Claude, and Gemini so the choice of vendor is a
configuration value rather than a code change. Owns retries, timeouts, token
budgets, and fallback between providers.

Only the Azure OpenAI path is wired today, and it is **strictly optional**:
:func:`is_enabled` returns ``False`` unless ``settings.ai_enabled`` is on *and* an
endpoint/deployment is configured, and every entry point returns ``None`` (never
raises) when disabled or when the ``ai`` extra is not installed. Callers treat a
``None`` as "no model available" and fall back to deterministic text, so the
intake pipeline works fully offline and a missing model can never surface as a
runtime error.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...config.settings import get_settings


@dataclass(frozen=True)
class Credentials:
    """One caller's model credentials, supplied per request.

    Exists so a user can bring their own key without it being written anywhere.
    Mutating the global settings would be the obvious shortcut and is the wrong
    one: settings are process-wide, so two concurrent requests would overwrite
    each other's key, and whichever landed last would bill the wrong account.
    Passing the credential down the call instead keeps it on the stack, alive
    only for the request that supplied it.

    Never log, persist, or echo one of these back to a client.
    """

    provider: str = "azure"
    api_key: str | None = None
    endpoint: str | None = None
    deployment: str | None = None
    base_url: str | None = None
    model: str | None = None

    def is_usable(self) -> bool:
        if not self.api_key:
            return False
        if self.provider == "openai":
            return bool(self.base_url and self.model)
        return bool(self.endpoint and self.deployment)

    def __repr__(self) -> str:  # pragma: no cover - guards accidental logging
        held = "set" if self.api_key else "unset"
        return f"Credentials(provider={self.provider!r}, api_key=<{held}>)"


def is_enabled() -> bool:
    """True only when AI is switched on *and* a provider is configured."""
    settings = get_settings()
    if not settings.ai_enabled:
        return False
    if settings.ai_provider == "openai":
        return bool(
            settings.openai_base_url
            and settings.openai_model
            and settings.openai_api_key
        )
    return bool(settings.azure_openai_endpoint and settings.azure_openai_deployment)


def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 700,
    credentials: Credentials | None = None,
) -> str | None:
    """Single chat completion, or ``None`` if AI is off or anything fails.

    ``credentials`` overrides the configured provider for this call only, which
    is how a signed-in user runs advisory judging with their own key.

    Deliberately swallows every error into ``None``: this is an advisory,
    best-effort enrichment, so a model outage or a missing dependency must
    degrade to the deterministic fallback rather than break the request.
    """
    if credentials is None and not is_enabled():
        return None
    if credentials is not None and not credentials.is_usable():
        return None
    settings = get_settings()
    provider = credentials.provider if credentials else settings.ai_provider
    try:  # pragma: no cover - exercised only when a live model is configured
        if provider == "openai":
            # Any OpenAI-compatible gateway (MAQ AI, GitHub Models, OpenAI.com, Ollama).
            from openai import OpenAI

            client = OpenAI(
                base_url=credentials.base_url if credentials else settings.openai_base_url,
                api_key=credentials.api_key if credentials else settings.openai_api_key,
            )
            model = credentials.model if credentials else settings.openai_model
        else:
            from openai import AzureOpenAI

            if credentials:
                client = AzureOpenAI(
                    azure_endpoint=credentials.endpoint,  # type: ignore[arg-type]
                    api_key=credentials.api_key,
                    api_version="2024-06-01",
                )
                model = credentials.deployment
            else:
                client = AzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,  # type: ignore[arg-type]
                    api_version="2024-06-01",
                )
                model = settings.azure_openai_deployment
        response = client.chat.completions.create(
            model=model,  # type: ignore[arg-type]
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip() or None
    except Exception:  # noqa: BLE001 - advisory path must never raise
        return None


_ADVISORY_SYSTEM = (
    "You are a Microsoft Fabric Well-Architected reviewer. Given a best-practice "
    "checklist point, write a concise, actionable assessment: what to verify, why "
    "it matters, and how to remediate. Do not invent scores. Keep it under 180 words."
)


def advisory_for_point(point: str, *, covered: bool, context: str = "") -> str | None:
    """An AI-authored, explicitly-unscored assessment of one checklist point.

    Returns ``None`` when AI is disabled so the caller uses deterministic text.
    """
    stance = (
        "This point is already assessed deterministically; add depth a reviewer "
        "would want."
        if covered
        else "This point is not yet a deterministic check; guide a manual review."
    )
    return complete(_ADVISORY_SYSTEM, f"{stance}\n\nCHECKLIST POINT:\n{point}\n\n{context}")
