"""14.1.4 - a repeat across two VARs is not duplicated work.

A reviewer supplied a measure that the check flagged for "repeated
sub-expression" while it was, in fact, well structured - ten VARs, each building
a different string. Three of them happened to test the same lookup:

    VAR _selectionHelperMeasure   = IF(SELECTEDVALUE('T'[OBJECT_TYPE]) = "MEASURE", ...)
    VAR _selectionHelperPartition = IF(SELECTEDVALUE('T'[OBJECT_TYPE]) = "PARTITION", ...)

Counting repeats across the whole expression made those look like the same value
computed twice. They are separate branches doing separate jobs - and the more
VARs a measure declared, the more likely it was to be flagged, which is exactly
backwards.

Worth recording what the research found while fixing this: **"repeated
sub-expression" is not a Microsoft Best Practice Analyzer rule.** It appears in
no published BPA rule set (github.com/microsoft/Analysis-Services). And SQLBI
documents that acting on it can be *wrong*: a VAR is evaluated once, in the
filter context where it is defined, so hoisting a repeat out of
``DIVIDE(x, CALCULATE(x, ALL(Product)))`` changes the result. The checklist point
names it explicitly, so the check keeps it - scoped correctly, and reported as a
readability signal rather than a defect.
"""
from __future__ import annotations

from auditfast.core.check._dax import repeated_subexpressions

# The reviewer's measure, trimmed to the shape that mattered.
_WELL_STRUCTURED = """
Dependency Insights =
var _selectionObjectType = DISTINCTCOUNT('Calc Dependencies'[OBJECT_TYPE])
var _objectTypeName = IF([# Objects] = 1, MIN('Calc Dependencies'[SingularName]),
                                          MIN('Calc Dependencies'[PluralName]))
var _selectionHelperMeasure = IF(SELECTEDVALUE('Calc Dependencies'[OBJECT_TYPE]) = "MEASURE",
    "DAX expressions in Measures contains Table(s) and/or Column(s).", "")
var _selectionHelperPartition = IF(SELECTEDVALUE('Calc Dependencies'[OBJECT_TYPE]) = "PARTITION",
    "One Table has at least one Partition.", "")
RETURN _selectionHelperMeasure & UNICHAR(10) & _selectionHelperPartition
"""

# The genuine fault: one block computing the same thing twice.
_REAL_REPEAT = """
Margin % =
DIVIDE(
    SUMX(Sales, Sales[Quantity] * Sales[NetPrice]) - SUMX(Sales, Sales[Quantity] * Sales[Cost]),
    SUMX(Sales, Sales[Quantity] * Sales[NetPrice])
)
"""


def test_a_call_reused_across_separate_vars_is_not_a_repeat():
    """The reviewer's case: ten VARs, one shared lookup, no duplicated work."""
    assert repeated_subexpressions(_WELL_STRUCTURED) == []


def test_a_call_written_twice_in_one_block_is_a_repeat():
    """The real fault the rule exists for - one expression, same value twice."""
    found = repeated_subexpressions(_REAL_REPEAT)
    assert found
    assert any("SUMX" in span for span in found)


def test_a_repeat_inside_a_single_var_is_still_caught():
    """Scoping per block must not blind the rule inside a VAR."""
    expression = """
    Ratio =
    VAR _both = DIVIDE(
        CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024),
        CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024)
    )
    RETURN _both
    """
    assert repeated_subexpressions(expression)


def test_a_measure_with_no_vars_is_unaffected():
    """Splitting on VAR/RETURN must leave a plain expression whole."""
    expression = (
        'IF(SELECTEDVALUE(T[Type]) = "A", CALCULATE(SUM(Sales[Amount]), ALL(Product)), '
        'CALCULATE(SUM(Sales[Amount]), ALL(Product)))'
    )
    assert repeated_subexpressions(expression)


def test_the_var_keyword_inside_a_name_does_not_split():
    """``VARIANCE`` and a column called ``[Variance]`` are not block boundaries."""
    expression = """
    Spread =
    VAR _v = VARIANCE(Sales[Amount])
    RETURN _v + [Variance Target]
    """
    # No repeat here; the point is that it does not crash or mis-split.
    assert repeated_subexpressions(expression) == []
