"""Regression pins for the 4.2.5 / 14.1.8 validation round.

Both checks reported a partial score against estates that had done the work,
because each judged a population it should not have:

* **4.2.5 TB-AUDITCOLS** counted *every* readable table. On a real 1,820-table
  workspace that included Fabric's own ``managed_delta_table_*`` bookkeeping, SQL
  ``dm_*`` views, Dynamics ``msdyn_*`` system tables and Power Query staging
  tables named with a GUID whose columns are ``Column1..Column8``. Nobody
  designed those, so they can neither evidence nor fail a deliberate audit
  practice - and they were the bulk of the "failing" 1,276.

* **14.1.8 R-MODEL-HIDDEN-KEYS** used ``is_key_column``, whose vocabulary
  deliberately includes ``code``/``no``/``num``/``number``. That breadth is right
  for fact-purity checks (over-counting only makes them more lenient) but here it
  makes the check more *accusatory*: ``PostalCode`` was flagged 132 times as a
  technical key that ought to be hidden from report authors. Of 3,861 columns it
  judged, only 12 were keys the model itself declared.

The rule both share: judge the objects the customer built, not the ones the
platform created, and never turn a business attribute into a technical defect.
"""
from __future__ import annotations

import pytest

from auditfast.core.check._semantic import is_row_identifier
from auditfast.core.check._tables import is_key_column
from auditfast.core.check.registry import REGISTRY
from auditfast.core.models import CheckContext, WorkspaceContext


def _spec(check_id: str):
    return next(s for s in REGISTRY if s.id == check_id)


def _run(check_id: str, **kwargs):
    ws = WorkspaceContext(id="ws", display_name="ws", **kwargs)
    return _spec(check_id).fn(CheckContext(ws, {}, "ws", None))


def _table(*names: str) -> dict:
    return {"columns": [{"name": n, "type": "varchar(50)"} for n in names]}


# -- 4.2.5 judges solution tables only ----------------------------------------


def test_platform_tables_are_excluded_from_the_audit_column_population():
    """Fabric/SQL/Dynamics bookkeeping is not the customer's audit practice."""
    tables = {
        "dim_customer": _table("customer_sk", "name", "created_date"),
        "fact_sales": _table("sales_sk", "amount"),
        # Platform noise - present in every workspace, designed by nobody.
        "managed_delta_table_log_files": _table("commit_time", "rows_inserted"),
        "ADF_Assignment.dm_db_external_tables": _table("object_id", "last_update_time_utc"),
        "msdyn_solutioncomponent": _table("createdon", "modifiedon"),
    }
    verdict = _run("TB-AUDITCOLS", tables=tables)
    assert "1 of 2" in verdict.evidence, (
        f"only the two solution tables belong in the ratio: {verdict.evidence}")
    assert "fact_sales" in verdict.evidence, "the failing table should be named"


@pytest.mark.parametrize("name", [
    "exec_sessions_history",
    "frequently_run_queries",
    "long_running_queries",
    "managed_delta_tables",
    "sql_pool_insights",
])
def test_queryinsights_views_are_platform_tables(name):
    """Fabric's SQL-endpoint telemetry ships with every Lakehouse and Warehouse.

    Found on a real workspace, where these five were the entire "failing"
    population of 4.2.5 - a check about a deliberate lineage practice reporting a
    gap in views the customer never created.
    """
    from auditfast.core.check._tables import is_platform_table

    assert is_platform_table(name)
    assert is_platform_table(f"MyLakehouse.{name}"), "also when schema-qualified"


def test_guid_named_staging_tables_with_unnamed_columns_are_excluded():
    """A Power Query staging table (GUID name, Column1..N) was never designed."""
    tables = {
        "dim_product": _table("product_sk", "name", "load_date"),
        "2cd9369686744423bd830d9331b8de74_57202": _table("Column1", "Column2", "Column3"),
        "4a17f9ba863f420aa437185238be9e86_62fc9": _table("Column1"),
    }
    verdict = _run("TB-AUDITCOLS", tables=tables)
    assert "1 of 1" in verdict.evidence
    assert verdict.score == 3


