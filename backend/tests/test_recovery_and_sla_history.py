"""Nothing-to-judge is N/A: post-failure integrity (9.3.4) and SLA history (9.4.4).

Both checks previously scored an environment as *failing* a practice it could not
possibly have:

* ``XW-POST-FAILURE-INTEGRITY`` failed a workspace with **no recovery path**, so
  its owner was told to add an integrity re-check to code that does not exist.
* ``XW-SLA-HISTORY`` failed a workspace with **no pipeline or notebook**, so a
  reporting workspace was told to retain execution history for jobs it never runs.

9.3.4 also kept a second, looser copy of the recovery-word table, which matched
``recover``/``repair``/``rerun`` inside longer words and reported capacity-metrics
notebooks as recovery paths.
"""
from __future__ import annotations

from auditfast.core.check.operations_reliability.data_operations.group import (
    sla_history_consistent,
)
from auditfast.core.check.operations_reliability.data_prep.group import (
    _notebook_recovery_contexts,
    post_failure_integrity_consistent,
)
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, Item, WorkspaceContext


def _ws(name: str, **kwargs) -> WorkspaceContext:
    ctx = WorkspaceContext(id=name, display_name=name, layer=Layer.MIXED)
    ctx.items = list(kwargs.pop("items", []))
    ctx.notebooks = dict(kwargs.pop("notebooks", {}))
    ctx.pipelines = dict(kwargs.pop("pipelines", {}))
    ctx.run_history = dict(kwargs.pop("run_history", {}))
    for resource in kwargs.pop("unavailable", ()):
        ctx.unavailable.add(resource)
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="G",
        members=tuple(GroupMemberContext(ws, lvl, Layer.MIXED) for ws, lvl in members),
        settings={},
    )


