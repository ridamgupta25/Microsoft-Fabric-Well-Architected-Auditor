"""Regression tests for the checks added for refs 4.1.2, 5.2.6, 5.3.1, 5.5.6, 14.1.x,
and for the Operations · Data Operations checks (1.1.3, 1.1.8, 10.5.1, 11.1.4,
11.4.2, 11.4.5, 11.5.1).

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
    nb_grain_unique,
    nb_key_quality,
    nb_layer_recon,
    nb_merge_valid,
    nb_orphan_detect,
    nb_type_cast,
    nb_unknown_monitored,
)
from auditfast.core.check.data_management_quality.data_storage.automated import (
    _shadow_reason,
    shortcut_scope,
    table_partition_strategy,
    table_relationships_declared,
    table_surrogate_generated,
    table_type_sizing,
)
from auditfast.core.check.data_management_quality.reporting_semantic.automated import (
    _normalised,
    complex_measures_use_variables,
    measures_not_duplicated,
    single_direction_relationships,
)
from auditfast.core.check.operations_reliability.data_logs.automated import (
    eventhouse_for_telemetry,
    kql_queries_version_controlled,
)
from auditfast.core.check.operations_reliability.data_operations.automated import (
    _TEST_NAME_RE,
    activator_configured,
    branching_strategy,
    environment_isolation,
    git_covers_every_artifact,
    semantic_model_deployment,
    single_source_of_truth,
    unit_tests_exist,
    warehouse_deployment_automated,
)
from auditfast.core.check.operations_reliability.data_prep.automated import (
    notebook_transaction_boundary,
)
from auditfast.core.check.operations_reliability.reporting_semantic.automated import (
    bi_content_source_controlled_and_promoted,
)
from auditfast.core.check.performance_capacity.data_prep.automated import (
    sql_ingestion_tuned,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext

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


def test_scored_dax_verdict_names_the_models_and_points_to_the_detail_rows():
    """The aggregate names every model it checked and how many need attention.

    It deliberately does NOT repeat the failing measures: doing so produced a
    single cell holding hundreds of names with each measure's model buried in a
    semicolon-separated run, so a reviewer could not tell which measure belonged
    to which model. Each model has its own row carrying its own measures.
    """
    no_var_only = " + ".join(f"SUM(t[a{i}])" for i in range(40))  # >400 chars, no VAR
    passing = "DIVIDE(SUM(Sales[Amount]), SUM(Sales[Quantity]), 0) + AVERAGE(Sales[Discount])"
    assert len(_normalised(no_var_only)) > 400
    models = {"SalesModel": {"measures": [
        {"name": "Bad Measure", "expression": no_var_only},
        {"name": "Good Measure", "expression": passing},
    ]}}
    verdicts = complex_measures_use_variables(_model_ctx(models))
    scored = _scored(verdicts)
    assert scored.status is None                       # the scored aggregate, not a note
    assert "1 of 2" in scored.evidence
    assert "SalesModel" in scored.evidence             # every model it checked is named
    assert "1 model(s) carry at least one measure needing attention" in scored.evidence

    # ...and the failing measure is on its own row, attributed to its model.
    rows = {v.obj: v.evidence for v in verdicts if v.obj}
    assert "SalesModel" in rows
    assert "Bad Measure (no VAR)" in rows["SalesModel"]
    assert "Good Measure" not in scored.evidence       # a compliant measure is not flagged


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
    assert "Notebook 'nb'" in verdict.evidence
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


@pytest.mark.parametrize("source", [
    'columns = ", ".join(df.columns)',
    'message = "\\n".join(lines)',
    'path = os.path.join(root, "errors.json")',
    'instructions = " ".join(values)',
])
def test_string_and_path_joins_are_not_data_joins(source: str):
    assert nb_orphan_detect(_nb_ctx(source)).status is Status.NA


@pytest.mark.parametrize("source", [
    'result = facts.join(dimensions, "customer_id", "inner")',
    'SELECT * FROM facts f LEFT JOIN dimensions d ON f.customer_id = d.customer_id',
])
def test_dataframe_and_sql_joins_remain_in_scope(source: str):
    assert nb_orphan_detect(_nb_ctx(source)).score == 0


def test_a_multiline_sql_anti_join_still_binds_to_its_variable():
    """A ``spark.sql(\"\"\"...\"\"\")` block puts the anti-join on a later line."""
    code = ('orphans = spark.sql("""\n'
            "    SELECT f.* FROM fact f\n"
            "    LEFT ANTI JOIN dim d ON f.k = d.k\n"
            '""")\n'
            'orphans.write.saveAsTable("orphan_log")\n')
    assert nb_orphan_detect(_nb_ctx(code)).score == 3


def test_comma_style_sql_join_is_detected_as_a_join():
    """``FROM a t, b i, c g WHERE ...`` is an implicit inner join that silently
    drops unmatched rows - exactly the orphan risk - so it must not read as
    'does not perform joins'."""
    code = ('df = spark.sql("""\n'
            "    SELECT t.amt, i.name, g.acct\n"
            "    FROM in_tran_tmlc t, in_item_tbl i, gl_interface_tmlc g\n"
            "    WHERE t.item_id = i.item_id AND t.gl_id = g.gl_id\n"
            '""")\n')
    verdict = nb_orphan_detect(_nb_ctx(code))
    assert verdict.score == 0
    assert "without orphan record detection" in verdict.evidence


def test_a_select_column_list_is_not_a_comma_join():
    """A comma in the SELECT list with a single-table FROM is not an implicit join."""
    code = 'df = spark.sql("SELECT a, b, c FROM one_table WHERE a > 0")\n'
    assert nb_orphan_detect(_nb_ctx(code)).status is Status.NA


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


# =============================================================================
# Operations & Reliability · Data Operations — refs 1.1.3, 1.1.8, 10.5.1,
# 11.1.4, 11.4.2, 11.4.5, 11.5.1
#
# Every check below is workspace-scoped, so each case builds the workspace the
# rule is about. The N/A cases are the important ones: "we could not read it"
# and "this workspace has none of the thing" must never score as a failure.
# =============================================================================

def _ws_ctx(**kwargs) -> CheckContext:
    """A workspace-scoped context — ``ctx.obj`` is the workspace itself."""
    workspace = WorkspaceContext(**kwargs)
    return CheckContext(workspace=workspace, settings={},
                        obj_name=workspace.name, obj=workspace)


def _items(*pairs: tuple[str, str]) -> list[Item]:
    return [Item(id=f"id-{n}", type=t, display_name=n) for t, n in pairs]


def _script_pipeline(sql: str, *, parameters: dict | None = None) -> dict:
    activity = {"name": "Deploy", "type": "Script",
                "typeProperties": {"scripts": [{"text": sql}]}}
    properties: dict = {"activities": [activity]}
    if parameters:
        properties["parameters"] = parameters
    return {"properties": properties}


# --- 1.1.8 single source of truth -------------------------------------------

def test_a_lakehouse_and_warehouse_for_one_purpose_is_a_duplicate_store():
    verdict = single_source_of_truth(
        _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Sales"), ("Warehouse", "WH_SALES")))
    )
    assert verdict.score == 0
    assert "share a purpose" in verdict.evidence


def test_distinct_purposes_are_not_duplicates():
    verdict = single_source_of_truth(
        _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Sales"),
                                     ("Lakehouse", "LH_Finance"),
                                     ("Warehouse", "WH_Sales_Curated")))
    )
    assert verdict.score == 3


