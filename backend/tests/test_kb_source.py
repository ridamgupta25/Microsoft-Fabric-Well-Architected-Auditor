"""KB-source integration tests: seed + snapshot provider + end-to-end runner.

Reuses a crawl snapshot dict (same shape as ``WorkspaceContext.to_dict()``) to
drive the whole pipeline read-only, with no network and a fake generator.
"""
from __future__ import annotations

import pytest

from auditfast.ai.custom_runtime.base_check import clear_custom_registry
from auditfast.ai.orchestrator import pipeline
from auditfast.ai.orchestrator.kb_source import SnapshotFetchProvider, seed_session
from auditfast.ai.orchestrator.state import CustomCheckSession, FetchPlan, LifecycleStatus

_SNAPSHOT = {
    "id": "ws-123",
    "display_name": "Finance",
    "git_connected": True,
    "refresh_schedules": {"Sales": {"enabled": True}},
    "warehouse_audit": {},  # present key but empty -> treated as absent
}

_GOOD = (
    "class Chk(BaseAuditCheck):\n"
    "    check_id = 'chk_gen'\n"
    "    def evaluate(self, kb):\n"
    "        return {'status': 'PASS', 'score': 100.0, 'findings': [], 'recommendations': []}\n"
)
def _generate(*_args):
    return _GOOD


def _noop_router(check):
    return check


_GEN = _generate
_NOOP_ROUTER = _noop_router


@pytest.fixture(autouse=True)
def _clean():
    clear_custom_registry()
    yield
    clear_custom_registry()


# -- seed ----------------------------------------------------------------------

def test_seed_session_loads_snapshot_by_id():
    session = CustomCheckSession()
    seed_session(session, _SNAPSHOT)
    assert "ws-123" in session.shared_kb
    assert session.shared_kb["ws-123"]["git_connected"] is True


def test_seed_accepts_object_with_to_dict():
    class Ctx:
        def to_dict(self):
            return {"id": "ws-9", "git_connected": False}

    session = CustomCheckSession()
    seed_session(session, Ctx())
    assert session.shared_kb["ws-9"]["git_connected"] is False


# -- snapshot provider ---------------------------------------------------------

def test_provider_serves_present_field():
    provider = SnapshotFetchProvider(_SNAPSHOT)
    plan = FetchPlan(field="refresh_schedules", resource="SEMANTIC_MODEL", endpoint="x")
    response = provider.fetch(plan, "item_rest")
    assert response.status == 200
    assert response.body == {"Sales": {"enabled": True}}


def test_provider_404s_for_absent_or_empty_field():
    provider = SnapshotFetchProvider(_SNAPSHOT)
    assert provider.fetch(FetchPlan(field="warehouse_audit", resource="W", endpoint="x"), "item_rest").status == 404
    assert provider.fetch(FetchPlan(field="spark_settings", resource="S", endpoint="x"), "item_rest").status == 404


# -- end-to-end ----------------------------------------------------------------

def test_seeded_present_data_reaches_codegen():
    # No-op router forces the unique path so the identifier/codegen path runs.
    session = pipeline.run_custom_checks(
        ["verify git integration is present"], _SNAPSHOT,
        router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    check = session.checks[0]
    # git_connected is in the seed -> identifier marks it present.
    assert check.lifecycle_status is LifecycleStatus.PROCESSED_CUSTOM
    assert check.code_gen.status == "GENERATED"


def test_known_intent_routes_to_default_via_real_router():
    # A prompt close to an existing default check is deduplicated (realistic).
    session = pipeline.run_custom_checks(["verify git integration is present"], _SNAPSHOT)
    assert session.checks[0].lifecycle_status is LifecycleStatus.ROUTED_DEFAULT


def test_unseeded_field_is_fetched_from_snapshot():
    # Do not seed: the field must be fetched from the snapshot by Node 3b.
    session = CustomCheckSession()
    provider = SnapshotFetchProvider(_SNAPSHOT)
    pipeline.run_batch(
        ["ensure semantic models have incremental refresh"],
        session=session, provider=provider, router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    check = session.checks[0]
    assert check.lifecycle_status is LifecycleStatus.KB_AUGMENTED
    assert "refresh_schedules" in session.shared_kb
    assert check.kb_update.provenance[0]["source"] == "item_rest"


def test_run_custom_checks_produces_a_report():
    session = pipeline.run_custom_checks(
        ["verify git integration is present"], _SNAPSHOT,
        router=_NOOP_ROUTER, generator=_GEN, reviewer=None,
    )
    pipeline.approve(session.checks[0])
    report = pipeline.render_report(session)
    assert "# Custom Checks Report" in report
    assert "## Custom Checks" in report