def _nb(source: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": source}]}


#: A recovery path that re-validates Bronze vs Silver counts and fails loudly.
_VALIDATING = (
    "if backfill_mode:\n"
    "    bronze_count = bronze_df.count()\n"
    "    silver_count = silver_df.count()\n"
    "    assert bronze_count == silver_count\n"
)
#: A recovery path with no integrity comparison at all.
_BARE_RECOVERY = "if backfill_mode:\n    reload_partition()\n"


# -- 9.3.4 ---------------------------------------------------------------------

def test_no_recovery_path_anywhere_is_na_not_a_failure():
    """The real Leadership Reporting case: nothing runs after a failure.

    "Validate integrity after a failure" presupposes something that runs after
    one. Reporting a workspace that has no such path as a gap sends its owner to
    fix code that does not exist.
    """
    envs = [_ws(name, notebooks={"NB": _nb("df = spark.table('sales')")})
            for name in ("rep-a", "rep-b")]
    verdict = post_failure_integrity_consistent(_group((envs[0], 9), (envs[1], 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "no recovery, replay or backfill path exists" in verdict.evidence


def test_an_environment_without_a_recovery_path_is_excluded_not_counted():
    """One env has a recovery path, one does not: only the first is judged."""
    has_path = _ws("dev", notebooks={"NB": _nb(_BARE_RECOVERY)})
    no_path = _ws("rep", notebooks={"NB": _nb("df = spark.table('sales')")})
    other = _ws("prod", notebooks={"NB": _nb(_BARE_RECOVERY)})
    verdict = post_failure_integrity_consistent(
        _group((has_path, 1), (no_path, 5), (other, 10)))
    assert verdict.score == 0
    assert "0 of 2 environment(s) that have one" in verdict.evidence
    assert "1 environment(s) excluded with nothing to validate on" in verdict.evidence


def test_a_recovery_path_that_revalidates_passes():
    envs = [_ws(name, notebooks={"NB_Backfill": _nb(_VALIDATING)})
            for name in ("dev", "prod")]
    verdict = post_failure_integrity_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3
    assert "NB_Backfill" in verdict.evidence


def test_a_recovery_path_without_a_recheck_still_fails():
    envs = [_ws(name, notebooks={"NB_Backfill": _nb(_BARE_RECOVERY)})
            for name in ("dev", "prod")]
    verdict = post_failure_integrity_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "do not re-validate cross-layer counts" in verdict.evidence


def test_a_lone_unvalidated_recovery_path_is_still_named_in_the_na():
    """N/A must not swallow a real finding.

    Only DEV has a recovery path, so there is no cross-environment comparison --
    but "DEV has a recovery path that does not re-validate" is exactly what 9.3.4
    exists to surface, and it would otherwise disappear into the N/A message.
    """
    dev = _ws("dev", notebooks={"NB_Backfill": _nb(_BARE_RECOVERY)})
    rep = _ws("rep", notebooks={"NB": _nb("df = spark.table('sales')")})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (rep, 10)))
    assert verdict.status is Status.NA
    assert "NB_Backfill" in verdict.evidence
    assert "do not re-validate cross-layer counts" in verdict.evidence


def test_a_lone_validating_environment_is_named_in_the_na():
    dev = _ws("dev", notebooks={"NB_Backfill": _nb(_VALIDATING)})
    rep = _ws("rep", notebooks={"NB": _nb("df = spark.table('sales')")})
    verdict = post_failure_integrity_consistent(_group((dev, 1), (rep, 10)))
    assert verdict.status is Status.NA
    assert "does re-validate on its recovery path" in verdict.evidence


# -- the recovery vocabulary ----------------------------------------------------

def test_recovery_words_in_a_printed_help_message_are_not_a_recovery_path():
    """The real MDM case: 11 notebooks flagged on a user instruction string.

    ``print("... Correct Step 1, re-run it, then re-run this cell.")`` is help
    text, not a recovery path. The unbounded pattern matched ``re-run`` inside it
    and reported capacity-metrics notebooks as failing to re-validate integrity.
    """
    code = 'print("Nothing to check. Correct Step 1, re-run it, then re-run this cell.")'
    assert _notebook_recovery_contexts(code) == []


def test_recovery_words_inside_a_longer_word_are_not_a_recovery_path():
    """Regression: the looser copy matched anywhere, flagging capacity notebooks.

    ``repairs`` and ``prerun`` both contained a recovery word as a substring, so
    an unbounded pattern read them as "this notebook runs after a failure".
    """
    for code in ("repairs_count = 0", "prerun_checks()", "discover_tables()"):
        assert _notebook_recovery_contexts(code) == [], code


def test_real_recovery_wordings_are_still_detected():
    assert "backfill" in _notebook_recovery_contexts("if backfill_mode:\n    pass")
    assert "recovery" in _notebook_recovery_contexts("run_recovery_job()")
    assert "replay" in _notebook_recovery_contexts("do_replay()")


def test_the_group_and_per_workspace_checks_share_one_recovery_table():
    """One vocabulary, two call sites on ref 9.3.4: they must not drift apart."""
    from auditfast.core.check.operations_reliability.data_prep import automated, group

    assert group._RECOVERY_CONTEXTS is automated._RECOVERY_CONTEXTS


# -- 9.4.4 ---------------------------------------------------------------------

def _pipeline_item(name: str) -> Item:
    return Item(id=f"{name}-id", type="DataPipeline", display_name=name)


def test_a_workspace_with_nothing_to_run_is_excluded():
    """The real Leadership Reporting case: reporting workspaces run no jobs."""
    envs = [_ws(name, items=[Item(id="r", type="Report", display_name="R")])
            for name in ("rep-a", "rep-b")]
    verdict = sla_history_consistent(_group((envs[0], 9), (envs[1], 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
    assert "holds no pipeline or notebook to run" in verdict.evidence


def test_recorded_runs_in_every_environment_passes():
    envs = [
        _ws(name, items=[_pipeline_item("PL")], run_history={"PL-id": ["2026-01-01"]})
        for name in ("dev", "prod")
    ]
    verdict = sla_history_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 3
    assert "1 of 1 have recorded runs" in verdict.evidence


def test_a_pipeline_with_no_recorded_run_is_a_real_gap():
    """One of two environments retains history: half coverage, so PARTIAL."""
    good = _ws("dev", items=[_pipeline_item("PL")],
               run_history={"PL-id": ["2026-01-01"]})
    bad = _ws("prod", items=[_pipeline_item("PL")], run_history={})
    verdict = sla_history_consistent(_group((good, 1), (bad, 10)))
    assert verdict.score == 1
    assert verdict.coverage == 0.5
    assert "none of 1 have recorded runs" in verdict.evidence


def test_history_is_resolved_by_item_type_not_by_any_run_at_all():
    """A semantic-model refresh is not pipeline history.

    Regression: the loose helper fell back to "the workspace recorded some run
    somewhere", so an environment whose only runs belonged to another item type
    counted as having pipeline history.
    """
    envs = [
        _ws(name,
            items=[_pipeline_item("PL"),
                   Item(id="sm-id", type="SemanticModel", display_name="SM")],
            run_history={"sm-id": ["2026-01-01"]})
        for name in ("dev", "prod")
    ]
    verdict = sla_history_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert verdict.score == 0
    assert "none of 1 have recorded runs" in verdict.evidence


def test_the_reporting_half_is_declared_unverifiable():
    envs = [
        _ws(name, items=[_pipeline_item("PL")], run_history={"PL-id": ["2026-01-01"]})
        for name in ("dev", "prod")
    ]
    verdict = sla_history_consistent(_group((envs[0], 1), (envs[1], 10)))
    assert "monitoring admin APIs this audit does not call" in verdict.evidence


def test_unreadable_run_history_is_skipped_not_failed():
    good = _ws("dev", items=[_pipeline_item("PL")],
               run_history={"PL-id": ["2026-01-01"]})
    unreadable = _ws("prod", items=[_pipeline_item("PL")],
                     unavailable=(Resource.ITEM_RUN_HISTORY,))
    verdict = sla_history_consistent(_group((good, 1), (unreadable, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
