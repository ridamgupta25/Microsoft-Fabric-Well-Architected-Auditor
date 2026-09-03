"""The reader labels; code scores.

These tests pin the property the design exists for: a score is arithmetic over
labels, computed here, so a reader cannot invent a count and two runs over the
same labels cannot disagree.
"""
from __future__ import annotations

import pytest

from auditfast.ai.classify import UNDETERMINED, GuideError, score, validate
from auditfast.core.judging import GUIDES, JudgingGuide


def _pair() -> JudgingGuide:
    return JudgingGuide(ref="4.5.1", shape="pair",
                        labels=("fact", "dimension", "neither"),
                        pair=("fact", "dimension"), classify="...")


def _ratio() -> JudgingGuide:
    return JudgingGuide(ref="4.5.1", shape="ratio", labels=("compliant", "not"),
                        compliant="compliant", classify="...")


# --- every shipped JudgingGuide must be able to produce a score -------------------

@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_every_guide_is_internally_consistent(check_id):
    """A JudgingGuide naming a label it never declared would fail after the work."""
    validate(check_id, GUIDES[check_id])


@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_every_guide_says_what_the_rule_gets_wrong(check_id):
    """A guide that never names the rule's mistake invites the reader to repeat it.

    Most of these checks are advisory because the rule matched a *name*. The
    code-validation ones are advisory because it matched *vocabulary in the
    code* - 'does this notebook contain the word reconcile' rather than 'does
    it compare two numbers'. Both are the same error, reading surface text
    instead of substance, and the guide has to say which one applies.

    This replaced an assertion that every guide contained the word "name",
    which was true only while every advisory check was a name-matching one.
    """
    text = GUIDES[check_id].classify.lower()
    assert "the rule" in text, (
        f"{check_id}: the instruction never says what the deterministic rule "
        f"concluded, so the reader cannot know where to disagree with it"
    )
    surface = ("name", "naming", "spelling", "vocabulary", "word", "prefix")
    assert any(term in text for term in surface), (
        f"{check_id}: the instruction never says the rule matches surface text, "
        f"so a reader has no reason not to do the same"
    )


@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_every_guide_points_at_a_real_advisory_check(check_id):
    """Guides are keyed by check id, and a typo there is invisible at runtime.

    ``guide_for`` would simply return ``None``, the check would fall through to
    the bundle, and the guide would look like a coverage gap rather than a
    mistake. Seven advisory refs carry two checks each, so keying by ref is not
    an option - which makes this the only thing standing between a mistyped id
    and a guide that never runs.
    """
    from auditfast.core.advisory import is_advisory
    from auditfast.core.check.registry import REGISTRY

    spec = REGISTRY.get(check_id)
    assert spec is not None, f"{check_id} is not a registered check"
    assert spec.ref == GUIDES[check_id].ref, (
        f"{check_id} is ref {spec.ref}, but its guide claims {GUIDES[check_id].ref}"
    )
    assert is_advisory(spec.ref), f"{check_id} (ref {spec.ref}) is not advisory"


@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_no_guide_is_written_for_a_check_that_reads_measured_data(check_id):
    """A check consuming executed query results has nothing for a reader to judge.

    ``SM-FK-RI-DATA`` reads real orphan counts from
    ``workspace.query_results``. When that evidence exists the verdict is
    arithmetic over measured facts; when it does not, the check is already
    N/A. Either way there is no name being matched and no judgment to add - a
    guide for it scored a different question from the one the check asks, which
    is worse than having no guide at all.
    """
    import inspect

    from auditfast.core.check.registry import REGISTRY

    spec = REGISTRY.get(check_id)
    source = inspect.getsource(spec.fn)
    assert "query_results" not in source, (
        f"{check_id} reads measured query results, so its verdict is arithmetic "
        f"rather than judgment. Remove its guide - it belongs on the bundle path."
    )


