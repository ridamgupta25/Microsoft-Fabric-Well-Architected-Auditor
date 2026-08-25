"""The offline advisory bundle: export, judge elsewhere, import.

The route exists so advisory findings can be judged without a server-side model
key. It introduces one risk the API route does not have - a CSV a human can edit
- so the tests that matter most are the ones proving that CSV cannot reach the
deterministic score.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from auditfast.ai.advisory_bundle import (
    AdvisoryVerdictError,
    apply_verdicts,
    build_bundle,
    bundle_contexts,
    bundle_ids,
    finding_id,
    read_verdicts,
    results_from_bundle,
    theme_of,
    write_bundle,
    write_themed_bundles,
)
from auditfast.core.enums import Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult

ADVISORY_REF = "4.5.2"       # in ADVISORY_CHECKLIST
DETERMINISTIC_REF = "3.3.2"  # not in ADVISORY_CHECKLIST


def _result(ref=ADVISORY_REF, check_id="TB-FACT-GRAIN", score=0,
            status=Status.FAIL, obj="dim_x") -> CheckResult:
    return CheckResult(
        check_id=check_id, ref=ref, title="Fact grain",
        pillar=Pillar.DATA_MODELING, status=status, score=score,
        evidence="deterministic evidence", severity=Severity.MEDIUM,
        workspace="WS", obj=obj, scope=Scope.WORKSPACE,
    )


def _csv(tmp_path, rows: str, name="verdicts.csv"):
    path = tmp_path / name
    path.write_text(rows, encoding="utf-8")
    return path


# --- export -----------------------------------------------------------------

def test_the_bundle_carries_what_a_judge_needs():
    records = build_bundle([_result()], {})
    assert len(records) == 1
    record = records[0]
    for field in ("finding_id", "ref", "check_id", "rule", "workspace",
                  "object", "deterministic", "evidence", "why_advisory"):
        assert field in record
    assert record["deterministic"]["status"] == "FAIL"


def test_only_advisory_findings_are_exported():
    records = build_bundle([_result(), _result(ref=DETERMINISTIC_REF)], {})
    assert [r["ref"] for r in records] == [ADVISORY_REF]


def test_the_bundle_is_valid_jsonl(tmp_path):
    path = write_bundle([_result(), _result(obj="dim_y")], {}, tmp_path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["ref"] == ADVISORY_REF for line in lines)


def test_finding_ids_are_stable_for_identical_content():
    assert finding_id(_result(), "ctx") == finding_id(_result(), "ctx")


def test_finding_ids_change_when_the_evidence_changes():
    """A re-crawl that altered the evidence must invalidate an old verdict."""
    assert finding_id(_result(), "ctx") != finding_id(_result(), "different ctx")


def test_distinct_objects_get_distinct_ids():
    assert finding_id(_result(obj="a"), "c") != finding_id(_result(obj="b"), "c")


def test_two_workspaces_sharing_a_name_do_not_collide():
    """CheckResult carries the display name only, so the id would otherwise be
    identical for the same check and object in two workspaces both called Dev."""
    result = _result(obj="load.ipynb")
    assert finding_id(result, "same evidence", "workspace-guid-1") != finding_id(
        result, "same evidence", "workspace-guid-2"
    )


def test_the_bundle_uses_the_workspace_id_when_the_context_has_one():
    from auditfast.core.models import WorkspaceContext

    result = _result(obj="load.ipynb")
    one = build_bundle([result], {"WS": WorkspaceContext(id="guid-1", display_name="WS")})
    two = build_bundle([result], {"WS": WorkspaceContext(id="guid-2", display_name="WS")})
    assert one[0]["finding_id"] != two[0]["finding_id"]


# --- import: the safety properties -----------------------------------------

def test_a_verdict_cannot_reach_a_deterministic_check(tmp_path):
    """The whole point of the guard: an edited CSV must not move the score."""
    result = _result(ref=DETERMINISTIC_REF, score=0)
    key = finding_id(result, "")
    csv_path = _csv(tmp_path, f"finding_id,score,confidence\n{key},3,high\n")
    out, summary = apply_verdicts([result], {}, csv_path)
    assert out[0].score == 0
    assert out[0].source == "automated"
    assert summary["rejected_non_advisory"] == 1


def test_a_low_confidence_verdict_is_not_applied(tmp_path):
    result = _result(score=0)
    key = finding_id(result, "")
    csv_path = _csv(tmp_path, f"finding_id,score,confidence\n{key},3,low\n")
    out, summary = apply_verdicts([result], {}, csv_path)
    assert out[0].score == 0
    assert out[0].source == "automated"
    assert summary["skipped_low_confidence"] == 1


def test_a_stale_verdict_does_not_match(tmp_path):
    """A verdict keyed to different evidence is unmatched, not misapplied."""
    csv_path = _csv(tmp_path, "finding_id,score,confidence\ndeadbeefdeadbeef,3,high\n")
    out, summary = apply_verdicts([_result(score=1)], {}, csv_path)
    assert out[0].score == 1
    assert summary["unmatched"] == 1
    assert summary["applied"] == 0


# --- import: the happy path -------------------------------------------------

def test_a_high_confidence_verdict_rewrites_the_advisory_result(tmp_path):
    result = _result(score=0, status=Status.FAIL)
    key = finding_id(result, "")
    csv_path = _csv(
        tmp_path,
        "finding_id,score,evidence,recommendation,confidence,judged_by\n"
        f"{key},3,\"Grain is declared in the notebook\",,high,copilot\n",
    )
    out, summary = apply_verdicts([result], {}, csv_path)
    assert summary["applied"] == 1
    assert out[0].score == 3
    assert out[0].status is Status.PASS
    assert out[0].source == "advisory-offline"


def test_the_evidence_records_who_judged_it_and_how_sure(tmp_path):
    """A reader must never mistake an offline judgment for a rule-based one."""
    result = _result()
    key = finding_id(result, "")
    csv_path = _csv(
        tmp_path,
        "finding_id,score,evidence,recommendation,confidence,judged_by\n"
        f"{key},2,Partly met,Do X,medium,copilot\n",
    )
    out, _ = apply_verdicts([result], {}, csv_path)
    assert out[0].evidence.startswith("[offline - copilot - medium confidence]")
    assert out[0].recommendation == "Do X"


# --- import: malformed input ------------------------------------------------

def test_a_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(AdvisoryVerdictError, match="No verdict file"):
        read_verdicts(tmp_path / "nope.csv")


def test_missing_columns_name_what_is_missing(tmp_path):
    path = _csv(tmp_path, "finding_id,evidence\nabc,hello\n")
    with pytest.raises(AdvisoryVerdictError, match="score"):
        read_verdicts(path)


@pytest.mark.parametrize("bad", ["4", "-1", "99"])
def test_a_score_outside_the_rubric_is_rejected(tmp_path, bad):
    path = _csv(tmp_path, f"finding_id,score\nabc,{bad}\n")
    with pytest.raises(AdvisoryVerdictError, match="outside 0-3"):
        read_verdicts(path)


def test_a_non_numeric_score_is_rejected(tmp_path):
    path = _csv(tmp_path, "finding_id,score\nabc,excellent\n")
    with pytest.raises(AdvisoryVerdictError, match="not a whole number"):
        read_verdicts(path)


def test_confidence_defaults_to_medium_when_absent(tmp_path):
    path = _csv(tmp_path, "finding_id,score\nabc,2\n")
    assert read_verdicts(path)["abc"]["confidence"] == "medium"


# --- the full round trip ----------------------------------------------------
#
# Export -> judge -> import has to work from the bundle *alone*: the reviewer may
# apply verdicts on another machine, days later, with the audit long gone from
# memory and no ability to re-crawl. If the bundle is not self-contained the
# whole offline route is theatre.

def test_the_bundle_alone_can_rebuild_the_results(tmp_path):
    original = _result(score=1, status=Status.PARTIAL, obj="fact_sales")
    write_bundle([original], {}, tmp_path)
    rebuilt = results_from_bundle(tmp_path / "advisory-bundle.jsonl")
    assert len(rebuilt) == 1
    for field in ("check_id", "ref", "title", "workspace", "obj",
                  "score", "severity", "pillar", "scope"):
        assert getattr(rebuilt[0], field) == getattr(original, field)


def test_verdicts_apply_against_a_bundle_with_no_live_context(tmp_path):
    """The offline path: nothing but the bundle and the CSV."""
    original = _result(score=0, status=Status.FAIL, obj="fact_sales")
    write_bundle([original], {}, tmp_path)
    bundle = tmp_path / "advisory-bundle.jsonl"

    evidence = bundle_contexts(bundle)
    key = next(iter(evidence))
    csv_path = _csv(
        tmp_path,
        "finding_id,score,evidence,recommendation,confidence,judged_by\n"
        f"{key},3,Grain is declared,,high,copilot\n",
    )

    rebuilt = results_from_bundle(bundle)
    judged, summary = apply_verdicts(
        rebuilt, {}, csv_path, evidence_by_id=evidence
    )
    assert summary["applied"] == 1
    assert judged[0].score == 3
    assert judged[0].status is Status.PASS
    assert judged[0].source == "advisory-offline"


def test_a_malformed_bundle_line_is_a_clear_error(tmp_path):
    path = tmp_path / "advisory-bundle.jsonl"
    path.write_text('{"check_id": "X"}\n', encoding="utf-8")
    with pytest.raises(AdvisoryVerdictError, match="record 1 is malformed"):
        results_from_bundle(path)


def test_a_missing_bundle_is_a_clear_error(tmp_path):
    with pytest.raises(AdvisoryVerdictError, match="No bundle"):
        results_from_bundle(tmp_path / "nope.jsonl")


# --- the evidence a workspace-scope check is given --------------------------
#
# These checks ask about table *columns* - audit columns, surrogate keys, grain.
# An earlier version sent table names only, which made them unanswerable: the
# model had strictly less to go on than the rule it was meant to improve.

def test_a_workspace_summary_carries_columns_not_just_table_names():
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.fact_sales": {"columns": [
            {"name": "sales_key"}, {"name": "created_date"}, {"name": "batch_id"},
        ]},
    })
    summary = _workspace_summary(workspace)
    assert "created_date" in summary
    assert "batch_id" in summary
    assert "fact_sales" in summary


def test_platform_tables_do_not_crowd_out_solution_tables():
    """Fabric's own bookkeeping tables are numerous and tell a reviewer nothing."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.fact_sales": {"columns": [{"name": "sales_key"}]},
        "external_delta_tables": {"columns": [{"name": "x"}]},
        "managed_delta_table_checkpoints": {"columns": [{"name": "y"}]},
    })
    summary = _workspace_summary(workspace)
    assert "fact_sales" in summary
    assert "external_delta_tables" not in summary
    assert "platform/staging table(s) omitted" in summary