def test_a_lakehouses_auto_created_endpoint_is_not_a_duplicate():
    """Fabric creates a SQLEndpoint and SemanticModel beside every Lakehouse."""
    verdict = single_source_of_truth(
        _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Sales"),
                                     ("SQLEndpoint", "LH_Sales"),
                                     ("SemanticModel", "LH_Sales")))
    )
    assert verdict.score == 3


def test_a_version_suffix_does_not_hide_a_duplicate_store():
    verdict = single_source_of_truth(
        _ws_ctx(id="w", items=_items(("Lakehouse", "Sales"), ("Lakehouse", "Sales_v2")))
    )
    assert verdict.score == 0


def test_single_source_is_na_without_items():
    ctx = _ws_ctx(id="w", unavailable={Resource.ITEMS})
    assert single_source_of_truth(ctx).status is Status.NA


def test_single_source_is_na_when_the_workspace_stores_nothing():
    ctx = _ws_ctx(id="w", items=_items(("DataPipeline", "PL_Load")))
    assert single_source_of_truth(ctx).status is Status.NA


# --- 1.1.3 environment isolation --------------------------------------------

def test_a_prod_pipeline_reaching_into_dev_is_a_cross_environment_dependency():
    pipeline = {"properties": {"activities": [
        {"name": "Copy from DEV", "type": "Copy",
         "typeProperties": {"source": {"path": "/lake/MLC_DEV/raw"}}}]}}
    verdict = environment_isolation(
        _ws_ctx(id="w", display_name="MLC-Prod-Ops", pipelines={"PL_Load": pipeline})
    )
    assert verdict.score == 0
    assert "another environment" in verdict.evidence


def test_a_prod_pipeline_naming_only_prod_is_isolated():
    pipeline = {"properties": {"activities": [
        {"name": "Copy PROD", "type": "Copy",
         "typeProperties": {"source": {"path": "/lake/MLC_PROD/raw"}}}]}}
    verdict = environment_isolation(
        _ws_ctx(id="w", display_name="MLC-Prod-Ops", pipelines={"PL_Load": pipeline})
    )
    assert verdict.score == 3


def test_a_test_activity_is_not_a_cross_environment_reference():
    """"Run Unit Tests" must not read as the Test environment — WS-UNIT-TESTS asks for it."""
    pipeline = {"properties": {"activities": [{"name": "Run Unit Tests", "type": "TridentNotebook"}]}}
    verdict = environment_isolation(
        _ws_ctx(id="w", display_name="MLC-Prod-Ops", pipelines={"PL_Load": pipeline})
    )
    assert verdict.score == 3


def test_dev_inside_a_longer_word_is_not_an_environment_reference():
    pipeline = {"properties": {"activities": [{"name": "Load device telemetry", "type": "Copy"}]}}
    verdict = environment_isolation(
        _ws_ctx(id="w", display_name="MLC-Prod-Ops", pipelines={"PL_Load": pipeline})
    )
    assert verdict.score == 3


def test_environment_isolation_is_na_without_pipeline_definitions():
    ctx = _ws_ctx(id="w", display_name="MLC-Prod-Ops",
                  unavailable={Resource.PIPELINE_DEFINITIONS})
    assert environment_isolation(ctx).status is Status.NA


def test_environment_isolation_is_na_when_the_name_declares_no_tier():
    pipeline = {"properties": {"activities": [{"name": "Copy from DEV", "type": "Copy"}]}}
    ctx = _ws_ctx(id="w", display_name="Analytics Workspace", pipelines={"PL": pipeline})
    assert environment_isolation(ctx).status is Status.NA


# --- 10.5.1 Data Activator ---------------------------------------------------

def test_an_activator_with_a_live_rule_satisfies_the_check():
    ctx = _ws_ctx(
        id="w",
        items=_items(("DataPipeline", "PL_Load"), ("Reflex", "RX_Alerts")),
        activators={"RX_Alerts": {"rules": 1, "active_rules": 1, "sources": 1, "actions": 1}},
    )
    verdict = activator_configured(ctx)
    assert verdict.score == 3
    assert "RX_Alerts" in verdict.evidence


def test_an_empty_activator_with_no_rules_does_not_pass():
    """A Reflex item created but carrying no rule triggers nothing — the reviewer's gap."""
    ctx = _ws_ctx(
        id="w",
        items=_items(("DataPipeline", "PL_Load"), ("Reflex", "RX_Empty")),
        activators={"RX_Empty": {"rules": 0, "active_rules": 0, "sources": 0, "actions": 0}},
    )
    assert activator_configured(ctx).score == 0


def test_an_activator_with_only_paused_rules_does_not_pass():
    ctx = _ws_ctx(
        id="w",
        items=_items(("DataPipeline", "PL_Load"), ("Reflex", "RX_Paused")),
        activators={"RX_Paused": {"rules": 2, "active_rules": 0, "sources": 1, "actions": 1}},
    )
    assert activator_configured(ctx).score == 0


