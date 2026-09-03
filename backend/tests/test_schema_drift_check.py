"""The cross-workspace schema-drift check (ref 11.4.3b).

Unit-tests the ``XW-SCHEMA-DRIFT`` group check directly. It scores **shared**
tables — those present in every environment — on whether their column sets match.
A table present in only some environments is an inventory difference, reported but
not scored, and machine-generated tables are excluded outright. Missing schemas ⇒
N/A, never a low score.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_operations.group import schema_drift
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext


def _ws(name: str, tables: dict, *, columns_readable: bool = True) -> WorkspaceContext:
    ctx = WorkspaceContext(id=name, display_name=name, layer=Layer.OPERATIONS)
    ctx.tables = tables
    if not columns_readable:
        ctx.unavailable.add(Resource.TABLE_COLUMNS)
    return ctx


def _cols(*names: str) -> list[dict]:
    return [{"name": n, "type": "int"} for n in names]


def _group(*members: tuple[str, dict, int]) -> GroupContext:
    return GroupContext(
        name="Sales",
        members=tuple(
            GroupMemberContext(_ws(name, tables), level, Layer.OPERATIONS)
            for name, tables, level in members
        ),
        settings={},
    )


def test_identical_schemas_pass():
    tables = {"fact": {"columns": _cols("a", "b")}, "dim": {"columns": _cols("k")}}
    verdict = schema_drift(_group(("DEV", tables, 1), ("PROD", dict(tables), 10)))
    assert verdict.score == 3
    assert verdict.scored is True


def test_a_table_in_only_one_environment_is_reported_but_not_scored():
    """An inventory difference, not schema drift.

    Dev legitimately holds work in progress Prod has never seen. Scoring those
    made a real Dev/UAT/Prod estate 88% "drifted" (822 of 928 tables) and buried
    the finding that matters: the same table modelled two different ways.
    """
    dev = {"fact": {"columns": _cols("a", "b")}, "scratch": {"columns": _cols("x")}}
    prod = {"fact": {"columns": _cols("a", "b")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.score == 3
    assert "inventory difference" in verdict.evidence
    assert "scratch" in verdict.evidence


def test_a_machine_generated_table_is_not_compared_at_all():
    """The real MDM case: the first 'drifted' table was named ``<guid>_<guid>``."""
    guid_table = ("9fb7a13f6f324e3d929eddb3453222de_667e88b1_002d1954"
                  "_002d4541_002da514_002d5a0149c19352")
    dev = {"fact": {"columns": _cols("a", "b")}, guid_table: {"columns": _cols("x")}}
    prod = {"fact": {"columns": _cols("a", "b")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.score == 3
    assert guid_table not in verdict.evidence
    assert "inventory difference" not in verdict.evidence


def test_na_when_no_table_is_present_in_every_environment():
    dev = {"only_dev": {"columns": _cols("a")}}
    prod = {"only_prod": {"columns": _cols("a")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "no shared schema" in verdict.evidence


def test_column_mismatch_is_drift():
    dev = {"fact": {"columns": _cols("a", "b")}}
    prod = {"fact": {"columns": _cols("a", "b", "c")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "column mismatch" in verdict.evidence


def test_case_only_difference_is_not_drift():
    dev = {"Fact": {"columns": _cols("Amount")}}
    prod = {"fact": {"columns": _cols("amount")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.score == 3


def test_fewer_than_two_readable_members_is_na():
    dev = {"fact": {"columns": _cols("a")}}
    prod = {"fact": {"columns": _cols("a")}}
    group = GroupContext(
        name="Sales",
        members=(
            GroupMemberContext(_ws("DEV", dev), 1, Layer.OPERATIONS),
            GroupMemberContext(_ws("PROD", prod, columns_readable=False), 10, Layer.OPERATIONS),
        ),
        settings={},
    )
    verdict = schema_drift(group)
    assert verdict.status is Status.NA
    assert verdict.scored is False
