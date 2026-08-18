"""Engine, scoring, and check-library behaviour.

The parity tests here are the safety net for the whole refactor: they pin the
exact score the pre-refactor implementation produced.
"""
from __future__ import annotations

from auditfast.core.check.registry import REGISTRY
from auditfast.core.engine import run_audit
from auditfast.core.enums import Automation, Layer, Pillar, Scope, Status
from auditfast.core.models import CheckContext, WorkspaceContext
from auditfast.core.scoring import aggregate, band_from_coverage, rating

from .conftest import (
    EXPECTED_OVERALL,
    EXPECTED_RESULT_ROWS,
    EXPECTED_SCORED_CHECKS,
    FIXTURE_SETTINGS,
    FIXTURE_TARGETS,
)


def _run(provider, **kwargs):
    return run_audit(provider, FIXTURE_TARGETS, FIXTURE_SETTINGS, **kwargs)


# -- scoring maths -------------------------------------------------------------

def test_coverage_bands():
    assert band_from_coverage(1.0) == 3
    assert band_from_coverage(0.85) == 2
    assert band_from_coverage(0.6) == 1
    assert band_from_coverage(0.2) == 0


def test_band_boundaries_are_exact():
    """80% must band to 2 and 79.9% to 1 — "almost" is not best practice."""
    assert band_from_coverage(0.8) == 2
    assert band_from_coverage(0.799) == 1
    assert band_from_coverage(0.999) == 2


def test_rating_labels():
    assert rating(95)[0] == "Excellent"
    assert rating(30)[0] == "Critical"
    assert rating(None)[0] == "Not assessed"


def test_not_assessed_is_not_zero(provider):
    """A pillar with no scored checks reports None, never 0.0 — they differ.

    Asserted as an invariant over every pillar rather than naming one, because
    naming a pillar makes the test a hostage to coverage: this previously pinned
    Governance & Compliance, and broke the moment that pillar gained its first
    automated check. The rule being protected is "no data" ≠ "scored zero".
    """
    agg = aggregate(_run(provider))
    unassessed = [p for p, facts in agg["by_pillar"].items() if facts["count"] == 0]
    for pillar in unassessed:
        assert agg["by_pillar"][pillar]["pct"] is None, (
            f"{pillar} has no scored checks, so its percentage must be None, not "
            f"{agg['by_pillar'][pillar]['pct']}"
        )
    # …and the converse: a pillar that did score must report a number.
    for facts in agg["by_pillar"].values():
        if facts["count"] > 0:
            assert facts["pct"] is not None


# -- parity with the pre-refactor implementation -------------------------------

def test_overall_score_is_unchanged(provider):
    agg = aggregate(_run(provider))
    assert agg["overall"] == EXPECTED_OVERALL


def test_result_and_scored_counts_are_unchanged(provider):
    results = _run(provider)
    agg = aggregate(results)
    assert len(results) == EXPECTED_RESULT_ROWS
    assert agg["total_scored"] == EXPECTED_SCORED_CHECKS


def test_status_counts_are_unchanged(provider):
    agg = aggregate(_run(provider))
    assert agg["counts"][Status.PASS] == 67
    assert agg["counts"][Status.PARTIAL] == 24
    assert agg["counts"][Status.FAIL] == 67
    assert agg["counts"][Status.NA] == 207
    assert agg["counts"][Status.INFO] == 10


def test_mixed_layer_runs_every_layers_checks():
    """A workspace tagged Mixed plays every role, so every check applies to it."""
    mixed = REGISTRY.select(layer=Layer.MIXED)
    prep = REGISTRY.select(layer=Layer.PREP)
    assert len(mixed) == len(list(REGISTRY))   # Mixed selects the whole library
    assert len(mixed) > len(prep)              # a single layer is a strict subset


def test_no_selected_check_is_missing_when_its_objects_are_absent(provider):
    """An object-scoped check with no object still appears, as N/A with a reason."""
    results = _run(provider)
    # The fixture's operations workspace has no notebook, but notebook checks apply
    # to the Data Operations layer — they must still be reported, not dropped.
    na_notebook = [r for r in results
                   if r.check_id.startswith("NB-") and r.status is Status.NA]
    assert na_notebook
    assert any("No notebooks" in r.evidence for r in na_notebook)


