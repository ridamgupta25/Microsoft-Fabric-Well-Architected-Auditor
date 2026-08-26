"""Adaptive workspace batching.

A large run is split into sequential batches; the cooldown between batches is
paid only after a batch that was actually throttled, so a clean tenant never
waits but a big throttled run spaces its Power BI calls across separate
rate-limit windows. Batching is opt-in (AUDITFAST_WORKSPACE_BATCH_SIZE); off by
default, behaviour is unchanged.
"""
from __future__ import annotations

import auditfast.core.engine as engine
from auditfast.core.check.helpers import binary
from auditfast.core.check.registry import CheckRegistry, check
from auditfast.core.engine import _context_was_throttled, run_audit
from auditfast.core.enums import Layer, Pillar, Resource, Scope
from auditfast.core.errors import WorkspaceAccessError
from auditfast.core.models import Item, WorkspaceContext


def _throttled_ctx(wid: str) -> WorkspaceContext:
    ctx = WorkspaceContext(id=wid, display_name=wid, layer=Layer.PREP,
                           items=[Item(id="nb", type="Notebook", display_name="NB")])
    ctx.read_failures["notebookDefinitions"] = {
        "attempted": 1, "read": 0, "failed": 1,
        "forbidden": 0, "transient": 1, "empty": 0,
    }
    return ctx


def _clean_ctx(wid: str) -> WorkspaceContext:
    return WorkspaceContext(id=wid, display_name=wid, layer=Layer.PREP,
                            items=[Item(id="nb", type="Notebook", display_name="NB")])


class _MapProvider:
    def __init__(self, by_id: dict[str, WorkspaceContext]):
        self.by_id = by_id
        self.fetched: list[str] = []

    def fetch(self, wid, layer=Layer.MIXED, resources=()):
        self.fetched.append(wid)
        return self.by_id[wid]

    def list_workspaces(self):
        return []


def _registry() -> CheckRegistry:
    reg = CheckRegistry()

    @check(id="NB-X", ref="9.9", title="nb", pillar=Pillar.DATA_PROCESSING,
           scope=Scope.NOTEBOOK, requires=[Resource.NOTEBOOK_DEFINITIONS], registry=reg)
    def _nb(c):  # pragma: no cover - verdict irrelevant to batching
        return binary(True, "ok")

    return reg


def _targets(*ids: str):
    return [(wid, Layer.PREP) for wid in ids]


# -- the throttle detector -----------------------------------------------------

def test_context_was_throttled_detects_transient():
    assert _context_was_throttled(_throttled_ctx("w")) is True


def test_context_was_throttled_ignores_clean():
    assert _context_was_throttled(_clean_ctx("w")) is False


def test_context_was_throttled_flags_429_but_not_403():
    assert _context_was_throttled(WorkspaceAccessError("w", status=429)) is True
    assert _context_was_throttled(WorkspaceAccessError("w", status=403)) is False


# -- adaptive cooldown between batches -----------------------------------------

def test_throttled_batch_triggers_one_cooldown(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(engine, "WORKSPACE_BATCH_SIZE", 2)
    monkeypatch.setattr(engine, "BATCH_COOLDOWN_SECONDS", 30.0)

    # 4 workspaces -> 2 batches of 2; only the first batch is throttled.
    provider = _MapProvider({
        "w1": _throttled_ctx("w1"), "w2": _clean_ctx("w2"),
        "w3": _clean_ctx("w3"), "w4": _clean_ctx("w4"),
    })
    run_audit(provider, _targets("w1", "w2", "w3", "w4"), {}, registry=_registry())

    assert slept == [30.0]  # exactly one cooldown, after the throttled batch


def test_clean_batches_never_cool_down(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(engine, "WORKSPACE_BATCH_SIZE", 2)
    monkeypatch.setattr(engine, "BATCH_COOLDOWN_SECONDS", 30.0)

    provider = _MapProvider({
        "w1": _clean_ctx("w1"), "w2": _clean_ctx("w2"),
        "w3": _clean_ctx("w3"), "w4": _clean_ctx("w4"),
    })
    run_audit(provider, _targets("w1", "w2", "w3", "w4"), {}, registry=_registry())

    assert slept == []


def test_no_cooldown_after_the_final_batch(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(engine, "WORKSPACE_BATCH_SIZE", 2)
    monkeypatch.setattr(engine, "BATCH_COOLDOWN_SECONDS", 30.0)

    # Only the LAST batch is throttled -> no cooldown (nothing follows it).
    provider = _MapProvider({
        "w1": _clean_ctx("w1"), "w2": _clean_ctx("w2"),
        "w3": _throttled_ctx("w3"), "w4": _throttled_ctx("w4"),
    })
    run_audit(provider, _targets("w1", "w2", "w3", "w4"), {}, registry=_registry())

    assert slept == []


def test_batching_disabled_by_default_never_cools_down(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(engine, "WORKSPACE_BATCH_SIZE", 0)  # off

    provider = _MapProvider({
        "w1": _throttled_ctx("w1"), "w2": _throttled_ctx("w2"),
    })
    run_audit(provider, _targets("w1", "w2"), {}, registry=_registry())

    assert slept == []


def test_all_workspaces_are_still_crawled_when_batched(monkeypatch):
    monkeypatch.setattr(engine.time, "sleep", lambda _s: None)
    monkeypatch.setattr(engine, "WORKSPACE_BATCH_SIZE", 2)
    monkeypatch.setattr(engine, "BATCH_COOLDOWN_SECONDS", 5.0)

    provider = _MapProvider({wid: _clean_ctx(wid) for wid in ("w1", "w2", "w3", "w4", "w5")})
    run_audit(provider, _targets("w1", "w2", "w3", "w4", "w5"), {}, registry=_registry())

    assert sorted(provider.fetched) == ["w1", "w2", "w3", "w4", "w5"]
