"""Labels in, scored results out.

The tests that matter here are the ones proving a label file cannot quietly do
the wrong thing: a vocabulary it invented, an object from another export, two
labels for one thing. Each has to fail loudly, because the alternative is a
customer report built on a judgment that was never made.
"""
from __future__ import annotations

import pytest

from auditfast.ai.jobs import build_job, write_jobs
from auditfast.ai.labels import (
    JUDGED_SOURCE,
    LabelError,
    apply_labels,
    load_jobs,
    read_labels,
)
from auditfast.core.enums import Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult, WorkspaceContext

REF = "4.5.1"
CHECK = "TB-STARSCHEMA"


def _workspace() -> WorkspaceContext:
    return WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.DimCustomer": {"columns": [
            {"name": "customer_key"}, {"name": "city"}, {"name": "country"},
        ]},
        "dbo.FactSales": {"columns": [
            {"name": "sales_key"}, {"name": "customer_key"},
            {"name": "product_key"}, {"name": "amount", "type": "decimal"},
        ]},
        "dbo.stg_load": {"columns": [{"name": "raw"}]},
    })


def _result() -> CheckResult:
    return CheckResult(
        check_id="TB-STARSCHEMA", ref=REF, title="Star schema",
        pillar=Pillar.DATA_MODELING, status=Status.FAIL, score=0,
        evidence="Star-schema naming not detected", severity=Severity.MEDIUM,
        workspace="WS", obj="", scope=Scope.WORKSPACE,
    )


def _job() -> dict:
    return build_job(CHECK, [_result()], _workspace())


def _labels(tmp_path, rows: str, name="4.5.1-labels.csv"):
    path = tmp_path / name
    path.write_text(rows, encoding="utf-8")
    return path


# --- the job carries what a report needs ------------------------------------

def test_a_job_carries_enough_to_rebuild_the_result(tmp_path):
    """The report is produced later, elsewhere, without re-crawling."""
    job = _job()
    meta = job["findings"]["(workspace)"]
    for field in ("pillar", "severity", "layer", "scope", "weight"):
        assert field in meta
    assert meta["deterministic"]["score"] == 0


def test_a_job_file_round_trips(tmp_path):
    write_jobs({CHECK: [_result()]}, {"WS": _workspace()}, tmp_path)
    jobs = load_jobs(tmp_path / "jobs")
    assert CHECK in jobs
    assert jobs[CHECK]["objects"] == 3


def test_the_jobs_manifest_is_not_clobbered_by_the_bundle_manifest(tmp_path):
    """Both stages write into one output directory, and the bundle runs second.

    They shared the filename ``advisory-manifest.json``, so a real run left 50
    job files on disk and a manifest describing one leftover bundle finding.
    The judging agent reads that manifest to discover its work, so the jobs
    were invisible - and the failure looked like a successful audit.
    """
    from auditfast.ai.advisory_bundle import MANIFEST_NAME as BUNDLE_MANIFEST
    from auditfast.ai.jobs import MANIFEST_NAME as JOBS_MANIFEST

    assert JOBS_MANIFEST != BUNDLE_MANIFEST, (
        "the jobs and bundle manifests share a filename; whichever stage runs "
        "second silently overwrites the other"
    )