def test_notebook_checks_run_on_notebook_definitions(provider):
    """The notebook checks read the notebook's code cells and flag real issues."""
    nb = {r.check_id: r for r in _run(provider)
          if r.scope is Scope.NOTEBOOK and r.obj == "NB_Gold_Build"}
    assert nb, "no notebook results were produced"
    assert nb["NB-IMPORTS"].status is Status.FAIL     # from pyspark.sql.functions import *
    assert nb["NB-DISPLAY"].status is Status.FAIL     # display(df)
    assert nb["NB-SECRETS"].status is Status.PASS
    assert nb["NB-PARAMS"].status is Status.PASS


def test_table_checks_run_on_table_metadata(provider):
    """The table checks read the lakehouse table schemas and score them."""
    tb = {r.check_id: r for r in _run(provider)
          if r.check_id.startswith("TB-")}
    assert tb, "no table results were produced"
    # dim_date + a fact/dim split means the star-schema and date-dimension checks pass.
    assert tb["TB-STARSCHEMA"].status is Status.PASS
    assert tb["TB-DATEDIM"].status is Status.PASS
    assert tb["TB-SURROGATE"].status is Status.PASS
    # StagingTemp is External/Parquet and non-snake, so these are only partial.
    assert tb["TB-MANAGED-DELTA"].status is Status.PARTIAL
    assert tb["TB-NAMING"].status is Status.PARTIAL


def test_pipeline_load_checks_run(provider):
    """The pipeline load-pattern checks read the pipeline definitions and score them."""
    results = _run(provider)
    inc = {r.obj: r for r in results if r.check_id == "PL-INCREMENTAL"}
    assert inc, "no incremental-load results were produced"
    assert inc["PL_Silver_Merge"].status is Status.PASS   # MERGE INTO = incremental
    assert inc["PL_Bronze_Load"].status is Status.FAIL     # plain Copy, no watermark
    orch = [r for r in results
            if r.check_id == "PL-ORCHESTRATION" and r.status is Status.FAIL]
    assert orch  # ws-prep-01 has two pipelines but none orchestrates the others


def test_tls_check_requires_explicit_version_evidence():
    from auditfast.core.check.security.data_operations.automated import connections_use_tls12

    def verdict(version):
        ctx = WorkspaceContext(id="ws", connections=[{
            "id": "c1", "minimum_tls_version": version,
        }])
        return connections_use_tls12(CheckContext(workspace=ctx, settings={}))

    assert verdict("TLS1.2").score == 3
    assert verdict("1.1").score == 0
    assert verdict(None).status is Status.NA


def test_deadletter_checks_require_explicit_record_routing():
    from auditfast.core.check.operations_reliability.data_prep.automated import (
        nb_deadletter,
        pl_deadletter,
    )

    pipeline = {
        "properties": {"activities": [{
            "name": "Quarantine_Invalid_Records",
            "type": "Copy",
            "typeProperties": {"sink": {"dataset": "dead_letter_records"}},
        }]}
    }
    notebook = {"cells": [{
        "cell_type": "code",
        "source": "invalid = df.filter('is_valid = false')\ninvalid.write.saveAsTable('quarantine.records')",
    }]}
    pipeline_ctx = WorkspaceContext(id="ws", pipelines={"p": pipeline})
    notebook_ctx = WorkspaceContext(id="ws", notebooks={"n": notebook})

    assert pl_deadletter(CheckContext(pipeline_ctx, {}, "p", pipeline)).score == 3
    assert nb_deadletter(CheckContext(notebook_ctx, {}, "n", notebook)).score == 3


def test_pipeline_idempotency_check_scores_rerun_safety_patterns():
    from auditfast.core.check.operations_reliability.data_prep.automated import pipeline_idempotent

    def verdict(text):
        definition = {"properties": {"activities": [{
            "name": text, "type": "Copy",
        }]}}
        workspace = WorkspaceContext(id="ws")
        return pipeline_idempotent(CheckContext(workspace, {}, "pipeline", definition))

    assert verdict("MERGE by batch_id").score == 3
    assert verdict("Append raw records").score == 0


