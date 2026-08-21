"""Node 5 - the hardened Local Runner.

Executes LLM-authored custom checks in-process **safely**. A naive ``exec`` of
generated Python is arbitrary code execution (OWASP: untrusted code execution): a
check could read env vars, open files, or make network calls. This runner refuses
to run anything that could:

1. **import** outside a tiny safe set, or
2. reference a dangerous builtin (``eval``/``exec``/``open``/``__import__``/...), or
3. reach a **dunder** attribute (``__globals__``/``__class__``/... - the classic
   introspection escape).

Code that clears the AST allow-list is exec'd in a **restricted namespace** (a safe
builtins subset only - no ``os``/``sys``/``socket``/file handles) and run under a
**timeout**. The result is shape-validated before it is trusted.

Design source: plan Section 13 (Node 5 hardening - MANDATORY).
"""
from __future__ import annotations

import ast
import threading
from typing import Any

from .base_check import MAX_SCORE, MIN_SCORE, RESULT_KEYS, BaseAuditCheck

#: The only modules a generated check may import. Read-only, pure, no I/O.
ALLOWED_IMPORTS = frozenset(
    {"math", "re", "datetime", "json", "statistics", "typing", "collections"}
)

#: Builtins a generated check may never name.
BANNED_NAMES = frozenset(
    {
        "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
        "globals", "locals", "vars", "getattr", "setattr", "delattr", "hasattr",
        "memoryview", "exit", "quit", "help", "id",
    }
)

#: The safe builtins a generated check runs with. Nothing else is reachable.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "int", "isinstance", "issubclass", "len", "list", "map",
    "max", "min", "range", "reversed", "round", "set", "sorted", "str", "sum",
    "tuple", "zip", "True", "False", "None", "print", "repr", "abs",
)


class UnsafeCodeError(ValueError):
    """Raised when generated source fails the AST allow-list."""


def _safe_builtins() -> dict[str, Any]:
    import builtins

    safe = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES if hasattr(builtins, name)}
    # A curated, harmless exception set so a check can raise/catch normally.
    for exc in ("Exception", "ValueError", "KeyError", "TypeError", "IndexError"):
        safe[exc] = getattr(builtins, exc)
    # Needed to execute a ``class`` statement under a restricted ``__builtins__``.
    safe["__build_class__"] = builtins.__build_class__
    return safe


class _AstGuard(ast.NodeVisitor):
    """Collects every allow-list violation in a parsed module."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORTS:
                self.violations.append(f"import of {alias.name!r} is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_IMPORTS:
            self.violations.append(f"import from {node.module!r} is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BANNED_NAMES:
            self.violations.append(f"use of {node.id!r} is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append(f"dunder attribute {node.attr!r} is not allowed")
        self.generic_visit(node)


def validate_source(source: str) -> tuple[bool, str]:
    """``(ok, reason)`` from the AST allow-list. Never executes ``source``."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg}"
    guard = _AstGuard()
    guard.visit(tree)
    if guard.violations:
        return False, "; ".join(guard.violations)
    return True, ""


def load_check(source: str) -> type[BaseAuditCheck]:
    """Validate then load the ``BaseAuditCheck`` subclass defined in ``source``.

    Raises :class:`UnsafeCodeError` if the source fails the allow-list or defines
    no check class.
    """
    ok, reason = validate_source(source)
    if not ok:
        raise UnsafeCodeError(reason)
    namespace: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "__name__": "custom_check",
        "BaseAuditCheck": BaseAuditCheck,
    }
    exec(compile(source, "<custom_check>", "exec"), namespace)  # noqa: S102 - AST-gated + sandboxed
    subclasses = [
        obj
        for obj in namespace.values()
        if isinstance(obj, type) and issubclass(obj, BaseAuditCheck) and obj is not BaseAuditCheck
    ]
    if not subclasses:
        raise UnsafeCodeError("source defines no BaseAuditCheck subclass")
    return subclasses[-1]


def validate_result(result: Any) -> tuple[bool, str]:
    """``(ok, reason)`` for the ``evaluate`` result contract."""
    if not isinstance(result, dict):
        return False, "result is not a dict"
    missing = [k for k in RESULT_KEYS if k not in result]
    if missing:
        return False, f"result missing keys: {missing}"
    score = result["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return False, "score is not a number"
    if not (MIN_SCORE <= float(score) <= MAX_SCORE):
        return False, f"score {score} outside [{MIN_SCORE}, {MAX_SCORE}]"
    if not isinstance(result["findings"], list) or not isinstance(result["recommendations"], list):
        return False, "findings/recommendations must be lists"
    if not isinstance(result["status"], str):
        return False, "status is not a string"
    return True, ""


def _run_with_timeout(fn, timeout: float) -> tuple[Any, Exception | None]:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - reported as a check error
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, TimeoutError(f"evaluate exceeded {timeout}s")
    if "error" in box:
        return None, box["error"]
    return box.get("value"), None


def _error_result(reason: str, kind: str) -> dict:
    return {
        "status": "ERROR",
        "score": 0.0,
        "findings": [reason],
        "recommendations": [],
        "error": kind,
    }


def run_check(check_cls: type[BaseAuditCheck], kb: dict, *, timeout: float = 5.0) -> dict:
    """Instantiate and run ``check_cls`` against ``kb`` under a timeout.

    Always returns a valid result dict: a genuine evaluation, or a shape-valid
    ``ERROR`` result describing the failure - it never raises.
    """
    try:
        instance = check_cls()
    except Exception as exc:  # noqa: BLE001
        return _error_result(f"instantiation failed: {exc}", type(exc).__name__)

    value, err = _run_with_timeout(lambda: instance.evaluate(kb), timeout)
    if err is not None:
        return _error_result(str(err), type(err).__name__)
    ok, reason = validate_result(value)
    if not ok:
        return _error_result(f"invalid result: {reason}", "InvalidResult")
    return value


def load_and_run(source: str, kb: dict, *, timeout: float = 5.0) -> dict:
    """Convenience: validate + load + run, returning a valid result dict."""
    try:
        check_cls = load_check(source)
    except UnsafeCodeError as exc:
        return _error_result(f"rejected: {exc}", "UnsafeCodeError")
    return run_check(check_cls, kb, timeout=timeout)


__all__ = [
    "ALLOWED_IMPORTS",
    "BANNED_NAMES",
    "UnsafeCodeError",
    "validate_source",
    "validate_result",
    "load_check",
    "run_check",
    "load_and_run",
]
