"""Node 3b companion — generate the *read-only* REST-fetch code for a check.

When Node 3a finds a check needs a KB field the snapshot does not have, this agent
asks the model to write the **read-only** Python that would fetch that field from
the Fabric REST API, and validates it for safety. The result is stored on
``check.fetch_code`` and archived per run, so an operator can see exactly what code
the AI produced to enrich the knowledge base.

Scope (important): this module **generates and validates** the fetch code — it does
not execute it. Live execution needs a signed-in, read-only Fabric client and a
network sandbox, which the offline snapshot mode does not have; the actual KB
augmentation stays on the fixed, read-only strategies in
:mod:`auditfast.ai.agents.kb_updater_agent`. See ``docs/custom-checks-flow.md``.

Safety: the generated code passes the same AST allow-list as generated audit code
(no ``os``/``sys``/``socket``/file imports, no ``eval``/``exec``/dunder access) plus
a write-verb screen, so a stored snippet can never express a mutation.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from functools import partial

from ..custom_runtime.local_runner import validate_source
from ..orchestrator import complete, is_enabled
from ..orchestrator.ai_config import AiConfig
from ..orchestrator.state import CustomCheck, FetchPlan
from .code_gen_agent import _extract_code

#: A fetch generator maps ``(prompt, plan)`` to source, or ``None`` when AI is off.
FetchGenerator = Callable[[str, FetchPlan], "str | None"]

#: Method calls that mutate a resource. The read-only exception (a ``getDefinition``
#: POST is a read) means ``post`` is allowed; only genuine write verbs are blocked.
_MUTATION = re.compile(
    r"\.(delete|put|patch|create\w*|update\w*|remove\w*|insert\w*|drop\w*|write\w*|"
    r"grant\w*|revoke\w*|set_\w*)\s*\(",
    re.IGNORECASE,
)

_FETCH_SYSTEM = (
    "You write a single Python function that fetches ONE piece of read-only Microsoft "
    "Fabric metadata for an audit check. Signature exactly:\n\n"
    "    def fetch(client, workspace_id):\n"
    "        ...\n"
    "        return data  # JSON-serialisable\n\n"
    "`client` is an injected READ-ONLY Fabric REST client; call only its read methods "
    "(e.g. client.get(path), client.get_item_definition(...)). The code is READ-ONLY: "
    "it must NOT create, update, delete, grant, or otherwise modify anything. Do NOT "
    "import os/sys/subprocess/socket/requests/httpx/urllib; do NOT open files or use "
    "eval/exec/getattr or dunder attributes. Return only the function code."
)


def default_fetch_generator(
    prompt: str, plan: FetchPlan, *, ai: AiConfig | None = None
) -> str | None:
    """LLM-backed fetch-code generator. ``None`` when AI is off."""
    if not is_enabled(ai):
        return None
    user = (
        f"Audit check: {prompt!r}.\n"
        f"Missing KB field: {plan.field!r}.\n"
        f"Resource: {plan.resource or 'unknown'}. "
        f"Suggested endpoint: {plan.endpoint or 'unknown'}.\n"
        "Write the read-only fetch(client, workspace_id) for this field."
    )
    raw = complete(_FETCH_SYSTEM, user, max_tokens=600, ai=ai)
    return _extract_code(raw) if raw else None


def validate_fetch_source(source: str) -> tuple[bool, str]:
    """``(ok, reason)`` — the fetch code must be safe *and* free of write verbs."""
    ok, reason = validate_source(source)
    if not ok:
        return False, reason
    if _MUTATION.search(source):
        return False, "write/mutation call detected; fetch code must be read-only"
    return True, ""


def generate_fetch_code(
    check: CustomCheck,
    *,
    generator: FetchGenerator = default_fetch_generator,
    ai: AiConfig | None = None,
) -> CustomCheck:
    """Generate + validate the read-only fetch code for ``check`` in place.

    Only acts when the check carries a :class:`FetchPlan` (Node 3a flagged missing
    data). Stores validated code on ``check.fetch_code``; leaves it ``None`` when AI
    is off or the code fails the safety screen.
    """
    if check.fetch_plan is None:
        return check
    if generator is default_fetch_generator:
        generator = partial(default_fetch_generator, ai=ai)

    source = generator(check.raw_prompt, check.fetch_plan)
    if source is None:  # AI unavailable -> no artifact, not an error
        return check
    ok, _reason = validate_fetch_source(source)
    if ok:
        check.fetch_code = source
    return check


__all__ = ["generate_fetch_code", "default_fetch_generator", "validate_fetch_source"]
