"""The cross-workspace medallion completeness check (ref 1.1.5).

``XW-MEDALLION-CONSIST`` used to pass an environment that named *any* medallion
token, so a Bronze-only estate scored 3 against a title promising
"Bronze -> Silver -> Gold implemented consistently". It now grades the tiers
common to every environment on the same 0-3 ladder as the per-workspace
``WS-MEDALLION``, which shares the ref.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_operations.group import (
    medallion_consistent,
)
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext
from auditfast.core.scoring import status_from_score


def _ws(name: str, *stores: tuple[str, str], unreadable: bool = False) -> WorkspaceContext:
    """A workspace whose ``stores`` are ``(display_name, item_type)`` pairs."""
    ctx = WorkspaceContext(id=name, display_name=name, layer=Layer.OPERATIONS)
    ctx.items = [
        Item(id=f"{name}-{index}", type=kind, display_name=store)
        for index, (store, kind) in enumerate(stores)
    ]
    if unreadable:
        ctx.unavailable.add(Resource.ITEMS)
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="MDM",
        members=tuple(
            GroupMemberContext(ws, level, Layer.OPERATIONS) for ws, level in members
        ),
        settings={},
    )


def _full(name: str) -> WorkspaceContext:
    return _ws(name,
               ("LH_Bronze", "Lakehouse"),
               ("LH_Silver", "Lakehouse"),
               ("WH_Gold", "Warehouse"))


def test_all_three_tiers_everywhere_scores_three():
    verdict = medallion_consistent(_group((_full("dev"), 1), (_full("prod"), 10)))
    assert verdict.score == 3
    assert "Bronze -> Silver -> Gold are all named" in verdict.evidence


def test_bronze_and_silver_only_is_partial_not_a_pass():
    """The real MDM estate: Bronze + Silver in every environment, no Gold.

    This is the regression. The old check returned 3 because each environment
    named at least one tier.
    """
    envs = [_ws(name, ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"))
            for name in ("dev", "uat", "prod")]
    verdict = medallion_consistent(
        _group((envs[0], 1), (envs[1], 5), (envs[2], 9)))
    assert verdict.score == 2
    assert status_from_score(verdict.score) is Status.PARTIAL
    assert "2 of 3 medallion tier(s)" in verdict.evidence
    assert "Gold" in verdict.evidence


def test_a_single_tier_everywhere_scores_one():
    envs = [_ws(name, ("LH_Bronze", "Lakehouse")) for name in ("dev", "prod")]
    verdict = medallion_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 1
    assert "1 of 3 medallion tier(s)" in verdict.evidence


def test_no_tier_named_anywhere_scores_zero():
    envs = [_ws(name, ("LH_One", "Lakehouse"), ("WH_Two", "Warehouse"))
            for name in ("dev", "prod")]
    verdict = medallion_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "no medallion tier is named in all" in verdict.evidence


def test_a_tier_present_in_only_one_environment_is_not_common():
    """Gold in Dev alone is named as drift, but does not count as implemented.

    Scoring the drift itself belongs to XW-MEDALLION-DRIFT (11.4.3a); this check
    only reports it so the two do not double-count the same gap.
    """
    dev = _full("dev")
    prod = _ws("prod", ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"))
    verdict = medallion_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 2
    assert "Gold is named only in" in verdict.evidence
    assert "dev" in verdict.evidence


def test_a_serving_warehouse_name_counts_as_gold():
    """'serving' is a Gold token, so this estate does declare the full set."""
    envs = [
        _ws(name, ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"),
            ("WH_Serving_Store", "Warehouse"))
        for name in ("dev", "prod")
    ]
    verdict = medallion_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3


def test_untiered_warehouse_is_named_as_the_gold_candidate():
    envs = [
        _ws(name, ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"),
            ("WH_Reporting_Store", "Warehouse"))
        for name in ("dev", "prod")
    ]
    verdict = medallion_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 2
    assert "serving Warehouse is present but unnamed" in verdict.evidence
    assert "WH_Reporting_Store" in verdict.evidence


def test_a_workspace_with_no_data_store_is_excluded_not_failed():
    """The cross-LOB case: a reporting workspace has no tier to declare.

    A workspace holding no Lakehouse, Warehouse or database implements no
    medallion tier because it has nothing to place in one. Scoring it 0 for "not
    declaring its layers" is a finding about a practice it cannot have -- the
    same category error as telling a Warehouse-less workspace to enable SQL
    audit. The per-workspace WS-MEDALLION already returns N/A here.
    """
    reporting = [
        _ws(name, ("Sales Report", "Report"), ("Sales Model", "SemanticModel"))
        for name in ("rep-a", "rep-b")
    ]
    verdict = medallion_consistent(_group((reporting[0], 9), (reporting[1], 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "holding no Lakehouse, Warehouse or database" in verdict.evidence


def test_a_storeless_member_does_not_drag_down_the_others():
    """Two store-holding environments are still judged; the third is excluded."""
    dev = _full("dev")
    prod = _full("prod")
    reporting = _ws("rep", ("Sales Report", "Report"))
    verdict = medallion_consistent(
        _group((dev, 1), (reporting, 5), (prod, 10)))
    assert verdict.score == 3
    assert "1 environment(s) excluded" in verdict.evidence
    assert "rep" in verdict.evidence


def test_stores_present_but_unnamed_is_still_a_real_zero():
    """The exclusion must not swallow the genuine finding it sits next to."""
    envs = [_ws(name, ("LH_One", "Lakehouse"), ("WH_Two", "Warehouse"))
            for name in ("dev", "prod")]
    verdict = medallion_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "no medallion tier is named" in verdict.evidence


def test_fewer_than_two_readable_members_is_na():
    dev = _full("dev")
    prod = _ws("prod", ("LH_Bronze", "Lakehouse"), unreadable=True)
    verdict = medallion_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False


def test_workspace_name_can_declare_the_tier():
    """An estate giving each tier its own workspace declares it in the name."""
    dev = _ws("Gold_Serving_Dev", ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"))
    prod = _ws("Gold_Serving_Prod", ("LH_Bronze", "Lakehouse"), ("LH_Silver", "Lakehouse"))
    verdict = medallion_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_the_two_token_maps_agree():
    """One vocabulary, two call sites: they must not drift apart again."""
    from auditfast.core.check import _xw
    from auditfast.core.check.operations_reliability.data_operations import automated

    assert {
        token.upper(): tier for token, tier in _xw.MEDALLION_TOKENS.items()
    } == automated.MEDALLION_TOKENS
    assert automated.MEDALLION_ORDER == _xw.MEDALLION_ORDER