def test_pipeline_idempotency_accepts_ordered_cleanup_before_copy():
    from auditfast.core.check.operations_reliability.data_prep.automated import pipeline_idempotent

    definition = {"properties": {"activities": [
        {"name": "Delete old files", "type": "Delete", "dependsOn": []},
        {"name": "Copy sales.csv", "type": "Copy", "dependsOn": [{
            "activity": "Delete old files",
            "dependencyConditions": ["Succeeded"],
        }]},
    ]}}
    context = CheckContext(
        WorkspaceContext(id="ws"), {}, "pipeline", definition,
    )
    assert pipeline_idempotent(context).score == 3


def test_notebook_idempotency_check_scores_rerun_safety_patterns():
    from auditfast.core.check.operations_reliability.data_prep.automated import notebook_idempotent

    def verdict(text):
        definition = {"cells": [{
            "cell_type": "code",
            "source": text,
        }]}
        workspace = WorkspaceContext(id="ws")
        return notebook_idempotent(CheckContext(workspace, {}, "notebook", definition))

    assert verdict("df.dropDuplicates(['id']); df.write.mode('append').saveAsTable('t')").score == 3
    assert verdict("df.write.mode('append').saveAsTable('t')").score == 0


def test_late_arrival_checks_require_safe_versioned_writes():
    from auditfast.core.check.data_management_quality.data_prep.automated import (
        nb_late_arrival,
        pl_late_arrival,
    )

    pipeline = {"properties": {"activities": [{
        "name": "MERGE latest version by event_time",
        "type": "Script",
    }]}}
    notebook = {"cells": [{
        "cell_type": "code",
        "source": "event_time = df.event_time\nlatest = df.dropDuplicates(['id', 'version'])\nMERGE INTO target",
    }]}
    pipeline_context = CheckContext(WorkspaceContext(id="ws"), {}, "p", pipeline)
    notebook_context = CheckContext(WorkspaceContext(id="ws"), {}, "n", notebook)

    assert pl_late_arrival(pipeline_context).score == 3
    assert nb_late_arrival(notebook_context).score == 3


def test_reconciliation_checks_require_source_target_control_metrics():
    from auditfast.core.check.governance_compliance.data_prep.automated import (
        notebook_reconciliation,
        pipeline_reconciliation,
    )

    pipeline = {"properties": {"activities": [{
        "name": "Reconcile source target row_count",
        "type": "Script",
    }]}}
    notebook = {"cells": [{
        "cell_type": "code",
        "source": "source_count = source.count()\ntarget_count = target.count()\nreconcile_source_target = source_count == target_count",
    }]}
    pipeline_context = CheckContext(WorkspaceContext(id="ws"), {}, "p", pipeline)
    notebook_context = CheckContext(WorkspaceContext(id="ws"), {}, "n", notebook)

    assert pipeline_reconciliation(pipeline_context).score == 3
    assert notebook_reconciliation(notebook_context).score == 3


def test_progress_callback_fires_per_workspace(provider):
    """on_progress is called once per audited workspace, with cumulative results.

    This is what lets a slow run surface a *partial* report — the results so far
    — instead of an all-or-nothing wait.
    """
    snapshots: list[int] = []
    results = _run(provider, on_progress=lambda partial: snapshots.append(len(partial)))
    assert len(snapshots) == len(FIXTURE_TARGETS)   # one snapshot per workspace
    assert snapshots == sorted(snapshots)           # cumulative — never shrinks
    assert snapshots[-1] == len(results)            # final snapshot is the whole run


def test_registry_is_fully_populated():
    """226 checks are evaluated; remaining roadmap checks are not loaded."""
    evaluated = [s for s in REGISTRY if s.automation is Automation.AUTOMATED]
    assert len(evaluated) == 226
    assert len([s for s in evaluated if s.scope is Scope.WORKSPACE]) == 102
    assert len([s for s in evaluated if s.scope is Scope.PIPELINE]) == 32
    assert len([s for s in evaluated if s.scope is Scope.NOTEBOOK]) == 84
    # Roadmap (gated N/A) checks are intentionally not registered — see
    # auditfast.core.check.__init__._CHECK_MODULES — so none remain in the registry.
    assert len([s for s in REGISTRY if s.automation is Automation.ROADMAP]) == 0
    assert len([s for s in REGISTRY if s.automation is Automation.INTERACTIVE]) == 40
    assert all(
        s.automation is Automation.INTERACTIVE for s in REGISTRY if s.manual
    )


