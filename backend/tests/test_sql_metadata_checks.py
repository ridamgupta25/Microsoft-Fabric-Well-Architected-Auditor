"""Checks that gained evidence from the SQL catalog batch.

The crawl now reads declared constraints, partition row counts and database
principals alongside the column schemas. Three validated checks can use that:

* **4.5.6 TB-SURROGATE** - a declared primary key is the model saying "this is my
  key", which beats inferring one from the column name;
* **4.5.1 TB-STARSCHEMA** - row counts make "is this fact big enough for its
  shape to matter" measurable rather than assumed;
* **IMPL-01 / IMPL-02** - workspace role assignments need *Member or higher*, so
  on a Viewer sign-in they are unavailable and both checks went N/A. Database
  principals answer the same question with no elevated role.

The rule these share: prefer what the platform *declares* over what a name
*suggests*, and say which source was used so the two are never conflated.
"""
from __future__ import annotations

from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Resource
from auditfast.core.models import CheckContext, RoleAssignment, WorkspaceContext


def _spec(check_id: str):
    return next(s for s in REGISTRY if s.id == check_id)


def _run(check_id: str, **kwargs):
    ws = WorkspaceContext(id="ws", display_name="ws", **kwargs)
    outcome = _spec(check_id).fn(CheckContext(ws, {}, "ws", None))
    return outcome[0] if isinstance(outcome, list) else outcome


def _cols(*names: str) -> list[dict]:
    return [{"name": n, "type": "varchar(50)"} for n in names]


# -- 4.5.6 prefers a declared key over a name guess ----------------------------


def test_a_declared_primary_key_counts_even_when_the_name_does_not():
    """``sys.key_constraints`` is the Warehouse declaring its own key.

    ``business_ref`` reads as no kind of surrogate key, but the model says it is
    the key - and the model outranks the name.
    """
    tables = {
        "dim_customer": {"columns": _cols("business_ref", "name"),
                         "has_declared_key": True},
    }
    verdict = _run("TB-SURROGATE", tables=tables)
    assert verdict.score == 3
    assert "declare a primary/unique key constraint" in verdict.evidence


def test_name_inference_still_works_without_declared_constraints():
    """A Lakehouse table declares no constraints, so the naming rule remains."""
    tables = {"dim_product": {"columns": _cols("ProductKey", "name")}}
    assert _run("TB-SURROGATE", tables=tables).score == 3


def test_a_dimension_with_neither_signal_still_fails():
    tables = {"dim_geography": {"columns": _cols("state", "region")}}
    verdict = _run("TB-SURROGATE", tables=tables)
    assert verdict.score == 0
    assert "dim_geography" in verdict.evidence


# -- 4.5.1 reports size from partition metadata --------------------------------


def test_star_schema_reports_row_counts_without_reading_rows():
    tables = {
        "fact_sales": {"columns": _cols("sales_sk", "amount"), "row_count": 4_200_000},
        "dim_customer": {"columns": _cols("customer_sk")},
    }
    verdict = _run("TB-STARSCHEMA", tables=tables)
    assert verdict.score == 3
    assert "4,200,000 rows" in verdict.evidence
    assert "no row was read" in verdict.evidence


def test_star_schema_omits_size_when_no_row_count_was_read():
    """A Lakehouse table may carry no partition metadata - say nothing, not zero."""
    tables = {
        "fact_sales": {"columns": _cols("sales_sk", "amount")},
        "dim_customer": {"columns": _cols("customer_sk")},
    }
    verdict = _run("TB-STARSCHEMA", tables=tables)
    assert verdict.score == 3
    assert "rows" not in verdict.evidence.split("Widest fact")[-1].split(";")[0]


# -- IMPL-01 / IMPL-02 fall back to database principals ------------------------


def _principals(*entries: tuple[str, str]) -> list[dict]:
    return [{"name": n, "type": t, "authentication": "EXTERNAL", "store": "WH"}
            for n, t in entries]


def test_role_check_uses_workspace_assignments_when_they_are_readable():
    """The primary source wins - the fallback is only for when it is missing."""
    ws_roles = [RoleAssignment(principal_type="Group", display_name="Analysts",
                               role="Member")]
    verdict = _run("WS-ROLES-GROUPS", role_assignments=ws_roles,
                   sql_principals=_principals(("someone@x.com", "EXTERNAL_USER")))
    assert "SQL analytics endpoint" not in verdict.evidence


def test_role_check_falls_back_to_database_principals():
    """A Viewer sign-in cannot read workspace roles; this still answers the point."""
    ws = WorkspaceContext(
        id="ws", display_name="ws",
        unavailable={Resource.ROLE_ASSIGNMENTS},
        sql_principals=_principals(
            ("analysts@contoso.com", "EXTERNAL_USER"),
            ("Data Engineers", "EXTERNAL_GROUP"),
        ),
    )
    verdict = _spec("WS-ROLES-GROUPS").fn(CheckContext(ws, {}, "ws", None))
    assert verdict.score is not None, "the point is answerable without Member access"
    assert "1 of 2" in verdict.evidence
    assert "Database scope only" in verdict.evidence, "the weaker source must be named"


def test_role_check_is_na_when_neither_source_is_available():
    ws = WorkspaceContext(id="ws", display_name="ws",
                          unavailable={Resource.ROLE_ASSIGNMENTS})
    assert _spec("WS-ROLES-GROUPS").fn(CheckContext(ws, {}, "ws", None)).score is None


def test_guest_check_finds_external_principals_in_the_database():
    ws = WorkspaceContext(
        id="ws", display_name="ws",
        unavailable={Resource.ROLE_ASSIGNMENTS},
        sql_principals=_principals(
            ("guest_contoso.com#EXT#@fabrikam.onmicrosoft.com", "EXTERNAL_USER"),
            ("Data Engineers", "EXTERNAL_GROUP"),
        ),
    )
    verdict = _spec("WS-GUESTS").fn(CheckContext(ws, {}, "ws", None))
    assert verdict.score == 0, "an #EXT# principal is a guest wherever it is read"
    assert "#EXT#" in verdict.evidence


def test_guest_check_passes_when_no_external_principal_exists():
    ws = WorkspaceContext(
        id="ws", display_name="ws",
        unavailable={Resource.ROLE_ASSIGNMENTS},
        sql_principals=_principals(("Data Engineers", "EXTERNAL_GROUP")),
    )
    verdict = _spec("WS-GUESTS").fn(CheckContext(ws, {}, "ws", None))
    assert verdict.score == 3
    assert "Database scope only" in verdict.evidence