def test_activator_is_na_when_its_definition_could_not_be_read():
    """A present Activator whose rules are unreadable is unverified, never a FAIL."""
    ctx = _ws_ctx(
        id="w",
        items=_items(("DataPipeline", "PL_Load"), ("Reflex", "RX_Alerts")),
        unavailable={Resource.ACTIVATOR_DEFINITIONS},
    )
    assert activator_configured(ctx).status is Status.NA


def test_operational_items_without_an_activator_fail():
    ctx = _ws_ctx(id="w", items=_items(("DataPipeline", "PL_Load"), ("Notebook", "NB_Build")))
    assert activator_configured(ctx).score == 0


def test_activator_is_na_without_items():
    assert activator_configured(_ws_ctx(id="w", unavailable={Resource.ITEMS})).status is Status.NA


def test_activator_is_na_when_nothing_can_raise_an_event():
    ctx = _ws_ctx(id="w", items=_items(("Report", "RPT_Sales")))
    assert activator_configured(ctx).status is Status.NA


# --- 11.1.4 branching strategy ----------------------------------------------

def test_a_prod_workspace_on_main_follows_the_strategy():
    ctx = _ws_ctx(id="w", display_name="MLC-Prod-Ops", git_connected=True,
                  git_details={"connected": True, "branch": "main"})
    assert branching_strategy(ctx).score == 3


def test_a_prod_workspace_on_a_feature_branch_has_no_promotion_gate():
    ctx = _ws_ctx(id="w", display_name="MLC-Prod-Ops", git_connected=True,
                  git_details={"connected": True, "branch": "feature/wip-rewrite"})
    verdict = branching_strategy(ctx)
    assert verdict.score == 0
    assert "feature branch" in verdict.evidence


def test_an_ad_hoc_branch_name_matches_no_strategy():
    ctx = _ws_ctx(id="w", display_name="MLC-Dev-Ops", git_connected=True,
                  git_details={"connected": True, "branch": "anmol-scratch"})
    assert branching_strategy(ctx).score == 0


def test_a_dev_prefixed_branch_follows_the_development_strategy():
    ctx = _ws_ctx(id="w", display_name="Explore Fabric - NOIDA", git_connected=True,
                  git_details={"connected": True, "branch": "DEV_FABRIC"})
    verdict = branching_strategy(ctx)
    assert verdict.score == 3
    assert "DEV_FABRIC" in verdict.evidence
    assert "develop branch" in verdict.evidence


def test_a_dev_workspace_on_a_feature_branch_is_isolated():
    ctx = _ws_ctx(id="w", display_name="MLC-Dev-Ops", git_connected=True,
                  git_details={"connected": True, "branch": "feature/new-load"})
    assert branching_strategy(ctx).score == 3


def test_a_dev_workspace_on_trunk_is_partial_not_a_failure():
    ctx = _ws_ctx(id="w", display_name="MLC-Dev-Ops", git_connected=True,
                  git_details={"connected": True, "branch": "main"})
    assert branching_strategy(ctx).score == 2


def test_branching_is_na_when_git_is_unreadable():
    ctx = _ws_ctx(id="w", unavailable={Resource.GIT})
    assert branching_strategy(ctx).status is Status.NA


def test_branching_is_na_when_the_workspace_is_not_git_connected():
    """Not connected is WS-GIT's finding, not a second failure here."""
    ctx = _ws_ctx(id="w", display_name="MLC-Prod-Ops", git_connected=False)
    assert branching_strategy(ctx).status is Status.NA


# --- 11.4.2 warehouse deployment --------------------------------------------

_PARAM_DDL = "CREATE TABLE @{pipeline().parameters.schema}.dim_date (d DATE)"
_LITERAL_DDL = "CREATE TABLE prod_dbo.dim_date (d DATE)"


def test_parameterized_warehouse_ddl_under_git_passes():
    ctx = _ws_ctx(id="w", items=_items(("Warehouse", "WH_Gold")), git_connected=True,
                  pipelines={"PL_Deploy": _script_pipeline(_PARAM_DDL)})
    assert warehouse_deployment_automated(ctx).score == 3


def test_literal_ddl_with_no_automation_is_manual_tsql():
    ctx = _ws_ctx(id="w", items=_items(("Warehouse", "WH_Gold")),
                  pipelines={"PL_Deploy": _script_pipeline(_LITERAL_DDL)})
    verdict = warehouse_deployment_automated(ctx)
    assert verdict.score == 0
    assert "manual T-SQL" in verdict.evidence


def test_declared_pipeline_parameters_count_as_parameterization():
    ctx = _ws_ctx(id="w", items=_items(("Warehouse", "WH_Gold")), deployment_pipeline=True,
                  pipelines={"PL_Deploy": _script_pipeline(
                      _LITERAL_DDL, parameters={"schema": {"type": "string"}})})
    assert warehouse_deployment_automated(ctx).score == 3


def test_a_load_without_ddl_is_judged_on_automation_alone():
    ctx = _ws_ctx(id="w", items=_items(("Warehouse", "WH_Gold")), git_connected=True,
                  pipelines={"PL_Load": _script_pipeline("MERGE INTO dim_date t USING s ON 1=1")})
    verdict = warehouse_deployment_automated(ctx)
    assert verdict.score == 3
    assert "No schema T-SQL" in verdict.evidence


def test_warehouse_deployment_is_na_without_a_warehouse():
    ctx = _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Bronze")))
    assert warehouse_deployment_automated(ctx).status is Status.NA


def test_warehouse_deployment_is_na_when_pipeline_definitions_are_unreadable():
    ctx = _ws_ctx(id="w", items=_items(("Warehouse", "WH_Gold")), git_connected=True,
                  unavailable={Resource.PIPELINE_DEFINITIONS})
    assert warehouse_deployment_automated(ctx).status is Status.NA


# --- 11.4.5 semantic model deployment ---------------------------------------

_REFRESH_PIPELINE = {"properties": {"activities": [
    {"name": "Refresh model", "type": "PBISemanticModelRefresh"}]}}
_PLAIN_PIPELINE = {"properties": {"activities": [{"name": "Copy", "type": "Copy"}]}}


def test_a_versioned_and_orchestrated_semantic_model_passes():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")), git_connected=True,
                  pipelines={"PL_Consume": _REFRESH_PIPELINE})
    assert semantic_model_deployment(ctx).score == 3


