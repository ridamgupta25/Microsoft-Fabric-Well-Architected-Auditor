"""The cross-workspace post-failure-integrity check (ref 9.3.4).

Unit-tests ``XW-POST-FAILURE-INTEGRITY``: every environment must have a notebook
that re-checks cross-layer integrity on a recovery/replay path. Fewer than two
readable members ⇒ N/A, never a low score.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_prep.group import (
    post_failure_integrity_consistent,
)
from auditfast.core.check.registry import GROUP_REGISTRY
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext

#: A notebook whose recovery path compares two layers and blocks on a mismatch.
_VALIDATING_CODE = (
    "if backfill_mode:\n"
    "    bronze_count = bronze_df.count()\n"
    "    silver_count = silver_df.count()\n"
    "    assert bronze_count == silver_count, 'layer mismatch'\n"
)
#: A recovery path that compares nothing — detected, not enforced.
_NON_VALIDATING_CODE = "if backfill_mode:\n    silver_df.write.save('/tmp/out')\n"


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}]}


def _ws(ws_id: str, *, notebooks: dict | None = None,
        pipelines: dict | None = None, readable: bool = True) -> WorkspaceContext:
    ctx = WorkspaceContext(id=ws_id, display_name=ws_id, layer=Layer.PREP)
    ctx.notebooks = notebooks or {}
    ctx.pipelines = pipelines or {}
    if not readable:
        ctx.unavailable.add(Resource.NOTEBOOK_DEFINITIONS)
        ctx.unavailable.add(Resource.PIPELINE_DEFINITIONS)
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="Sales",
        members=tuple(
            GroupMemberContext(ws, level, Layer.PREP) for ws, level in members
        ),
        settings={},
    )


def test_registered_with_ref_and_remediation():
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    spec = GROUP_REGISTRY.get("XW-POST-FAILURE-INTEGRITY")
    assert spec is not None
    assert spec.ref == "9.3.4"
    assert load_remediation(load_project(PROJECT_FILE)).get("9.3.4")


def test_all_environments_validate_passes():
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", notebooks={"NB": _nb(_VALIDATING_CODE)})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3
    assert verdict.scored is True


def test_validation_missing_in_one_environment_is_drift():
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", notebooks={"NB": _nb(_NON_VALIDATING_CODE)})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "ws-prod" in verdict.evidence


def test_commented_out_validation_does_not_count():
    commented = "if backfill_mode:\n    # assert bronze_df.count() == silver_df.count()\n    pass\n"
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", notebooks={"NB": _nb(commented)})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3


def test_fewer_than_two_readable_members_is_na():
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", readable=False)
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False


#: A pipeline whose on-failure branch runs a row-count reconciliation.
_VALIDATING_PIPELINE = {
    "properties": {"activities": [
        {"name": "Load", "type": "TridentNotebook"},
        {"name": "Reconcile row_count on failure", "type": "Lookup",
         "dependsOn": [{"activity": "Load",
                        "dependencyConditions": ["Failed"]}]},
    ]}
}


def test_pipeline_post_failure_reconciliation_counts_as_validation():
    dev = _ws("ws-dev", pipelines={"PL": _VALIDATING_PIPELINE})
    prod = _ws("ws-prod", pipelines={"PL": _VALIDATING_PIPELINE})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_failure_branch_without_reconciliation_does_not_count():
    # A failure branch that only sends an alert is not integrity validation.
    alert_only = {"properties": {"activities": [
        {"name": "Load", "type": "TridentNotebook"},
        {"name": "Send alert", "type": "WebActivity",
         "dependsOn": [{"activity": "Load", "dependencyConditions": ["Failed"]}]},
    ]}}
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", pipelines={"PL": alert_only})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
    assert "ws-prod" in verdict.evidence


def test_reconciliation_without_a_failure_path_does_not_count():
    # Routine reconciliation with no recovery/failure signal is 5.4.6, not 9.3.4.
    routine = {"properties": {"activities": [
        {"name": "Reconcile row_count", "type": "Lookup"},
    ]}}
    dev = _ws("ws-dev", notebooks={"NB": _nb(_VALIDATING_CODE)})
    prod = _ws("ws-prod", pipelines={"PL": routine})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3
