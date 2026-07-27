"""The deterministic check library.

Importing this package registers every check. Each check module below is imported
purely for its side effect: the ``@check`` decorators run at import time and
populate :data:`auditfast.checks.registry.REGISTRY`.

**Adding a new check module?** Import it here, or its checks will silently never
run — registration is an import side effect, so a module nobody imports registers
nothing and raises nothing. :func:`registered_modules` exists so a test can
assert this list has not drifted from what is on disk.
"""
from __future__ import annotations

from .helpers import (
    RemediationBook,
    Verdict,
    binary,
    covered,
    graded,
    not_applicable,
    note,
)
from .pipeline import opex as _pl_opex
from .pipeline import reliability as _pl_reliability
from .pipeline import security as _pl_security
from .registry import REGISTRY, CheckRegistry, DuplicateCheckError, check
from .workspace import cost as _ws_cost
from .workspace import foundation as _ws_foundation
from .workspace import opex as _ws_opex
from .workspace import security as _ws_security

_MODULES = (
    _ws_opex, _ws_security, _ws_cost, _ws_foundation,
    _pl_opex, _pl_reliability, _pl_security,
)


def registered_modules() -> tuple[str, ...]:
    """Dotted names of every module imported here for check registration."""
    return tuple(m.__name__ for m in _MODULES)


__all__ = [
    "REGISTRY",
    "CheckRegistry",
    "DuplicateCheckError",
    "RemediationBook",
    "Verdict",
    "binary",
    "check",
    "covered",
    "graded",
    "not_applicable",
    "note",
    "registered_modules",
]
