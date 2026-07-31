"""The deterministic check library.

Importing this package registers every check. The library is organised on disk as
``<pillar>/<layer>/{automated,manual,questionnaire}.py``; each module is imported
purely for its side effect — the ``@check`` / ``manual_check`` / ``questionnaire_check``
calls run at import time and populate
:data:`auditfast.core.check.registry.REGISTRY`.

Modules are discovered automatically by walking the sub-packages, so a new
``automated.py``, ``manual.py`` or ``questionnaire.py`` is picked up without
editing this file. Helper modules whose name starts with ``_`` (e.g. ``_pipeline``)
are skipped. :func:`registered_modules` reports what was loaded, so a test can
assert the tree on disk was fully imported.
"""
from __future__ import annotations

import importlib
import pkgutil

from ..models import CheckOption
from .helpers import (
    RemediationBook,
    Verdict,
    binary,
    covered,
    graded,
    manual,
    not_applicable,
    note,
)
from .registry import (
    REGISTRY,
    CheckRegistry,
    DuplicateCheckError,
    check,
    manual_check,
    questionnaire_check,
)

#: An interactive check's answer, re-exported here so questionnaire modules can
#: import it alongside :func:`questionnaire_check` from one place.
Option = CheckOption

#: Leaf module names that carry check registrations.
_CHECK_MODULES = {"automated", "manual", "questionnaire", "roadmap"}


def _discover() -> tuple[str, ...]:
    """Import every check module under this package and return their names."""
    loaded: list[str] = []
    for info in pkgutil.walk_packages(__path__, prefix=f"{__name__}."):
        if info.ispkg:
            continue
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf in _CHECK_MODULES:
            importlib.import_module(info.name)
            loaded.append(info.name)
    return tuple(sorted(loaded))


_MODULES: tuple[str, ...] = _discover()


def registered_modules() -> tuple[str, ...]:
    """Dotted names of every module imported here for check registration."""
    return _MODULES


__all__ = [
    "REGISTRY",
    "CheckRegistry",
    "DuplicateCheckError",
    "Option",
    "CheckOption",
    "RemediationBook",
    "Verdict",
    "binary",
    "check",
    "covered",
    "graded",
    "manual",
    "manual_check",
    "not_applicable",
    "note",
    "questionnaire_check",
    "registered_modules",
]