def test_dataflow_staging_artefacts_are_skipped():
    """GUID-named staging tables sort first and ate the sample the model sees."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    noisy = "1ea4e6199bee4048942d5ec1c8be2a44_b3d7785a_002D15f6_002D4cf1"
    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        noisy: {"columns": [{"name": "column1"}]},
        "dbo.fact_sales": {"columns": [{"name": "sales_key"}, {"name": "batch_id"}]},
    })
    summary = _workspace_summary(workspace)
    assert "fact_sales" in summary
    assert "batch_id" in summary
    assert noisy not in summary


def test_a_table_whose_columns_could_not_be_read_says_so():
    """Silence would read as 'no columns'; the reviewer must see it was unreadable."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 tables={"dbo.mystery": {}})
    assert "columns not readable" in _workspace_summary(workspace)


# --- evidence the widened workspace checks depend on ------------------------
#
# Several refs were moved to the advisory list because their rule reads a name
# vocabulary. A reader can only do better if the summary carries the structural
# facts the rule was guessing at.

def test_the_summary_lists_items_with_their_run_stamps():
    """WS-GOLD-FRESHNESS and OPS-MONITOR-REFRESH have nothing to judge without."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import Item, WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", items=[
        Item(id="1", type="Lakehouse", display_name="lh_analytics",
             last_run_utc="2026-08-01T00:00:00Z"),
    ])
    summary = _workspace_summary(workspace)
    assert "lh_analytics (Lakehouse)" in summary
    assert "last_run=2026-08-01" in summary


def test_the_summary_lists_role_assignments():
    """WS-METADATA-WRITE asks who may write; it needs the principals."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import RoleAssignment, WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", role_assignments=[
        RoleAssignment(principal_type="User", display_name="a.person", role="Admin"),
    ])
    summary = _workspace_summary(workspace)
    assert "a.person (User) = Admin" in summary


