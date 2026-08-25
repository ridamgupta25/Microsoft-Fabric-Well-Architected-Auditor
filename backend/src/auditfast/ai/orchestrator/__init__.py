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

from ...config.settings import get_settings
from .ai_config import AiConfig


def is_enabled(ai: AiConfig | None = None) -> bool:
    """True when AI is usable — via a per-request ``ai`` config or ``settings``.

    A supplied ``AiConfig`` (a user-provided key) takes precedence; when it is
    ``None`` this falls back to the process-wide ``settings`` exactly as before.
    """
    if ai is not None:
        return ai.is_configured()
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
    system: str, user: str, *, max_tokens: int = 700, ai: AiConfig | None = None
) -> str | None:
    """Single chat completion, or ``None`` if AI is off or anything fails.

    Uses the per-request ``ai`` config when supplied (the user's own key), else
    the process-wide ``settings``. Deliberately swallows every error into
    ``None``: a model outage or a missing dependency must degrade to the
    deterministic fallback rather than break the request.
    """
    # Call the no-arg form when there is no per-request config so existing
    # zero-arg test doubles for ``is_enabled`` keep working.
    if not (is_enabled(ai) if ai is not None else is_enabled()):
        return None
    try:  # pragma: no cover - exercised only when a live model is configured
        client, model = _client_for(ai)
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


def _client_for(ai: AiConfig | None):  # pragma: no cover - needs a live SDK/model
    """Build the OpenAI/Azure client + model name from ``ai`` or ``settings``."""
    if ai is not None:
        if ai.provider == "openai":
            from openai import OpenAI

            return OpenAI(base_url=ai.base_url, api_key=ai.api_key), ai.model
        from openai import AzureOpenAI

        return (
            AzureOpenAI(
                azure_endpoint=ai.endpoint,  # type: ignore[arg-type]
                api_key=ai.api_key,
                api_version="2024-06-01",
            ),
            ai.deployment,
        )
    settings = get_settings()
    if settings.ai_provider == "openai":
        from openai import OpenAI

        return (
            OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key),
            settings.openai_model,
        )
    from openai import AzureOpenAI

    return (
        AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,  # type: ignore[arg-type]
            api_version="2024-06-01",
        ),
        settings.azure_openai_deployment,
    )



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