@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_every_guide_gets_evidence_that_reaches_inside_the_object(check_id):
    """A guide that asks for substance its family cannot carry is unusable.

    ``WS-STAGING``'s instruction said "judge from what the table is for - its
    schema, its position, its column shape" while its family sent only a name
    and a column count. A reader following that guide could only fall back to
    the name, which is the exact failure the check is advisory to escape.

    The property that separates a usable family from that one is whether the
    evidence names anything from *within* the object - a column, an activity,
    a line of code, a measured timestamp - rather than merely identifying it.
    Each object below carries a distinctive marker inside it, so a family that
    only reports counts cannot pass.
    """
    from auditfast.ai.evidence import BUILDERS
    from auditfast.core.models import Item, RoleAssignment, WorkspaceContext

    markers = (
        "marker_column", "MARKER_NOTEBOOK", "MARKER_ACTIVITY", "MARKER_DAX",
        "marker_model_column", "2026-01-02T03:04:05Z", "MARKER_PRINCIPAL",
    )
    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        tables={"wh.dbo.orders": {
            "store": "wh", "store_kind": "Warehouse",
            "columns": [{"name": "order_id", "type": "int"},
                        {"name": "marker_column", "type": "varchar(20)"}],
        }},
        notebooks={"NB": {"cells": [
            {"cell_type": "code", "source": ["df.write  # MARKER_NOTEBOOK"]},
        ]}},
        pipelines={"PL": {"properties": {"activities": [
            {"name": "MARKER_ACTIVITY", "type": "Copy"},
        ]}}},
        semantic_models={"SM": {
            "tables": ["Sales"],
            "measures": [{"name": "Total", "table": "Sales",
                          "expression": "SUM(Sales[MARKER_DAX])"}],
            "columns": [{"table": "Sales", "name": "marker_model_column",
                         "data_type": "double"}],
        }},
        items=[Item(id="i1", type="Warehouse", display_name="WH",
                    last_run_utc="2026-01-02T03:04:05Z")],
        role_assignments=[RoleAssignment(principal_type="User",
                                         display_name="MARKER_PRINCIPAL",
                                         role="Admin")],
    )

    family = GUIDES[check_id].evidence
    records = BUILDERS[family](workspace)
    assert records, f"{check_id}: its evidence family produced nothing to judge"

    blob = "\n".join(r["facts"] for r in records)
    assert any(marker in blob for marker in markers), (
        f"{check_id}: evidence family {family!r} reports only counts - "
        f"{records[0]['facts']!r}. A reader told to judge substance cannot, and "
        f"would fall back to the object's name, which is the failure this check "
        f"exists to escape."
    )


@pytest.mark.parametrize("check_id", sorted(GUIDES))
def test_every_guide_names_an_evidence_family_that_exists(check_id):
    """A missing builder yields no objects, so the job is silently never written."""
    from auditfast.ai.evidence import BUILDERS

    family = GUIDES[check_id].evidence
    assert family in BUILDERS, f"{check_id}: no evidence builder named {family!r}"


# --- validation catches a broken JudgingGuide at import time ----------------------

def test_an_unknown_shape_is_rejected():
    with pytest.raises(GuideError, match="is not one of"):
        validate("x", JudgingGuide(ref="1.1.1", shape="vibes", labels=("a",),
                                   classify="..."))


def test_a_compliant_label_outside_the_vocabulary_is_rejected():
    with pytest.raises(GuideError, match="not in"):
        validate("x", JudgingGuide(ref="1.1.1", shape="ratio", labels=("a", "b"),
                                   compliant="c", classify="..."))


def test_a_pair_needs_two_labels():
    with pytest.raises(GuideError, match="exactly two"):
        validate("x", JudgingGuide(ref="1.1.1", shape="pair", labels=("a", "b"),
                                   pair=("a",), classify="..."))


def test_undetermined_cannot_be_declared():
    """It is always allowed; declaring it invites a JudgingGuide to score on it."""
    with pytest.raises(GuideError, match="always allowed"):
        validate("x", JudgingGuide(ref="1.1.1", shape="binary",
                                   labels=("ok", UNDETERMINED),
                                   compliant="ok", classify="..."))


# --- scoring --------------------------------------------------------------

def test_a_ratio_is_banded_the_way_the_engine_bands_it():
    """An advisory 2 must mean what a deterministic 2 means."""
    assert score(_ratio(), ["compliant"] * 8 + ["not"] * 2)[0] == 2
    assert score(_ratio(), ["compliant"] * 10)[0] == 3
    assert score(_ratio(), ["compliant"] * 5 + ["not"] * 5)[0] == 1
    assert score(_ratio(), ["compliant"] + ["not"] * 9)[0] == 0