def test_a_masked_column_is_marked():
    """WS-DDM cannot tell a protected column from an exposed one otherwise."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.customer": {"columns": [
            {"name": "email", "is_masked": True},
            {"name": "order_id"},
        ]},
    })
    summary = _workspace_summary(workspace)
    assert "email [masked]" in summary
    assert "order_id," in summary or "order_id" in summary


def test_the_summary_names_notebooks_and_pipelines():
    """Workspace-scoped audit checks ask what runs here, not just what is stored."""
    from auditfast.ai.advisory import _workspace_summary
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 notebooks={"nb_load": {}},
                                 pipelines={"pl_ingest": {}})
    summary = _workspace_summary(workspace)
    assert "nb_load" in summary
    assert "pl_ingest" in summary


# --- what a semantic-model check is given -----------------------------------
#
# These checks fell through to the workspace summary, so 5.4.1 - "do
# relationships join on surrogate keys?", the largest single ref on a real
# estate at 411 findings - was handed a list of tables and never saw a
# relationship at all.

def _model_context(model: dict, obj: str = "M"):
    from auditfast.ai.advisory import _kb_context
    from auditfast.core.enums import Scope
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 semantic_models={obj: model},
                                 tables={"dbo.unrelated": {"columns": [{"name": "x"}]}})
    result = _result(ref="5.4.1", check_id="SM-FK-SURROGATE", obj=obj)
    return _kb_context(replace(result, scope=Scope.SEMANTIC_MODEL), workspace)


def test_a_semantic_model_check_sees_its_relationships():
    context = _model_context({
        "tables": ["Sales", "Customer"],
        "relationships": [{
            "from_table": "Sales", "from_column": "customer_key",
            "to_table": "Customer", "to_column": "customer_key",
            "from_cardinality": "many", "to_cardinality": "one",
        }],
    })
    assert "RELATIONSHIPS (1)" in context
    assert "Sales[customer_key] -> Customer[customer_key]" in context
    assert "many->one" in context


def test_a_semantic_model_check_sees_its_measures_not_the_table_list():
    context = _model_context({
        "measures": [{"table": "Sales", "name": "Total",
                      "expression": "SUM(Sales[Amount])"}],
    })
    assert "Sales[Total] = SUM(Sales[Amount])" in context
    assert "dbo.unrelated" not in context


def test_an_unreadable_model_falls_back_to_the_workspace_view():
    """Better the estate than nothing when the definition could not be read."""
    from auditfast.ai.advisory import _kb_context
    from auditfast.core.enums import Scope
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", semantic_models={},
                                 tables={"dbo.fact": {"columns": [{"name": "k"}]}})
    result = replace(_result(obj="missing"), scope=Scope.SEMANTIC_MODEL)
    assert "dbo.fact" in _kb_context(result, workspace)


# --- themed split -----------------------------------------------------------
#
# One workspace produced 1,940 advisory findings. A single review session cannot
# judge that many well, so the bundle is split by the question each check asks.

def test_every_advisory_ref_belongs_to_a_theme():
    """An unthemed ref would silently land in 'other' and be judged out of context."""
    from auditfast.core.advisory import ADVISORY_CHECKLIST

    unthemed = sorted(r for r in ADVISORY_CHECKLIST if theme_of(r) == "other")
    assert not unthemed, f"refs missing from THEMES: {unthemed}"


def test_every_ref_names_a_theme_that_exists():
    """A typo in a theme name would give a job no question to work to."""
    from auditfast.core.advisory import ADVISORY_CHECKLIST, THEMES

    unknown = sorted({entry[0] for entry in ADVISORY_CHECKLIST.values()} - set(THEMES))
    assert not unknown, f"themes used but not defined: {unknown}"


def test_every_theme_is_used_by_at_least_one_ref():
    """A theme nobody references is dead weight that reads as coverage."""
    from auditfast.core.advisory import ADVISORY_CHECKLIST, THEMES

    used = {entry[0] for entry in ADVISORY_CHECKLIST.values()}
    assert not sorted(set(THEMES) - used), f"themes with no refs: {sorted(set(THEMES) - used)}"


def test_no_ref_is_claimed_by_two_themes():
    """One ref, one theme - the structure makes this true by construction."""
    from auditfast.core.advisory import ADVISORY_CHECKLIST

    assert all(isinstance(entry, tuple) and len(entry) == 2
               for entry in ADVISORY_CHECKLIST.values())


def test_the_scoring_guide_states_the_engine_bands():
    """A reader must band a ratio the same way band_from_coverage does."""
    from auditfast.core.advisory import SCORING_GUIDE

    for band in ("1.00", "0.80", "0.50"):
        assert band in SCORING_GUIDE
    assert "confidence=low" in SCORING_GUIDE


def test_the_scoring_guide_addresses_the_sample():
    """The evidence holds 40 tables of hundreds, so 'count them' is not possible.

    Without this the reader either extrapolates from the sample - inventing a
    number - or refuses to score. It must take the population count from the
    deterministic evidence and use the sample to test the classification.
    """
    from auditfast.core.advisory import SCORING_GUIDE

    assert "SAMPLE" in SCORING_GUIDE
    assert "deterministic evidence" in SCORING_GUIDE
    assert "Never state a count you did not derive" in SCORING_GUIDE


def test_the_manifest_carries_the_scoring_guide(tmp_path):
    """The rubric travels with the work, so it cannot drift from the code."""
    from auditfast.core.advisory import SCORING_GUIDE

    written = write_themed_bundles([_result(ref="4.5.2")], {}, tmp_path)
    manifest = json.loads(Path(written["advisory_bundle_manifest"]).read_text(encoding="utf-8"))
    assert manifest["scoring_guide"] == SCORING_GUIDE


def test_themed_bundles_split_the_findings_and_write_a_manifest(tmp_path):
    results = [
        _result(ref="4.5.2", check_id="TB-FACT-GRAIN"),
        _result(ref="4.5.4", check_id="TB-DIM-DENORM"),
        _result(ref="5.4.1", check_id="SM-FK-SURROGATE"),
    ]
    written = write_themed_bundles(results, {}, tmp_path)

    manifest = json.loads(Path(written["advisory_bundle_manifest"]).read_text(encoding="utf-8"))
    assert manifest["total_findings"] == 3
    themes = {job["theme"]: job["findings"] for job in manifest["jobs"]}
    assert themes["dimensional-modelling"] == 2
    assert themes["referential-integrity"] == 1


def test_the_manifest_names_the_workspaces_it_covers(tmp_path):
    """A reviewer asked to judge 'the NOIDA audit' must be able to confirm it."""
    results = [_result(ref="4.5.2"), _result(ref="5.4.1", obj="b")]
    written = write_themed_bundles(results, {}, tmp_path)
    manifest = json.loads(Path(written["advisory_bundle_manifest"]).read_text(encoding="utf-8"))
    assert manifest["workspaces"] == ["WS"]
    assert manifest["generated"].endswith("Z")


def test_the_manifest_lists_the_biggest_job_first(tmp_path):
    """A reviewer plans the session from this; size order is the useful order."""
    results = [_result(ref="4.5.2")] + [_result(ref="5.4.1", obj=f"o{i}")
                                        for i in range(3)]
    written = write_themed_bundles(results, {}, tmp_path)
    manifest = json.loads(Path(written["advisory_bundle_manifest"]).read_text(encoding="utf-8"))
    assert manifest["jobs"][0]["theme"] == "referential-integrity"


def test_a_directory_of_themed_bundles_reads_as_one(tmp_path):
    results = [_result(ref="4.5.2"), _result(ref="5.4.1", obj="other")]
    write_themed_bundles(results, {}, tmp_path)
    rebuilt = results_from_bundle(tmp_path / "advisory-bundles")
    assert len(rebuilt) == 2


def test_verdicts_from_several_themes_apply_together(tmp_path):
    """Themes are judged separately, then applied in one step."""
    results = [_result(ref="4.5.2", obj="a"), _result(ref="5.4.1", obj="b")]
    write_themed_bundles(results, {}, tmp_path)
    bundles = tmp_path / "advisory-bundles"

    evidence = bundle_contexts(bundles)
    keys = list(evidence)
    (bundles / "one-verdicts.csv").write_text(
        f"finding_id,score,confidence\n{keys[0]},3,high\n", encoding="utf-8")
    (bundles / "two-verdicts.csv").write_text(
        f"finding_id,score,confidence\n{keys[1]},3,high\n", encoding="utf-8")

    rebuilt = results_from_bundle(bundles)
    _, summary = apply_verdicts(rebuilt, {}, bundles, evidence_by_id=evidence)
    assert summary["applied"] == 2


def test_an_empty_verdicts_directory_is_a_clear_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(AdvisoryVerdictError, match="No .*verdicts.*csv"):
        read_verdicts(tmp_path / "empty")


# --- the CSV boundary -------------------------------------------------------
#
# A human hand-edits this file and it rewrites a customer report, so every way
# it can be wrong should say so rather than resolve silently.

def test_two_verdicts_for_one_finding_is_an_error(tmp_path):
    """Last-wins would resolve contradictory judgments by file sort order."""
    path = _csv(tmp_path, "finding_id,score\nabc,3\nabc,0\n")
    with pytest.raises(AdvisoryVerdictError, match="already has a verdict"):
        read_verdicts(path)


@pytest.mark.parametrize("bad", ["2.7", "0.9", "1.5"])
def test_a_fractional_score_is_rejected_not_truncated(tmp_path, bad):
    """int(float('2.7')) == 2 silently changed a reviewer's answer."""
    path = _csv(tmp_path, f"finding_id,score\nabc,{bad}\n")
    with pytest.raises(AdvisoryVerdictError, match="not a whole number"):
        read_verdicts(path)


