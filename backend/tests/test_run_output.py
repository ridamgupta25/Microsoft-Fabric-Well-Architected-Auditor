"""One directory per audit run.

Runs used to write into ``output/`` directly, so auditing a second workspace
destroyed the first one's report and re-auditing the same workspace destroyed
whatever you were comparing against. These tests pin the two properties that
fix: a run never overwrites another, and a later step can find the run it wants.
"""
from __future__ import annotations

from datetime import datetime, timezone

from auditfast.services.run_output import (
    latest_run_dir,
    new_run_dir,
    prune_empty_runs,
    run_dirs,
    run_label,
    slugify,
)


def _at(second: int) -> datetime:
    return datetime(2026, 8, 26, 14, 30, second, tzinfo=timezone.utc)


def test_two_workspaces_do_not_share_a_directory(tmp_path):
    a = new_run_dir(tmp_path, "Workspace A", now=_at(1))
    b = new_run_dir(tmp_path, "Workspace B", now=_at(2))

    assert a != b
    assert a.exists() and b.exists()


def test_re_auditing_the_same_workspace_keeps_the_earlier_run(tmp_path):
    first = new_run_dir(tmp_path, "NOIDA", now=_at(1))
    (first / "audit-report.md").write_text("first", encoding="utf-8")

    second = new_run_dir(tmp_path, "NOIDA", now=_at(2))

    assert second != first
    # The whole point: the earlier report is still readable afterwards.
    assert (first / "audit-report.md").read_text(encoding="utf-8") == "first"


def test_two_runs_in_the_same_second_still_get_their_own_directory(tmp_path):
    first = new_run_dir(tmp_path, "NOIDA", now=_at(1))
    second = new_run_dir(tmp_path, "NOIDA", now=_at(1))

    assert first != second, "a coincidence of timing must not cost a run"


def test_the_latest_run_is_the_newest_one(tmp_path):
    new_run_dir(tmp_path, "NOIDA", now=_at(1))
    newest = new_run_dir(tmp_path, "NOIDA", now=_at(9))

    assert latest_run_dir(tmp_path) == newest


def test_latest_can_be_narrowed_to_one_workspace(tmp_path):
    mine = new_run_dir(tmp_path, "Workspace A", now=_at(1))
    new_run_dir(tmp_path, "Workspace B", now=_at(9))

    # Without the filter the newest run wins, which would be the wrong estate.
    assert latest_run_dir(tmp_path, "Workspace A") == mine


def test_unrelated_folders_are_not_mistaken_for_runs(tmp_path):
    (tmp_path / "jobs").mkdir()
    (tmp_path / "advisory-judged").mkdir()
    run = new_run_dir(tmp_path, "NOIDA", now=_at(1))

    assert run_dirs(tmp_path) == [run]


def test_no_runs_yet_is_none_not_an_error(tmp_path):
    assert latest_run_dir(tmp_path) is None
    assert latest_run_dir(tmp_path / "does-not-exist") is None


def test_a_single_workspace_run_is_named_for_the_workspace():
    label = run_label("My Project", [{"id": "guid", "name": "Explore Fabric - NOIDA"}])

    assert "Explore" in label and "NOIDA" in label


def test_a_multi_workspace_run_is_named_for_the_project():
    # Naming a six-workspace run after whichever came first would be misleading.
    label = run_label("My Project", [{"name": "A"}, {"name": "B"}])

    assert label == slugify("My Project")


def test_a_workspace_with_no_name_falls_back_to_its_id():
    assert run_label("Proj", [{"id": "2a740b96"}]) == "2a740b96"


def test_names_that_would_break_a_path_are_made_safe(tmp_path):
    directory = new_run_dir(tmp_path, 'A/B:C*?"<>|D', now=_at(1))

    assert directory.exists()
    assert not any(ch in directory.name for ch in '/:*?"<>|')


def test_an_empty_name_still_produces_a_directory(tmp_path):
    assert new_run_dir(tmp_path, "", now=_at(1)).exists()


# -- clearing up after a run that never got going ------------------------------

def test_an_empty_run_directory_is_pruned(tmp_path):
    """A failed audit leaves an empty folder, which reads as "found nothing"."""
    failed = new_run_dir(tmp_path, "NOIDA", now=_at(1))

    removed = prune_empty_runs(tmp_path)

    assert removed == [failed]
    assert not failed.exists()


def test_a_run_that_wrote_anything_is_left_alone(tmp_path):
    kept = new_run_dir(tmp_path, "NOIDA", now=_at(1))
    (kept / "audit-report.md").write_text("real", encoding="utf-8")

    assert prune_empty_runs(tmp_path) == []
    assert (kept / "audit-report.md").exists()


def test_pruning_ignores_folders_that_are_not_runs(tmp_path):
    """Only timestamped run directories are ours to delete."""
    (tmp_path / "kb-cache").mkdir()

    prune_empty_runs(tmp_path)

    assert (tmp_path / "kb-cache").exists()


# -- a refresh re-crawls the SAME run --------------------------------------

def test_the_kb_refresh_reuses_the_original_run_directory(tmp_path, monkeypatch):
    """The refresh replaces an audit's report, so it must replace its files.

    Given a fresh directory it would create a newer, EMPTY one while the live
    crawl ran, and the newest folder is what both a person and `latest_run_dir`
    reach for - so a report would be downloaded, or scored, from a run that had
    written nothing yet. That is exactly what a reviewer hit: a successful audit
    whose newest output folder was empty.
    """
    import asyncio
    from types import SimpleNamespace

    from auditfast.database.models import AuditJob
    from auditfast.database.repositories.memory import InMemoryAuditJobRepository
    from auditfast.services import audit_runner as runner_module
    from auditfast.services.audit_runner import AuditRunner

    original = new_run_dir(tmp_path, "NOIDA", now=_at(1))
    (original / "audit-report.md").write_text("first", encoding="utf-8")

    captured: dict = {}

    def _fake_run_audit(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(out_dir=kwargs.get("run_dir"))

    monkeypatch.setattr(runner_module.audit_service, "run_audit", _fake_run_audit)
    monkeypatch.setattr(runner_module.audit_service, "to_json", lambda _run: {"overall": 1.0})

    repository = InMemoryAuditJobRepository()
    job = AuditJob(id="job1", out_dir=str(original))

    async def _exercise():
        await repository.add(job)
        await AuditRunner(repository)._refresh_in_background(
            job, project_path="p", pillars=None, workspaces=None,
            out_dir=str(tmp_path), token=None,
        )

    asyncio.run(_exercise())

    assert captured.get("run_dir") == str(original), (
        "the refresh must re-crawl into the job's own directory"
    )
    assert run_dirs(tmp_path) == [original], "a refresh must not add a directory"