def test_the_evidence_states_the_count_it_scored_on():
    """A reviewer must be able to check the arithmetic."""
    _, evidence = score(_ratio(), ["compliant"] * 7 + ["not"] * 2)
    assert "7 of 9" in evidence


def test_a_pair_needs_both_labels_present():
    assert score(_pair(), ["fact", "dimension", "neither"])[0] == 3
    assert score(_pair(), ["fact", "fact", "neither"])[0] == 1
    assert score(_pair(), ["neither", "neither"])[0] == 0


def test_undetermined_objects_leave_the_denominator():
    """N/A-not-FAIL, per object: unjudged is not the same as non-compliant."""
    band, evidence = score(_ratio(), ["compliant"] * 4 + [UNDETERMINED] * 6)
    assert band == 3, "4 of 4 judged objects comply"
    assert "6 of 10 object(s) could not be judged" in evidence


def test_a_large_undetermined_share_is_called_out_not_just_counted():
    """A score resting on half the estate must say so.

    On one real check 30 of 56 objects were unassessable - the load happened
    inside notebooks the pipeline definition cannot see. That scores the same
    as 56 objects all failing, so without a note the report cannot distinguish
    "this estate is bad" from "we could only see half of it".
    """
    labels = ["not"] * 26 + [UNDETERMINED] * 30
    _, evidence = score(_ratio(), labels)
    assert "30 of 56" in evidence
    assert "54%" in evidence
    assert "rests on 26 object(s)" in evidence


def test_a_small_undetermined_share_stays_terse():
    """Noting every stray undetermined would train a reader to ignore the note."""
    _, evidence = score(_ratio(), ["compliant"] * 19 + [UNDETERMINED])
    assert "1 object(s) could not be judged" in evidence
    assert "NOTE" not in evidence


def test_an_out_of_scope_label_leaves_the_denominator_like_undetermined():
    """"Not a serving item" is a judgment, not a gap - but it must not score.

    On a training estate nearly every item is out of scope. Counting those as
    non-compliant scores 0 and reads as "the Gold layer is broken" when the
    truth is "there is no Gold layer". Counting them as `undetermined` would
    hide whether the estate was assessable at all, so they need their own
    label that still leaves the denominator.
    """
    guide = JudgingGuide(
        ref="5.4.7", shape="ratio",
        labels=("fresh", "stale", "not_serving"),
        compliant="fresh", out_of_scope=("not_serving",),
        classify="... the rule ... name ...",
    )
    band, evidence = score(guide, ["fresh", "stale"] + ["not_serving"] * 100)
    assert band == 1, "1 of 2 in-scope objects are fresh"
    assert "100 object(s) were out of scope" in evidence


def test_everything_out_of_scope_produces_no_score():
    """A workspace the check never applied to keeps the rule's verdict."""
    guide = JudgingGuide(
        ref="5.4.7", shape="ratio",
        labels=("fresh", "stale", "not_serving"),
        compliant="fresh", out_of_scope=("not_serving",),
        classify="... the rule ... name ...",
    )
    band, evidence = score(guide, ["not_serving"] * 50)
    assert band is None
    assert "No object was in scope" in evidence


def test_an_out_of_scope_label_must_be_declared():
    with pytest.raises(GuideError, match="not in"):
        validate("x", JudgingGuide(ref="1.1.1", shape="ratio",
                                   labels=("a", "b"), compliant="a",
                                   out_of_scope=("nope",), classify="..."))


def test_the_compliant_label_cannot_also_be_out_of_scope():
    """Otherwise nothing could ever score."""
    with pytest.raises(GuideError, match="both the compliant label"):
        validate("x", JudgingGuide(ref="1.1.1", shape="ratio",
                                   labels=("a", "b"), compliant="a",
                                   out_of_scope=("a",), classify="..."))