def test_an_unknown_confidence_is_rejected(tmp_path):
    """A typo must not read as 'confident enough to overwrite a rule'."""
    path = _csv(tmp_path, "finding_id,score,confidence\nabc,3,unsure\n")
    with pytest.raises(AdvisoryVerdictError, match="not one of"):
        read_verdicts(path)


def test_a_score_with_no_finding_id_is_an_error(tmp_path):
    """Excel mangling the id column would otherwise apply nothing, silently."""
    path = _csv(tmp_path, "finding_id,score\n,3\n")
    with pytest.raises(AdvisoryVerdictError, match="no finding_id"):
        read_verdicts(path)


def test_an_unjudged_template_row_is_skipped_quietly(tmp_path):
    """A blank row in a pre-filled template is normal, not an error."""
    path = _csv(tmp_path, "finding_id,score\nabc,\ndef,2\n")
    verdicts = read_verdicts(path)
    assert set(verdicts) == {"def"}


def test_verdict_rows_matching_nothing_are_reported(tmp_path):
    """The half of stale-bundle detection that was missing."""
    csv_path = _csv(tmp_path, "finding_id,score,confidence\nnosuchid00000000,3,high\n")
    _, summary = apply_verdicts([_result()], {}, csv_path)
    assert summary["orphaned_verdicts"] == 1
    assert "nosuchid00000000" in summary["orphaned_ids"]