def test_a_guid_named_table_with_real_columns_is_still_judged():
    """The exclusion needs *both* signals - a GUID name alone is not enough."""
    tables = {
        "2cd9369686744423bd830d9331b8de74_57202": _table("customer_id", "region"),
    }
    verdict = _run("TB-AUDITCOLS", tables=tables)
    assert "0 of 1" in verdict.evidence, "named columns mean somebody modelled it"


def test_audit_check_is_na_when_only_platform_tables_exist():
    """Nothing the customer built means nothing to judge - N/A, never 0."""
    verdict = _run("TB-AUDITCOLS", tables={
        "managed_delta_table_log_files": _table("commit_time", "rows_inserted"),
    })
    assert verdict.score is None
    assert "platform bookkeeping" in verdict.evidence


def test_the_audit_vocabulary_itself_is_unchanged():
    """The detector was never the problem - only the population it was applied to."""
    from auditfast.core.check._tables import is_audit_column

    for accepted in ("CreationDate", "created_date", "ModifiedOn", "load_ts",
                     "batch_id", "SourceSystem", "LastModified"):
        assert is_audit_column(accepted), f"{accepted} is a lineage column"
    for rejected in ("order_date", "birth_date", "due_date", "Date"):
        assert not is_audit_column(rejected), f"{rejected} is a business date"


# -- 14.1.8 judges technical keys only ----------------------------------------


@pytest.mark.parametrize("name", [
    "CustomerKey", "customer_sk", "ProductKey", "DateKey",
    "CustomerID", "Id", "row_guid", "OrderUUID",
])
def test_technical_keys_are_still_recognised(name):
    """A surrogate/warehouse key is exactly what should be hidden from consumers."""
    assert is_row_identifier({"name": name}), f"{name} is a technical key"


@pytest.mark.parametrize("name", [
    "PostalCode",       # flagged 132x on the reference estate - a business attribute
    "ProductCode",
    "OrderNumber",
    "InvoiceNo",
    "CountryCode",
    "monkey",
    "turkey",
    "key_account_manager",
])
def test_business_attributes_are_not_technical_keys(name):
    """A report author legitimately shows these; flagging them is a false finding."""
    assert not is_row_identifier({"name": name}), (
        f"{name} is a business attribute, not a technical key")


def test_the_narrower_rule_only_applies_to_the_semantic_layer():
    """``is_key_column`` keeps its broad vocabulary for the table checks.

    Two different questions: "is this any kind of identifier?" (fact purity,
    where over-counting is lenient) versus "is this a technical key a consumer
    should never see?" (here, where over-counting accuses).
    """
    assert is_key_column("PostalCode") is True, "the broad rule is unchanged"
    assert is_row_identifier({"name": "PostalCode"}) is False, "the narrow rule excludes it"


def test_a_declared_key_is_always_a_key_whatever_it_is_called():
    """``isKey`` is the model saying so outright - it outranks any name guess."""
    assert is_row_identifier({"name": "Month", "is_key": True})
    assert is_row_identifier({"name": "PostalCode", "is_key": True})


def test_14_1_8_scores_only_technical_keys():
    """End to end: a model whose only visible key-ish column is a business code."""
    models = {
        "Sales": {"columns": [
            {"name": "CustomerKey", "table": "DimCustomer", "is_hidden": True},
            {"name": "PostalCode", "table": "DimCustomer", "is_hidden": False},
            {"name": "CustomerName", "table": "DimCustomer", "is_hidden": False},
        ]},
    }
    outcome = _run("R-MODEL-HIDDEN-KEYS", semantic_models=models)
    verdict = outcome[0] if isinstance(outcome, list) else outcome
    assert verdict.score == 3, (
        f"the only technical key is hidden, so this model is compliant: {verdict.evidence}")
    assert "1 of 1" in verdict.evidence
