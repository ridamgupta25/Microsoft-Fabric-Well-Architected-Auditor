"""Regression tests for the checks added for refs 4.1.2, 5.2.6, 5.3.1, 5.5.6, 14.1.x.

Every case here is a defect a review found in the first implementation. Each pairs
the misjudged input with the input that must keep working, so a future rewrite of a
detector cannot silently reintroduce the false PASS or false FAIL.
"""
from __future__ import annotations

import pytest

from auditfast.core.check._dax import (
    call_spans,
    expensive_iterator,
    repeated_subexpressions,
)
from auditfast.core.check.data_management_quality.data_prep.automated import (
    _COUNT_RECONCILE,
    _DEDUP_PATTERN,
    _FK_INTEGRITY,
    _KEY_QUALITY,
    _TYPE_CAST,
    nb_dedup,
    nb_key_quality,
    nb_merge_valid,
    nb_orphan_detect,
    nb_type_cast,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    _shadow_reason,
    shortcut_scope,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    _normalised,
    complex_measures_use_variables,
    measures_not_duplicated,
    single_direction_relationships,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext

_WRITES = 'df.write.saveAsTable("t")\n'


def _nb(code: str) -> dict:
    return {"cells": [{"cell_type": "code", "source": code}], "metadata": {}}


def _nb_ctx(code: str) -> CheckContext:
    return CheckContext(workspace=WorkspaceContext(id="w"), settings={},
                        obj_name="nb", obj=_nb(code))


def _model_ctx(models: dict) -> CheckContext:
    workspace = WorkspaceContext(id="w", semantic_models=models)
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


# --- detector precision -----------------------------------------------------

@pytest.mark.parametrize("source,expected,why", [
    ('print(f"Error during deduplication: {e}")', False, "a log message is not a control"),
    ('df.withColumn("UserID", row_number().over(w))', False, "ranking alone removes nothing"),
    ('df.withColumn("rn", row_number().over(w)).filter("rn = 1")', True, "ranking kept to rank 1"),
    ('dim = df.drop_duplicates()', True, "snake_case spelling"),
    ('df_raw.duplicated().sum()', True, "pandas duplicated()"),
    ('df.dropDuplicates()', True, "camelCase spelling"),
])
def test_dedup_detector(source: str, expected: bool, why: str):
    assert bool(_DEDUP_PATTERN.search(source)) is expected, why


@pytest.mark.parametrize("source,expected,why", [
    ("cols = [f.name for f in df.schema.fields if f.dataType in [IntegerType()]]",
     False, "type introspection casts nothing"),
    ("CREATE TABLE IF NOT EXISTS dim (\n  OrgID INT,\n  Name STRING\n)",
     True, "typed SQL DDL is explicit typing"),
    ("CREATE TABLE t AS SELECT * FROM raw -- loaded by DATE",
     False, "untyped CTAS that merely mentions a type name"),
    ('df.selectExpr("CAST(x AS DATE) as d")', True, "SQL cast via selectExpr"),
    ('spark.sql("SELECT CAST(a AS INT) FROM t")', True, "SQL cast via spark.sql"),
    ('StructType([StructField("Name", StringType(), True)])', True, "explicit schema"),
    ('df.withColumn("d", col("d").cast("date"))', True, "an explicit cast"),
])
def test_type_cast_detector(source: str, expected: bool, why: str):
    assert bool(_TYPE_CAST.search(source)) is expected, why


@pytest.mark.parametrize("source,expected,why", [
    ('posts.filter(posts["OwnerUserId"].isNotNull())', True, "isNotNull on a CamelCase Id"),
    ('df.filter(col("CustomerID").isNotNull())', True, "isNotNull on a CamelCase ID"),
    ('df.dropDuplicates(["Id"])', True, "dedup keyed on a column list"),
    ('df.dropna(subset=["user_id"])', True, "dropna on a key subset"),
    ('df.filter(col("valid").isNull())', False, "'valid' ends in 'id' but is not a key"),
    ('df.filter(col("monkey").isNull())', False, "'monkey' ends in 'key' but is not a key"),
    ('assert df.filter(col("CustomerKey").isNull()).count() == 0', True, "key null assertion"),
])
def test_key_quality_detector(source: str, expected: bool, why: str):
    assert bool(_KEY_QUALITY.search(source)) is expected, why


# --- the N/A-not-FAIL gate --------------------------------------------------

@pytest.mark.parametrize("evaluator", [nb_dedup, nb_type_cast, nb_key_quality])
def test_notebook_checks_are_na_when_the_notebook_writes_nothing(evaluator):
    assert evaluator(_nb_ctx("print(1)\n")).status is Status.NA


@pytest.mark.parametrize("evaluator", [nb_dedup, nb_type_cast, nb_key_quality])
def test_notebook_checks_ignore_commented_out_code(evaluator):
    """A commented-out control must not satisfy the check.

    ``binary`` leaves ``status`` for the engine to derive, so the score is what
    carries the verdict here.
    """
    commented = _WRITES + "# df.dropDuplicates()\n# df.cast('date')\n# col('user_id').isNull()\n"
    assert evaluator(_nb_ctx(commented)).score == 0


# --- semantic-model checks --------------------------------------------------

def _scored(outcome) -> object:
    """The scored aggregate verdict a multi-verdict check returns first."""
    return outcome[0] if isinstance(outcome, list) else outcome


def _details(outcome) -> list:
    """The unscored per-object detail rows that follow the aggregate."""
    return outcome[1:] if isinstance(outcome, list) else []


def test_models_without_relationships_are_excluded_from_the_denominator():
    models = {
        "empty": {"relationships": []},
        "single": {"relationships": [{"cross_filter": "oneDirection"}]},
        "bidi": {"relationships": [{"cross_filter": "bothDirections"}]},
    }
    verdict = _scored(single_direction_relationships(_model_ctx(models)))
    assert "1 of 2" in verdict.evidence


def test_relationship_check_is_na_when_no_model_declares_one():
    verdict = _scored(single_direction_relationships(_model_ctx({"m": {"relationships": []}})))
    assert verdict.status is Status.NA


def test_trivial_expressions_are_not_counted_as_duplicated_logic():
    """The constant 0 repeats across models by convention, not by copy-paste."""
    models = {f"m{i}": {"measures": [{"expression": "0"}]} for i in range(5)}
    assert _scored(measures_not_duplicated(_model_ctx(models))).status is Status.NA


def test_pretty_printing_does_not_make_a_measure_look_complex():
    """Length is judged on collapsed whitespace, so indentation cannot inflate it."""
    padded = "CALCULATE(\n" + "    \n" * 200 + "    SUM(t[x])\n)"
    assert len(padded) > 400
    assert len(_normalised(padded)) < 400
    models = {"m": {"measures": [{"expression": padded}]}}
    assert _scored(complex_measures_use_variables(_model_ctx(models))).status is Status.NA


def test_a_column_named_var_is_not_a_variable_declaration():
    """A loose 'VAR' substring matches a column called 'Var Amount'."""
    long_no_var = "CALCULATE(SUM(t[Var Amount]), " + "FILTER(t, t[a] = 1), " * 25 + "ALL(t))"
    assert len(_normalised(long_no_var)) > 400
    models = {"m": {"measures": [{"expression": long_no_var}]}}
    assert _scored(complex_measures_use_variables(_model_ctx(models))).score == 0


# --- per-object detail rows -------------------------------------------------

def test_the_failing_model_is_named_in_a_detail_row():
    """"Which model fails" must be reportable, not just "how many"."""
    models = {
        "GoodModel": {"relationships": [{"cross_filter": "oneDirection"}]},
        "BadModel": {"relationships": [
            {"cross_filter": "bothDirections", "from_table": "Bridge", "to_table": "Dim"},
        ]},
    }
    details = _details(single_direction_relationships(_model_ctx(models)))
    assert [d.obj for d in details] == ["BadModel"]
    assert "Bridge <-> Dim" in details[0].evidence


def test_detail_rows_are_unscored_so_the_score_is_unchanged():
    """They inform; only the aggregate row scores, and only it reaches the risk register."""
    models = {
        "A": {"relationships": [{"cross_filter": "bothDirections"}]},
        "B": {"relationships": [{"cross_filter": "bothDirections"}]},
    }
    outcome = single_direction_relationships(_model_ctx(models))
    assert _scored(outcome).scored is True
    assert all(d.scored is False and d.status is Status.INFO for d in _details(outcome))


def test_a_passing_model_gets_no_detail_row():
    models = {"AllGood": {"relationships": [{"cross_filter": "oneDirection"}]}}
    assert _details(single_direction_relationships(_model_ctx(models))) == []


def test_offending_measures_are_named_against_their_model():
    long_no_var = "CALCULATE(SUM(t[Var Amount]), " + "FILTER(t, t[a] = 1), " * 25 + "ALL(t))"
    models = {"M": {"measures": [{"name": "Bad Measure", "expression": long_no_var}]}}
    details = _details(complex_measures_use_variables(_model_ctx(models)))
    assert [d.obj for d in details] == ["M"]
    assert "Bad Measure" in details[0].evidence


def test_named_measures_are_capped_so_one_model_cannot_fill_the_report():
    long_no_var = "CALCULATE(SUM(t[Var Amount]), " + "FILTER(t, t[a] = 1), " * 25 + "ALL(t))"
    models = {"M": {"measures": [
        {"name": f"M{i}", "expression": long_no_var} for i in range(40)
    ]}}
    details = _details(complex_measures_use_variables(_model_ctx(models)))
    assert len(details) == 1                      # one row per model, not per measure
    assert "40 measure(s)" in details[0].evidence
    assert "(+15 more)" in details[0].evidence


# --- 14.1.4: the two DAX practices beyond VAR -------------------------------

def test_a_repeated_substantial_subexpression_is_detected():
    """The duplication a VAR exists to remove."""
    expr = ("DIVIDE(CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024), "
            "CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024))")
    assert repeated_subexpressions(expr)


def test_assigning_a_subexpression_to_a_var_removes_the_duplication():
    """Written once and reused by name, so nothing repeats."""
    expr = ("VAR Amt = CALCULATE(SUM(Sales[Amount]), Sales[Year] = 2024) "
            "RETURN DIVIDE(Amt, Amt)")
    assert not repeated_subexpressions(expr)


def test_a_short_repeated_call_is_not_flagged():
    """``SUM(t[x])`` twice is ordinary DAX, not copy-paste worth reporting."""
    assert not repeated_subexpressions("DIVIDE(SUM(t[x]), SUM(t[x]))")


@pytest.mark.parametrize("expr,expected,why", [
    ("CALCULATE(SUM(t[x]), FILTER(t, t[Year] = 2024))", True,
     "a single column predicate over a bare table is a boolean argument"),
    ("CALCULATE(SUM(t[x]), FILTER(ALL(t), t[Year] = 2024))", False,
     "FILTER(ALL(...)) replaces filter context - no boolean equivalent"),
    ("CALCULATE(SUM(t[x]), FILTER(VALUES(t[Year]), t[Year] > 2020))", False,
     "FILTER(VALUES(...)) is not a bare table scan"),
    ("CALCULATE(SUM(t[x]), t[Year] = 2024)", False,
     "already written as a boolean argument"),
    ("SUMX(Sales, SUMX(Lines, Lines[Qty]))", True,
     "an iterator nested inside another multiplies row context"),
    ("SUMX(Sales, Sales[Qty] * Sales[Price])", False,
     "a single iterator is the normal way to write this"),
])
def test_expensive_iterator_detector(expr: str, expected: bool, why: str):
    assert expensive_iterator(expr) is expected, why


def test_a_closing_paren_inside_a_string_does_not_unbalance_the_scan():
    """A format string may contain ')' - paren matching must ignore string literals."""
    expr = 'FORMAT(SUM(t[x]), "#,##0);(#,##0)")'
    assert call_spans(expr)[0] == expr


# --- 4.1.2: shadow storage --------------------------------------------------

def _conn(**kwargs) -> dict:
    base = {"connectivity_type": "ShareableCloud", "connection_type": "",
            "endpoint": ""}
    base.update(kwargs)
    return base


@pytest.mark.parametrize("conn,expected,why", [
    (_conn(connectivity_type="OnPremisesGatewayPersonal", connection_type="File",
           endpoint=r"C:\Users\someone\Downloads\Sales.xlsx"), True,
     "a spreadsheet on a laptop behind a personal gateway"),
    (_conn(connection_type="Web",
           endpoint="https://contoso-my.sharepoint.com/personal/someone/"), True,
     "a personal OneDrive is not a governed store"),
    (_conn(connection_type="HttpServer",
           endpoint="https://raw.githubusercontent.com/x/y/main/sales.csv"), True,
     "an ad-hoc file pulled over HTTP"),
    (_conn(connection_type="AzureDataLakeStorage",
           endpoint="https://acct.dfs.core.windows.net/"), False,
     "a governed enterprise object store is allowed"),
    (_conn(connection_type="GoogleCloudStorage", endpoint="storage.googleapis.com"), False,
     "external but governed through a shareable cloud connection"),
    (_conn(connection_type="Lakehouse", endpoint="Lakehouse"), False,
     "OneLake-native"),
    (_conn(connection_type="Web", endpoint="https://contoso.sharepoint.com/sites/finance"), False,
     "a governed team site is not a personal drive"),
])
def test_shadow_storage_classifier(conn: dict, expected: bool, why: str):
    assert (_shadow_reason(conn) is not None) is expected, why


def _storage_ctx(shortcuts: dict, connections: list, unavailable=frozenset()) -> CheckContext:
    workspace = WorkspaceContext(id="w", shortcuts=shortcuts, connections=connections,
                                 unavailable=set(unavailable))
    return CheckContext(workspace=workspace, settings={}, obj_name="w", obj=workspace)


def test_shadow_storage_is_scored_not_just_reported():
    """4.1.2 must move the score - a note() would leave the point ungraded."""
    connections = [
        _conn(connection_type="Lakehouse"),
        _conn(connectivity_type="OnPremisesGatewayPersonal", connection_type="File",
              endpoint=r"C:\Users\a\Downloads\x.xlsx"),
    ]
    verdict = shortcut_scope(_storage_ctx({"lh": [{"target_type": "OneLake"}]}, connections))
    assert verdict.scored is True
    assert verdict.score == 1          # 1 of 2 governed -> 50% band
    assert "1 are ungoverned shadow storage" in verdict.evidence


def test_shortcut_scope_is_na_when_connections_could_not_be_read():
    """Unreadable data is N/A, never a failure."""
    ctx = _storage_ctx({"lh": [{"target_type": "OneLake"}]}, [],
                       unavailable={Resource.CONNECTIONS})
    assert shortcut_scope(ctx).status is Status.NA


# --- 5.2.5: a count assertion must reconcile, not probe ----------------------

@pytest.mark.parametrize("source,expected,why", [
    ('assert df.filter(col("CustomerKey").isNull()).count() == 0', False,
     "a key null probe counts rows but reconciles nothing (this is 5.5.6)"),
    ("assert df.count() > 0", False, "an emptiness guard is not a reconciliation"),
    ("assert df.count() == expected_rows", True, "compared against an expectation"),
    ("assert source_df.count() == target_df.count()", True, "source vs target counts"),
    ("assert abs(src.count() - tgt.count()) < tolerance", True,
     "a tolerance comparison still reconciles"),
    ("row_count = df.count()", False, "assigned but never compared"),
    ('df.groupBy("k").agg(count("*").alias("row_count"))', False,
     "a column alias is not a comparison"),
])
def test_count_reconcile_detector(source: str, expected: bool, why: str):
    assert bool(_COUNT_RECONCILE.search(source)) is expected, why


# --- 5.3.2: an integrity check must be performed, not mentioned --------------

@pytest.mark.parametrize("source,expected,why", [
    ('df.join(dim, "k", "left_anti")', True, "an anti-join is the standard RI probe"),
    ("SELECT * FROM f LEFT ANTI JOIN d ON f.k = d.k", True, "the SQL spelling"),
    ('f.join(d, "k", "left").filter(col("d.k").isNull())', True,
     "left join then null test isolates unmatched rows"),
    ("referential_integrity(df, dim)", True, "a named check that is actually called"),
    ("fk_check = validate(df)", True, "a named check that is assigned"),
    ("referential integrity is handled upstream by the source system", False,
     "prose mentioning the idea is not a check"),
    ('df = df.withColumn("referential_note", lit("see doc"))', False,
     "a column name is not a check"),
])
def test_fk_integrity_detector(source: str, expected: bool, why: str):
    assert bool(_FK_INTEGRITY.search(source)) is expected, why


# --- 5.3.7: identified is not the same as handled ----------------------------

_JOINS = 'src.join(dim, "k", "left_anti")\n'


def test_orphans_detected_but_dropped_score_in_the_middle():
    """Computing the unmatched set and never using it satisfies half the point."""
    verdict = nb_orphan_detect(_nb_ctx('orphans = src.join(dim, "k", "left_anti")\n'))
    assert verdict.score == 1
    assert "not handled" in verdict.evidence


@pytest.mark.parametrize("tail,why", [
    ('orphans.write.saveAsTable("orphan_log")\n', "persisted"),
    ("assert orphans.count() == 0\n", "raised on"),
    ('logger.warning("orphans: %s", orphans.count())\n', "recorded"),
])
def test_orphans_detected_and_handled_pass(tail: str, why: str):
    code = 'orphans = src.join(dim, "k", "left_anti")\n' + tail
    assert nb_orphan_detect(_nb_ctx(code)).score == 3, why


def test_a_quarantine_target_counts_as_handling():
    code = 'bad = src.join(dim, "k", "left_anti")\nbad.write.saveAsTable("reject_records")\n'
    assert nb_orphan_detect(_nb_ctx(code)).score == 3


def test_an_unrelated_reject_column_is_not_orphan_handling():
    """A business column named ``rejected_flag`` must not read as quarantining."""
    code = ('orphans = src.join(dim, "k", "left_anti")\n'
            'summary = src.withColumn("rejected_flag", lit(False))\n')
    assert nb_orphan_detect(_nb_ctx(code)).score == 1


def test_orphan_check_is_na_without_a_join():
    assert nb_orphan_detect(_nb_ctx("print(1)\n")).status is Status.NA


def test_a_multiline_sql_anti_join_still_binds_to_its_variable():
    """A ``spark.sql(\"\"\"...\"\"\")` block puts the anti-join on a later line."""
    code = ('orphans = spark.sql("""\n'
            "    SELECT f.* FROM fact f\n"
            "    LEFT ANTI JOIN dim d ON f.k = d.k\n"
            '""")\n'
            'orphans.write.saveAsTable("orphan_log")\n')
    assert nb_orphan_detect(_nb_ctx(code)).score == 3


# --- 5.3.9: the validated table must be the merged table ---------------------

def test_merge_validated_on_a_different_table_fails():
    """DESCRIBE HISTORY on another table proves nothing about the merge."""
    code = ('spark.sql("MERGE INTO silver_watermark t USING s ON t.id = s.id")\n'
            'spark.sql("DESCRIBE HISTORY gold_sales")\n')
    verdict = nb_merge_valid(_nb_ctx(code))
    assert verdict.score == 0
    assert "different table is validated" in verdict.evidence


def test_merge_validated_on_the_merged_table_passes():
    code = ('spark.sql("MERGE INTO gold_sales t USING s ON t.id = s.id")\n'
            'spark.sql("DESCRIBE HISTORY gold_sales")\n')
    assert nb_merge_valid(_nb_ctx(code)).score == 3


def test_a_schema_qualified_target_matches_its_bare_name():
    code = ('spark.sql("MERGE INTO gold.sales t USING s ON t.id = s.id")\n'
            'spark.sql("DESCRIBE HISTORY sales")\n')
    assert nb_merge_valid(_nb_ctx(code)).score == 3


def test_merge_metrics_need_no_table_cross_check():
    """operationMetrics describes the write that just happened."""
    code = ('DeltaTable.forName(spark, "gold_sales").merge(src, "t.id = s.id").whenMatchedUpdateAll().execute()\n'
            'm = spark.sql("DESCRIBE HISTORY gold_sales").select("operationMetrics")\n')
    assert nb_merge_valid(_nb_ctx(code)).score == 3


def test_an_unresolvable_merge_target_is_na_not_a_guess():
    code = ("tgt = DeltaTable.forName(spark, TABLE_NAME)\n"
            "tgt.merge(src, cond).whenMatchedUpdateAll().execute()\n"
            'spark.sql("DESCRIBE HISTORY " + AUDIT_TABLE)\n')
    assert nb_merge_valid(_nb_ctx(code)).status is Status.NA


def test_merge_without_any_validation_fails():
    code = 'spark.sql("MERGE INTO gold_sales t USING s ON t.id = s.id")\n'
    assert nb_merge_valid(_nb_ctx(code)).score == 0


def test_merge_check_is_na_without_a_merge():
    assert nb_merge_valid(_nb_ctx("print(1)\n")).status is Status.NA
