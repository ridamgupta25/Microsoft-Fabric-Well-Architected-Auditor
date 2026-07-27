"""Engine, scoring, and check-library behaviour.

The parity tests here are the safety net for the whole refactor: they pin the
exact score the pre-refactor implementation produced.
"""
from __future__ import annotations

from auditfast.core.checks.registry import REGISTRY
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Pillar, Scope, Status
from auditfast.core.scoring import aggregate, band_from_coverage, rating

from .conftest import (
    EXPECTED_OVERALL,
    EXPECTED_RESULT_ROWS,
    EXPECTED_SCORED_CHECKS,
    MOCK_SETTINGS,
    MOCK_TARGETS,
)


def _run(provider, **kwargs):
    return run_audit(provider, MOCK_TARGETS, MOCK_SETTINGS, **kwargs)


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
    """A pillar with no checks scores None, never 0.0 — they mean different things."""
    agg = aggregate(_run(provider))
    assert agg["by_pillar"][Pillar.PERFORMANCE]["pct"] is None
    assert agg["by_pillar"][Pillar.PERFORMANCE]["count"] == 0


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
    assert agg["counts"][Status.PASS] == 30
    assert agg["counts"][Status.PARTIAL] == 9
    assert agg["counts"][Status.FAIL] == 18
    assert agg["counts"][Status.INFO] == 3


# -- the check library ---------------------------------------------------------

def test_registry_is_fully_populated():
    assert len(REGISTRY) == 20
    assert len(REGISTRY.select(scope=Scope.WORKSPACE)) == 12
    assert len(REGISTRY.select(scope=Scope.PIPELINE)) == 8


def test_every_check_has_traceable_metadata():
    """Metadata must be complete: it drives filtering, fetching, and the catalog."""
    for spec in REGISTRY:
        assert spec.id and spec.ref, f"{spec.id}: missing id/ref"
        assert spec.title, f"{spec.id}: missing title"
        assert spec.requires, f"{spec.id}: declares no required resources"
        assert spec.weight > 0, f"{spec.id}: non-positive weight"


def test_check_ids_are_unique():
    ids = [spec.id for spec in REGISTRY]
    assert len(ids) == len(set(ids))


def test_duplicate_registration_is_rejected():
    """A copy-pasted check id must fail loudly, not silently shadow the original."""
    import pytest

    from auditfast.core.checks.registry import CheckRegistry, DuplicateCheckError, check

    registry = CheckRegistry()
    kwargs = dict(
        ref="1.1", title="t", pillar=Pillar.OPEX, scope=Scope.WORKSPACE, registry=registry
    )
    check(id="X-DUP", **kwargs)(lambda ctx: None)
    with pytest.raises(DuplicateCheckError):
        check(id="X-DUP", **kwargs)(lambda ctx: None)


def test_explicit_registry_is_isolated_from_the_global_one():
    """An empty CheckRegistry is falsy (it defines __len__).

    A ``registry or REGISTRY`` fallback would therefore leak test checks into the
    global catalog; this pins the identity check that prevents it.
    """
    from auditfast.core.checks.registry import CheckRegistry, check

    registry = CheckRegistry()
    check(id="X-ISOLATED", ref="0.0", title="t", pillar=Pillar.OPEX,
          scope=Scope.WORKSPACE, registry=registry)(lambda ctx: None)

    assert registry.get("X-ISOLATED") is not None
    assert REGISTRY.get("X-ISOLATED") is None, "test check leaked into the global registry"
    assert len(REGISTRY) == 20


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
        if spec.pillar is not Pillar.FOUNDATION and not book.get(spec.ref)
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
    results = run_audit(provider, [("does-not-exist", Layer.PREP)], MOCK_SETTINGS)
    access = [r for r in results if r.check_id == "WS-ACCESS"]
    assert len(access) == 1
    assert access[0].scored is False
    assert aggregate(results)["overall"] is None


def test_a_crashing_check_does_not_abort_the_run(provider):
    """One broken rule must not take the whole audit down."""
    from auditfast.core.checks.registry import CheckRegistry, check

    registry = CheckRegistry()

    @check(id="X-BOOM", ref="0.0", title="explodes",
           pillar=Pillar.OPEX, scope=Scope.WORKSPACE, registry=registry)
    def _boom(ctx):
        raise RuntimeError("kaboom")

    results = run_audit(provider, MOCK_TARGETS, MOCK_SETTINGS, registry=registry)
    assert len(results) == len(MOCK_TARGETS)
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
