"""Node 1 - the Guardrails AI input safety gate.

The first node every plain-English custom check hits. It answers one question:
*is this prompt safe to turn into a real audit check?* A prompt that shows write
intent, a prompt-injection / jailbreak attempt, or that is oversized is dropped
here and never reaches an LLM or any metadata.

Design source: ``local/Planning/Guardrail AI - Node.md`` (authoritative).

Two paths, composed **fail-closed** (an optional layer can only escalate
PASS -> DROP, never the reverse):

1. **Deterministic screen (always on, zero deps).** A regex implementation of the
   same rules - length bound, prompt-injection / jailbreak, and write-intent with
   a verification-phrasing neutraliser so "ensure X *is disabled*" reads as a read,
   not a command. This alone enforces the zero-write policy, so the feature is safe
   on a base install with no optional extras.
2. **Guardrails AI ``Guard`` (optional).** When the ``guardrails`` extra is
   installed and AI is enabled, a Guard of Hub validators
   (``ValidLength`` -> ``DetectJailbreak`` -> ``DetectPromptInjection`` ->
   ``FabricZeroWriteValidator`` -> ``DetectPII`` -> ``SecretsPresent`` ->
   ``RestrictToTopic``) runs on top and may drop a prompt the regex let through.

Read-only POST exception: the guardrail judges *side effects*, not HTTP verbs, so a
read-only ``getDefinition``-style POST is never treated as a write.
"""
from __future__ import annotations

import re

from ...config.settings import get_settings
from ..orchestrator.state import CustomCheck, GuardrailVerdict, LifecycleStatus

# -- deterministic rule set ---------------------------------------------------

#: Base-form (imperative) verbs that mutate a Fabric resource. Word boundaries
#: mean the past participles that describe a desired *state* - "disabled",
#: "enabled", "deleted", "configured" - are NOT matched, which is the
#: verification-phrasing neutraliser: "ensure X is disabled" is a read.
_WRITE_VERB = re.compile(
    r"\b("
    r"create|delete|drop|remove|update|modify|alter|grant|revoke|insert|"
    r"rename|truncate|disable|enable|configure|deploy|provision|overwrite|"
    r"purge|destroy|reset|wipe"
    r")\b",
    re.IGNORECASE,
)

#: Cues that flip a write verb into a read assertion - "users *cannot* delete",
#: "*prevent* deletion". Looked for in the short window before the verb.
_NEGATION_CUE = re.compile(
    r"\b("
    r"cannot|can'?t|can not|unable to|not|never|no one|nobody|none|without|"
    r"prevent(?:ed|s)?|block(?:ed|s)?|restrict(?:ed|s|ion)?|disallow(?:ed|s)?|"
    r"deny|denied|prohibit(?:ed|s)?|forbid(?:den)?"
    r")\b",
    re.IGNORECASE,
)

#: A stative predicate right after a write-looking word marks it as a noun in a
#: verification clause - "soft-delete *is enabled*", "retention *is configured*" -
#: rather than an imperative command.
_STATE_AFTER = re.compile(
    r"^[\s\-]*(?:\w+\s+){0,2}(?:is|are|was|were|be|been|being)\b",
    re.IGNORECASE,
)

#: Prompt-injection / jailbreak signatures. Any hit is an immediate drop.
_INJECTION = re.compile(
    r"("
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above)\s+(?:instruction|prompt|rule)|"
    r"disregard\s+(?:all\s+|the\s+|any\s+)?(?:previous|prior|above|system|instruction)|"
    r"forget\s+(?:everything|all|the\s+above|your\s+instruction)|"
    r"system\s+prompt|"
    r"you\s+are\s+now|"
    r"act\s+as\s+(?:if\s+)?(?:a\s+|an\s+|you)|"
    r"developer\s+mode|"
    r"jailbreak|"
    r"do\s+anything\s+now|"
    r"\bdan\b\s+mode|"
    r"override\s+(?:your\s+|the\s+)?(?:instruction|rule|guardrail|safety|filter)|"
    r"bypass\s+(?:the\s+|your\s+)?(?:guardrail|safety|filter|restriction)|"
    r"reveal\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instruction)|"
    r"print\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instruction)|"
    r"new\s+instructions?\s*:|"
    r"pretend\s+(?:to\s+be|you\s+are)"
    r")",
    re.IGNORECASE,
)


def _has_write_intent(text: str) -> str:
    """The first un-negated write verb in ``text``, or ``""`` if none."""
    for match in _WRITE_VERB.finditer(text):
        window = text[max(0, match.start() - 40) : match.start()]
        if _NEGATION_CUE.search(window):
            continue  # "cannot delete", "prevent ... remove" -> a read assertion
        if _STATE_AFTER.match(text[match.end() : match.end() + 30]):
            continue  # "soft-delete is enabled" -> a state, not a command
        return match.group(0).lower()
    return ""


def _deterministic_screen(prompt: str) -> GuardrailVerdict:
    """The always-available regex gate. This is the zero-write floor."""
    text = prompt.strip()
    max_chars = get_settings().guardrail_max_prompt_chars

    if not text:
        return GuardrailVerdict(
            passed=False,
            reason="Empty prompt.",
            matched_rule="ValidLength",
            failed_validator="ValidLength",
            layer="regex",
        )
    if len(text) > max_chars:
        return GuardrailVerdict(
            passed=False,
            reason=f"Prompt exceeds {max_chars} characters.",
            matched_rule="ValidLength",
            failed_validator="ValidLength",
            layer="regex",
        )
    if _INJECTION.search(text):
        return GuardrailVerdict(
            passed=False,
            reason="Prompt-injection or jailbreak phrasing detected.",
            matched_rule="DetectPromptInjection",
            failed_validator="DetectPromptInjection",
            layer="regex",
        )
    verb = _has_write_intent(text)
    if verb:
        return GuardrailVerdict(
            passed=False,
            reason=f"Write/mutation intent detected ({verb!r}); checks are read-only.",
            matched_rule="FabricZeroWriteValidator",
            failed_validator="FabricZeroWriteValidator",
            layer="regex",
        )
    return GuardrailVerdict(passed=True, layer="regex")


def _guardrails_ai_screen(prompt: str) -> GuardrailVerdict | None:
    """Optional Guardrails AI Hub pass. ``None`` when the extra/AI is unavailable.

    Kept as a separate seam so the installed path can be mocked in tests without
    the heavy ``guardrails`` extra present. Returns a verdict only to *tighten*
    the deterministic result (fail-closed); never loosens it.
    """
    settings = get_settings()
    if not settings.ai_enabled:
        return None
    try:  # pragma: no cover - exercised only with the `guardrails` extra installed
        from ._guardrails_ai import run_guard
    except Exception:  # noqa: BLE001 - extra absent -> deterministic path only
        return None
    try:  # pragma: no cover
        return run_guard(prompt)
    except Exception:  # noqa: BLE001 - a guard outage must not break the request
        return None


def screen(check: CustomCheck) -> CustomCheck:
    """Run Node 1 on ``check`` and record the verdict in place.

    On a failure the check's ``lifecycle_status`` becomes ``DROPPED_GUARDRAIL``;
    on success it is left ``PENDING`` for Node 2 to route.
    """
    verdict = _deterministic_screen(check.raw_prompt)
    if verdict.passed:
        escalated = _guardrails_ai_screen(check.raw_prompt)
        if escalated is not None and not escalated.passed:
            verdict = escalated
    check.guardrail = verdict
    if not verdict.passed:
        check.lifecycle_status = LifecycleStatus.DROPPED_GUARDRAIL
    return check


__all__ = ["screen"]
