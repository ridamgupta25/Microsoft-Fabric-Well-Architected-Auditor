"""The contract every generated custom check implements.

A generated check subclasses :class:`BaseAuditCheck` and implements
``evaluate(kb)``, returning a 0-100 float score with evidence. Subclassing
auto-registers the class in :data:`CUSTOM_REGISTRY` (Decision 10) - a registry that
is **separate** from the ``core`` ``REGISTRY``, so a custom check can never enter
the pinned deterministic 0-3 scorecard.

The result contract is fixed: ``{status, score, findings, recommendations}`` with
``score`` a float in ``[0, 100]``; the local runner validates this shape before a
result is trusted.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

#: Auto-populated registry of generated checks, keyed by ``check_id``.
#: Separate from ``auditfast.core.check.registry.REGISTRY`` by design.
CUSTOM_REGISTRY: dict[str, type[BaseAuditCheck]] = {}

#: The keys a valid ``evaluate`` result must carry.
RESULT_KEYS = ("status", "score", "findings", "recommendations")

#: Custom checks score on this scale, never the deterministic 0-3 one.
MIN_SCORE = 0.0
MAX_SCORE = 100.0


class BaseAuditCheck(ABC):
    """Base class for a generated, read-only custom audit check."""

    #: Stable id for the generated check (e.g. ``"chk_a1b2c3d4"``).
    check_id: str = ""
    #: Optional human checklist ref and title for the report.
    check_ref: str = ""
    title: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        key = cls.check_id or cls.check_ref or cls.__name__
        CUSTOM_REGISTRY[key] = cls

    @abstractmethod
    def evaluate(self, kb: dict) -> dict:
        """Assess ``kb`` (a read-only copy) and return the result contract.

        Must return ``{status, score, findings, recommendations}`` and must never
        mutate ``kb`` or reach outside it.
        """
        raise NotImplementedError


def clear_custom_registry() -> None:
    """Empty the registry (test isolation; the runner does not depend on it)."""
    CUSTOM_REGISTRY.clear()


__all__ = [
    "BaseAuditCheck",
    "CUSTOM_REGISTRY",
    "RESULT_KEYS",
    "MIN_SCORE",
    "MAX_SCORE",
    "clear_custom_registry",
]