def test_the_manifest_describes_the_jobs_it_wrote(tmp_path):
    """A reader discovers its work through the manifest, so it must match."""
    import json

    write_jobs({CHECK: [_result()]}, {"WS": _workspace()}, tmp_path)
    manifest = json.loads(
        (tmp_path / "advisory-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["checks"] == 1
    assert manifest["jobs"][0]["check_id"] == CHECK
    assert manifest["workspaces"] == ["WS"]
    assert "labelling_rules" in manifest


def test_an_export_clears_jobs_from_a_previous_run(tmp_path):
    """``load_jobs`` reads every *.json, so a leftover job would be judged.

    The output directory is reused between runs. A job from another estate -
    or from a check whose guide has since been removed - would otherwise load
    alongside this run's and be reported as part of it.
    """
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    stale = jobs_dir / "4.5.1.json"
    stale.write_text('{"check_id": "OLD-CHECK", "ref": "9.9.9"}', encoding="utf-8")
    (jobs_dir / "4.5.1-labels.csv").write_text("check_id,object,label\n", encoding="utf-8")

    write_jobs({CHECK: [_result()]}, {"WS": _workspace()}, tmp_path)

    assert not stale.exists(), "a job from a previous export was left behind"
    assert set(load_jobs(jobs_dir)) == {CHECK}


def test_judged_labels_are_archived_rather_than_destroyed(tmp_path):
    """A re-run for an unrelated reason wiped twenty checks' worth of judging.

    Labels cannot be left in place - they were produced against a different
    export and are correctly rejected downstream - but hours of work must not
    vanish because someone re-crawled.
    """
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "OLD-CHECK-labels.csv").write_text(
        "check_id,object,label,reason,confidence\n"
        "OLD-CHECK,dbo.thing,fact,carefully judged,high\n",
        encoding="utf-8",
    )

    write_jobs({CHECK: [_result()]}, {"WS": _workspace()}, tmp_path)

    archived = list(jobs_dir.glob("previous-labels-*/OLD-CHECK-labels.csv"))
    assert archived, "judged labels were destroyed rather than archived"
    assert "carefully judged" in archived[0].read_text(encoding="utf-8")


def test_an_unjudged_template_is_not_archived(tmp_path):
    """Archiving empty templates every run would bury the real ones."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "OLD-CHECK-labels.csv").write_text(
        "check_id,object,label,reason,confidence\nOLD-CHECK,dbo.thing,,,\n",
        encoding="utf-8",
    )

    write_jobs({CHECK: [_result()]}, {"WS": _workspace()}, tmp_path)

    assert not list(jobs_dir.glob("previous-labels-*"))


# --- reading labels ---------------------------------------------------------

def test_blank_rows_are_skipped_not_treated_as_verdicts(tmp_path):
    """A template row the reader did not reach is normal, not an error."""
    path = _labels(tmp_path, "check_id,object,label\nTB-STARSCHEMA,dbo.DimCustomer,\n"
                             "TB-STARSCHEMA,dbo.FactSales,fact\n")
    labels = read_labels(path)
    assert set(labels[CHECK]) == {"dbo.FactSales"}


def test_a_label_with_no_object_is_an_error(tmp_path):
    """Excel mangling the id column must not silently apply nothing."""
    path = _labels(tmp_path, "check_id,object,label\nTB-STARSCHEMA,,fact\n")
    with pytest.raises(LabelError, match="no check_id or object"):
        read_labels(path)


def test_two_labels_for_one_object_is_an_error(tmp_path):
    path = _labels(tmp_path, "check_id,object,label\nTB-STARSCHEMA,dbo.X,fact\n"
                             "TB-STARSCHEMA,dbo.X,dimension\n")
    with pytest.raises(LabelError, match="already has a label"):
        read_labels(path)


def test_missing_columns_name_what_is_missing(tmp_path):
    path = _labels(tmp_path, "check_id,label\nTB-STARSCHEMA,fact\n")
    with pytest.raises(LabelError, match="object"):
        read_labels(path)


# --- applying: the guards ---------------------------------------------------

def test_a_label_outside_the_vocabulary_is_rejected(tmp_path):
    """'probably a dimension' must fail, not be read as 'neither'."""
    labels = {CHECK: {"dbo.DimCustomer": {"label": "probably a dimension",
                                        "reason": "", "confidence": "high"}}}
    with pytest.raises(LabelError, match="not one of"):
        apply_labels({CHECK: _job()}, labels)


def test_an_object_from_another_export_is_rejected(tmp_path):
    """Applying them would score this workspace from another's judgment."""
    labels = {CHECK: {"dbo.SomethingElse": {"label": "fact",
                                          "reason": "", "confidence": "high"}}}
    with pytest.raises(LabelError, match="different export"):
        apply_labels({CHECK: _job()}, labels)


def test_an_object_name_with_a_trailing_space_still_matches(tmp_path):
    """A Fabric item can genuinely be named with a trailing space.

    One pipeline on a real estate is called ``"For Agent "``. Stripping the
    object column on read turned that valid label into "not an object in this
    job", which failed the whole check - 56 pipelines unscored because of one
    invisible character.
    """
    path = _labels(tmp_path, 'check_id,object,label\nTB-STARSCHEMA,"dbo.Trailing ",fact\n')
    labels = read_labels(path)
    assert "dbo.Trailing " in labels[CHECK], "the trailing space was stripped"


def test_a_whitespace_only_mismatch_says_so_rather_than_blaming_the_export(tmp_path):
    """The wrong diagnosis costs a re-run; naming the real cause costs nothing."""
    labels = {CHECK: {"dbo.FactSales ": {"label": "fact", "reason": "",
                                         "confidence": "high"}}}
    with pytest.raises(LabelError, match="differ only in whitespace"):
        apply_labels({CHECK: _job()}, labels)


# --- applying: the happy path -----------------------------------------------

def test_labels_become_a_score_computed_in_code(tmp_path):
    labels = {CHECK: {
        "dbo.DimCustomer": {"label": "dimension", "reason": "describes a customer",
                            "confidence": "high"},
        "dbo.FactSales": {"label": "fact", "reason": "keys and a measure",
                          "confidence": "high"},
        "dbo.stg_load": {"label": "neither", "reason": "staging",
                         "confidence": "high"},
    }}
    results, summary = apply_labels({CHECK: _job()}, labels)
    assert results[0].score == 3, "both a fact and a dimension were labelled"
    assert results[0].status is Status.PASS
    assert results[0].source == JUDGED_SOURCE
    assert summary["judged"] == 1


def test_the_evidence_says_who_judged_it(tmp_path):
    """A reader must never mistake a judged verdict for a rule-based one."""
    labels = {CHECK: {"dbo.FactSales": {"label": "fact", "reason": "",
                                      "confidence": "high"}}}
    results, _ = apply_labels({CHECK: _job()}, labels, judged_by="copilot")
    assert results[0].evidence.startswith("[judged - copilot]")


def test_unlabelled_objects_are_reported_not_hidden(tmp_path):
    labels = {CHECK: {"dbo.FactSales": {"label": "fact", "reason": "",
                                      "confidence": "high"}}}
    results, _ = apply_labels({CHECK: _job()}, labels)
    assert "2 object(s) were not labelled" in results[0].evidence


def test_a_job_with_no_labels_keeps_the_rules_verdict(tmp_path):
    """Not judging is not the same as judging it badly."""
    results, summary = apply_labels({CHECK: _job()}, {})
    assert results[0].score == 0
    assert results[0].source == "automated"
    assert summary["unjudged"] == 1


def test_everything_undetermined_keeps_the_rules_verdict(tmp_path):
    """The N/A-not-FAIL rule, applied to a whole check."""
    labels = {CHECK: {name: {"label": "undetermined", "reason": "", "confidence": "low"}
                    for name in ("dbo.DimCustomer", "dbo.FactSales", "dbo.stg_load")}}
    results, _ = apply_labels({CHECK: _job()}, labels)
    assert results[0].score == 0, "the deterministic verdict, unchanged"
    assert results[0].source == "automated"


def test_the_summary_separates_agreement_from_disagreement(tmp_path):
    """Where a reader disagrees with the rule is the finding worth having."""
    labels = {CHECK: {
        "dbo.DimCustomer": {"label": "dimension", "reason": "", "confidence": "high"},
        "dbo.FactSales": {"label": "fact", "reason": "", "confidence": "high"},
    }}
    _, summary = apply_labels({CHECK: _job()}, labels)
    assert summary["changed"] == [
        {"check_id": CHECK, "ref": REF, "finding": "(workspace)",
         "was": 0, "now": 3}
    ]
    assert summary["agreed"] == []


# --- object-scoped checks keep one row per object ---------------------------

def _table_result(obj: str, score: int) -> CheckResult:
    """A table-scoped advisory result, as the engine emits one per object."""
    return CheckResult(
        check_id="TB-STARSCHEMA", ref=REF, title="Star schema",
        pillar=Pillar.DATA_MODELING, status=Status.FAIL, score=score,
        evidence="rule verdict", severity=Severity.MEDIUM,
        workspace="WS", obj=obj, scope=Scope.NOTEBOOK,
    )


def test_an_object_scoped_check_keeps_one_finding_per_object():
    """Collapsing them would drop rows the deterministic report still has."""
    results = [_table_result("dbo.DimCustomer", 0), _table_result("dbo.FactSales", 0)]
    job = build_job(CHECK, results, _workspace())

    assert set(job["findings"]) == {"dbo.DimCustomer", "dbo.FactSales"}
    # dbo.stg_load has no result of its own, so it is not judged here.
    assert job["objects"] == 2

    labels = {CHECK: {
        "dbo.DimCustomer": {"label": "dimension", "reason": "", "confidence": "high"},
        "dbo.FactSales": {"label": "fact", "reason": "", "confidence": "high"},
    }}
    judged, summary = apply_labels({CHECK: job}, labels)
    assert len(judged) == 2, "one row per object, as the engine produced"
    assert {r.obj for r in judged} == {"dbo.DimCustomer", "dbo.FactSales"}
    assert summary["judged"] == 2


def test_an_unlabelled_object_keeps_only_its_own_rules_verdict():
    """One judged notebook must not silently rescore its neighbours."""
    results = [_table_result("dbo.DimCustomer", 0), _table_result("dbo.FactSales", 0)]
    job = build_job(CHECK, results, _workspace())
    labels = {CHECK: {"dbo.FactSales": {"label": "fact", "reason": "",
                                      "confidence": "high"}}}

    judged, summary = apply_labels({CHECK: job}, labels)
    by_obj = {r.obj: r for r in judged}
    assert by_obj["dbo.FactSales"].source == JUDGED_SOURCE
    assert by_obj["dbo.DimCustomer"].source == "automated"
    assert summary["judged"] == 1
    assert summary["unjudged"] == 1


def test_a_job_from_an_older_export_is_rejected():
    """Silently scoring nothing is worse than saying the file is stale."""
    stale = {"ref": REF, "check_id": "TB-STARSCHEMA", "title": "Star schema",
             "chunks": [], "result": {"pillar": "Security"}}
    with pytest.raises(LabelError, match="older export"):
        apply_labels({CHECK: stale}, {})