def test_a_versioned_model_no_pipeline_refresh_is_partial():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")), git_connected=True,
                  pipelines={"PL_Load": _PLAIN_PIPELINE})
    assert semantic_model_deployment(ctx).score == 2


def test_an_unversioned_unorchestrated_model_fails():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")),
                  pipelines={"PL_Load": _PLAIN_PIPELINE})
    verdict = semantic_model_deployment(ctx)
    assert verdict.score == 0
    assert "neither" in verdict.evidence


def test_a_workspace_with_no_pipeline_is_judged_on_versioning_only():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")), git_connected=True)
    verdict = semantic_model_deployment(ctx)
    assert verdict.score == 3
    assert "No pipeline in this workspace" in verdict.evidence


def test_semantic_model_deployment_is_na_without_a_model():
    ctx = _ws_ctx(id="w", items=_items(("DataPipeline", "PL_Load")))
    assert semantic_model_deployment(ctx).status is Status.NA


def test_semantic_model_deployment_is_na_when_git_is_unreadable():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")),
                  unavailable={Resource.GIT})
    assert semantic_model_deployment(ctx).status is Status.NA


# --- 11.5.1 unit tests -------------------------------------------------------

@pytest.mark.parametrize("name,expected,why", [
    ("NB_Test_Sales", True, "test as its own token"),
    ("TestSalesLoad", True, "test opening a CamelCase name"),
    ("nb_unit_tests", True, "unit tests suffix"),
    ("Run Unit Tests", True, "an activity name"),
    ("NB_Latest_Load", False, "'latest' is not a test"),
    ("NB_Contest_Rules", False, "'contest' is not a test"),
    ("NB_Tested_Rows", False, "'tested' is not a test asset"),
])
def test_test_name_detector(name: str, expected: bool, why: str):
    assert bool(_TEST_NAME_RE.search(name)) is expected, why


def test_a_test_framework_notebook_satisfies_the_unit_test_check():
    ctx = _ws_ctx(id="w", notebooks={
        "NB_Build": _nb(_WRITES),
        "NB_Checks": _nb("import pytest\n\ndef test_scd2(): assert transform(1) == 2\n"),
    })
    verdict = unit_tests_exist(ctx)
    assert verdict.score == 3
    assert "NB_Checks" in verdict.evidence


def test_transformations_with_no_test_asset_fail():
    ctx = _ws_ctx(id="w", notebooks={"NB_Build": _nb(_WRITES)})
    verdict = unit_tests_exist(ctx)
    assert verdict.score == 0
    assert "no test notebook" in verdict.evidence


def test_a_commented_out_test_framework_does_not_count():
    ctx = _ws_ctx(id="w", notebooks={"NB_Build": _nb(_WRITES + "# import pytest\n")})
    assert unit_tests_exist(ctx).score == 0


def test_a_row_count_assert_is_not_a_unit_test():
    """A data-quality gate on production rows is not a test of the transform."""
    ctx = _ws_ctx(id="w", notebooks={
        "NB_Build": _nb(_WRITES + "assert df.count() > 0\n")})
    assert unit_tests_exist(ctx).score == 0


def test_a_test_activity_in_a_pipeline_counts():
    ctx = _ws_ctx(
        id="w",
        notebooks={"NB_Build": _nb(_WRITES)},
        pipelines={"PL_Load": {"properties": {"activities": [
            {"name": "Run Unit Tests", "type": "TridentNotebook"}]}}},
    )
    assert unit_tests_exist(ctx).score == 3


def test_a_few_tests_beside_many_transforms_is_partial_not_a_pass():
    """Presence is not coverage: 1 test against 8 transforms must not score 3.

    Regression for a real workspace where 9 test notebooks covering 61
    transformation notebooks (15%) reported a clean PASS.
    """
    notebooks = {f"NB_Build_{i}": _nb(_WRITES) for i in range(8)}
    notebooks["NB_Checks"] = _nb("import pytest\n\ndef test_scd2(): assert f(1) == 2\n")
    ctx = _ws_ctx(id="w", notebooks=notebooks)
    verdict = unit_tests_exist(ctx)
    assert verdict.score < 3, "a 1-in-8 coverage ratio is not a passing test posture"
    assert verdict.coverage == pytest.approx(1 / 8)
    assert "NB_Checks" in verdict.evidence


def test_more_tests_than_transforms_is_fully_covered():
    """The ratio is clamped, so a test-heavy workspace still passes cleanly."""
    ctx = _ws_ctx(id="w", notebooks={
        "NB_Build": _nb(_WRITES),
        "NB_Checks": _nb("import pytest\n\ndef test_a(): assert 1\n"),
        "NB_More_Checks": _nb("import pytest\n\ndef test_b(): assert 1\n"),
    })
    assert unit_tests_exist(ctx).score == 3


def test_unit_tests_is_na_without_notebook_definitions():
    ctx = _ws_ctx(id="w", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    assert unit_tests_exist(ctx).status is Status.NA


def test_unit_tests_is_na_when_no_notebook_transforms_anything():
    ctx = _ws_ctx(id="w", notebooks={"NB_Explore": _nb("df = spark.table('t')\ndisplay(df)\n")})
    assert unit_tests_exist(ctx).status is Status.NA


# =============================================================================
# Operations & Reliability · Data Logs — refs 10.3.1, 10.3.2
#
# Both are judged from the item inventory (plus the Git state for 10.3.2). The
# N/A cases matter most: a workspace with nothing to store and nothing to query
# has no telemetry posture to grade, and must not be scored as if it failed.
# =============================================================================

def test_an_eventhouse_satisfies_the_telemetry_store_check():
    ctx = _ws_ctx(id="w", items=_items(("Eventhouse", "EH_Ops"),
                                       ("Eventstream", "ES_Telemetry")))
    verdict = eventhouse_for_telemetry(ctx)
    assert verdict.score == 3
    assert "EH_Ops" in verdict.evidence


def test_a_kql_database_alone_is_still_the_real_time_store():
    ctx = _ws_ctx(id="w", items=_items(("KQLDatabase", "KDB_Logs")))
    assert eventhouse_for_telemetry(ctx).score == 3


def test_a_stream_with_nowhere_real_time_to_land_fails():
    ctx = _ws_ctx(id="w", items=_items(("Eventstream", "ES_Telemetry"),
                                       ("Lakehouse", "LH_Logs")))
    verdict = eventhouse_for_telemetry(ctx)
    assert verdict.score == 0
    assert "no Eventhouse" in verdict.evidence


def test_a_batch_only_log_workspace_is_partial_not_a_failure():
    """A low-volume log store may legitimately be a Lakehouse."""
    ctx = _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Logs")))
    verdict = eventhouse_for_telemetry(ctx)
    assert verdict.score == 1
    assert "batch store" in verdict.evidence


def test_telemetry_store_is_na_without_items():
    ctx = _ws_ctx(id="w", unavailable={Resource.ITEMS})
    assert eventhouse_for_telemetry(ctx).status is Status.NA


def test_one_store_seen_under_two_item_types_is_counted_once():
    """An Eventhouse and the KQLDatabase inside it share a display name.

    Regression for evidence that read "EH_Ops, EH_Ops" and claimed two stores
    where the workspace holds one.
    """
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(
        ("Eventhouse", "EH_Ops"), ("KQLDatabase", "EH_Ops"),
        ("KQLQueryset", "QS_Failures")))
    assert eventhouse_for_telemetry(ctx).evidence.count("EH_Ops") == 1
    assert "1 store(s)" in kql_queries_version_controlled(ctx).evidence