def test_the_scoring_sentence_states_the_denominator_rule():
    """Leaving it implicit reversed a real verdict.

    On TB-CONFIG-SINGLE-STORE 515 of 537 objects were undetermined. Whether
    they counted was the difference between 16/537 -> score 0 and 16/22 ->
    score 1: the same labels, the opposite answer. The reader has to be told,
    not left to infer it from a sentence in a different field.
    """
    from auditfast.ai.jobs import _scoring_sentence

    ratio = JudgingGuide(ref="4.6.2", shape="ratio", labels=("a", "b"),
                         compliant="a", classify="... the rule ... name ...")
    text = _scoring_sentence(ratio)
    assert "excluded from BOTH the numerator" in text

    scoped = JudgingGuide(ref="5.4.7", shape="ratio",
                          labels=("a", "b", "c"), compliant="a",
                          out_of_scope=("c",), classify="... the rule ... name ...")
    assert "'c'" in _scoring_sentence(scoped)


def test_nothing_judged_returns_no_score():
    """The caller keeps the deterministic verdict rather than inventing a zero."""
    band, evidence = score(_ratio(), [UNDETERMINED] * 5)
    assert band is None
    assert "deterministic verdict stands" in evidence


def test_labels_are_read_case_insensitively():
    """A reader writing 'Dimension' must not silently score as 'neither'."""
    assert score(_pair(), ["Fact", "DIMENSION"])[0] == 3


def test_the_same_labels_always_give_the_same_score():
    """The reproducibility the deterministic engine has, restored to this half."""
    labels = ["fact", "dimension", "neither", "fact"]
    assert score(_pair(), labels) == score(_pair(), list(labels))


# --- the graded shape -------------------------------------------------------

def _graded() -> JudgingGuide:
    return JudgingGuide(ref="5.1.9", shape="graded",
                        labels=("hard_stop", "soft_exit", "carries_on"),
                        bands=(3, 2, 0), classify="... name ...")


def test_a_graded_label_keeps_the_band_its_check_gives_it():
    """A 3/2/0 check must not be flattened to 3/2/1 by label position.

    ``worst`` derives the band from where a label sits in the vocabulary, so
    the third label can only ever score 1. ``NB-DQ-HALT`` scores a notebook
    that carries on past bad data 0, and 1 would say the practice is partly
    met when it is absent.
    """
    assert score(_graded(), ["hard_stop"])[0] == 3
    assert score(_graded(), ["soft_exit"])[0] == 2
    assert score(_graded(), ["carries_on"])[0] == 0


def test_the_weakest_graded_label_sets_the_score():
    """One unhandled case defeats the practice however many handled ones exist."""
    assert score(_graded(), ["hard_stop"] * 9 + ["carries_on"])[0] == 0


def test_a_graded_guide_needs_a_band_for_every_label():
    with pytest.raises(GuideError, match="one band per label"):
        validate("x", JudgingGuide(ref="1.1.1", shape="graded",
                                   labels=("a", "b"), bands=(3,), classify="..."))


def test_a_graded_band_outside_the_scale_is_rejected():
    with pytest.raises(GuideError, match="outside 0-3"):
        validate("x", JudgingGuide(ref="1.1.1", shape="graded",
                                   labels=("a",), bands=(7,), classify="..."))


def test_graded_undetermined_still_leaves_the_denominator():
    band, evidence = score(_graded(), ["hard_stop", UNDETERMINED, UNDETERMINED])
    assert band == 3
    assert "2 of 3 object(s) could not be judged" in evidence


def test_best_takes_the_strongest_not_the_weakest():
    """Some questions are about the estate, not about every object in it.

    "Does anything here persist run history?" is answered by one artefact
    doing it properly. The notebooks that do not are silent, not failing, so
    `graded` - which takes the weakest - would score the estate 0 whenever a
    single notebook did not write a run log.
    """
    best = JudgingGuide(ref="10.1.1", shape="best",
                        labels=("full", "partial", "none"), bands=(3, 2, 0),
                        classify="... name ...")
    assert score(best, ["none"] * 40 + ["full"])[0] == 3
    assert score(best, ["none"] * 40 + ["partial"])[0] == 2
    assert score(best, ["none"] * 40)[0] == 0


# --- the facts a reader is shown --------------------------------------------