def test_every_check_has_traceable_metadata():
    """Metadata must be complete: it drives filtering, fetching, and the catalog.

    Manual checks are attestation-only and read no data, so the required-resources
    rule does not apply to them.
    """
    for spec in REGISTRY:
        assert spec.id and spec.ref, f"{spec.id}: missing id/ref"
        assert spec.title, f"{spec.id}: missing title"
        assert spec.weight > 0, f"{spec.id}: non-positive weight"
        if not spec.manual:
            assert spec.requires, f"{spec.id}: declares no required resources"


def test_check_ids_are_unique():
    ids = [spec.id for spec in REGISTRY]
    assert len(ids) == len(set(ids))


def test_duplicate_registration_is_rejected():
    """A copy-pasted check id must fail loudly, not silently shadow the original."""
    import pytest

    from auditfast.core.check.registry import CheckRegistry, DuplicateCheckError, check

    registry = CheckRegistry()
    kwargs = dict(
        ref="1.1", title="t", pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, registry=registry
    )
    check(id="X-DUP", **kwargs)(lambda ctx: None)
    with pytest.raises(DuplicateCheckError):
        check(id="X-DUP", **kwargs)(lambda ctx: None)


def test_explicit_registry_is_isolated_from_the_global_one():
    """An empty CheckRegistry is falsy (it defines __len__).

    A ``registry or REGISTRY`` fallback would therefore leak test checks into the
    global catalog; this pins the identity check that prevents it.
    """
    from auditfast.core.check.registry import CheckRegistry, check

    registry = CheckRegistry()
    check(id="X-ISOLATED", ref="0.0", title="t", pillar=Pillar.OPERATIONS,
          scope=Scope.WORKSPACE, registry=registry)(lambda ctx: None)

    assert registry.get("X-ISOLATED") is not None
    assert REGISTRY.get("X-ISOLATED") is None, "test check leaked into the global registry"
    before = len([s for s in REGISTRY if s.automation is Automation.AUTOMATED])
    assert before == 226

# -- selection and dispatch ----------------------------------------------------

def test_pillar_filter_runs_less_work(provider):
    """Filtering happens before execution, so fewer checks actually run."""
    everything = _run(provider)
    security_only = _run(provider, pillars=[Pillar.SECURITY])
    assert len(security_only) < len(everything)
    assert {r.pillar for r in security_only} == {Pillar.SECURITY}


def test_pipeline_checks_skip_non_pipeline_layers(provider):
    """A Data Storage workspace gets no pipeline checks even though it has pipelines."""
    results = _run(provider)
    storage = [r for r in results
               if r.workspace == "Sales-Prod-DataStorage" and r.scope is Scope.PIPELINE]
    assert storage == []


def test_storage_workspace_flags_its_pipeline_as_foreign(provider):
    """Layer separation still sees the pipeline, even with no pipeline checks run."""
    results = _run(provider)
    sep = next(r for r in results
               if r.check_id == "WS-LAYER-SEP" and r.workspace == "Sales-Prod-DataStorage")
    assert sep.status is Status.FAIL
    assert "DataPipeline" in sep.evidence


def test_required_resources_narrow_with_selection():
    """Deselecting pillars must reduce what the provider is asked to fetch."""
    from auditfast.core.enums import Resource

    all_specs = REGISTRY.select(layer=Layer.PREP)
    cost_specs = REGISTRY.select(layer=Layer.PREP, pillars=[Pillar.COST])
    assert Resource.PIPELINE_DEFINITIONS in REGISTRY.required_resources(all_specs)
    # Cost checks read the workspace and its items — never pipeline definitions,
    # which are the expensive one-call-per-pipeline fetch.
    assert Resource.PIPELINE_DEFINITIONS not in REGISTRY.required_resources(cost_specs)


