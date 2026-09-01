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
from auditfast.ai.jobs import build_job, write_jobs
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


def _ws(name: str) -> WorkspaceContext:
    """A workspace whose fact/dimension names are unique to it."""
    return WorkspaceContext(id=name, display_name=name, tables={
        f"dbo.Dim{name}": {"columns": [{"name": "k"}, {"name": "a"}, {"name": "b"}]},
        f"dbo.Fact{name}": {"columns": [
            {"name": "fk"}, {"name": "amount", "type": "decimal"},
        ]},
    })


def _ws_result(name: str) -> CheckResult:
    spec = REGISTRY.get(CHECK)
    return CheckResult(
        check_id=CHECK, ref=spec.ref, title=spec.title, pillar=Pillar.DATA_MODELING,
        status=Status.FAIL, score=0, evidence="Star-schema naming not detected",
        severity=Severity.MEDIUM, workspace=name, obj="", scope=Scope.WORKSPACE,
    )


def test_build_job_covers_every_workspace_not_just_the_first():
    # The bug this guards: build_job built objects from results[0]'s workspace
    # only, so a multi-workspace audit stranded every workspace after the first.
    contexts = {"Alpha": _ws("Alpha"), "Beta": _ws("Beta")}
    job = build_job(CHECK, [_ws_result("Alpha"), _ws_result("Beta")], contexts)

    ids = [o["id"] for chunk in job["chunks"] for o in chunk["objects"]]
    assert any(i.startswith("Alpha :: ") for i in ids), "Alpha's objects are missing"
    assert any(i.startswith("Beta :: ") for i in ids), "Beta's objects are missing"
    # A workspace-scoped check gets one finding per workspace, not a single
    # collapsed one, so each workspace's verdict can be judged on its own.
    assert len(job["findings"]) == 2


def test_workspace_objects_map_to_the_workspace_finding_not_a_model_detail_row():
    # The bug this guards: a workspace-scoped check that also emits per-model
    # detail rows mapped every object to the LAST model's finding instead of the
    # "(workspace)" row, so the reader's labels scored the wrong finding.
    spec = REGISTRY.get(CHECK)

    def _model_row(ws: str, model: str) -> CheckResult:
        return CheckResult(
            check_id=CHECK, ref=spec.ref, title=spec.title, pillar=Pillar.DATA_MODELING,
            status=Status.FAIL, score=0, evidence=f"{model} detail",
            severity=Severity.MEDIUM, workspace=ws, obj=model, scope=Scope.WORKSPACE,
        )

    # Per-model detail rows come after the workspace row, reproducing the overwrite.
    results = [
        _ws_result("Alpha"), _model_row("Alpha", "ModelA"),
        _ws_result("Beta"), _model_row("Beta", "ModelB"),
    ]
    job = build_job(CHECK, results, {"Alpha": _ws("Alpha"), "Beta": _ws("Beta")})

    findings = {o["finding"] for chunk in job["chunks"] for o in chunk["objects"]}
    assert findings == {"Alpha :: (workspace)", "Beta :: (workspace)"}, findings



def test_write_jobs_manifest_lists_all_workspaces(tmp_path):
    contexts = {"Alpha": _ws("Alpha"), "Beta": _ws("Beta")}
    write_jobs({CHECK: [_ws_result("Alpha"), _ws_result("Beta")]}, contexts, tmp_path)

    manifest = json.loads(
        (tmp_path / "advisory-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["workspaces"] == ["Alpha", "Beta"]

    # The label template must carry both workspaces' objects, with unique ids so
    # the reader (which rejects a repeated object) can label all of them.
    body = (tmp_path / advisory_service.JOBS_DIRNAME / f"{CHECK}-labels.csv").read_text(
        encoding="utf-8"
    )
    assert "Alpha :: " in body and "Beta :: " in body