def test_a_relationship_marker_is_suppressed_when_the_name_is_ambiguous():
    """The marker leaked across stores and would mislead exactly where it matters.

    ``normalise_table_name`` drops the store prefix and ``related_columns``
    returns one workspace-wide set, so a single relationship on `badges` marked
    Bronze.badges, Silver.badges and Gold.badges alike. On one estate 47% of
    tables shared a base name - the duplicated-copy case where "which copy is
    actually served" is the whole question.
    """
    from auditfast.ai.evidence import table_detail
    from auditfast.core.models import WorkspaceContext

    cols = [{"name": "badge_id", "type": "int"}, {"name": "name", "type": "varchar"}]
    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "Bronze.badges": {"store": "Bronze", "columns": list(cols)},
        "Gold.badges": {"store": "Gold", "columns": list(cols)},
        "Gold.solo": {"store": "Gold", "columns": list(cols)},
    }, semantic_models={"M": {"relationships": [
        {"from_table": "badges", "from_column": "badge_id",
         "to_table": "solo", "to_column": "badge_id"},
    ]}})

    facts = {r["id"]: r["facts"] for r in table_detail(workspace)}
    assert "in-relationship" not in facts["Bronze.badges"]
    assert "in-relationship" not in facts["Gold.badges"]
    assert "2 tables share this base name" in facts["Gold.badges"]
    assert "in-relationship" in facts["Gold.solo"], "an unambiguous name keeps it"


def test_platform_and_staging_tables_are_not_put_to_the_reader():
    """57 of 67 objects in one job were Fabric bookkeeping the reader would have
    had to label 'neither' one at a time."""
    from auditfast.ai.evidence import table_detail
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "dbo.fact_sales": {"columns": [{"name": "sales_key"}]},
        "lh.queryinsights.exec_requests_history": {"columns": [{"name": "x"}]},
        "lh.sys.external_delta_tables": {"columns": [{"name": "y"}]},
        "04b3be05f70243cb9c2c03efee0fcaef_e1002d82": {"columns": [{"name": "z"}]},
    })
    ids = [record["id"] for record in table_detail(workspace)]
    assert ids == ["dbo.fact_sales"]


def test_an_unreadable_table_says_so_rather_than_reading_as_empty():
    from auditfast.ai.evidence import table_detail
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 tables={"dbo.mystery": {}})
    assert "columns not readable" in table_detail(workspace)[0]["facts"]


# --- the pipeline and notebook families -------------------------------------

def test_a_pipeline_keeps_the_wiring_a_check_reads():
    """Eleven checks read different parts of the same definition.

    ``dependsOn`` conditions decide PL-FAILURE-ALERT and PL-DQ-GATE,
    ``typeProperties`` decides PL-DEADLETTER. A summary that dropped either
    would leave those questions silently unanswerable, so the definition goes
    through structurally intact.
    """
    from auditfast.ai.evidence import pipeline_definition
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", pipelines={
        "PL_Load": {"properties": {"activities": [
            {"name": "Copy customers", "type": "Copy", "description": None,
             "typeProperties": {"enableSkipIncompatibleRow": True}},
            {"name": "Step_9", "type": "Teams", "userProperties": [],
             "dependsOn": [{"activity": "Copy customers",
                            "dependencyConditions": ["Failed"]}]},
        ]}},
    })
    facts = pipeline_definition(workspace)[0]["facts"]
    assert "2 activities (Copy, Teams)" in facts
    assert "dependencyConditions" in facts and "Failed" in facts
    assert "enableSkipIncompatibleRow" in facts
    assert '"description"' not in facts, "empty branches are pruned"
    assert '"userProperties"' not in facts


def test_a_notebook_keeps_its_comments_and_markdown():
    """The rules strip comments; a reader judging intent needs them.

    A regex cannot tell '# we hash the email here' from code that hashes it,
    which is why it strips comments. A reader can, and that difference is most
    of why these checks are advisory rather than deterministic.
    """
    from auditfast.ai.evidence import notebook_code_evidence
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", notebooks={
        "NB_Load": {"cells": [
            {"cell_type": "markdown", "source": ["# Loads customers nightly"]},
            {"cell_type": "code", "source": ["# hash the email\n", "df.write\n"]},
        ]},
    })
    facts = notebook_code_evidence(workspace)[0]["facts"]
    assert "Loads customers nightly" in facts
    assert "# hash the email" in facts, "comments carry intent a regex cannot read"
    assert "1 markdown cell(s)" in facts


