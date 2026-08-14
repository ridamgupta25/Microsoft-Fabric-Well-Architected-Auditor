"""Step 4a: the engine capability to run a cross-workspace (group) check.

These pin the new behaviour without adding any production group check, so an
ordinary audit is unaffected:

* a group check runs once per group over its members' contexts, sorted dev → prod;
* fewer than two readable members ⇒ N/A, never a low score;
* no groups (or an empty group registry) ⇒ no group results at all;
* the global GROUP_REGISTRY is empty, so real audits run no group checks.
"""
from __future__ import annotations

from auditfast.core.check.helpers import binary
from auditfast.core.check.registry import (
    GROUP_REGISTRY,
    CheckRegistry,
    GroupCheckRegistry,
    group_check,
)
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Pillar, Scope, Status
from auditfast.core.models import GroupContext

from .conftest import FIXTURE_SETTINGS


def test_group_registry_holds_the_registered_cross_workspace_checks():
    """The production group registry holds the cross-workspace checks that ship."""
    ids = {spec.id for spec in GROUP_REGISTRY}
    assert "XW-SCHEMA-DRIFT" in ids


def test_group_check_runs_over_members_sorted_dev_to_prod(provider):
    reg = GroupCheckRegistry()
    seen: dict[str, object] = {}

    @group_check(id="G-CMP", ref="G.1", title="compare members",
                 pillar=Pillar.OPERATIONS, registry=reg)
    def compare(ctx: GroupContext):
        seen["count"] = len(ctx.members)
        seen["levels"] = [m.environment_level for m in ctx.members]
        seen["name"] = ctx.name
        return binary(True, f"{len(ctx.members)} members compared")

    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        groups=[("Proj", (("ws-prep-01", Layer.PREP, 10), ("ws-store-01", Layer.STORAGE, 1)))],
        group_registry=reg,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    assert len(group_results) == 1
    result = group_results[0]
    assert result.workspace == "Proj"
    assert result.status is Status.PASS
    assert result.scored is True
    # The check saw both members, ordered by environment level (dev → prod).
    assert seen["count"] == 2
    assert seen["levels"] == [1, 10]
    assert seen["name"] == "Proj"


def test_group_check_reports_na_with_fewer_than_two_readable_members(provider):
    reg = GroupCheckRegistry()

    @group_check(id="G-NA", ref="G.2", title="needs two",
                 pillar=Pillar.OPERATIONS, registry=reg)
    def compare(ctx: GroupContext):
        return binary(True, "should not be reached")

    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        groups=[("Solo", (("ws-prep-01", Layer.PREP, 1), ("missing-ws", Layer.MIXED, 10)))],
        group_registry=reg,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    assert len(group_results) == 1
    assert group_results[0].status is Status.NA
    assert group_results[0].scored is False


def test_no_group_results_when_no_groups_passed(provider):
    results = run_audit(
        provider, [("ws-prep-01", Layer.PREP)], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
    )
    assert not any(r.scope is Scope.GROUP for r in results)


def test_group_check_reuses_already_crawled_member_contexts(provider):
    """A member audited individually is not re-fetched for the group phase."""
    fetched: list[str] = []
    original = provider.fetch

    def counting_fetch(workspace_id, layer=Layer.MIXED, resources=()):
        fetched.append(workspace_id)
        return original(workspace_id, layer, resources)

    provider.fetch = counting_fetch  # type: ignore[method-assign]

    reg = GroupCheckRegistry()

    @group_check(id="G-REUSE", ref="G.3", title="reuse",
                 pillar=Pillar.OPERATIONS, registry=reg)
    def compare(ctx: GroupContext):
        return binary(len(ctx.members) == 2, "two members")

    # Both members are also audited individually (single check registry empty is
    # fine — pass them as targets so the main loop fetches them once).
    single = CheckRegistry()
    run_audit(
        provider, [("ws-prep-01", Layer.PREP), ("ws-store-01", Layer.STORAGE)],
        FIXTURE_SETTINGS,
        registry=single,
        groups=[("Proj", (("ws-prep-01", Layer.PREP, 1), ("ws-store-01", Layer.STORAGE, 2)))],
        group_registry=reg,
    )
    # An empty single registry fetches nothing in the main loop, so the group
    # phase fetches each member exactly once — never twice.
    assert sorted(fetched) == ["ws-prep-01", "ws-store-01"]
