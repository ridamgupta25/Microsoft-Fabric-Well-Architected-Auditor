"""A network failure is not a permission denial.

``dataset_refresh_schedule`` tries two URL forms. It classified the outcome with
a single flag that a 401/403 on the *first* attempt latched permanently - so a
DNS failure or reset connection on the second was still filed as ``forbidden``.

That distinction is load-bearing. ``forbidden`` tells the knowledge base "this
will never work with this token", which suppresses a retry; ``transient`` says
"try again". On a real crawl, 7 DNS failures and 1 reset were recorded as 410
forbidden reads, making a network blip look like a tenant-wide permission gap.
"""
from __future__ import annotations

import pytest

from auditfast.clients import powerbi as powerbi_module
from auditfast.clients.powerbi import PowerBIClient


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """The read retries a transport failure three times, sleeping between.

    Real sleeps would add seconds per test for nothing: the classification is
    what is under test, not the backoff.
    """
    monkeypatch.setattr(powerbi_module.time, "sleep", lambda _seconds: None)


class _StubClient(PowerBIClient):
    """A client whose HTTP layer replays a scripted list of ``(status, body)``.

    ``__init__`` is deliberately not called: the real one builds a ``requests``
    session, and this test needs none - only the classification logic sitting
    above the transport.

    **The seam is** :meth:`PowerBIClient._get_with_meta`, **not** ``_get``.
    ``dataset_refresh_schedule`` reads through the meta form so it can honour
    ``Retry-After``, and ``_get`` is now only a thin wrapper over it. A stub
    overriding ``_get`` was therefore never called at all: the real
    ``_get_with_meta`` ran, reached for the ``requests`` session this stub does
    not build, and turned every scripted status into a transport error - so the
    403 case asserting ``forbidden`` silently saw ``transient``, and the
    URL-order case recorded no paths. Always stub the lowest method that touches
    the network.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.paths: list[str] = []

    def _get_with_meta(self, path: str):
        self.paths.append(path)
        status, body = self._responses.pop(0) if self._responses else (None, None)
        # No Retry-After: the scripted cases exercise classification, not backoff.
        return status, body, None


def test_a_transport_failure_is_transient_not_forbidden():
    """The real-crawl case: DNS could not resolve api.powerbi.com."""
    client = _StubClient([(None, None), (None, None)])
    schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert schedule is None
    assert failure == "transient", "a DNS failure says nothing about the token"


def test_a_403_followed_by_a_transport_failure_is_still_transient():
    """The latching bug: the last attempt decides, not the first.

    A 403 on the group-scoped URL is routine - the model may simply not be in
    that group - and must not mask a genuine network failure on the fallback.
    """
    client = _StubClient([(403, None), (None, None)])
    _schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert failure == "transient"


def test_a_genuine_permission_denial_is_still_forbidden():
    """The fix must not turn a real 403 into a retry loop."""
    client = _StubClient([(403, None), (401, None)])
    _schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert failure == "forbidden"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_throttling_and_server_errors_are_transient(status):
    client = _StubClient([(status, None), (status, None)])
    _schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert failure == "transient"


@pytest.mark.parametrize("status", [400, 404])
def test_no_schedule_is_a_real_answer_not_a_failure(status):
    """A Direct Lake or push model has no schedule - that is the finding."""
    client = _StubClient([(status, None)])
    schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert schedule is None
    assert failure == "", "absence of a schedule is not a read failure"


def test_a_successful_read_returns_the_schedule():
    body = {"enabled": True, "notifyOption": "MailOnFailure",
            "days": ["Monday"], "times": ["06:00"]}
    client = _StubClient([(200, body)])
    schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert failure == ""
    assert schedule is not None
    assert schedule.get("enabled") is True


def test_the_group_scoped_url_is_tried_first():
    """The group-scoped form is the documented one; the bare form is the fallback."""
    client = _StubClient([(404, None)])
    client.dataset_refresh_schedule("ds", group_id="ws")
    assert client.paths[0].startswith("/groups/ws/datasets/ds")


def test_the_stub_is_wired_to_the_method_production_actually_calls():
    """Regression: the stub overrode ``_get``, which this code path never calls.

    Every scripted status was then replaced by a transport error, so four tests
    quietly asserted the wrong thing and one could not see any URL at all. If
    ``dataset_refresh_schedule`` is ever re-plumbed onto a different transport
    helper, this fails immediately instead of turning the suite into a slow
    retry loop that agrees with itself.
    """
    client = _StubClient([(200, {"enabled": True})])
    schedule, failure = client.dataset_refresh_schedule("ds", group_id="ws")
    assert client.paths, "the stub's transport was never called"
    assert failure == "", "a scripted 200 must not read as a transport failure"
    assert schedule is not None
