"""Optional Guardrails AI ``Guard`` for Node 1 (activated by the ``guardrails`` extra).

This module is imported **lazily** by :mod:`guardrails_agent` and only when AI is
enabled. Importing it fails fast (``ImportError``) on a base install because the
``guardrails`` package is absent - the agent catches that and stays on its
always-on deterministic regex floor. When the extra *is* installed, this builds
the Guard specified in ``local/Planning/Guardrail AI - Node.md`` (authoritative):

    ValidLength -> DetectJailbreak -> DetectPromptInjection ->
    FabricZeroWriteValidator (custom) -> DetectPII -> SecretsPresent ->
    RestrictToTopic

The Guard only ever *tightens* the deterministic verdict (fail-closed): the agent
consults it after the regex screen already passed, so a failure here escalates
PASS -> DROP and a pass changes nothing.

Read-only POST exception: the custom ``FabricZeroWriteValidator`` reuses the
agent's side-effect-aware write detector, so a read-only ``getDefinition``-style
POST is never treated as a write.
"""
from __future__ import annotations

import threading

from guardrails import Guard  # type: ignore[import-not-found]
from guardrails.hub import (  # type: ignore[import-not-found]
    DetectJailbreak,
    DetectPII,
    DetectPromptInjection,
    RestrictToTopic,
    SecretsPresent,
    ValidLength,
)

try:  # validator base moved across guardrails releases
    from guardrails.validator_base import (  # type: ignore[import-not-found]
        FailResult,
        PassResult,
        Validator,
        register_validator,
    )
except Exception:  # pragma: no cover - older layout
    from guardrails.validators import (  # type: ignore[import-not-found]
        FailResult,
        PassResult,
        Validator,
        register_validator,
    )

from ...config.settings import get_settings
from ..orchestrator.state import GuardrailVerdict
from .guardrails_agent import _has_write_intent

#: Topics a custom Fabric audit check is allowed to be about.
_VALID_TOPICS = ["data governance", "microsoft fabric", "azure", "auditing"]

#: Hub validator ``name`` -> the label surfaced on the ledger / to HITL.
_VALIDATOR_LABEL = {
    "valid-length": "ValidLength",
    "guardrails/valid_length": "ValidLength",
    "detect-jailbreak": "DetectJailbreak",
    "guardrails/detect_jailbreak": "DetectJailbreak",
    "detect-prompt-injection": "DetectPromptInjection",
    "guardrails/detect_prompt_injection": "DetectPromptInjection",
    "fabric/zero-write": "FabricZeroWriteValidator",
    "detect-pii": "DetectPII",
    "guardrails/detect_pii": "DetectPII",
    "secrets-present": "SecretsPresent",
    "guardrails/secrets_present": "SecretsPresent",
    "restrict-to-topic": "RestrictToTopic",
    "tryolabs/restricttotopic": "RestrictToTopic",
}


@register_validator(name="fabric/zero-write", data_type="string")
class FabricZeroWriteValidator(Validator):  # type: ignore[misc]
    """Block genuine create/update/delete intent, judged by side effect not verb."""

    def validate(self, value, metadata=None):  # noqa: ANN001, ARG002
        verb = _has_write_intent(str(value))
        if verb:
            return FailResult(
                error_message=(
                    f"Write/mutation intent detected ({verb!r}); "
                    "custom audit checks are read-only."
                )
            )
        return PassResult()


_GUARD: Guard | None = None
_GUARD_LOCK = threading.Lock()


def _build_guard() -> Guard:
    """Compose the ordered Guard. Built once and reused across requests."""
    max_chars = get_settings().guardrail_max_prompt_chars
    guard = Guard()
    guard.use_many(
        ValidLength(min=1, max=max_chars, on_fail="exception"),
        DetectJailbreak(on_fail="exception"),
        DetectPromptInjection(on_fail="exception"),
        FabricZeroWriteValidator(on_fail="exception"),
        DetectPII(on_fail="exception"),
        SecretsPresent(on_fail="exception"),
        RestrictToTopic(
            valid_topics=_VALID_TOPICS,
            disable_llm=True,
            on_fail="exception",
        ),
    )
    return guard


def _get_guard() -> Guard:
    global _GUARD
    if _GUARD is None:
        with _GUARD_LOCK:
            if _GUARD is None:
                _GUARD = _build_guard()
    return _GUARD


def _label_from_text(text: str) -> str:
    """Best-effort map of a raw failure message to a validator label."""
    low = text.lower()
    for needle, label in (
        ("length", "ValidLength"),
        ("jailbreak", "DetectJailbreak"),
        ("injection", "DetectPromptInjection"),
        ("write", "FabricZeroWriteValidator"),
        ("mutation", "FabricZeroWriteValidator"),
        ("pii", "DetectPII"),
        ("secret", "SecretsPresent"),
        ("topic", "RestrictToTopic"),
    ):
        if needle in low:
            return label
    return "GuardrailsAI"


def _first_failure(outcome) -> tuple[str, str]:  # noqa: ANN001
    """Pull (label, reason) from the first failing validator on an outcome."""
    summaries = getattr(outcome, "validation_summaries", None) or []
    for summary in summaries:
        name = getattr(summary, "validator_name", "") or ""
        reason = (
            getattr(summary, "failure_reason", None)
            or getattr(summary, "error_message", None)
            or "Guardrails AI validation failed."
        )
        label = _VALIDATOR_LABEL.get(name.lower(), _label_from_text(f"{name} {reason}"))
        return label, str(reason)[:300]
    return "GuardrailsAI", "Guardrails AI validation failed."


def run_guard(prompt: str) -> GuardrailVerdict:
    """Validate ``prompt`` through the Guard, mapped to a :class:`GuardrailVerdict`.

    Returns ``passed=True`` (layer ``"guardrails"``) when every validator clears,
    otherwise a drop verdict naming the first failing validator.
    """
    guard = _get_guard()
    try:
        outcome = guard.validate(prompt)
    except Exception as exc:  # noqa: BLE001 - on_fail="exception" path
        label = _label_from_text(str(exc))
        return GuardrailVerdict(
            passed=False,
            reason=str(exc)[:300] or "Guardrails AI validation failed.",
            matched_rule=label,
            failed_validator=label,
            layer="guardrails",
        )
    if getattr(outcome, "validation_passed", True):
        return GuardrailVerdict(passed=True, layer="guardrails")
    label, reason = _first_failure(outcome)
    return GuardrailVerdict(
        passed=False,
        reason=reason,
        matched_rule=label,
        failed_validator=label,
        layer="guardrails",
    )


__all__ = ["run_guard", "FabricZeroWriteValidator"]
