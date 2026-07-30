"""Tests for the on-disk knowledge base: serialization, store, caching provider."""
from __future__ import annotations

from auditfast.core.enums import Layer, Resource
from auditfast.core.models import Item, RoleAssignment, WorkspaceContext
from auditfast.services.context_store import CachingProvider, ContextStore


def _rich_context() -> WorkspaceContext:
    return WorkspaceContext(
        id="w1", display_name="WS One", layer=Layer.PREP, capacity_id="cap-1",
        git_connected=True, deployment_pipeline=True,
        role_assignments=[RoleAssignment(
            principal_type="User", display_name="a@x", role="Admin", principal_id="1")],
        items=[Item(id="i1", type="Notebook", display_name="NB",
                    sensitivity_label="L1", last_run_utc="2026-01-01")],
        pipelines={"pl": {"properties": {"activities": []}}},
        notebooks={"nb": {"cells": [{"cell_type": "code", "source": "x=1"}]}},
        tables={"t": {"type": "Managed", "format": "Delta"}},
        shortcuts={"lh": [{"name": "s", "path": "/x", "target_type": "OneLake"}]},
        semantic_models={"m": {"measures": ["Total"]}},
        git_details={"provider": "AzureDevOps", "branch": "main"},
        unavailable={Resource.GIT},
    )


# -- serialization -------------------------------------------------------------

def test_workspace_context_round_trips():
    ctx = _rich_context()
    back = WorkspaceContext.from_dict(ctx.to_dict())
    assert back.id == "w1"
    assert back.display_name == "WS One"
    assert back.layer is Layer.PREP
    assert back.capacity_id == "cap-1"
    assert back.git_connected is True
    assert back.deployment_pipeline is True
    assert back.items[0].type == "Notebook"
    assert back.items[0].sensitivity_label == "L1"
    assert back.role_assignments[0].role == "Admin"
    assert back.pipelines == ctx.pipelines
    assert back.notebooks == ctx.notebooks
    assert back.tables == ctx.tables
    assert back.git_details["branch"] == "main"
    assert Resource.GIT in back.unavailable


def test_to_dict_is_json_safe():
    import json

    payload = json.dumps(_rich_context().to_dict())
    assert '"WS One"' in payload


# -- store ---------------------------------------------------------------------

def test_store_save_and_load(tmp_path):
    store = ContextStore(tmp_path)
    assert store.load("w1") is None
    store.save(_rich_context())
    loaded = store.load("w1")
    assert loaded is not None and loaded.display_name == "WS One"
    assert store.age_seconds("w1") is not None
    assert "w1" in store.workspaces()


def test_store_survives_a_new_instance(tmp_path):
    ContextStore(tmp_path).save(_rich_context())
    # A fresh store (cold in-memory cache) reads the snapshot back off disk.
    assert ContextStore(tmp_path).load("w1").layer is Layer.PREP


def test_store_delete(tmp_path):
    store = ContextStore(tmp_path)
    store.save(_rich_context())
    assert store.delete("w1") is True
    assert store.load("w1") is None


# -- caching provider ----------------------------------------------------------

class _FakeLive:
    def __init__(self, ctx: WorkspaceContext):
        self._ctx = ctx
        self.calls = 0

    def fetch(self, workspace_id, layer=Layer.MIXED, resources=None):
        self.calls += 1
        return self._ctx

    def list_workspaces(self):
        return [{"id": "w1"}]


def test_first_fetch_crawls_and_caches(tmp_path):
    live = _FakeLive(_rich_context())
    provider = CachingProvider(live, ContextStore(tmp_path))
    provider.fetch("w1")
    assert live.calls == 1
    assert provider.served_from_cache is False


def test_second_fetch_is_served_from_cache(tmp_path):
    ctx = _rich_context()
    store = ContextStore(tmp_path)
    live = _FakeLive(ctx)
    CachingProvider(live, store).fetch("w1")   # warms the cache (1 live call)
    provider = CachingProvider(live, store)    # fresh provider, same store
    provider.fetch("w1")
    assert live.calls == 1                      # no new live call
    assert provider.served_from_cache is True


def test_force_refresh_always_crawls(tmp_path):
    store = ContextStore(tmp_path)
    live = _FakeLive(_rich_context())
    CachingProvider(live, store).fetch("w1")
    CachingProvider(live, store, force_refresh=True).fetch("w1")
    assert live.calls == 2


def test_hard_stale_snapshot_is_recrawled(tmp_path):
    live = _FakeLive(_rich_context())
    # ttl of -1 makes every snapshot hard-stale, so each fetch re-crawls.
    provider = CachingProvider(live, ContextStore(tmp_path), ttl_seconds=-1.0)
    provider.fetch("w1")
    provider.fetch("w1")
    assert live.calls == 2


def test_soft_stale_serves_cache_without_background_when_disabled(tmp_path):
    store = ContextStore(tmp_path)
    live = _FakeLive(_rich_context())
    CachingProvider(live, store).fetch("w1")
    # soft=-1 => always soft-stale; background disabled => served, no extra crawl.
    provider = CachingProvider(live, store, soft_seconds=-1.0, background_refresh=False)
    provider.fetch("w1")
    assert live.calls == 1
    assert provider.served_from_cache is True
