"""Cross-workspace (group) checks ported from the local check set.

Seventeen best-practice points are implemented as ``@group_check``s in the
separate ``GROUP_REGISTRY``, so they run only for a project group (>=2 members)
and never touch a normal single-workspace audit. These tests pin that they are
registered, run over the fixture group without error, and obey N/A-not-FAIL.
"""
from __future__ import annotations

import pytest

from auditfast.core.check.registry import GROUP_REGISTRY, CheckRegistry
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Scope, Status

from .conftest import FIXTURE_SETTINGS

#: The 17 checks ported from the local set, id -> ref.
PORTED = {
    "XW-MEDALLION-CONSIST": "1.1.5",
    "XW-PIPELINE-SLA": "9.4.2",
    "XW-SLA-ALERTS": "9.4.3",
    "XW-SLA-HISTORY": "9.4.4",
    "XW-TIER-SEP": "11.3.1",
    "XW-MEDALLION-DRIFT": "11.4.3",
    "XW-SPARK-LOGS": "10.1.2",
    "XW-WH-LOAD-MON": "10.1.5",
    "XW-AUDIT-SCHEMA": "10.2.1",
    "XW-AUDIT-QUERYABLE": "10.2.5",
    "XW-CONFORMED-DIM": "4.4.9",
    "XW-AGG-CONSIST": "5.4.3",
    "XW-ACCESS-AUDIT": "7.4.3",
    "XW-LINEAGE-E2E": "8.1.2",
    "XW-TECH-METADATA": "8.3.2",
    "XW-CU-ALERTS": "12.2.7",
    "XW-SECRET-SCAN": "11.1.8",
}

_THREE_MEMBER_GROUP = [(
    "Proj",
    (
        ("ws-prep-01", Layer.PREP, 1),
        ("ws-store-01", Layer.STORAGE, 5),
        ("ws-ops-01", Layer.OPERATIONS, 10),
    ),
)]


def test_all_seventeen_ported_checks_are_registered():
    specs = {spec.id: spec for spec in GROUP_REGISTRY}
    for check_id, ref in PORTED.items():
        assert check_id in specs, f"{check_id} not registered"
        assert specs[check_id].ref == ref


@pytest.mark.parametrize("check_id,ref", sorted(PORTED.items()))
def test_ported_ref_has_remediation_text(check_id, ref):
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    assert load_remediation(load_project(PROJECT_FILE)).get(ref), (
        f"{check_id} (ref {ref}) has no remediation text"
    )


def test_group_checks_run_over_the_fixture_group_without_error(provider):
    """Every group check produces exactly one scored-or-N/A result, no exception."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=_THREE_MEMBER_GROUP,
        group_registry=GROUP_REGISTRY,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    # One result per registered group check (18 = 17 ported + XW-SCHEMA-DRIFT).
    assert len(group_results) == len(GROUP_REGISTRY)
    valid = {Status.PASS, Status.PARTIAL, Status.FAIL, Status.NA}
    for result in group_results:
        assert result.status in valid, f"{result.check_id}: {result.status}"
        assert result.workspace == "Proj"
        assert result.evidence


def test_ported_checks_are_na_with_a_single_readable_member(provider):
    """Fewer than two readable members => N/A for every group check (never FAIL)."""
    results = run_audit(
        provider, [], FIXTURE_SETTINGS,
        registry=CheckRegistry(),
        groups=[("Solo", (("ws-prep-01", Layer.PREP, 1), ("missing-ws", Layer.MIXED, 10)))],
        group_registry=GROUP_REGISTRY,
    )
    group_results = [r for r in results if r.scope is Scope.GROUP]
    ported_ids = set(PORTED)
    for result in group_results:
        if result.check_id in ported_ids:
            assert result.status is Status.NA
            assert result.scored is False
