"""Running the check library over saved / uploaded KB snapshots — no live tenant.

These cover the "Saved KB" and "Upload KB" audit sources end to end: the archive
picker (:class:`KBArchive.index`), lazy replay
(:class:`SnapshotProvider`), upload validation
(:func:`audit_service.validate_snapshot`), and the two REST endpoints plus a
sign-in-free ``source="kb"`` audit. Snapshots are built from the recorded-tenant
fixture so ``to_dict``/``from_dict`` round-tripping is exercised for real.
"""
from __future__ import annotations

import json
import time

import pytest

from auditfast.core.enums import Layer
from auditfast.core.errors import WorkspaceAccessError
from auditfast.services import audit_service
from auditfast.services.context_store import KBArchive, SnapshotProvider

from .fixtures.provider import RecordedProvider


def _ctx(workspace_id: str = "ws-prep-01", layer: Layer = Layer.PREP):
    """A real WorkspaceContext drawn from the recorded-tenant fixture."""
    return RecordedProvider().fetch(workspace_id, layer)


def _wait_for_audit(client, audit_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/audit/{audit_id}").json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"audit {audit_id} did not finish within {timeout}s")


# -- KBArchive: the saved-KB picker --------------------------------------------

def test_index_lists_one_row_per_workspace(tmp_path):
    archive = KBArchive(tmp_path)
    archive.save(_ctx("ws-prep-01", Layer.PREP))
    archive.save(_ctx("ws-store-01", Layer.STORAGE))

    rows = archive.index()
    assert {r["id"] for r in rows} == {"ws-prep-01", "ws-store-01"}
    # Rows are sorted by display name so the picker is stable.
    assert rows == sorted(rows, key=lambda r: (r["name"] or "").lower())


def test_load_latest_picks_the_newest_snapshot(tmp_path):
    """Every run appends a dated folder; replay must load the most recent one."""
    archive = KBArchive(tmp_path)
    base = tmp_path / "ws"
    for stamp, name in (("20240101_000000", "old"), ("20240102_000000", "new")):
        leaf = base / f"ws_{stamp}"
        leaf.mkdir(parents=True)
        data = _ctx("ws-prep-01", Layer.PREP).to_dict()
        data["display_name"] = name
        (leaf / "workspace.json").write_text(json.dumps(data), encoding="utf-8")
        (leaf / "summary.json").write_text(
            json.dumps(
                {
                    "workspace_id": "ws-prep-01",
                    "workspace": name,
                    "captured_at": stamp,
                    "layer": Layer.PREP.value,
                    "complete": True,
                    "items": 0,
                    "pipelines_read": 0,
                }
            ),
            encoding="utf-8",
        )

    loaded = archive.load_latest("ws-prep-01")
    assert loaded is not None
    assert loaded.display_name == "new"


def test_load_latest_returns_none_for_unknown_workspace(tmp_path):
    assert KBArchive(tmp_path).load_latest("nope") is None


# -- SnapshotProvider: the replay provider -------------------------------------

def test_provider_serves_uploaded_and_overrides_layer():
    provider = SnapshotProvider(uploaded={"ws-prep-01": _ctx("ws-prep-01", Layer.PREP)})
    served = provider.fetch("ws-prep-01", Layer.STORAGE)
    assert served.id == "ws-prep-01"
    # The layer is an audit-time role, applied per run — exactly as a live crawl.
    assert served.layer is Layer.STORAGE


def test_provider_falls_back_to_the_archive(tmp_path):
    archive = KBArchive(tmp_path)
    archive.save(_ctx("ws-ops-01", Layer.OPERATIONS))
    provider = SnapshotProvider(archive=archive)
    assert provider.fetch("ws-ops-01", Layer.OPERATIONS).id == "ws-ops-01"


def test_provider_raises_for_a_workspace_not_in_the_kb():
    provider = SnapshotProvider(uploaded={})
    with pytest.raises(WorkspaceAccessError):
        provider.fetch("ghost")


# -- validate_snapshot: upload validation --------------------------------------

def test_validate_snapshot_accepts_a_top_level_context():
    result = audit_service.validate_snapshot(_ctx("ws-prep-01", Layer.PREP).to_dict())
    assert result["workspace"]["id"] == "ws-prep-01"
    assert result["snapshot"]["id"] == "ws-prep-01"


def test_validate_snapshot_accepts_a_cache_wrapped_context():
    wrapped = {"saved_at": 1.0, "context": _ctx("ws-prep-01", Layer.PREP).to_dict()}
    assert audit_service.validate_snapshot(wrapped)["workspace"]["id"] == "ws-prep-01"


def test_validate_snapshot_rejects_a_non_snapshot():
    with pytest.raises(audit_service.AuditError):
        audit_service.validate_snapshot({"nonsense": True})


# -- REST surface --------------------------------------------------------------

def test_kb_workspaces_endpoint_needs_no_sign_in(client):
    response = client.get("/api/v1/workspaces/kb")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_kb_upload_roundtrips_a_snapshot(client):
    response = client.post(
        "/api/v1/workspaces/kb/upload", json=_ctx("ws-prep-01", Layer.PREP).to_dict()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["id"] == "ws-prep-01"
    assert body["snapshot"]["id"] == "ws-prep-01"


def test_kb_upload_rejects_an_invalid_file(client):
    response = client.post("/api/v1/workspaces/kb/upload", json={"nonsense": True})
    assert response.status_code == 400
    assert response.json()["correlation_id"]


def test_kb_audit_runs_without_sign_in(client):
    """source="kb" needs no token: an uploaded snapshot drives the whole run."""
    accepted = client.post(
        "/api/v1/audit",
        json={"source": "kb", "snapshots": [_ctx("ws-prep-01", Layer.PREP).to_dict()]},
    )
    assert accepted.status_code == 202

    finished = _wait_for_audit(client, accepted.json()["audit_id"])
    assert finished["status"] == "succeeded"
    assert finished["report"]["kb"]["source"] == "kb"
