"""Crawl completeness: partial read failures are tracked, surfaced, and never cached.

R1 vs R2 in the field differed only by token permission: R1 could not read role
assignments or any getDefinition, so it scored 6; R2 could, and scored 2,316. The
danger was that R1's near-empty crawl looked like a genuine low score and, worse,
could have been cached for 24h. These tests pin the fix: failures are counted by
kind (forbidden vs transient), surfaced as visible warnings, and an incomplete
snapshot is never served from the cache.
"""
from __future__ import annotations

from auditfast.clients.live import LiveFabricProvider
from auditfast.core.check.helpers import binary
from auditfast.core.check.registry import CheckRegistry, check
from auditfast.core.engine import READ_INCOMPLETE_CHECK_ID, run_audit
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Status
from auditfast.core.models import Item, WorkspaceContext
from auditfast.services.context_store import (
    ArchivingProvider,
    CachingProvider,
    ContextStore,
    KBArchive,
)


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Session:
    def __init__(self, resp):
        self._resp = resp
        self.headers = {}

    def post(self, url, timeout=None):
        return self._resp


class _CountingLive:
    def __init__(self, ctx):
        self._ctx = ctx
        self.calls = 0

    def fetch(self, wid, layer=Layer.MIXED, resources=None):
        self.calls += 1
        return self._ctx

    def list_workspaces(self):
        return []


# -- the provider classifies failure kind -------------------------------------

def test_forbidden_and_transient_are_distinguished(monkeypatch):
    import auditfast.clients.live as live_mod
    monkeypatch.setattr(live_mod.time, "sleep", lambda *_a, **_k: None)  # skip retry backoff
    provider = LiveFabricProvider("t")
    provider._session = _Session(_Resp(403))
    assert provider._definition_parts("w", "i")[1] == "forbidden"
    provider._session = _Session(_Resp(500))
    assert provider._definition_parts("w", "i")[1] == "transient"


# -- record failures + is_complete --------------------------------------------

def test_partial_failure_keeps_reads_but_flags_the_gap():
    ctx = WorkspaceContext(id="w")
    LiveFabricProvider._record_failures(
        ctx, Resource.NOTEBOOK_DEFINITIONS, attempted=138, read=96, forbidden=42, transient=0)
    assert ctx.read_failures["notebookDefinitions"] == {
        "attempted": 138, "read": 96, "failed": 42,
        "forbidden": 42, "transient": 0, "empty": 0}
    # some read — the resource is NOT marked fully unavailable
    assert Resource.NOTEBOOK_DEFINITIONS not in ctx.unavailable
    assert ctx.is_complete is False


def test_unusable_definitions_are_reported_but_stay_cacheable():
    """A definition that came back empty is a real gap a re-crawl cannot fix."""
    ctx = WorkspaceContext(id="w")
    LiveFabricProvider._record_failures(
        ctx, Resource.SEMANTIC_MODEL_DEFINITIONS,
        attempted=414, read=413, forbidden=0, transient=0, empty=1)
    assert ctx.read_failures["semanticModelDefinitions"]["empty"] == 1
    assert ctx.is_complete is True


def test_total_failure_marks_the_resource_unavailable():
    ctx = WorkspaceContext(id="w")
    LiveFabricProvider._record_failures(
        ctx, Resource.PIPELINE_DEFINITIONS, attempted=50, read=0, forbidden=0, transient=50)
    assert Resource.PIPELINE_DEFINITIONS in ctx.unavailable
    assert ctx.is_complete is False


def test_clean_context_is_complete():
    assert WorkspaceContext(id="w").is_complete is True


def test_unavailable_role_assignments_makes_it_incomplete():
    assert WorkspaceContext(id="w", unavailable={Resource.ROLE_ASSIGNMENTS}).is_complete is False


def test_read_failures_round_trip_through_the_kb():
    ctx = WorkspaceContext(id="w")
    ctx.read_failures["notebookDefinitions"] = {
        "attempted": 3, "read": 1, "failed": 2, "forbidden": 2, "transient": 0}
    back = WorkspaceContext.from_dict(ctx.to_dict())
    assert back.read_failures == ctx.read_failures
    assert back.is_complete is False


# -- the engine surfaces the incomplete crawl ---------------------------------

class _Stub:
    def __init__(self, ctx):
        self._ctx = ctx

    def fetch(self, wid, layer=Layer.MIXED, resources=()):
        return self._ctx

    def list_workspaces(self):
        return []


def test_engine_emits_a_read_incomplete_warning():
    ctx = WorkspaceContext(
        id="w1", display_name="WS", layer=Layer.PREP,
        items=[Item(id="nb", type="Notebook", display_name="NB")])
    ctx.read_failures["notebookDefinitions"] = {
        "attempted": 138, "read": 0, "failed": 138, "forbidden": 138, "transient": 0}
    ctx.unavailable.add(Resource.NOTEBOOK_DEFINITIONS)

    reg = CheckRegistry()

    @check(id="NB-X", ref="9.9", title="nb", pillar=Pillar.PERFORMANCE,
           scope=Scope.NOTEBOOK, requires=[Resource.NOTEBOOK_DEFINITIONS], registry=reg)
    def _nb(c):  # pragma: no cover - not reached when definitions are absent
        return binary(True, "ok")

    results = run_audit(_Stub(ctx), [("w1", Layer.PREP)], {}, registry=reg)

    warnings = [r for r in results if r.check_id == READ_INCOMPLETE_CHECK_ID]
    assert len(warnings) == 1
    assert "138 of 138" in warnings[0].evidence
    assert warnings[0].status is Status.NA  # a read we could not make is not a failure
    nb = next(r for r in results if r.check_id == "NB-X")
    assert "138 of 138" in nb.evidence and "could not be read" in nb.evidence


# -- caching never serves an incomplete snapshot ------------------------------

def test_incomplete_snapshot_is_re_crawled_not_served(tmp_path):
    ctx = WorkspaceContext(id="w1", display_name="WS", layer=Layer.PREP)
    ctx.read_failures["notebookDefinitions"] = {
        "attempted": 5, "read": 0, "failed": 5, "forbidden": 5, "transient": 0}
    store = ContextStore(tmp_path)
    live = _CountingLive(ctx)
    CachingProvider(live, store).fetch("w1")      # crawl + cache (incomplete)
    CachingProvider(live, store).fetch("w1")      # must re-crawl, not serve
    assert live.calls == 2


# -- the permanent timestamped archive ----------------------------------------

def test_kb_archive_writes_a_dated_nested_folder(tmp_path):
    archive = KBArchive(tmp_path)
    ctx = WorkspaceContext(id="w1", display_name="Explore Fabric", layer=Layer.MIXED,
                           items=[Item(id="i", type="Notebook")])
    folder = archive.save(ctx)
    assert (folder / "workspace.json").exists()
    assert (folder / "summary.json").exists()
    # <root>/<workspace>/<workspace>_<timestamp>/
    assert folder.parent.name == "Explore_Fabric"
    assert folder.name.startswith("Explore_Fabric_")


def test_archiving_provider_saves_on_every_fetch(tmp_path):
    ctx = WorkspaceContext(id="w1", display_name="WS", layer=Layer.PREP)
    provider = ArchivingProvider(_CountingLive(ctx), KBArchive(tmp_path))
    provider.fetch("w1")
    assert len(list(tmp_path.rglob("workspace.json"))) == 1
