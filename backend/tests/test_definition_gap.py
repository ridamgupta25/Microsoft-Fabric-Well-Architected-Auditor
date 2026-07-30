"""The definition-gap fix: a blocked getDefinition must be visible, not silent.

When the crawl cannot read a notebook/pipeline definition (a token without the
Item.ReadWrite scope Fabric requires, or a transient failure), the artifact used
to vanish and the report said "no notebooks were found". These tests pin the new
behaviour: the provider reports the read as *failed* (not empty), and the engine
turns that into an actionable N/A that names the likely cause — never a FAIL, so
the score is unaffected.
"""
from __future__ import annotations

from auditfast.clients.live import LiveFabricProvider
from auditfast.core.check.helpers import binary
from auditfast.core.check.registry import CheckRegistry, check
from auditfast.core.engine import run_audit
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Status
from auditfast.core.models import Item, WorkspaceContext

# -- fakes ---------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int, body=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.headers: dict = {}

    def post(self, url, timeout=None):
        return self._response


class _StubProvider:
    """A provider that returns one hand-built context, contacting nothing."""

    def __init__(self, ctx: WorkspaceContext):
        self._ctx = ctx

    def fetch(self, workspace_id, layer=Layer.MIXED, resources=()):
        return self._ctx

    def list_workspaces(self):
        return []


# -- provider: a blocked read is reported with its kind, not empty -------------

def test_permission_denied_is_forbidden():
    provider = LiveFabricProvider("token")
    provider._session = _FakeSession(_FakeResponse(401))
    parts, failure = provider._definition_parts("ws", "item")
    assert parts == []
    assert failure == "forbidden"


def test_successful_read_is_not_flagged_failed():
    body = {"definition": {"parts": [{"path": "x.ipynb", "payload": "e30="}]}}
    provider = LiveFabricProvider("token")
    provider._session = _FakeSession(_FakeResponse(200, body))
    parts, failure = provider._definition_parts("ws", "item", fmt="ipynb")
    assert failure == ""
    assert parts == body["definition"]["parts"]


# -- engine: a blocked read becomes an actionable N/A --------------------------

def _notebook_registry() -> CheckRegistry:
    reg = CheckRegistry()

    @check(id="NB-GAPTEST", ref="9.99", title="Notebook gap test",
           pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK,
           requires=[Resource.NOTEBOOK_DEFINITIONS], registry=reg)
    def _nb(ctx):  # pragma: no cover - never reached when definitions are absent
        return binary(True, "ran")

    return reg


def test_unreadable_definitions_yield_an_actionable_na():
    ctx = WorkspaceContext(
        id="ws1", display_name="WS One", layer=Layer.PREP,
        items=[Item(id="nb1", type="Notebook", display_name="NB One")],
        unavailable={Resource.NOTEBOOK_DEFINITIONS},
    )
    results = run_audit(_StubProvider(ctx), [("ws1", Layer.PREP)], {},
                        registry=_notebook_registry())
    assert len(results) == 1
    assert results[0].status is Status.NA  # never FAIL — score is unaffected
    assert "could not be read" in results[0].evidence
    assert "Item.ReadWrite.All" in results[0].evidence


def test_genuinely_absent_objects_keep_the_plain_message():
    ctx = WorkspaceContext(id="ws2", display_name="WS Two", layer=Layer.PREP, items=[])
    results = run_audit(_StubProvider(ctx), [("ws2", Layer.PREP)], {},
                        registry=_notebook_registry())
    assert len(results) == 1
    assert results[0].status is Status.NA
    assert "No notebooks were found" in results[0].evidence
