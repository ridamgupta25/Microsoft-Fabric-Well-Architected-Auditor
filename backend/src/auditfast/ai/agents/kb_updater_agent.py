"""Node 3b - the KB Updater (read-only, 3-strategy fetch + diagnostics).

When Node 3a finds a check needs a field the shared KB does not have, this node
tries to fetch it **read-only** through a fetch provider. It never issues a write
and never mutates the default snapshot - fetched data is deep-merged into the
session's shared in-memory KB (copy-on-write).

Three strategies, tried in order (not three repeats of one):

1. ``item_rest``        - item-level REST GET.
2. ``git_artifact``     - the item's definition from the Git-connected repo.
3. ``workspace_bundle`` - a parent/workspace bundle, filtered locally.

A ``429`` honours ``Retry-After`` and retries the *same* strategy (it does not
burn a strategy). Success is ``200`` + non-empty + schema-valid (the field
catalog's validator) + JSON-shaped; a ``200`` with junk is a failure that advances
to the next strategy. When every strategy fails the check survives as
``KB_FETCH_FAILED`` with a diagnostic class, a most-likely root cause, and a
concrete remediation, so nothing is silently dropped.

Design source: ``local/Planning/Knowledge Base - Node`` (Phases 4-10).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..orchestrator.state import (
    CustomCheck,
    FeasibilityClass,
    FetchErrorClass,
    FetchPlan,
    KbUpdateLog,
    LifecycleStatus,
)
from ..rag.kb_field_catalog import KB_FIELD_CATALOG, KbField

#: The ordered fetch strategies. Each is a distinct source, not a retry.
STRATEGIES: tuple[str, ...] = ("item_rest", "git_artifact", "workspace_bundle")

#: Cap on ``429`` retries within one strategy before giving up on it.
_MAX_RATE_LIMIT_RETRIES = 3
_DEFAULT_BACKOFF_SECONDS = 1.0

#: Most-informative-first ordering when trials disagree on why they failed.
_DIAGNOSTIC_PRIORITY = (
    FetchErrorClass.INSUFFICIENT_PERMISSIONS,
    FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED,
    FetchErrorClass.METADATA_UNAVAILABLE,
    FetchErrorClass.RATE_LIMITED,
    FetchErrorClass.TRANSIENT,
)

#: diagnostic -> (root cause, remediation, feasibility when the field is mandatory)
_DIAGNOSIS: dict[FetchErrorClass, tuple[str, str, FeasibilityClass]] = {
    FetchErrorClass.INSUFFICIENT_PERMISSIONS: (
        "The caller lacks the read scope/role required to read this metadata.",
        "Grant the missing read-only permission (e.g. Item.Read.All) and re-run.",
        FeasibilityClass.NOT_FEASIBLE,
    ),
    FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED: (
        "No API exposes this metadata for this item type.",
        "The check does not apply to this item type; treat as not applicable.",
        FeasibilityClass.NOT_FEASIBLE,
    ),
    FetchErrorClass.RATE_LIMITED: (
        "The tenant is throttling requests (HTTP 429).",
        "Re-run the audit later once throttling clears.",
        FeasibilityClass.NOT_FEASIBLE,
    ),
    FetchErrorClass.METADATA_UNAVAILABLE: (
        "The API responded but does not expose the required field.",
        "Validate this manually; it cannot be automated from available APIs.",
        FeasibilityClass.MANUAL_VALIDATION_REQUIRED,
    ),
    FetchErrorClass.TRANSIENT: (
        "A temporary server error or timeout occurred.",
        "Re-run the audit; this failure is usually transient.",
        FeasibilityClass.NOT_FEASIBLE,
    ),
}


@dataclass(slots=True)
class FetchResponse:
    """A read-only fetch result. ``body`` is the value for the field's container."""

    status: int
    body: Any = None
    retry_after: float | None = None


class FetchProvider(Protocol):
    """Read-only fetch contract. Implementations must never mutate Fabric."""

    def fetch(self, plan: FetchPlan, strategy: str) -> FetchResponse: ...


def classify_error(status: int) -> FetchErrorClass:
    """Map an HTTP status to a diagnostic class (pure)."""
    if status == 403:
        return FetchErrorClass.INSUFFICIENT_PERMISSIONS
    if status in (400, 404):
        return FetchErrorClass.ITEM_TYPE_NOT_SUPPORTED
    if status == 429:
        return FetchErrorClass.RATE_LIMITED
    if status == 0 or status >= 500:
        return FetchErrorClass.TRANSIENT
    return FetchErrorClass.TRANSIENT


def _is_jsonish(value: Any) -> bool:
    """True when ``value`` is a plain JSON-shaped structure (data-poisoning guard)."""
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_jsonish(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_is_jsonish(v) for v in value)
    return False


def _non_empty(value: Any) -> bool:
    if isinstance(value, (dict, list, str)):
        return bool(value)
    return value is not None


def _catalog_field(path: str) -> KbField | None:
    for f in KB_FIELD_CATALOG:
        if f.path == path:
            return f
    return None