def test_telemetry_store_is_na_when_there_is_no_telemetry_to_place():
    ctx = _ws_ctx(id="w", items=_items(("Report", "RPT_Ops")))
    assert eventhouse_for_telemetry(ctx).status is Status.NA


def test_a_versioned_queryset_satisfies_the_kql_query_check():
    ctx = _ws_ctx(id="w", git_connected=True,
                  items=_items(("Eventhouse", "EH_Ops"), ("KQLQueryset", "QS_Failures")))
    verdict = kql_queries_version_controlled(ctx)
    assert verdict.score == 3
    assert "QS_Failures" in verdict.evidence


def test_a_real_time_dashboard_counts_as_saved_kql():
    ctx = _ws_ctx(id="w", git_connected=True,
                  items=_items(("KQLDatabase", "KDB_Logs"), ("KQLDashboard", "DB_Ops")))
    assert kql_queries_version_controlled(ctx).score == 3


def test_querysets_outside_source_control_are_partial():
    ctx = _ws_ctx(id="w", items=_items(("Eventhouse", "EH_Ops"),
                                       ("KQLQueryset", "QS_Failures")))
    verdict = kql_queries_version_controlled(ctx)
    assert verdict.score == 1
    assert "no history" in verdict.evidence


def test_an_eventhouse_with_no_saved_query_fails():
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(("Eventhouse", "EH_Ops")))
    verdict = kql_queries_version_controlled(ctx)
    assert verdict.score == 0
    assert "no saved KQL queryset" in verdict.evidence


def test_kql_queries_is_na_without_a_store_to_query():
    """A queryset check has nothing to say about a workspace with no KQL data."""
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(("Lakehouse", "LH_Logs")))
    assert kql_queries_version_controlled(ctx).status is Status.NA


def test_kql_queries_is_na_when_git_is_unreadable():
    ctx = _ws_ctx(id="w", items=_items(("Eventhouse", "EH_Ops")),
                  unavailable={Resource.GIT})
    assert kql_queries_version_controlled(ctx).status is Status.NA


def test_kql_queries_is_na_without_items():
    assert kql_queries_version_controlled(
        _ws_ctx(id="w", unavailable={Resource.ITEMS})).status is Status.NA


# =============================================================================
# Data Management & Quality · Data Storage — refs 4.2.2, 4.4.3, 4.4.4, 4.4.5
# =============================================================================

def _table(columns: list[dict], *, table_type: str = "Managed", fmt: str = "Delta") -> dict:
    return {"type": table_type, "format": fmt, "columns": columns}


def _col(name: str, ctype: str, source_kind: str = "Warehouse") -> dict:
    return {"name": name, "type": ctype, "source_kind": source_kind}


def test_partition_strategy_is_na_when_only_a_column_name_hints_at_partitioning():
    """A date column is not evidence of a partitioning *strategy*.

    Fabric's table metadata carries no partition/clustering keys - verified
    against a real 1,845-table crawl, where ``partitionBy``/``partitionColumns``
    appear only inside notebook source, never on a table. So the check can see
    that a fact table *could* be partitioned by ``order_date``, but not whether
    it *is*. Scoring that guess produced a verdict on unreadable data; N/A is the
    honest answer, and the evidence names the permission that would be needed.
    """
    ctx = _ws_ctx(
        id="w",
        tables={
            "fact_sales": _table([
                _col("sales_sk", "bigint"),
                _col("order_date", "date"),
                _col("region_code", "varchar(20)"),
            ] * 12),
        },
    )
    verdict = table_partition_strategy(ctx)
    assert verdict.score is None
    assert "no partition/clustering metadata" in verdict.evidence


def test_partition_strategy_scores_only_declared_partition_metadata():
    """When a table does declare partition keys, that is readable and is scored."""
    partitioned = _table([_col("sales_sk", "bigint"), _col("order_date", "date")] * 12)
    partitioned["partitionBy"] = ["order_date"]
    plain = _table([_col("txn_sk", "bigint"), _col("amount", "decimal(18,2)")] * 12)

    ctx = _ws_ctx(id="w", tables={"fact_sales": partitioned, "fact_txn": plain})
    verdict = table_partition_strategy(ctx)
    assert verdict.score is not None, "declared metadata is readable, so it is scored"
    assert "1 of 1" in verdict.evidence, (
        "only the table whose strategy could be *inspected* belongs in the ratio; "
        "a table with no metadata is unknown, not a failure. NB: because the "
        "denominator counts only tables that declared metadata, this check can "
        "currently only ever return PASS or N/A - worth revisiting if Fabric "
        "starts exposing partition keys on the table listing"
    )


def test_partition_strategy_is_na_when_no_large_table_is_named():
    ctx = _ws_ctx(id="w", tables={"dim_customer": _table([_col("customer_sk", "bigint")])})
    assert table_partition_strategy(ctx).score is None


