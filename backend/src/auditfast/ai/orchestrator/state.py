"""Custom-checks lifecycle ledger and batch session.

The state layer for the custom-checks pipeline (Nodes 1-6). Every user-submitted
plain-English check is one :class:`CustomCheck` row that is *mutated in place* as
it flows through the nodes, so nothing is ever silently dropped: a check always
ends in a labelled :class:`LifecycleStatus`.

This module is deliberately **pure standard library**. It imports nothing from
:mod:`auditfast.core` (the determinism boundary), and nothing from LangGraph, a
vector store, or a Fabric client. That keeps it unit-testable on a base install
with ``ai_enabled = False`` and no optional extras present. The graph wiring adds
a thin ``TypedDict`` view over these dataclasses later; the dataclasses stay the
source of truth.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LifecycleStatus(str, Enum):
    """Where a check currently sits in the pipeline. One per ledger row."""

    PENDING = "PENDING"                       # just received, not yet screened
    DROPPED_GUARDRAIL = "DROPPED_GUARDRAIL"   # write intent / injection - rejected
    ROUTED_DEFAULT = "ROUTED_DEFAULT"         # duplicate of an existing check - reused
    PROCESSED_CUSTOM = "PROCESSED_CUSTOM"     # unique; data already in KB
    KB_AUGMENTED = "KB_AUGMENTED"             # unique; missing data fetched read-only
    KB_FETCH_FAILED = "KB_FETCH_FAILED"       # data unavailable after 3 trials
    AI_REQUIRED = "AI_REQUIRED"               # code-gen needs an LLM but AI is off


class FetchErrorClass(str, Enum):
    """Why a read-only fetch failed. Derived purely from ``(status, body)``."""

    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"   # 403
    ITEM_TYPE_NOT_SUPPORTED = "ITEM_TYPE_NOT_SUPPORTED"     # 400 / 404
    RATE_LIMITED = "RATE_LIMITED"                           # 429 persisted
    METADATA_UNAVAILABLE = "METADATA_UNAVAILABLE"           # 200 but field absent
    TRANSIENT = "TRANSIENT"                                 # 5xx / timeout


class FeasibilityClass(str, Enum):
    """Whether a surviving check can be evaluated, and how."""

    FULLY_FEASIBLE = "FULLY_FEASIBLE"
    PARTIALLY_FEASIBLE = "PARTIALLY_FEASIBLE"
    NOT_FEASIBLE = "NOT_FEASIBLE"
    MANUAL_VALIDATION_REQUIRED = "MANUAL_VALIDATION_REQUIRED"


@dataclass(slots=True)
class GuardrailVerdict:
    """Node 1 output: did the prompt clear the safety gate, and if not, why."""

    passed: bool
    reason: str = ""
    matched_rule: str = ""
    failed_validator: str = ""
    layer: str = ""  # "guardrails" (library) | "regex" (fallback) | "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "matched_rule": self.matched_rule,
            "failed_validator": self.failed_validator,
            "layer": self.layer,
        }


@dataclass(slots=True)
class RoutingResult:
    """Node 2 output: is this a duplicate of an existing default check?"""

    is_duplicate: bool
    matched_default_id: str | None = None
    similarity_score: float = 0.0
    stage: str = ""  # "deterministic" | "semantic" | "intent_critic"
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_duplicate": self.is_duplicate,
            "matched_default_id": self.matched_default_id,
            "similarity_score": self.similarity_score,
            "stage": self.stage,
            "reasoning": self.reasoning,
        }


@dataclass(slots=True)
class FetchPlan:
    """Node 3a output: the read-only field a check needs and where to get it."""

    field: str
    resource: str = ""
    endpoint: str = ""
    confidence: float = 0.0
    mandatory: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "resource": self.resource,
            "endpoint": self.endpoint,
            "confidence": self.confidence,
            "mandatory": self.mandatory,
        }


@dataclass(slots=True)
class KbUpdateLog:
    """Node 3b output: the record of a read-only augmentation attempt."""

    attempt_count: int = 0
    status: str = "PENDING"  # "SUCCESS" | "FAILED" | "PENDING"
    apis_called: list[str] = field(default_factory=list)
    fields_added: list[str] = field(default_factory=list)
    diagnostic: FetchErrorClass | None = None
    root_cause: str = ""
    remediation: str = ""
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_count": self.attempt_count,
            "status": self.status,
            "apis_called": list(self.apis_called),
            "fields_added": list(self.fields_added),
            "diagnostic": self.diagnostic.value if self.diagnostic else None,
            "root_cause": self.root_cause,
            "remediation": self.remediation,
            "provenance": [dict(p) for p in self.provenance],
        }


@dataclass(slots=True)
class CodeGenLog:
    """Node 4 output: the record of the bounded generate/validate/review loop."""

    attempts: int = 0
    status: str = "PENDING"  # "GENERATED" | "FAILED" | "AI_REQUIRED"
    stage_failed: str = ""   # "static" | "functional" | "review"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "status": self.status,
            "stage_failed": self.stage_failed,
            "reason": self.reason,
        }


@dataclass(slots=True)
class CustomCheck:
    """One user-submitted check, mutated in place as it flows through the nodes."""

    check_id: str
    raw_prompt: str
    lifecycle_status: LifecycleStatus = LifecycleStatus.PENDING
    guardrail: GuardrailVerdict | None = None
    routing: RoutingResult | None = None
    fetch_plan: FetchPlan | None = None
    kb_update: KbUpdateLog | None = None
    feasibility: FeasibilityClass | None = None
    code_gen: CodeGenLog | None = None
    generated_code: str | None = None
    #: Read-only REST-fetch code the AI wrote for a missing KB field (Node 3b).
    #: Generated + safety-validated, stored as an artifact; not executed here.
    fetch_code: str | None = None
    #: HITL decision: ``None`` = awaiting review, ``True`` = approved, ``False`` = rejected.
    approved: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """The ledger row: a plain, JSON-serialisable view for logs and reports."""
        return {
            "check_id": self.check_id,
            "raw_prompt": self.raw_prompt,
            "lifecycle_status": self.lifecycle_status.value,
            "guardrail": self.guardrail.to_dict() if self.guardrail else None,
            "routing": self.routing.to_dict() if self.routing else None,
            "fetch_plan": self.fetch_plan.to_dict() if self.fetch_plan else None,
            "kb_update": self.kb_update.to_dict() if self.kb_update else None,
            "feasibility": self.feasibility.value if self.feasibility else None,
            "code_gen": self.code_gen.to_dict() if self.code_gen else None,
            "generated_code": self.generated_code,
            "fetch_code": self.fetch_code,
            "approved": self.approved,
        }


@dataclass(slots=True)
class CustomCheckSession:
    """A batch of custom checks that share one in-memory KB and one fetch cache.

    Every check in a batch reads and augments the *same* ``shared_kb`` dict, so a
    field fetched for one check is instantly available to the next and is never
    fetched twice (``fetch_cache``). The shared KB is a working copy; the default
    on-disk snapshot is never mutated (copy-on-write is the updater's job).
    """

    checks: list[CustomCheck] = field(default_factory=list)
    shared_kb: dict[str, Any] = field(default_factory=dict)
    fetch_cache: dict[str, Any] = field(default_factory=dict)

    def add(self, prompt: str) -> CustomCheck:
        """Create (or return the existing) row for ``prompt`` and track it.

        Idempotent by :func:`make_check_id`: submitting the same prompt twice
        yields the same row instead of a duplicate.
        """
        check_id = make_check_id(prompt)
        for existing in self.checks:
            if existing.check_id == check_id:
                return existing
        check = CustomCheck(check_id=check_id, raw_prompt=prompt)
        self.checks.append(check)
        return check

    def ledger(self) -> list[dict[str, Any]]:
        """The full batch as JSON-serialisable ledger rows."""
        return [check.to_dict() for check in self.checks]


def make_check_id(prompt: str) -> str:
    """A stable ``CHK-<8hex>`` id for a prompt.

    Whitespace-normalised and case-folded so trivially different submissions of
    the same intent collapse to one id, which is what makes :meth:`add`
    idempotent and dedups re-submissions.
    """
    normalized = " ".join(prompt.split()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"CHK-{digest}"


__all__ = [
    "LifecycleStatus",
    "FetchErrorClass",
    "FeasibilityClass",
    "GuardrailVerdict",
    "RoutingResult",
    "FetchPlan",
    "KbUpdateLog",
    "CodeGenLog",
    "CustomCheck",
    "CustomCheckSession",
    "make_check_id",
]