def test_an_over_long_notebook_is_cut_with_an_instruction_not_dropped():
    """Truncating inside one object is recoverable; skipping objects is not."""
    from auditfast.ai.evidence import _MAX_BODY, notebook_code_evidence
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", notebooks={
        "NB_Huge": {"cells": [
            {"cell_type": "code", "source": ["x = 1\n" * (_MAX_BODY // 2)]},
        ]},
    })
    facts = notebook_code_evidence(workspace)[0]["facts"]
    assert "TRUNCATED" in facts
    assert "'undetermined'" in facts, "an unseen tail must not read as absent"


def test_every_pipeline_and_notebook_is_present_none_sampled():
    """The free pass this design exists to remove."""
    from auditfast.ai.evidence import notebook_code_evidence, pipeline_definition
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        pipelines={f"PL_{i}": {"properties": {"activities": []}} for i in range(200)},
        notebooks={f"NB_{i}": {"cells": [{"cell_type": "code", "source": ["x=1"]}]}
                   for i in range(200)},
    )
    assert len(pipeline_definition(workspace)) == 200
    assert len(notebook_code_evidence(workspace)) == 200


def test_an_unreadable_pipeline_says_so_rather_than_reading_as_compliant():
    from auditfast.ai.evidence import pipeline_definition
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 pipelines={"PL_Mystery": {}})
    assert "not readable" in pipeline_definition(workspace)[0]["facts"]


def test_table_detail_sends_every_column_not_the_first_twelve():
    """A truncated column list is the free pass, one level down.

    TB-AUDITCOLS looks for a lineage column anywhere in the table. If
    `created_date` sits at position 13 and the reader never sees it, the table
    is labelled 'no lineage columns' - a false FAIL produced by truncation.
    """
    from auditfast.ai.evidence import table_detail
    from auditfast.core.models import WorkspaceContext

    cols = [{"name": f"c{i}", "type": "int"} for i in range(20)]
    cols.append({"name": "created_date", "type": "datetime2"})
    workspace = WorkspaceContext(id="ws", display_name="WS",
                                 tables={"dbo.wide": {"columns": cols}})
    facts = table_detail(workspace)[0]["facts"]
    assert "created_date" in facts
    assert "21 columns" in facts


def test_table_detail_carries_the_type_and_the_store():
    """A junk dimension needs low-cardinality columns, and two checks group by
    store, so neither is expressible from a name and a count alone."""
    from auditfast.ai.evidence import table_detail
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", tables={
        "WH.dbo.fact": {"store": "WH", "store_kind": "Warehouse", "columns": [
            {"name": "reason", "type": "varchar(500)"},
            {"name": "email", "type": "varchar(255)", "is_masked": True},
        ]},
    })
    facts = table_detail(workspace)[0]["facts"]
    assert "store=WH (Warehouse)" in facts
    assert "reason:varchar(500)" in facts
    assert "MASKED" in facts, "WS-DDM cannot judge masking it cannot see"


def test_notebooks_and_pipelines_share_one_population_without_id_collision():
    """WS-RUN-HISTORY-EXPORT asks whether the estate persists run outcomes, and
    either kind can be where it happens - but two objects with one id cannot
    both be labelled."""
    from auditfast.ai.evidence import code_and_pipelines
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        pipelines={"Loader": {"properties": {"activities": []}}},
        notebooks={"Loader": {"cells": [{"cell_type": "code", "source": ["x=1"]}]}},
    )
    ids = [r["id"] for r in code_and_pipelines(workspace)]
    assert sorted(ids) == ["notebook:Loader", "pipeline:Loader"]


def test_a_measure_carries_its_dax_not_a_count_of_measures():
    """R-DAX-VAR judges the DAX itself; a count says nothing about quality."""
    from auditfast.ai.evidence import model_measures
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", semantic_models={
        "Sales": {"tables": ["Date", "Sales"], "measures": [
            {"name": "YTD", "table": "Sales",
             "expression": ["TOTALYTD(", "SUM(Sales[Amount]), Date[Date])"]},
        ]},
    })
    facts = model_measures(workspace)[0]["facts"]
    assert "TOTALYTD(SUM(Sales[Amount]), Date[Date])" in facts
    assert "Date, Sales" in facts


