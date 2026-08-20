"""Step 4b: the first real cross-workspace check — schema drift (ref 9.1.4).

Unit-tests the ``XW-SCHEMA-DRIFT`` group check directly: it compares each group
member's table/column signatures and flags tables that are missing in some
environment or whose columns differ. Missing schemas ⇒ N/A, never a low score.
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


def test_missing_table_is_drift():
    dev = {"fact": {"columns": _cols("a", "b")}, "scratch": {"columns": _cols("x")}}
    prod = {"fact": {"columns": _cols("a", "b")}}
    verdict = schema_drift(_group(("DEV", dev, 1), ("PROD", prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "scratch" in verdict.evidence


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
