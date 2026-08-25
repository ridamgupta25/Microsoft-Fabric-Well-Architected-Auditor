"""The crawl must explain a SQL-endpoint gap without anyone reading the log.

Lakehouse and Warehouse *column* schemas are read over the SQL analytics
endpoint, which needs ``pyodbc`` and an ODBC driver - neither installable from
Python. When they are absent the affected checks correctly report N/A, but the
reason used to reach ``log.warning`` only. A snapshot therefore showed "no
columns" with no explanation, and the log is gone once the process exits.

That is fine while an engineer runs the tool locally and can read the console.
It is not fine for a hosted deployment, where nobody sees the server output - so
these pin the two places the reason must now appear:

* ``read_failures`` on the snapshot, so the report's crawl-completeness section
  states the cause;
* ``/health``, so a bad deployment is visible before anyone runs an audit.
"""
from __future__ import annotations

from auditfast.clients.live import LiveFabricProvider
from auditfast.core.engine import read_incomplete_result
from auditfast.core.enums import Resource
from auditfast.core.models import WorkspaceContext


def _gap(reason: str, endpoints: int = 3,
         wanted: set[Resource] | None = None) -> WorkspaceContext:
    ctx = WorkspaceContext(id="ws")
    LiveFabricProvider._record_environment_gap(
        ctx,
        wanted if wanted is not None else {Resource.TABLE_COLUMNS,
                                           Resource.WAREHOUSE_SECURITY},
        reason,
        endpoints,
    )
    return ctx


def test_missing_driver_reason_is_persisted_not_just_logged():
    """The single most common cause of "no Lakehouse columns" must self-explain."""
    reason = "no 'ODBC Driver for SQL Server' is installed"
    ctx = _gap(reason)

    stat = ctx.read_failures[Resource.TABLE_COLUMNS.value]
    assert stat["read"] == 0
    assert stat["attempted"] == 3
    assert reason in stat["reasons"], "the snapshot must carry the reason"


def test_the_resource_is_marked_unavailable_so_checks_report_na():
    """Unreadable data is N/A, never FAIL - the library's central invariant."""
    ctx = _gap("pyodbc is not installed on the server")
    assert not ctx.has(Resource.TABLE_COLUMNS)
    assert not ctx.has(Resource.WAREHOUSE_SECURITY)


def test_only_the_requested_resources_are_recorded():
    """A run that never asked for warehouse security must not report a gap in it."""
    ctx = _gap("no SQL-audience token", wanted={Resource.TABLE_COLUMNS})
    assert Resource.TABLE_COLUMNS.value in ctx.read_failures
    assert Resource.WAREHOUSE_SECURITY.value not in ctx.read_failures
    assert ctx.has(Resource.WAREHOUSE_SECURITY)


def test_no_provisioned_endpoint_is_recorded_with_its_own_reason():
    """A paused capacity and a missing driver are different problems."""
    ctx = _gap("no provisioned SQL analytics endpoint was discovered", endpoints=0)
    reasons = ctx.read_failures[Resource.TABLE_COLUMNS.value]["reasons"]
    assert any("provisioned" in r for r in reasons)
    # Zero endpoints must still record a count, or the histogram reads as empty.
    assert sum(reasons.values()) >= 1


def test_no_endpoint_report_does_not_say_zero_of_zero_failed():
    ctx = _gap("no provisioned SQL analytics endpoint was discovered", endpoints=0)

    warning = read_incomplete_result(
        ctx,
        Resource.TABLE_COLUMNS.value,
        ctx.read_failures[Resource.TABLE_COLUMNS.value],
    )

    assert warning.evidence.startswith("Lakehouse/warehouse column schemas unavailable")
    assert "0 of 0" not in warning.evidence
    assert "capacity is running" in warning.recommendation
