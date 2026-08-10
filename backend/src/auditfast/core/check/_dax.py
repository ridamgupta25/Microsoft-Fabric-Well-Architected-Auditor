"""Shared helpers for DAX expression analysis.

Underscore-prefixed so the package auto-loader skips it: helpers only, no checks.

Everything here is a pure function of the expression text, so a measure always
produces the same verdict — the determinism the engine depends on.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")

#: A real DAX variable declaration. A loose ``VAR`` substring also matches a
#: column named "Var Amount".
VAR_DECLARATION = re.compile(r"\bVAR\s+\w+\s*=", re.IGNORECASE)

#: The start of a function call: an identifier immediately followed by ``(``.
#: DAX function names are ASCII letters, digits, dots and underscores.
_CALL_START = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

#: A sub-expression shorter than this is boilerplate (``SUM(x)``, ``MAX(y)``);
#: repeating it is normal DAX, not copy-paste worth flagging.
_MIN_SUBEXPR_CHARS = 25

#: Iterator ("X") functions. Nesting one inside another multiplies the row
#: context and is the usual cause of a slow measure.
_ITERATORS = (
    "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX",
    "CONCATENATEX", "RANKX", "MEDIANX", "PERCENTILEX.INC", "PERCENTILEX.EXC",
    "STDEVX.P", "STDEVX.S", "VARX.P", "VARX.S", "GEOMEANX",
)
_ITERATOR_CALL = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _ITERATORS) + r")\s*\(",
    re.IGNORECASE,
)

#: ``FILTER(<table>, <table>[col] <op> <value>)`` — a single column predicate over
#: a bare table reference. Inside CALCULATE this is the textbook avoidable
#: iterator: the same thing is expressible as a plain boolean filter argument.
#: A ``FILTER(ALL(...))`` / ``FILTER(VALUES(...))`` is deliberately excluded — it
#: removes or replaces filter context and has no boolean-argument equivalent.
_SIMPLE_FILTER = re.compile(
    r"\bFILTER\s*\(\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*,"
    r"\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)?\s*\[[^\]]+\]\s*"
    r"(?:<=|>=|<>|=|<|>)",
    re.IGNORECASE,
)


#: DAX time-intelligence functions — the mechanics a measure needs to compare a
#: period against another period. Their *presence* is what makes trend analysis
#: possible; whether any report actually plots the result is not readable from
#: the model, so callers must not claim more than "the model can do this".
TIME_INTELLIGENCE_FUNCTIONS = (
    "DATEADD", "SAMEPERIODLASTYEAR", "PARALLELPERIOD", "DATESINPERIOD", "DATESBETWEEN",
    "TOTALYTD", "TOTALQTD", "TOTALMTD", "DATESYTD", "DATESQTD", "DATESMTD",
    "PREVIOUSDAY", "PREVIOUSMONTH", "PREVIOUSQUARTER", "PREVIOUSYEAR",
    "NEXTDAY", "NEXTMONTH", "NEXTQUARTER", "NEXTYEAR",
    "STARTOFYEAR", "STARTOFQUARTER", "STARTOFMONTH",
    "ENDOFYEAR", "ENDOFQUARTER", "ENDOFMONTH",
    "OPENINGBALANCEMONTH", "OPENINGBALANCEQUARTER", "OPENINGBALANCEYEAR",
    "CLOSINGBALANCEMONTH", "CLOSINGBALANCEQUARTER", "CLOSINGBALANCEYEAR",
)
#: A *call* to one of them. The bare name also appears in a measure name
#: ("Sales SAMEPERIODLASTYEAR"), which computes nothing.
_TIME_INTELLIGENCE_CALL = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in TIME_INTELLIGENCE_FUNCTIONS) + r")\s*\(",
    re.IGNORECASE,
)


def normalised(expression: object) -> str:
    """Expression with runs of whitespace collapsed, so pretty-printing adds no length."""
    return _WHITESPACE.sub(" ", str(expression or "")).strip()


def uses_time_intelligence(expression: str) -> bool:
    """True when the expression *calls* a DAX time-intelligence function.

    Only the call form counts: a measure merely *named* after a period comparison
    shifts no date filter.
    """
    return bool(_TIME_INTELLIGENCE_CALL.search(expression or ""))


def time_intelligence_calls(expression: str) -> set[str]:
    """The upper-cased time-intelligence function names called in ``expression``."""
    return {match.group(1).upper() for match in _TIME_INTELLIGENCE_CALL.finditer(expression or "")}


def uses_variables(expression: str) -> bool:
    """True when the expression declares at least one DAX ``VAR``."""
    return bool(VAR_DECLARATION.search(expression))


def call_spans(expression: str) -> list[str]:
    """Every balanced ``FUNC(...)`` span in the expression, outermost first.

    Parenthesis matching ignores brackets inside string literals so a ``")"`` in
    a format string cannot unbalance the scan.
    """
    spans: list[str] = []
    for match in _CALL_START.finditer(expression):
        end = _matching_paren(expression, match.end() - 1)
        if end is not None:
            spans.append(expression[match.start():end + 1])
    return spans


def _matching_paren(text: str, open_index: int) -> int | None:
    """Index of the ``)`` closing the ``(`` at ``open_index``, or None if unbalanced."""
    depth = 0
    in_string = False
    index = open_index
    while index < len(text):
        char = text[index]
        if in_string:
            if char == '"':
                # A doubled quote is an escaped quote inside a DAX string.
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 1
                else:
                    in_string = False
        elif char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def repeated_subexpressions(expression: str) -> list[str]:
    """Substantial function-call sub-expressions written more than once.

    This is what a ``VAR`` exists to prevent: computing the same thing twice.
    Assigning it to a variable and reusing the name leaves one occurrence, so a
    measure that uses VAR properly reports nothing here.
    """
    seen: dict[str, int] = {}
    for span in call_spans(expression):
        key = _WHITESPACE.sub(" ", span).strip().upper()
        if len(key) < _MIN_SUBEXPR_CHARS:
            continue
        seen[key] = seen.get(key, 0) + 1
    return [span for span, count in seen.items() if count > 1]


def nested_iterators(expression: str) -> bool:
    """True when an iterator (``SUMX`` and friends) is called inside another one."""
    for span in call_spans(expression):
        if not _ITERATOR_CALL.match(span):
            continue
        # Skip the opening call itself; an iterator in the remainder is nested.
        inner = span[span.find("(") + 1:]
        if _ITERATOR_CALL.search(inner):
            return True
    return False


def avoidable_filter(expression: str) -> bool:
    """True when ``CALCULATE`` wraps a ``FILTER`` that a boolean argument would do.

    Only flags a ``FILTER`` over a *bare table* carrying a single column
    comparison. ``FILTER(ALL(t), ...)`` and multi-condition filters are left
    alone: they change filter context in ways a boolean argument cannot.
    """
    for span in call_spans(expression):
        if not re.match(r"\bCALCULATE\s*\(", span, re.IGNORECASE):
            continue
        if _SIMPLE_FILTER.search(span):
            return True
    return False


def expensive_iterator(expression: str) -> bool:
    """True when the measure uses an iterator pattern with a cheaper equivalent."""
    return nested_iterators(expression) or avoidable_filter(expression)