def test_a_verdict_aimed_at_a_scored_check_is_counted_as_rejected(tmp_path):
    """The counter must report the event it exists for, not always read 0."""
    result = _result(ref=DETERMINISTIC_REF, score=0)
    key = finding_id(result, "")
    csv_path = _csv(tmp_path, f"finding_id,score,confidence\n{key},3,high\n")
    out, summary = apply_verdicts([result], {}, csv_path)
    assert summary["rejected_non_advisory"] == 1
    assert out[0].score == 0


# --- the template -----------------------------------------------------------

def test_a_verdict_template_is_written_per_theme(tmp_path):
    """Hand-copying hundreds of content-hashed ids is the likeliest way to
    produce verdicts that then land as unmatched."""
    results = [_result(ref="4.5.2"), _result(ref="4.5.4", obj="other")]
    written = write_themed_bundles(results, {}, tmp_path)
    template = Path(written["advisory_template_dimensional-modelling"])
    assert template.exists()
    body = template.read_text(encoding="utf-8")
    assert "finding_id,score,evidence,recommendation,confidence,judged_by" in body
    for record in build_bundle(results, {}):
        assert record["finding_id"] in body


def test_the_manifest_points_at_the_template_that_exists(tmp_path):
    results = [_result(ref="4.5.2")]
    written = write_themed_bundles(results, {}, tmp_path)
    manifest = json.loads(Path(written["advisory_bundle_manifest"]).read_text(encoding="utf-8"))
    assert Path(manifest["jobs"][0]["verdicts"]).exists()


# --- matching ---------------------------------------------------------------

def test_bundle_ids_matches_verdicts_without_rehashing(tmp_path):
    """The bundle records each id, so matching is a lookup, not a re-derivation."""
    results = [_result(ref="4.5.2", obj="a"), _result(ref="4.5.4", obj="b")]
    write_themed_bundles(results, {}, tmp_path)
    bundles = tmp_path / "advisory-bundles"

    ids = bundle_ids(bundles)
    assert len(ids) == 2
    key = next(iter(ids.values()))
    csv_path = _csv(tmp_path, f"finding_id,score,confidence\n{key},3,high\n")
    _, summary = apply_verdicts(
        results_from_bundle(bundles), {}, csv_path, id_by_key=ids
    )
    assert summary["applied"] == 1


def test_a_missing_bundle_directory_is_a_clear_error(tmp_path):
    (tmp_path / "nothing").mkdir()
    with pytest.raises(AdvisoryVerdictError, match="No .jsonl bundles"):
        bundle_contexts(tmp_path / "nothing")


def test_a_bundle_line_that_is_not_json_is_a_clear_error(tmp_path):
    path = tmp_path / "advisory-bundle.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(AdvisoryVerdictError, match="not valid JSON"):
        bundle_contexts(path)