def _top_key(path: str) -> str:
    return path.split(".")[0].split("[")[0]


def _deep_merge(base: Any, incoming: Any) -> Any:
    """Merge ``incoming`` into ``base``; dicts recurse, lists merge by ``id``."""
    if isinstance(base, dict) and isinstance(incoming, dict):
        for key, value in incoming.items():
            base[key] = _deep_merge(base.get(key), value) if key in base else value
        return base
    if isinstance(base, list) and isinstance(incoming, list):
        by_id = {item["id"]: i for i, item in enumerate(base) if isinstance(item, dict) and "id" in item}
        merged = list(base)
        for item in incoming:
            if isinstance(item, dict) and item.get("id") in by_id:
                idx = by_id[item["id"]]
                merged[idx] = _deep_merge(merged[idx], item)
            else:
                merged.append(item)
        return merged
    return incoming  # scalar / type change -> incoming wins


def _select_diagnostic(seen: list[FetchErrorClass]) -> FetchErrorClass:
    for diag in _DIAGNOSTIC_PRIORITY:
        if diag in seen:
            return diag
    return FetchErrorClass.TRANSIENT


def augment(
    check: CustomCheck,
    provider: FetchProvider,
    session,
    *,
    sleeper=time.sleep,
) -> CustomCheck:
    """Run Node 3b on ``check`` in place, using ``session``'s shared KB and cache.

    Acts only on a ``PENDING`` check that carries a :class:`FetchPlan` from Node 3a.
    """
    plan = check.fetch_plan
    if check.lifecycle_status is not LifecycleStatus.PENDING or plan is None:
        return check

    field = _catalog_field(plan.field)
    validator = field.validator if field is not None else _non_empty
    log = KbUpdateLog()

    # Batch fetch cache: a field already fetched this batch is reused, never re-hit.
    if plan.field in session.fetch_cache:
        return _succeed(check, log, plan, source="cache", validated=True)

    seen: list[FetchErrorClass] = []
    for strategy in STRATEGIES:
        response = _fetch_with_backoff(provider, plan, strategy, log, sleeper)
        log.attempt_count += 1

        if response.status == 200:
            body = response.body
            if _is_jsonish(body) and _non_empty(body) and validator(body):
                _merge(session, plan, body)
                _record_provenance(log, plan, strategy)
                session.fetch_cache[plan.field] = True
                return _succeed(check, log, plan, source=strategy, validated=True)
            seen.append(FetchErrorClass.METADATA_UNAVAILABLE)
            continue

        if response.status == 429:  # exhausted backoff without a 200
            seen.append(FetchErrorClass.RATE_LIMITED)
            continue
        seen.append(classify_error(response.status))

    return _fail(check, log, plan, _select_diagnostic(seen))


def _fetch_with_backoff(provider, plan, strategy, log, sleeper) -> FetchResponse:
    """Fetch one strategy, honouring ``Retry-After`` on 429 without burning it."""
    response = provider.fetch(plan, strategy)
    log.apis_called.append(f"{strategy}:{plan.endpoint}")
    retries = 0
    while response.status == 429 and retries < _MAX_RATE_LIMIT_RETRIES:
        retries += 1
        sleeper(response.retry_after or _DEFAULT_BACKOFF_SECONDS)
        response = provider.fetch(plan, strategy)
    return response


def _merge(session, plan: FetchPlan, body: Any) -> None:
    key = _top_key(plan.field)
    existing = session.shared_kb.get(key)
    session.shared_kb[key] = _deep_merge(existing, body) if existing is not None else body


def _record_provenance(log: KbUpdateLog, plan: FetchPlan, source: str) -> None:
    log.provenance.append(
        {
            "source": source,
            "endpoint": plan.endpoint,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "resource_id": plan.field,
        }
    )


def _succeed(check, log, plan, *, source: str, validated: bool) -> CustomCheck:
    log.status = "SUCCESS"
    log.fields_added = [plan.field]
    if source == "cache" and not log.provenance:
        _record_provenance(log, plan, "cache")
    check.kb_update = log
    check.lifecycle_status = LifecycleStatus.KB_AUGMENTED
    check.feasibility = FeasibilityClass.FULLY_FEASIBLE
    return check


def _fail(check, log, plan: FetchPlan, diagnostic: FetchErrorClass) -> CustomCheck:
    root_cause, remediation, mandatory_feasibility = _DIAGNOSIS[diagnostic]
    log.status = "FAILED"
    log.diagnostic = diagnostic
    log.root_cause = root_cause
    log.remediation = remediation
    check.kb_update = log
    check.lifecycle_status = LifecycleStatus.KB_FETCH_FAILED
    # An optional field that couldn't be fetched still leaves a partial evaluation.
    check.feasibility = (
        mandatory_feasibility if plan.mandatory else FeasibilityClass.PARTIALLY_FEASIBLE
    )
    return check


__all__ = ["augment", "classify_error", "FetchResponse", "FetchProvider", "STRATEGIES"]