# -- findings ------------------------------------------------------------------

def test_hardcoded_secret_is_critical(provider):
    failures = [r for r in _run(provider)
                if r.check_id == "PL-SECRETS" and r.status is Status.FAIL]
    assert failures
    assert all(r.severity.value == "Critical" for r in failures)


def test_secret_evidence_never_leaks_the_secret(provider):
    """Evidence reports a count, never the matched text."""
    for r in _run(provider):
        if r.check_id == "PL-SECRETS" and r.status is Status.FAIL:
            assert "pattern match" in r.evidence
            assert "password=" not in r.evidence.lower()


def test_badly_named_workspace_fails_naming(provider):
    named = {(r.workspace, r.status) for r in _run(provider) if r.check_id == "WS-NAME"}
    assert ("salesops", Status.FAIL) in named


def test_every_scoreable_check_ref_has_remediation_text():
    """A ref missing from remediation.yaml renders an empty recommendation.

    That failure is silent — the finding still appears, just with nothing telling
    anyone what to do about it — so it is pinned here rather than left to review.

    Foundation checks are excluded: they describe the estate rather than judging
    it, never fail, and so have nothing to remediate.
    """
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    book = load_remediation(load_project(PROJECT_FILE))
    missing = sorted({
        f"{spec.id} (ref {spec.ref})"
        for spec in REGISTRY
        if spec.pillar is not Pillar.FOUNDATION
        and not spec.manual
        and spec.automation is not Automation.ROADMAP
        and not book.get(spec.ref)
    })
    assert missing == [], f"checks with no remediation text: {missing}"


def test_findings_all_carry_actionable_guidance(provider):
    """Every failing or partial check must say what to do about it."""
    from auditfast.services.project import load_project, load_remediation

    from .conftest import PROJECT_FILE

    results = _run(provider, remediation=load_remediation(load_project(PROJECT_FILE)))
    silent = [
        (r.check_id, r.workspace)
        for r in results
        if r.status in (Status.FAIL, Status.PARTIAL) and not r.recommendation
    ]
    assert silent == [], f"findings with no recommendation: {silent}"


def test_passing_checks_carry_no_severity_or_remediation(provider):
    """Severity describes a finding, so a pass is always Informational."""
    for r in _run(provider):
        if r.status is Status.PASS:
            assert r.severity.value == "Informational"
            assert r.recommendation == ""


# -- unreadable workspaces -----------------------------------------------------

def test_missing_workspace_is_reported_not_scored(provider):
    results = run_audit(provider, [("does-not-exist", Layer.PREP)], FIXTURE_SETTINGS)
    access = [r for r in results if r.check_id == "WS-ACCESS"]
    assert len(access) == 1
    assert access[0].scored is False
    assert aggregate(results)["overall"] is None


def test_a_crashing_check_does_not_abort_the_run(provider):
    """One broken rule must not take the whole audit down."""
    from auditfast.core.check.registry import CheckRegistry, check

    registry = CheckRegistry()

    @check(id="X-BOOM", ref="0.0", title="explodes",
           pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, registry=registry)
    def _boom(ctx):
        raise RuntimeError("kaboom")

    results = run_audit(provider, FIXTURE_TARGETS, FIXTURE_SETTINGS, registry=registry)
    assert len(results) == len(FIXTURE_TARGETS)
    assert all(r.status is Status.NA and not r.scored for r in results)
    assert "kaboom" in results[0].evidence


# -- the pillar x layer matrix -------------------------------------------------

def test_matrix_covers_every_pillar_and_present_layer(provider):
    agg = aggregate(_run(provider))
    assert set(agg["layers"]) == {"Data Prep", "Data Storage", "Data Operations"}
    for pillar in Pillar.scored():
        assert set(agg["matrix"][pillar]) == set(agg["layers"])


def test_weights_are_applied(provider):
    """Doubling one pillar's weight must move the overall score."""
    from dataclasses import replace

    results = _run(provider)
    baseline = aggregate(results)["overall"]
    weighted = [replace(r, weight=2.0) if r.pillar is Pillar.SECURITY else r for r in results]
    assert aggregate(weighted)["overall"] != baseline