def test_a_model_column_carries_its_source_type_and_whether_it_is_load_bearing():
    """SM-COLUMN-SHAPE exempts relationship-bound keys, and cost lives in the
    source type - 'uniqueidentifier' is expensive whatever the model calls it."""
    from auditfast.ai.evidence import model_columns
    from auditfast.core.models import WorkspaceContext

    workspace = WorkspaceContext(id="ws", display_name="WS", semantic_models={
        "Sales": {
            "columns": [
                {"table": "Sales", "name": "id", "data_type": "string",
                 "source_provider_type": "uniqueidentifier", "is_key": True},
                {"table": "Sales", "name": "cust_key", "data_type": "int64"},
            ],
            "relationships": [
                {"from_table": "Sales", "from_column": "cust_key",
                 "to_table": "Customer", "to_column": "key"},
            ],
        },
    })
    facts = model_columns(workspace)[0]["facts"]
    assert "source=uniqueidentifier" in facts
    assert "is_key" in facts
    assert "in-relationship" in facts


def test_a_semantic_model_carries_the_reports_that_read_it():
    """"What is this item for" is the signal name-matching cannot supply.

    WS-GOLD-FRESHNESS asks which items are *serving* items. A model a report
    reads is a serving surface whatever it is called; a model nothing reads is
    not. The report record's name field is `name`, not `display_name` - getting
    that wrong rendered every binding as "(unnamed report)".
    """
    from auditfast.ai.evidence import workspace_items
    from auditfast.core.models import Item, WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        items=[Item(id="m1", type="SemanticModel", display_name="Sales"),
               Item(id="m2", type="SemanticModel", display_name="Scratch")],
        reports=[{"id": "r1", "name": "Exec Dashboard", "dataset_id": "m1"}],
    )
    facts = {r["id"]: r["facts"] for r in workspace_items(workspace)}
    assert "read by 1 report(s): Exec Dashboard" in facts["SemanticModel:Sales"]
    assert "no report reads this model" in facts["SemanticModel:Scratch"]


def test_the_cadence_is_computed_in_code_not_left_to_the_reader():
    """OPS-MONITOR-REFRESH scores on the median gap between runs.

    Asking a model to compute a median over a date series is exactly the
    mistake this design exists to avoid, so the number is derived here and the
    reader only decides whether the item is monitoring data at all.
    """
    from auditfast.ai.evidence import workspace_items
    from auditfast.core.models import Item, WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        items=[Item(id="i1", type="DataPipeline", display_name="Telemetry")],
        run_history={"i1": [
            "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00Z", "2026-01-01T05:00:00Z",
        ]},
    )
    facts = workspace_items(workspace)[0]["facts"]
    assert "median gap between runs = 1.0 h over 4 run(s)" in facts


def test_an_unmeasurable_cadence_says_so_rather_than_reading_as_zero():
    """One run is not evidence of a slow cadence - it is no evidence."""
    from auditfast.ai.evidence import workspace_items
    from auditfast.core.models import Item, WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        items=[Item(id="i1", type="Lakehouse", display_name="Gold")],
        run_history={"i1": ["2026-01-01T00:00:00Z"]},
    )
    assert "not measurable" in workspace_items(workspace)[0]["facts"]


def test_role_records_carry_the_config_tables_the_reader_must_judge():
    """WS-METADATA-WRITE's weak half is 'is there a metadata store at all'.

    Who holds which role is read from Fabric and is reliable; whether the
    workspace holds config tables is matched from a vocabulary that includes
    the word 'job'. The reader cannot settle that without seeing the tables.
    """
    from auditfast.ai.evidence import workspace_roles
    from auditfast.core.models import RoleAssignment, WorkspaceContext

    workspace = WorkspaceContext(
        id="ws", display_name="WS",
        tables={"dbo.etl_config": {"columns": [{"name": "k"}]},
                "dbo.sales": {"columns": [{"name": "amount"}]}},
        role_assignments=[
            RoleAssignment(principal_type="User", display_name="Ana",
                           role="Admin"),
        ],
    )
    record = workspace_roles(workspace)[0]
    assert "principal_type=User" in record["facts"]
    assert "dbo.etl_config" in record["facts"]
    assert "dbo.sales" not in record["facts"]