def test_datatype_sizing_flags_oversized_text_and_invalid_decimal_precision():
    ctx = _ws_ctx(
        id="w",
        tables={
            "fact_sales": _table([
                _col("good_text", "varchar(200)"),
                _col("bad_text", "varchar(max)"),
                _col("good_amount", "decimal(18,2)"),
                _col("bad_amount", "decimal(50,3)"),
            ]),
        },
    )
    verdict = table_type_sizing(ctx)
    assert verdict.score == 1
    assert "oversized text" in verdict.evidence


def test_surrogate_generated_requires_surrogate_plus_generation_hint():
    ctx = _ws_ctx(
        id="w",
        tables={
            "dim_customer": _table([
                _col("customer_sk", "bigint"),
                _col("customer_hash", "varchar(64)"),
                _col("customer_code", "varchar(30)"),
            ]),
            "dim_product": _table([
                _col("product_id", "bigint"),
                _col("product_name", "varchar(120)"),
            ]),
        },
    )
    verdict = table_surrogate_generated(ctx)[0]
    assert verdict.score == 1


def test_relationships_declared_fails_when_fact_has_no_modeled_relationship():
    ctx = _ws_ctx(
        id="w",
        tables={
            "fact_sales": _table([_col("sales_sk", "bigint")]),
            "dim_customer": _table([_col("customer_sk", "bigint")]),
        },
        semantic_models={
            "model": {"tables": ["fact_sales", "dim_customer"], "relationships": []}
        },
    )
    assert table_relationships_declared(ctx)[0].score == 0


def test_relationships_declared_passes_when_fact_is_linked_in_model_relationships():
    ctx = _ws_ctx(
        id="w",
        tables={
            "fact_sales": _table([_col("customer_sk", "bigint")]),
            "dim_customer": _table([_col("customer_sk", "bigint")]),
        },
        semantic_models={
            "model": {
                "tables": ["fact_sales", "dim_customer"],
                "relationships": [
                    {
                        "name": "fact_to_customer",
                        "from_table": "fact_sales",
                        "from_column": "customer_sk",
                        "to_table": "dim_customer",
                        "to_column": "customer_sk",
                    }
                ],
            }
        },
    )
    assert table_relationships_declared(ctx)[0].score == 3


# =============================================================================
# Performance & Capacity · Data Prep — ref 2.6.5
#
# Three independent tunings on every Copy that reads a SQL database. Scored as
# coverage over all three, so a half-tuned pipeline lands in the middle.
# =============================================================================

def _copy_pipeline(source: dict, sink: dict | None = None, *, nested: bool = False) -> dict:
    copy = {"name": "Copy_Source", "type": "Copy",
            "typeProperties": {"source": source, "sink": sink or {"type": "DeltaSink"}}}
    if nested:
        loop = {"name": "ForEachTable", "type": "ForEach",
                "typeProperties": {"activities": [copy]}}
        return {"properties": {"activities": [loop]}}
    return {"properties": {"activities": [copy]}}


def _pl_ctx(definition: dict, *, unavailable=frozenset()) -> CheckContext:
    workspace = WorkspaceContext(id="w", unavailable=set(unavailable))
    return CheckContext(workspace=workspace, settings={}, obj_name="PL", obj=definition)


_TUNED_SOURCE = {
    "type": "AzureSqlSource",
    "sqlReaderQuery": "SELECT id, amount FROM dbo.orders WHERE modified >= @{pipeline().parameters.wm}",
    "partitionOption": "DynamicRange",
}
_TUNED_SINK = {"type": "DeltaSink", "writeBatchSize": 100000}


def test_a_fully_tuned_sql_copy_passes():
    verdict = sql_ingestion_tuned(_pl_ctx(_copy_pipeline(_TUNED_SOURCE, _TUNED_SINK)))
    assert verdict.score == 3
    assert "1 fold" in verdict.evidence


def test_a_tuned_copy_nested_in_a_foreach_is_still_seen():
    """Metadata-driven ingestion puts the Copy inside a loop."""
    verdict = sql_ingestion_tuned(
        _pl_ctx(_copy_pipeline(_TUNED_SOURCE, _TUNED_SINK, nested=True)))
    assert verdict.score == 3


def test_an_untuned_sql_copy_fails():
    verdict = sql_ingestion_tuned(_pl_ctx(_copy_pipeline({"type": "AzureSqlSource"})))
    assert verdict.score == 0
    assert "query folding" in verdict.evidence


def test_a_select_star_with_no_predicate_folds_nothing():
    source = {"type": "SqlServerSource", "sqlReaderQuery": "SELECT * FROM dbo.orders"}
    verdict = sql_ingestion_tuned(_pl_ctx(_copy_pipeline(source)))
    assert verdict.score == 0
    assert "0 fold the source read" in verdict.evidence


def test_a_select_star_with_a_predicate_does_fold():
    source = {"type": "SqlServerSource",
              "sqlReaderQuery": "SELECT * FROM dbo.orders WHERE modified > '2024-01-01'",
              "partitionOption": "PhysicalPartitionsOfTable"}
    verdict = sql_ingestion_tuned(_pl_ctx(_copy_pipeline(source, _TUNED_SINK)))
    assert verdict.score == 3


def test_a_stored_procedure_source_always_folds():
    source = {"type": "AzureSqlSource", "sqlReaderStoredProcedureName": "usp_get_orders"}
    assert "1 fold the source read" in sql_ingestion_tuned(
        _pl_ctx(_copy_pipeline(source))).evidence


def test_partition_option_none_is_not_a_ranged_read():
    """"None" is the default — it means nothing was chosen."""
    source = {"type": "AzureSqlSource", "sqlReaderStoredProcedureName": "usp_x",
              "partitionOption": "None"}
    assert "0 set a partitionOption" in sql_ingestion_tuned(
        _pl_ctx(_copy_pipeline(source))).evidence


def test_half_tuned_ingestion_lands_in_the_middle():
    source = {"type": "AzureSqlSource", "sqlReaderStoredProcedureName": "usp_x",
              "partitionOption": "DynamicRange"}
    verdict = sql_ingestion_tuned(_pl_ctx(_copy_pipeline(source)))
    assert 0 < verdict.score < 3


