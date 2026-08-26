"""Advisory judging as a separate, user-triggered step.

The run reads the jobs an audit left on disk, labels every object with a model,
writes the label CSVs, and scores them into a report. These tests pin the parts
that make a keyed run auditable rather than merely scored: the labels are
written, they round-trip through the reader the CLI uses, and a model that says
nothing useful is an error rather than a silent empty report.

No live model is called.
"""
from __future__ import annotations

import json

import pytest

from auditfast.ai import orchestrator
from auditfast.ai.jobs import build_job
from auditfast.ai.labels import read_labels
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Pillar, Scope, Severity, Status
from auditfast.core.models import CheckResult, WorkspaceContext
from auditfast.services import advisory_service

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
    })


def _result() -> CheckResult:
    spec = REGISTRY.get(CHECK)
    return CheckResult(
        check_id=CHECK, ref=spec.ref, title=spec.title, pillar=Pillar.DATA_MODELING,
        status=Status.FAIL, score=0, evidence="Star-schema naming not detected",
        severity=Severity.MEDIUM, workspace="WS", obj="", scope=Scope.WORKSPACE,
    )


@pytest.fixture
def out_dir(tmp_path):
    """An output directory holding one exported job, as an audit would leave it."""
    jobs = tmp_path / advisory_service.JOBS_DIRNAME
    jobs.mkdir(parents=True)
    job = build_job(CHECK, [_result()], _workspace())
    (jobs / f"{CHECK}.json").write_text(json.dumps(job), encoding="utf-8")
    return tmp_path


def _labeller(decide):
    """A model that answers with labels only - never a score."""
    import re

    def _complete(system, user, **kw):
        ids = re.findall(r"^--- OBJECT: (.*)$", user, re.MULTILINE)
        return json.dumps([
            {"object": obj, "label": decide(obj), "reason": f"because {obj}",
             "confidence": "high"}
            for obj in ids if decide(obj)
        ])
    return _complete


def _star(obj: str) -> str:
    lowered = obj.lower()
    if "fact" in lowered:
        return "fact"
    return "dimension" if "dim" in lowered else "neither"


def test_a_run_writes_the_label_csv_it_judged_from(monkeypatch, out_dir):
    monkeypatch.setattr(orchestrator, "complete", _labeller(_star))

    advisory_service.run_advisory(out_dir)

    csv_path = out_dir / advisory_service.JOBS_DIRNAME / f"{CHECK}-labels.csv"
    assert csv_path.exists(), "without the labels a reader cannot see why it scored"
    body = csv_path.read_text(encoding="utf-8")
    assert "fact" in body and "dimension" in body


def test_the_written_labels_round_trip_through_the_reader(monkeypatch, out_dir):
    monkeypatch.setattr(orchestrator, "complete", _labeller(_star))

    advisory_service.run_advisory(out_dir)

    # The same reader the CLI and the gates use must accept what we wrote,
    # or a keyed run leaves an artefact nothing else can check.
    labels = read_labels(out_dir / advisory_service.JOBS_DIRNAME)
    assert CHECK in labels
    assert {e["label"] for e in labels[CHECK].values()} <= {"fact", "dimension", "neither"}


def test_a_run_scores_from_the_labels_and_writes_a_report(monkeypatch, out_dir):
    monkeypatch.setattr(orchestrator, "complete", _labeller(_star))

    summary = advisory_service.run_advisory(out_dir)

    # The model returned no score anywhere, so this can only have been computed.
    assert summary["objects_labelled"] == 2
    assert summary["checks_labelled"] == 1
    assert (out_dir / advisory_service.JUDGED_DIRNAME).exists()
    assert summary["files"]


def test_a_label_outside_the_guide_is_not_written(monkeypatch, out_dir):
    monkeypatch.setattr(orchestrator, "complete", _labeller(lambda obj: "made_up_label"))

    with pytest.raises(advisory_service.AdvisoryError):
        advisory_service.run_advisory(out_dir)

    # Nothing survived validation, so nothing was written - a report scored from
    # invented labels would be worse than no report.
    csv_path = out_dir / advisory_service.JOBS_DIRNAME / f"{CHECK}-labels.csv"
    assert not csv_path.exists()


def test_no_jobs_is_a_clear_error(tmp_path):
    with pytest.raises(advisory_service.AdvisoryError) as exc:
        advisory_service.run_advisory(tmp_path)

    assert "Run an audit first" in str(exc.value)


def test_a_silent_model_is_an_error_not_an_empty_report(monkeypatch, out_dir):
    monkeypatch.setattr(orchestrator, "complete", lambda system, user, **kw: None)

    with pytest.raises(advisory_service.AdvisoryError) as exc:
        advisory_service.run_advisory(out_dir)

    assert "no usable labels" in str(exc.value)


def test_the_users_key_reaches_the_model_and_is_not_kept(monkeypatch, out_dir):
    seen: list[object] = []

    def _capture(system, user, *, credentials=None, **kw):
        seen.append(credentials)
        return _labeller(_star)(system, user)

    monkeypatch.setattr(orchestrator, "complete", _capture)
    creds = orchestrator.Credentials(
        provider="azure", api_key="secret-key",
        endpoint="https://x.openai.azure.com", deployment="gpt-4o",
    )

    summary = advisory_service.run_advisory(out_dir, credentials=creds)

    assert seen and seen[0] is creds, "the caller's key must be the one used"
    # Nothing that goes back to a client, or into the job store, may carry it.
    assert "secret-key" not in json.dumps(summary)
    assert "secret-key" not in repr(creds)