def test_sql_ingestion_is_na_without_a_sql_source():
    definition = _copy_pipeline({"type": "DelimitedTextSource"})
    assert sql_ingestion_tuned(_pl_ctx(definition)).status is Status.NA


def test_sql_ingestion_is_na_when_pipeline_definitions_are_unreadable():
    ctx = _pl_ctx(_copy_pipeline(_TUNED_SOURCE, _TUNED_SINK),
                  unavailable={Resource.PIPELINE_DEFINITIONS})
    assert sql_ingestion_tuned(ctx).status is Status.NA


# =============================================================================
# Operations & Reliability · Data Prep — ref 9.3.3 (transaction boundaries)
#
# Judged on the notebook surface: a notebook with two or more writes must bound
# the sequence. One write is not a multi-step operation and is N/A.
# =============================================================================

_TWO_WRITES = ('a.write.mode("append").saveAsTable("gold.dim")\n'
               'b.write.mode("append").saveAsTable("gold.fact")\n')


def test_a_single_write_notebook_has_no_boundary_to_judge():
    ctx = _nb_ctx('a.write.mode("append").saveAsTable("gold.dim")\n')
    assert notebook_transaction_boundary(ctx).status is Status.NA


def test_an_explicit_tsql_transaction_bounds_the_sequence():
    code = ('cur.execute("BEGIN TRANSACTION")\n' + _TWO_WRITES + "conn.commit()\n")
    verdict = notebook_transaction_boundary(_nb_ctx(code))
    assert verdict.score == 3
    assert "explicit transaction" in verdict.evidence


def test_a_stray_commit_without_transaction_opener_is_not_a_boundary():
    verdict = notebook_transaction_boundary(_nb_ctx(_TWO_WRITES + "conn.commit()\n"))
    assert verdict.score == 0


def test_a_staging_swap_bounds_the_sequence():
    code = ('a.write.saveAsTable("gold.dim_stg")\n'
            'b.write.saveAsTable("gold.fact_stg")\n'
            'spark.sql("ALTER TABLE gold.dim_stg RENAME TO gold.dim")\n')
    assert notebook_transaction_boundary(_nb_ctx(code)).score == 3


def test_failure_compensation_bounds_the_sequence():
    code = ("try:\n    " + _TWO_WRITES.replace("\n", "\n    ") +
            '\nexcept Exception:\n    spark.sql("RESTORE TABLE gold.dim VERSION AS OF 3")\n')
    verdict = notebook_transaction_boundary(_nb_ctx(code))
    assert verdict.score == 3
    assert "compensation" in verdict.evidence


def test_unrelated_cleanup_after_except_is_not_failure_compensation():
    code = ("try:\n    risky_call()\nexcept Exception:\n    print('failed')\n\n" +
            _TWO_WRITES + 'spark.sql("DROP TABLE old_backup")\n')
    verdict = notebook_transaction_boundary(_nb_ctx(code))
    assert verdict.score == 0
    assert "Notebook 'nb'" in verdict.evidence


def test_individually_atomic_writes_without_a_boundary_are_partial():
    """Each table survives; the set of them does not."""
    code = ('spark.sql("MERGE INTO gold.dim t USING s ON t.k = s.k")\n'
            'spark.sql("MERGE INTO gold.fact t USING s ON t.k = s.k")\n')
    verdict = notebook_transaction_boundary(_nb_ctx(code))
    assert verdict.score == 1
    assert "unbounded" in verdict.evidence


def test_unbounded_appending_writes_fail():
    verdict = notebook_transaction_boundary(_nb_ctx(_TWO_WRITES))
    assert verdict.score == 0
    assert "half-applied" in verdict.evidence


def test_a_commented_out_rollback_is_not_a_boundary():
    code = _TWO_WRITES + "# except Exception: rollback()\n"
    assert notebook_transaction_boundary(_nb_ctx(code)).score == 0


def test_transaction_boundary_is_na_without_notebook_definitions():
    workspace = WorkspaceContext(id="w", unavailable={Resource.NOTEBOOK_DEFINITIONS})
    ctx = CheckContext(workspace=workspace, settings={}, obj_name="nb",
                       obj=_nb(_TWO_WRITES))
    assert notebook_transaction_boundary(ctx).status is Status.NA


# =============================================================================
# Data Management & Quality · Data Prep — dimensional control precision
# =============================================================================

def test_generic_unknown_value_is_not_an_unknown_dimension_member():
    code = ('df = df.fillna({"city": "Unknown"})\n'
            'df.write.mode("overwrite").saveAsTable("silver.customer")\n')
    assert nb_unknown_monitored(_nb_ctx(code)).status is Status.NA


def test_unknown_dimension_member_without_monitoring_fails_with_location():
    code = ('fact = fact.join(dim, "customer_id", "left")\n'
            'fact = fact.withColumn("customer_key", '
            'coalesce(col("customer_key"), lit(-1)))\n')
    verdict = nb_unknown_monitored(_nb_ctx(code))
    assert verdict.score == 0
    assert "Notebook 'nb'" in verdict.evidence
    assert "coalesce" in verdict.evidence


def test_unknown_dimension_member_with_monitoring_passes():
    code = ('fact = fact.join(dim, "customer_id", "left")\n'
            'fact = fact.withColumn("customer_key", '
            'coalesce(col("customer_key"), lit(-1)))\n'
            'unknown_count = fact.filter(col("customer_key") == -1).count()\n')
    verdict = nb_unknown_monitored(_nb_ctx(code))
    assert verdict.score == 3
    assert "unknown_count" in verdict.evidence


def test_layer_names_in_comments_do_not_create_reconciliation_scope():
    code = ('# Read from Silver before writing Gold\n'
            'df.write.mode("overwrite").saveAsTable("gold.fact_sales")\n')
    assert nb_layer_recon(_nb_ctx(code)).status is Status.NA


def test_executable_silver_to_gold_without_reconciliation_fails():
    code = ('df = spark.read.table("silver.fact_sales")\n'
            'df.write.mode("overwrite").saveAsTable("gold.fact_sales")\n')
    verdict = nb_layer_recon(_nb_ctx(code))
    assert verdict.score == 0
    assert "Notebook 'nb'" in verdict.evidence


def test_dimension_only_write_is_outside_fact_grain_check():
    code = ('# Tiny city dimension\n'
            'df.write.mode("overwrite").saveAsTable("gold.dim_city")\n')
    assert nb_grain_unique(_nb_ctx(code)).status is Status.NA


def test_reading_fact_but_writing_dimension_is_outside_fact_grain_check():
    code = ('source = spark.read.table("silver.fact_sales")\n'
            'dim.write.mode("overwrite").saveAsTable("gold.dim_city")\n')
    assert nb_grain_unique(_nb_ctx(code)).status is Status.NA


def test_fact_write_with_duplicate_guard_has_detailed_pass_evidence():
    code = ('fact_sales = source.dropDuplicates(["sale_id"])\n'
            'fact_sales.write.mode("overwrite").saveAsTable("gold.fact_sales")\n')
    verdict = nb_grain_unique(_nb_ctx(code))
    assert verdict.score == 3
    assert "Notebook 'nb'" in verdict.evidence
    assert "dropDuplicates" in verdict.evidence


def test_ctas_write_is_recognised_so_the_na_reason_is_accurate():
    """A CTAS load writes a table; a non-fact CTAS target is N/A for a *fact*-grain
    check, but the reason must not claim the notebook 'writes no table'."""
    code = ('spark.sql("""\n'
            "    CREATE TABLE lh.bronze.gl_detail AS\n"
            "    SELECT * FROM staging.gl\n"
            '""")\n')
    verdict = nb_grain_unique(_nb_ctx(code))
    assert verdict.status is Status.NA
    assert "writes no table" not in verdict.evidence
    assert "no provable fact write target" in verdict.evidence


def test_ctas_into_a_fact_table_without_dedup_fails():
    code = 'spark.sql("CREATE TABLE gold.fact_sales AS SELECT * FROM staging.sales")\n'
    verdict = nb_grain_unique(_nb_ctx(code))
    assert verdict.score == 0
    assert "fact_sales" in verdict.evidence


# =============================================================================
# Operations & Reliability · Reporting / Semantic — ref 14.5.4
#
# Two distinct mechanics, credited separately: a version history (Git) and a
# promotion path (deployment pipeline).
# =============================================================================

def test_git_and_a_deployment_pipeline_together_pass():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales"), ("Report", "RPT_Sales")),
                  git_connected=True, deployment_pipeline=True)
    verdict = bi_content_source_controlled_and_promoted(ctx)
    assert verdict.score == 3
    assert "1 semantic model(s) and 1 report(s)" in verdict.evidence


def test_git_without_a_promotion_path_is_partial():
    ctx = _ws_ctx(id="w", items=_items(("Report", "RPT_Sales")), git_connected=True)
    verdict = bi_content_source_controlled_and_promoted(ctx)
    assert verdict.score == 2
    assert "promotion to the next tier is manual" in verdict.evidence


def test_a_promotion_path_without_a_history_scores_lower_than_git_alone():
    ctx = _ws_ctx(id="w", items=_items(("Report", "RPT_Sales")), deployment_pipeline=True)
    verdict = bi_content_source_controlled_and_promoted(ctx)
    assert verdict.score == 1
    assert "without a version history" in verdict.evidence


def test_neither_mechanic_fails():
    ctx = _ws_ctx(id="w", items=_items(("SemanticModel", "SM_Sales")))
    verdict = bi_content_source_controlled_and_promoted(ctx)
    assert verdict.score == 0
    assert "edited in place" in verdict.evidence


def test_bi_deploy_is_na_without_reporting_content():
    ctx = _ws_ctx(id="w", items=_items(("Lakehouse", "LH_Gold")), git_connected=True)
    assert bi_content_source_controlled_and_promoted(ctx).status is Status.NA


def test_a_classic_dashboard_is_not_git_supported_so_it_is_not_counted():
    ctx = _ws_ctx(id="w", items=_items(("Dashboard", "DASH_Sales")), git_connected=True)
    assert bi_content_source_controlled_and_promoted(ctx).status is Status.NA


def test_bi_deploy_is_na_when_git_is_unreadable():
    ctx = _ws_ctx(id="w", items=_items(("Report", "RPT_Sales")),
                  unavailable={Resource.GIT})
    assert bi_content_source_controlled_and_promoted(ctx).status is Status.NA


def test_bi_deploy_is_na_without_items():
    assert bi_content_source_controlled_and_promoted(
        _ws_ctx(id="w", unavailable={Resource.ITEMS})).status is Status.NA


# --- 11.1.2 source-control coverage ------------------------------------------
# Regression for a dedup error: `ref="11.1.2"` appears in the *docstrings* of
# helpers.py and registry.py as an illustrative example, so a grep for it looks
# like a hit. The real WS-GIT is ref 11.1.1, and 11.1.2 was genuinely unclaimed.

def test_git_coverage_is_na_without_the_git_state():
    ctx = _ws_ctx(id="w", items=_items(("Notebook", "NB")), unavailable={Resource.GIT})
    assert git_covers_every_artifact(ctx).status is Status.NA


def test_a_disconnected_workspace_covers_nothing():
    ctx = _ws_ctx(id="w", items=_items(("Notebook", "NB"), ("DataPipeline", "PL")))
    verdict = git_covers_every_artifact(ctx)
    assert verdict.score == 0
    assert "11.1.1" in verdict.evidence, "must point at the connection check"


def test_all_supported_types_are_fully_covered():
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(
        ("Notebook", "NB"), ("DataPipeline", "PL"), ("SemanticModel", "SM"),
        ("Warehouse", "WH")))
    assert git_covers_every_artifact(ctx).score == 3


def test_an_auto_created_sql_endpoint_is_not_a_coverage_gap():
    """Fabric creates a SQL endpoint per Lakehouse; it has no own definition."""
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(
        ("Lakehouse", "LH"), ("SQLEndpoint", "LH")))
    verdict = git_covers_every_artifact(ctx)
    assert verdict.score == 3
    assert "SQLEndpoint" not in verdict.evidence


def test_an_unsupported_artifact_type_is_reported_as_uncovered():
    ctx = _ws_ctx(id="w", git_connected=True, items=_items(
        ("Notebook", "NB"), ("MountedDataFactory", "ADF")))
    verdict = git_covers_every_artifact(ctx)
    assert verdict.score < 3
    assert "MountedDataFactory" in verdict.evidence
