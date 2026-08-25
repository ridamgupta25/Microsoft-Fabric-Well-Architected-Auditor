"""Judging guides - what a reader must do to assess one advisory check.

A deterministic check is a function: it reads the knowledge base and returns a
verdict. An advisory check needs the same thing written for a reader instead of
an interpreter - what to look at, what to call each thing it finds, and how those
labels turn into a score.

That is a **judging guide**, and there is one per advisory *check*. Writing it
down rather than letting a reader improvise per run is what makes two runs
comparable: the same guide over the same evidence should produce the same labels.

**A reader only ever produces labels.** It is never asked for a score. Turning
labels into a number happens in :mod:`auditfast.ai.classify`, in code, so a count
cannot be invented and the same labels always give the same score.

Guides live here rather than in :mod:`auditfast.core.advisory` because that
module answers one question - *is this ref advisory?* - and is read on every
result. This one is long-form text, read only when a job is built.

A check with no guide is still advisory: it reaches the Advisory report with its
deterministic verdict and is simply not AI-judged. Guides can therefore be added
one at a time without stranding anything.
"""
from __future__ import annotations

from dataclasses import dataclass

#: What a reader must do, and must not do, on every job. The bundle path has its
#: own guide in :data:`auditfast.core.advisory.SCORING_GUIDE`, which tells a
#: reader it is looking at a sample and asks it for a score. Neither is true
#: here - a job carries every object and code does the arithmetic - so sending
#: that text with a job would contradict the job itself.
LABELLING_RULES = """\
LABELLING RULES - these apply to every job.

1. You produce LABELS, never a score. Code turns your labels into a 0-3 band
   using the same rules the deterministic engine uses. Do not compute, argue
   about, or mention a score.

2. Label EVERY object you are given, one row each. Nothing is sampled - the
   objects in front of you are the whole population for this check, split
   across chunks only so each fits. An object you leave unlabelled is reported
   as unlabelled; it does not quietly pass.

3. Use only the labels the job lists, plus 'undetermined'.

4. 'undetermined' means the evidence does not let you tell - not that the
   object is bad. Undetermined objects leave the denominator entirely, so they
   neither pass nor fail. This is the N/A-not-FAIL rule, applied per object.
   Prefer 'undetermined' to a guess.

5. Judge the object in front of you, not its name. Every check here is
   advisory precisely because the rule it replaces matched names. If you label
   from the name too, the exercise achieves nothing.

6. Every job carries `rule_verdict` - what the deterministic rule concluded.
   Treat it as a claim to check, not an answer to copy. Agreeing with it is a
   valid outcome; so is contradicting it, and that disagreement is the finding
   worth having.

   Some objects also carry their own `rule_says`. Where it is present it is the
   rule's claim about THAT object. Where it is empty the rule reached no
   per-object verdict, and there is nothing to disagree with - judge from the
   facts alone rather than inventing a claim to argue against.

7. Give a short reason for any label that contradicts the rule. That is the
   audit trail for why the score moved.
"""


@dataclass(frozen=True)
class JudgingGuide:
    """The instruction sheet for judging one advisory check."""

    #: The checklist point this check implements. Carried so a test can assert
    #: the guide belongs to a ref that is actually advisory - a guide for a
    #: deterministic check would never be reached and would look like a
    #: coverage gap rather than a mistake.
    ref: str
    #: How labels become a score. See ``auditfast.ai.classify.SHAPES``.
    shape: str
    #: The vocabulary a reader may use. ``undetermined`` is always allowed on
    #: top of these and must not be declared - see ``classify.validate``.
    labels: tuple[str, ...]
    #: What to do, in the reader's own terms. Written from the check's docstring
    #: so it asks for the same thing the rule was trying to ask for, and naming
    #: the mistake the rule makes so the reader does not repeat it.
    classify: str
    #: Which evidence family to build for this check's objects.
    evidence: str = "table-shape"
    #: For ``ratio``/``binary``: the label that counts as meeting the practice.
    compliant: str = ""
    #: For ``pair``: the two labels that must both be present.
    pair: tuple[str, str] = ()
    #: For ``graded``/``best``: the 0-3 band each label earns, parallel to
    #: ``labels``.
    bands: tuple[int, ...] = ()
    #: Labels that are a real judgment but put the object OUT OF SCOPE, so they
    #: leave the denominator exactly as ``undetermined`` does.
    #:
    #: The two are different and the report needs both. "I could not tell" is a
    #: gap in the evidence; "this is not a serving item" is a decision, and on a
    #: training estate it is the decision for nearly every object. Without this
    #: a reader must either call them ``undetermined`` - hiding whether the
    #: estate was assessable at all - or let them score 0, which asserts the
    #: practice is absent from things it never applied to.
    out_of_scope: tuple[str, ...] = ()


#: ``check_id -> guide``.
#:
#: Keyed by **check id, not ref**. Seven advisory refs carry two checks each,
#: and five of those pairs differ in scope - ``5.1.9`` is both ``PL-DQ-GATE``
#: (a pipeline) and ``NB-DQ-HALT`` (a notebook), ``7.2.6`` is both
#: ``PL-RECONCILE`` and ``NB-RECONCILE``. A ref-keyed guide would hand a
#: pipeline instruction sheet to a notebook check, with a label vocabulary that
#: cannot apply and an evidence family that fetches the wrong objects.
#:
#: ``is_advisory`` stays keyed by ref, because *being advisory* is a property of
#: the checklist point. Each key answers the question it is for.
GUIDES: dict[str, JudgingGuide] = {
    "TB-STARSCHEMA": JudgingGuide(
        ref="4.5.1",
        shape="pair",
        labels=("fact", "dimension", "neither"),
        pair=("fact", "dimension"),
        evidence="table-detail",
        classify=(
            "For each table decide whether it is a FACT, a DIMENSION, or NEITHER.\n"
            "\n"
            "  fact      - it records events or measurements. Keys pointing at\n"
            "              other tables, values worth summing or averaging, and\n"
            "              usually a date. An aggregate or summary table is a\n"
            "              fact too, even with no key columns at all.\n"
            "  dimension - it describes one subject. A key or two, then\n"
            "              attributes that all belong to that subject. A small\n"
            "              two-column lookup of id plus name is a dimension.\n"
            "  neither   - it does not describe one subject and records no\n"
            "              events: a junk drawer of unrelated columns, or\n"
            "              something operational - staging, a log, config, run\n"
            "              control, a pipeline intermediate.\n"
            "\n"
            "These are shapes, not thresholds. A dimension with three keys is\n"
            "still a dimension if the rest of it describes one subject; a table\n"
            "with two keys and a measure is still a fact if that is what it is\n"
            "for.\n"
            "\n"
            "Judge from the columns. The store is given so you can see whether a\n"
            "table sits in a raw or staging area, which is the one case where\n"
            "where it lives tells you more than what it holds.\n"
            "\n"
            "'rule_says' is what the rule concluded. It is not naive name\n"
            "matching: it uses declared foreign keys first, then semantic-model\n"
            "relationships, then the table's name, then column-shape thresholds -\n"
            "and those thresholds require three keys before it will infer a fact,\n"
            "so a plain two-key fact comes back 'unknown'. Where you disagree,\n"
            "say so in your reason.\n"
            "\n"
            "Note that on the other table checks this same field carries the\n"
            "star-schema role rather than their verdict - here it IS the claim\n"
            "you are asked to check."
        ),
    ),

    # ---- staging (table names) ---------------------------------------------
    "WS-STAGING": JudgingGuide(
        ref="3.6.3",
        shape="binary",
        labels=("staging", "not_staging", "operational"),
        compliant="staging",
        out_of_scope=("operational",),
        evidence="table-detail",
        classify=(
            "Decide, for each table, whether it is a STAGING table - somewhere a\n"
            "load lands on its way to the model, rather than the thing anyone\n"
            "reports from.\n"
            "\n"
            "  staging      - a landing or buffer copy: raw source columns, an\n"
            "                 unparsed CSV whose columns are Column1..N, an\n"
            "                 ingestion-provenance column, a bronze or silver\n"
            "                 layer copy, a *_cleaned intermediate. A medallion\n"
            "                 bronze/silver table COUNTS even though it is kept\n"
            "                 permanently - what matters is that it is a step on\n"
            "                 the way, not that it is transient.\n"
            "  not_staging  - a table the solution serves or reports on. Where a\n"
            "                 raw-shaped table has no cleansed counterpart\n"
            "                 anywhere, it IS the data - label it not_staging.\n"
            "  operational  - a control, watermark, scratch or sample table. It\n"
            "                 is neither a buffer nor something served, so it is\n"
            "                 out of scope rather than a finding.\n"
            "\n"
            "Use 'undetermined' only where the columns could not be read.\n"
            "\n"
            "The rule matches the spellings stg / stage / staging and nothing\n"
            "else. On one estate it found 1 buffer out of about 90 - missing\n"
            "every bz_* bronze table, every *_cleaned intermediate and every\n"
            "landing table whose header parse failed - and passed the workspace\n"
            "on that single hit. Judge from the columns and the store."
        ),
    ),

    # ---- pipelines ---------------------------------------------------------
    "PL-NOTIFY": JudgingGuide(
        ref="2.4.5",
        shape="binary",
        labels=("notifies", "silent"),
        compliant="notifies",
        evidence="pipeline-definition",
        classify=(
            "Decide whether ANYONE IS TOLD when this pipeline fails.\n"
            "\n"
            "  notifies - some activity sends a message a person or on-call\n"
            "             channel will see: a Teams or Outlook activity, or a\n"
            "             Web / Azure Function call posting to a webhook such as\n"
            "             Slack, PagerDuty, ServiceNow or an internal alert API.\n"
            "  silent   - nothing in the pipeline tells anyone.\n"
            "\n"
            "Judge by what the activity DOES - its type, and the URL and body it\n"
            "posts - not by its display name. The rule only accepts a Web\n"
            "activity as a notifier when its name matches notif|alert|email|teams,\n"
            "so a Web activity posting to a Slack webhook called 'Post_Status' is\n"
            "missed entirely, while a Teams activity called 'Send_Alert_1' is\n"
            "credited even when it only announces success.\n"
            "\n"
            "Use 'undetermined' when the pipeline has no activities at all. An\n"
            "empty pipeline has nothing to notify about, so it is not silent -\n"
            "there is simply nothing there to judge.\n"
            "\n"
            "This check asks only whether a notifier EXISTS. Whether it is wired\n"
            "to the failure path is a different check - do not fail it here."
        ),
    ),
    "PL-FAILURE-ALERT": JudgingGuide(
        ref="10.1.4",
        shape="binary",
        labels=("wired_to_failure", "notifier_not_on_failure_path", "no_notifier"),
        compliant="wired_to_failure",
        evidence="pipeline-definition",
        classify=(
            "Decide whether a notification actually fires WHEN SOMETHING FAILS.\n"
            "This is about the wiring, not the presence of a notifier.\n"
            "\n"
            "Follow 'dependsOn'. An activity carries the conditions under which\n"
            "it runs - Succeeded, Failed, Skipped, Completed. A notifier reached\n"
            "only on Succeeded announces good news and warns nobody.\n"
            "\n"
            "  wired_to_failure             - a notifier runs on a Failed edge,\n"
            "                                 or in the failure branch of an\n"
            "                                 If/Switch, AND IS ENABLED.\n"
            "  notifier_not_on_failure_path - a notifier exists but only ever\n"
            "                                 runs on success.\n"
            "  no_notifier                  - nothing notifies at all, or the\n"
            "                                 only one is switched off.\n"
            "\n"
            "CHECK WHETHER IT IS SWITCHED ON. An activity carrying\n"
            "\"state\": \"Inactive\" never runs, however well it is wired - and\n"
            "\"onInactiveMarkAs\": \"Succeeded\" means the pipeline reports GREEN\n"
            "on the path that was meant to raise the alarm. On one estate the\n"
            "only pipeline passing this check had a disabled Teams activity on\n"
            "three Failed edges, with no recipient configured. Label that\n"
            "'no_notifier': a control that cannot fire is not a control.\n"
            "\n"
            "Use 'undetermined' when the pipeline has no activities at all -\n"
            "nothing can fail, so there is nothing to alert on - and when the\n"
            "work happens inside a notebook whose own error handling you cannot\n"
            "see from here.\n"
            "\n"
            "The rule requires the notifier activity ITSELF to carry the Failed\n"
            "dependency, so it misses a notifier one hop further down the failure\n"
            "branch, ignores 'Completed' conditions entirely, and cannot see a\n"
            "notifier nested inside an If branch. It matches Teams, Outlook and\n"
            "SendEmail by activity type - a correctly wired one called 'Step_9'\n"
            "passes fine - but a Web or Function activity only counts when its\n"
            "own name matches notif|alert|email|teams, so a Web activity posting\n"
            "to a Slack webhook called 'Post_Status' is missed."
        ),
    ),
    "PL-DEADLETTER": JudgingGuide(
        ref="2.4.4",
        shape="binary",
        labels=("routes_bad_rows", "drops_or_halts"),
        compliant="routes_bad_rows",
        evidence="pipeline-definition",
        classify=(
            "Decide what happens to a row this pipeline CANNOT LOAD.\n"
            "\n"
            "  routes_bad_rows - rejected rows are kept somewhere they can be\n"
            "                    inspected and replayed: a Copy activity with\n"
            "                    incompatible-row redirection or error logging\n"
            "                    configured, a sink fed by a Failed/Skipped\n"
            "                    dependency, or an explicit reject/quarantine\n"
            "                    write.\n"
            "  drops_or_halts  - bad rows are silently discarded, or one bad row\n"
            "                    stops the whole load with nothing retained.\n"
            "\n"
            "Use 'undetermined' when the pipeline moves no rows at all, or when\n"
            "the load happens inside a notebook, a script, a stored procedure, a\n"
            "dataflow or a child pipeline whose handling you cannot see from\n"
            "here. A Lookup reads a control value for branching - it is not a\n"
            "load. Most pipelines on a notebook-driven estate are undetermined,\n"
            "and that is the honest answer.\n"
            "\n"
            "Only a Copy activity configures reject handling in the pipeline's\n"
            "own JSON, so it is the only kind you can judge directly.\n"
            "\n"
            "The rule counts notebooks, scripts, stored procedures and Lookups as\n"
            "visible data movement, so it fails pipelines whose reject handling\n"
            "is somewhere it cannot see - on one estate, 15 of the 39 it named.\n"
            "It also credits any activity whose NAME matches reject|invalid|\n"
            "quarantine|dead.letter|error.output, so a step called\n"
            "'Check_Invalid_Rows' that only counts them passes. Look at where\n"
            "the rows actually go."
        ),
    ),
    "PL-IDEMPOTENT": JudgingGuide(
        ref="2.4.6",
        shape="binary",
        labels=("rerunnable", "appends_duplicates"),
        compliant="rerunnable",
        evidence="pipeline-definition",
        classify=(
            "Decide whether running this pipeline TWICE would duplicate data.\n"
            "\n"
            "  rerunnable         - a second run replaces or reconciles what the\n"
            "                       first wrote: a MERGE/UPSERT, an overwrite of\n"
            "                       the target or partition, a delete-then-insert\n"
            "                       of the same window, or a watermark that makes\n"
            "                       the second run a no-op.\n"
            "  appends_duplicates - the load appends unconditionally, so a rerun\n"
            "                       adds the same rows again.\n"
            "\n"
            "The rule searches the whole definition as text for words like merge,\n"
            "upsert, batch_id and watermark, so a column called 'batch_id' or a\n"
            "schema called 'merge_area' passes a pipeline that plainly appends.\n"
            "Read the write itself - the sink's write behaviour, the SQL a Script\n"
            "activity runs - and ignore words that appear only in a name."
        ),
    ),
    "PL-INCREMENTAL": JudgingGuide(
        ref="2.2.1",
        shape="binary",
        labels=("incremental", "full_reload"),
        compliant="incremental",
        evidence="pipeline-definition",
        classify=(
            "Decide whether this pipeline moves ONLY WHAT CHANGED.\n"
            "\n"
            "  incremental - the source query, filter or lookup restricts the\n"
            "                load to new or changed rows: a watermark or\n"
            "                high-water column, a date/partition predicate, CDC\n"
            "                or change tracking, or a merge keyed on the source.\n"
            "  full_reload - every run moves the entire source.\n"
            "\n"
            "Use 'undetermined' when the load runs inside a notebook, so the\n"
            "predicate is not in this definition at all.\n"
            "\n"
            "The rule matches those words anywhere in the definition text, so a\n"
            "target table named 'merge_staging' or a column named 'last_modified'\n"
            "passes a pipeline that reloads everything. Read the source query and\n"
            "its parameters, not the names around them."
        ),
    ),
    "PL-FULLLOAD": JudgingGuide(
        ref="2.2.2",
        shape="graded",
        labels=("reload_is_justified", "staging_only", "reload_of_a_large_table"),
        bands=(3, 1, 0),
        evidence="pipeline-definition",
        classify=(
            "This pipeline reloads a table wholesale - TRUNCATE, DROP, INSERT\n"
            "OVERWRITE, or a Copy sink set to overwrite. Decide whether that is\n"
            "reasonable for the table it targets.\n"
            "\n"
            "  reload_is_justified     - the target is small and reference-like (a\n"
            "                            lookup, a code list, a small dimension),\n"
            "                            or the reload is plainly a one-off\n"
            "                            initial/backfill run rather than the\n"
            "                            daily path.\n"
            "  staging_only            - the target is a staging or temporary\n"
            "                            table, so the reload is a load mechanic\n"
            "                            rather than a data-retention decision.\n"
            "  reload_of_a_large_table - a fact or transactional table is\n"
            "                            reloaded in full on the routine path.\n"
            "\n"
            "Use 'undetermined' when the target is a runtime expression such as\n"
            "@{item().TABLE}, because which table it is cannot be known here.\n"
            "\n"
            "The rule decides fact-vs-dimension from the target's NAME, so a\n"
            "large table not called fact_* is judged harmless and a small lookup\n"
            "called 'fact_codes' is judged serious. Judge from what the table\n"
            "holds."
        ),
    ),
    "PL-HIST-SEPARATION": JudgingGuide(
        ref="2.2.3",
        shape="binary",
        labels=("gated", "runs_on_the_daily_path"),
        compliant="gated",
        evidence="pipeline-definition",
        classify=(
            "This pipeline appears to contain a historical or backfill load.\n"
            "Decide whether that load is KEPT OFF the routine schedule.\n"
            "\n"
            "  gated                  - the historical work sits behind a\n"
            "                           condition - a parameter, an If, a Switch\n"
            "                           case - that a normal run does not take,\n"
            "                           or the whole pipeline is a dedicated\n"
            "                           one-off artefact nothing schedules.\n"
            "  runs_on_the_daily_path - the historical load executes on every\n"
            "                           run alongside the incremental work.\n"
            "\n"
            "Use 'undetermined' when nothing here is a historical load at all.\n"
            "\n"
            "The rule reads intent from the pipeline's name and its activity\n"
            "names, then requires a gate whose own condition text contains\n"
            "load-mode words. So a genuine gate that tests a parameter called\n"
            "@pipeline().parameters.mode is not recognised, and a pipeline merely\n"
            "named 'load_history_for_reporting' is treated as a backfill. Read\n"
            "the condition and what it actually admits."
        ),
    ),
    "PL-LOADMODE": JudgingGuide(
        ref="2.2.5",
        shape="binary",
        labels=("modes_separated", "one_undifferentiated_path"),
        compliant="modes_separated",
        evidence="pipeline-definition",
        classify=(
            "Decide whether FIRST LOAD and ONGOING LOAD are distinguishable here.\n"
            "\n"
            "  modes_separated           - a parameter, branch or Switch selects\n"
            "                              between them, or the pipeline is\n"
            "                              plainly one of the two and the other\n"
            "                              lives elsewhere.\n"
            "  one_undifferentiated_path - a single path serves both, so the\n"
            "                              first run and the thousandth do the\n"
            "                              same thing.\n"
            "\n"
            "The rule passes on the pipeline's NAME matching full.load|\n"
            "incremental|initial.load, so 'PL_incremental_copy' passes on its\n"
            "name alone and a properly parameterised pipeline whose parameter is\n"
            "called 'run_type' fails. Read the parameters and the branches."
        ),
    ),
    "PL-DQ-GATE": JudgingGuide(
        ref="5.1.9",
        shape="graded",
        labels=("all_gates_halt", "some_gates_halt", "no_gate_halts"),
        bands=(3, 1, 0),
        evidence="pipeline-definition",
        classify=(
            "Find the activities in this pipeline that VALIDATE something - a\n"
            "row-count check, a schema or null check, a reconciliation, an\n"
            "assertion - and decide whether failing one actually STOPS what\n"
            "follows.\n"
            "\n"
            "Follow 'dependsOn' downstream. A validation gates the load when the\n"
            "next step runs only on its success, or when a Fail activity runs on\n"
            "its failure. A downstream step depending on 'Completed' runs either\n"
            "way and gates nothing.\n"
            "\n"
            "  all_gates_halt  - every validation stops the flow when it fails.\n"
            "  some_gates_halt - at least one validation is decorative: bad data\n"
            "                    continues past it.\n"
            "  no_gate_halts   - no validation stops anything.\n"
            "\n"
            "Use 'undetermined' when the pipeline validates nothing.\n"
            "\n"
            "The rule finds validations by NAME (dq|validat|verif|assert|check),\n"
            "so a Script activity running a row-count assertion called 'Step_3'\n"
            "is never examined, and a Copy activity called 'copy_check_data' is\n"
            "treated as a validation. Judge by what the activity runs."
        ),
    ),
    "PL-SECRETS": JudgingGuide(
        ref="6.4.2",
        shape="binary",
        labels=("no_secret", "hardcoded_secret"),
        compliant="no_secret",
        evidence="pipeline-definition",
        classify=(
            "Decide whether a real CREDENTIAL VALUE is written into this\n"
            "definition.\n"
            "\n"
            "  no_secret        - no literal credential. A Key Vault reference, a\n"
            "                     managed-identity or workspace-identity\n"
            "                     connection, a linked-service reference, or a\n"
            "                     placeholder that is plainly not a real value.\n"
            "  hardcoded_secret - an actual password, account key or shared\n"
            "                     access key sits in the JSON as a literal.\n"
            "\n"
            "The rule matches patterns like password\\s*=\\s*..., so it fires on a\n"
            "Key Vault secret NAME such as 'password=@{secretName}', on a source\n"
            "column called AccountKey, and on any placeholder text - while an\n"
            "actual base64 key stored under an unusual property name is missed.\n"
            "Decide whether the string is a usable credential."
        ),
    ),

    # ---- notebooks ---------------------------------------------------------
    "NB-MARKDOWN": JudgingGuide(
        ref="3.1.4",
        shape="graded",
        labels=("explains_the_logic", "present_but_uninformative", "no_markdown"),
        bands=(3, 1, 0),
        evidence="notebook-code",
        classify=(
            "Decide whether this notebook's markdown WOULD HELP the next person\n"
            "who has to change it.\n"
            "\n"
            "  explains_the_logic        - the markdown says what the notebook is\n"
            "                              for, what it reads and writes, or why\n"
            "                              a rule is what it is.\n"
            "  present_but_uninformative - markdown exists but carries no\n"
            "                              meaning: a bare title, 'Notes', a\n"
            "                              stack trace, sample output, a heading\n"
            "                              with nothing under it.\n"
            "  no_markdown               - none at all.\n"
            "\n"
            "The rule counts markdown cells and passes on one, so a notebook with\n"
            "a single cell containing its own file name passes and a heavily\n"
            "commented notebook with no markdown cell fails. You can see both -\n"
            "the markdown and the code - so judge whether the notebook is\n"
            "explained, not whether a cell type is present."
        ),
    ),
    "NB-PII-TOKENISED": JudgingGuide(
        ref="5.5.4",
        shape="graded",
        labels=("tokenised_and_validated", "tokenised_only", "personal_data_in_clear"),
        bands=(3, 2, 0),
        evidence="notebook-code",
        classify=(
            "This notebook appears to handle personal data. Decide how it is\n"
            "treated on the way out.\n"
            "\n"
            "  tokenised_and_validated - personal columns are hashed, masked,\n"
            "                            encrypted or tokenised before the write,\n"
            "                            AND their format is checked.\n"
            "  tokenised_only          - protected but never format-validated.\n"
            "  personal_data_in_clear  - real personal data is written as-is.\n"
            "\n"
            "Use 'undetermined' when the notebook handles no personal data, or\n"
            "when the protection happens somewhere you cannot see - a view, a\n"
            "stored procedure, a downstream masking policy.\n"
            "\n"
            "First decide whether the data IS personal. The rule matches column\n"
            "NAME vocabulary, and that vocabulary has produced false findings on\n"
            "a table called dest_full_name, a product called 'Mobile Brokered\n"
            "Stone' and a GL account_number - none of which are personal data. A\n"
            "customer's email address is; an internal account code is not."
        ),
    ),
    "NB-DQ-HALT": JudgingGuide(
        ref="5.1.9",
        shape="graded",
        labels=("hard_stop", "soft_exit", "carries_on", "no_dq_evaluation"),
        bands=(3, 2, 0, 0),
        out_of_scope=("no_dq_evaluation",),
        evidence="notebook-code",
        classify=(
            "Decide what this notebook does when it finds BAD DATA.\n"
            "\n"
            "First decide whether it EVALUATES data quality at all. It does when\n"
            "it produces an answer that could be bad: a counted or measured\n"
            "quantity of null / duplicate / invalid / unmatched rows, a\n"
            "validation framework's result, or an assertion about the data.\n"
            "Silent cleaning is NOT an evaluation - a bare dropna, fillna or\n"
            "dropDuplicates fixes something without ever reporting how much, so\n"
            "there is no verdict for the notebook to act on.\n"
            "\n"
            "  hard_stop        - it raises, asserts or exits with an error, so\n"
            "                     the caller sees a failure.\n"
            "  soft_exit        - it calls notebook exit, which stops the\n"
            "                     notebook but reports success unless the caller\n"
            "                     inspects the value.\n"
            "  carries_on       - it prints, logs or displays the problem and\n"
            "                     keeps going.\n"
            "  no_dq_evaluation - it evaluates nothing, so there is no failing\n"
            "                     branch to judge. Most notebooks are this.\n"
            "\n"
            "Use 'undetermined' only when the definition could not be read or was\n"
            "truncated before you saw the relevant part.\n"
            "\n"
            "Where a notebook halts on one condition and only prints another,\n"
            "label it by the STRONGEST response it makes to a genuine data\n"
            "problem - the practice is 'critical failures halt', not 'every\n"
            "observation halts'.\n"
            "\n"
            "The rule fires on any of three things: a named framework (Great\n"
            "Expectations, PyDeequ, Soda, dbt), a bad-row count that is compared,\n"
            "or an assertion about data. The count path matches variable NAMES,\n"
            "so a count held in a dict key or a list - null_counts['x'],\n"
            "missing_cols - is invisible, and the plural 'counts' fails its own\n"
            "word boundary. It then credits a hard stop from any raise or assert\n"
            "anywhere in the notebook, even one guarding something unrelated, so\n"
            "read what the notebook does on the branch that actually finds bad\n"
            "data."
        ),
    ),
    "NB-DEADLETTER": JudgingGuide(
        ref="5.1.10",
        shape="graded",
        labels=("routes_bad_rows", "route_not_verifiable", "drops_bad_rows"),
        bands=(3, 1, 0),
        evidence="notebook-code",
        classify=(
            "This notebook separates good rows from bad. Decide what happens to\n"
            "the bad ones.\n"
            "\n"
            "  routes_bad_rows      - they are written somewhere durable and\n"
            "                         identifiable, so they can be inspected and\n"
            "                         replayed.\n"
            "  route_not_verifiable - a write exists but its destination is a\n"
            "                         variable or parameter you cannot resolve.\n"
            "  drops_bad_rows       - they are filtered out and never written,\n"
            "                         or only counted and printed.\n"
            "\n"
            "Use 'undetermined' when the notebook does no row-level validation.\n"
            "\n"
            "The rule identifies the sink from the DataFrame VARIABLE NAME and\n"
            "the literal table string, so 'df2.write' to a quarantine table is\n"
            "missed, and a source column called REJECT_CODE makes it think error\n"
            "handling exists. Follow the write."
        ),
    ),
    "NB-UNKNOWN-MONITOR": JudgingGuide(
        ref="5.4.4",
        shape="binary",
        labels=("monitored", "silently_accepted", "join_no_fallback",
                "no_dimensional_join"),
        compliant="monitored",
        out_of_scope=("no_dimensional_join",),
        evidence="notebook-code",
        classify=(
            "This check is about a lookup that MISSES. When a fact row's key\n"
            "finds no matching dimension row, something has to happen - and\n"
            "whatever happens, somebody should be able to find out how often.\n"
            "\n"
            "  monitored           - a fallback is substituted for unmatched rows\n"
            "                        AND the number of them is counted, logged,\n"
            "                        written or asserted on.\n"
            "  silently_accepted   - a fallback is substituted and nothing\n"
            "                        records how often. Note that counting AFTER\n"
            "                        the substitution does not count: it\n"
            "                        structurally always reports zero.\n"
            "  join_no_fallback    - a real fact-to-dimension join, but unmatched\n"
            "                        rows are simply dropped or left null. Not\n"
            "                        this check's failure, but worth recording.\n"
            "  no_dimensional_join - no fact-to-dimension lookup at all. Most\n"
            "                        notebooks are this.\n"
            "\n"
            "Use 'undetermined' only when the definition could not be read.\n"
            "\n"
            "A fallback means a value substituted BECAUSE THE LOOKUP MISSED -\n"
            "when(joined_col.isNull(), ...), coalesce(joined_col, 'Unknown'), a\n"
            "-1 key. A fillna at ingest, before any join, is ordinary cleaning\n"
            "and is not this. Substituting a RANDOM or invented value is the\n"
            "worst case: the fabricated rows cannot afterwards be told apart\n"
            "from real ones.\n"
            "\n"
            "Judge live code only. The rule strips comments and never sees\n"
            "markdown, so a commented-out fallback is not one.\n"
            "\n"
            "The rule opens on a flat regex over the code text -\n"
            "dim_ / dim. / dimension / fact_ / fct_ - which is both too wide\n"
            "(the ordinary words 'in fact ' or 'dimension' in a string open it)\n"
            "and too narrow (dimcustomer and factsales match nothing, because it\n"
            "requires a separator straight after). So an estate naming its tables\n"
            "'users' and 'posts' is never examined at all."
        ),
    ),
    "NB-POST-FAILURE-INTEGRITY": JudgingGuide(
        ref="9.3.4",
        shape="binary",
        labels=("verifies_layers_agree", "assumes_layers_agree"),
        compliant="verifies_layers_agree",
        evidence="notebook-code",
        classify=(
            "This notebook recovers, replays or reprocesses after a failure.\n"
            "Decide whether it PROVES the layers still agree before continuing.\n"
            "\n"
            "  verifies_layers_agree - it compares one layer against the next -\n"
            "                          row counts, key sets, checksums - and\n"
            "                          stops or repairs on a mismatch.\n"
            "  assumes_layers_agree  - it resumes without checking, so a partial\n"
            "                          write from the failed run survives.\n"
            "\n"
            "Use 'undetermined' when the notebook is not a recovery or replay\n"
            "path.\n"
            "\n"
            "The rule needs two medallion layer WORDS within 400 characters of\n"
            "each other, so it fires on table names like 'SilverMetadata' and\n"
            "'GoldConfig' that are not a layer comparison, and misses an estate\n"
            "calling its layers raw/curated/publish. Judge from what is compared."
        ),
    ),
    "NB-AUDIT-LOG": JudgingGuide(
        ref="4.6.4",
        shape="binary",
        labels=("writes_audit_log", "no_audit_log"),
        compliant="writes_audit_log",
        evidence="notebook-code",
        classify=(
            "Decide, for each notebook, whether it WRITES A RUN RECORD that could\n"
            "be queried later - rows processed, nulls found, errors seen, the run\n"
            "or batch it belongs to.\n"
            "\n"
            "  writes_audit_log - it persists such a record to a table or file.\n"
            "  no_audit_log     - it prints or displays them at most, or records\n"
            "                     nothing.\n"
            "\n"
            "The record must be WRITTEN. A notebook that computes a row count and\n"
            "prints it has left nothing behind. A notebook that READS an existing\n"
            "audit table is not writing one.\n"
            "\n"
            "The rule passes a notebook containing any write and any of a fixed\n"
            "list of words, so one that reads 'source_audit_log' and writes an\n"
            "unrelated table passes on the two matching independently. Follow the\n"
            "write to what it actually persists, and ignore the table name."
        ),
    ),

    # ---- dimensional modelling (table-detail) ------------------------------
    "TB-AUDITCOLS": JudgingGuide(
        ref="4.2.5",
        shape="ratio",
        labels=("has_lineage_columns", "no_lineage_columns"),
        compliant="has_lineage_columns",
        evidence="table-detail",
        classify=(
            "For each table decide whether it records WHERE ITS ROWS CAME FROM\n"
            "and WHEN THEY ARRIVED.\n"
            "\n"
            "  has_lineage_columns - at least one column records the load: when\n"
            "                        the row was created or last changed, which\n"
            "                        batch or run wrote it, or which source\n"
            "                        system it came from.\n"
            "  no_lineage_columns  - nothing in the table says how it was loaded.\n"
            "\n"
            "Use 'undetermined' for a table whose columns could not be read.\n"
            "\n"
            "A business date is not lineage. An order's order_date says when the\n"
            "order happened; a created_date says when the row was written. Judge\n"
            "the column's purpose, not its type.\n"
            "\n"
            "Rulings for shapes this estate is full of, so two readers agree:\n"
            "  * A source application's own created/modified timestamp DOES\n"
            "    count. It records the row's lifecycle, which is what the\n"
            "    practice asks for, even though it was set upstream.\n"
            "  * A run or error LOG table's own timestamps DO count - its rows\n"
            "    are written by the run they describe.\n"
            "  * SCD2 validity columns (valid_from, is_current) do NOT. They say\n"
            "    when the fact was true, not when the row was loaded.\n"
            "  * A watermark or control table's own columns do NOT. A table that\n"
            "    stores ingestion config is about the load; a job_id that is the\n"
            "    config row's own primary key records nothing about how that row\n"
            "    arrived.\n"
            "  * A measurement timestamp on telemetry does NOT. It says when the\n"
            "    reading was taken.\n"
            "\n"
            "The rule matches a four-branch regex with wildcard prefixes, so it\n"
            "over-matches in ways that are hard to predict: 'pickup_datetime'\n"
            "is credited because 'updat' straddles picku-p-dat-etime, and a bare\n"
            "'job_id' reads as a batch identifier. It has no token for 'lineage',\n"
            "so a WideWorldImporters 'LineageKey' - a direct reference to the ETL\n"
            "run that wrote the row - is missed entirely. You are shown every\n"
            "column, so decide from the whole table."
        ),
    ),
    "TB-DATEDIM": JudgingGuide(
        ref="4.5.7",
        shape="binary",
        labels=("date_dimension", "not_a_date_dimension"),
        compliant="date_dimension",
        evidence="table-detail",
        classify=(
            "Decide, for each table, whether it is a DATE DIMENSION - one row per\n"
            "day (or per period) carrying the calendar attributes reports group\n"
            "by: year, quarter, month, week, fiscal period, day-of-week, holiday\n"
            "flags.\n"
            "\n"
            "  date_dimension       - that table.\n"
            "  not_a_date_dimension - anything else, including a table that\n"
            "                         merely has date columns in it.\n"
            "\n"
            "The rule matches the names dimdate / calendar / datedim and nothing\n"
            "else, so 'DimTime', 'dt_reference' and 'D_Kalender' are all missed,\n"
            "while a table called 'dim_calendar_events' is credited without\n"
            "anyone checking it has calendar attributes at all. Judge from the\n"
            "columns."
        ),
    ),
    "TB-SCD2": JudgingGuide(
        ref="4.5.9",
        shape="ratio",
        labels=("scd2_complete", "scd2_incomplete", "keeps_no_history"),
        compliant="scd2_complete",
        out_of_scope=("keeps_no_history",),
        evidence="table-detail",
        classify=(
            "Some tables here keep HISTORY - more than one row per business key,\n"
            "each version valid for a period. For each such table, decide whether\n"
            "it carries everything needed to use that history.\n"
            "\n"
            "  scd2_complete    - a start of validity, an end of validity, AND a\n"
            "                     marker for which row is the current one.\n"
            "  scd2_incomplete  - it versions rows but a piece is missing.\n"
            "  keeps_no_history - an ordinary table with one row per key, or a\n"
            "                     table whose date pair is a VALIDITY PERIOD\n"
            "                     rather than row versioning: effective_date and\n"
            "                     expiration_date on a price list say when the\n"
            "                     price applied, which is a business fact, not a\n"
            "                     history of edits. Most tables are this, and it\n"
            "                     is a judgment rather than a gap.\n"
            "\n"
            "The distinction that decides most of these: a DIMENSION whose rows\n"
            "are versioned as the described thing changes is SCD2. Anything else\n"
            "carrying two dates is recording when something was true.\n"
            "\n"
            "Use 'undetermined' only for a table whose columns could not be read.\n"
            "Keeping that apart from 'keeps_no_history' is what lets the report\n"
            "say whether the estate was assessable.\n"
            "\n"
            "You are shown columns, not rows, so 'keeps history' is a judgment\n"
            "about the schema: a validity pair on a dimension, a version number,\n"
            "or a current flag alongside a business key that is plainly not\n"
            "unique.\n"
            "\n"
            "The rule matches column names against fixed spellings, so a suffixed\n"
            "variant like 'valid_from_instant' is not recognised and the table\n"
            "drops out of the population without being reported. Judge by what\n"
            "each column means, whatever it is named."
        ),
    ),
    "TB-SCD-STRATEGY": JudgingGuide(
        ref="4.5.8",
        shape="graded",
        labels=("history_kept", "history_partial", "overwrites_in_place"),
        bands=(3, 1, 0),
        evidence="table-detail",
        classify=(
            "For each DIMENSION, decide how it handles a changed attribute.\n"
            "\n"
            "  history_kept        - the old value survives: validity dates, a\n"
            "                        version or sequence, a current-row marker,\n"
            "                        or previous-value columns.\n"
            "  history_partial     - versioning is started but incomplete, so the\n"
            "                        history cannot be read back cleanly.\n"
            "  overwrites_in_place - a change replaces the old value and it is\n"
            "                        gone.\n"
            "\n"
            "Use 'undetermined' for anything that is not a dimension.\n"
            "\n"
            "Overwriting is a legitimate choice for an attribute nobody reports\n"
            "historically. This check asks whether the choice is IMPLEMENTED\n"
            "consistently, not whether history is always kept.\n"
            "\n"
            "The rule reads marker column names from a fixed list, so a house\n"
            "convention is read as no strategy at all. Judge from the columns'\n"
            "meaning."
        ),
    ),
    "TB-FACT-GRAIN": JudgingGuide(
        ref="4.5.2",
        shape="ratio",
        labels=("grain_is_clear", "grain_is_ambiguous"),
        compliant="grain_is_clear",
        evidence="table-detail",
        classify=(
            "For each FACT table, decide whether its schema says WHAT ONE ROW\n"
            "MEANS.\n"
            "\n"
            "  grain_is_clear     - the combination of keys and the date/time\n"
            "                       column tells you the grain: one row per\n"
            "                       order line per day, per customer per month.\n"
            "  grain_is_ambiguous - the columns do not settle it. A single\n"
            "                       foreign key with no time dimension, or a\n"
            "                       mixture that could be several grains at once.\n"
            "\n"
            "Use 'undetermined' for anything that is not a fact table.\n"
            "\n"
            "The rule counts grain components and needs at least two, matching\n"
            "keys by name, so 'customer_sk' is recognised but a key column named\n"
            "for the thing itself is not. You can see every column and its type -\n"
            "judge whether a reader of this schema would know the grain."
        ),
    ),
    "TB-DIM-DENORM": JudgingGuide(
        ref="4.5.4",
        shape="ratio",
        labels=("flat", "snowflaked"),
        compliant="flat",
        evidence="table-detail",
        classify=(
            "For each DIMENSION, decide whether its attributes are held on the\n"
            "table itself or spread across further lookup tables.\n"
            "\n"
            "  flat       - the attributes a report needs are on this table, even\n"
            "               if that means repeating a value across rows.\n"
            "  snowflaked - the table holds a key into another dimension, so a\n"
            "               report has to join twice to get an attribute.\n"
            "\n"
            "Use 'undetermined' for anything that is not a dimension.\n"
            "\n"
            "A key pointing at a FACT is not a snowflake, and neither is a\n"
            "self-reference such as a manager key on an employee dimension.\n"
            "\n"
            "The rule matches a key's name against other dimension names, so it\n"
            "reports a snowflake only when the target dimension happens to be in\n"
            "the same workspace under a matching name - and it once matched a\n"
            "table against itself. You can see the whole table list, so check\n"
            "whether the target actually exists."
        ),
    ),
    "TB-DEGENERATE-JUNK-DIM": JudgingGuide(
        ref="4.5.11",
        shape="ratio",
        labels=("modelled_appropriately", "should_use_a_junk_or_degenerate_dim"),
        compliant="modelled_appropriately",
        evidence="table-detail",
        classify=(
            "For each FACT table, decide whether its low-value columns are\n"
            "modelled sensibly.\n"
            "\n"
            "  modelled_appropriately             - transaction identifiers sit\n"
            "                                       on the fact as degenerate\n"
            "                                       dimensions, and flags are\n"
            "                                       either few or already\n"
            "                                       collapsed into a junk\n"
            "                                       dimension.\n"
            "  should_use_a_junk_or_degenerate_dim - several independent\n"
            "                                       low-cardinality flags are\n"
            "                                       spread across the fact, or a\n"
            "                                       key points at a dimension\n"
            "                                       that does not exist.\n"
            "\n"
            "Use 'undetermined' for anything that is not a fact table.\n"
            "\n"
            "Column TYPES are shown. A 'reason' column typed varchar(500) is\n"
            "prose, not a flag, and does not belong in a junk dimension. A key\n"
            "marked [in-relationship] is load-bearing and is not degenerate.\n"
            "\n"
            "The rule decides which columns are flags partly from the name\n"
            "suffix, so it can count a free-text column as a flag. Use the type."
        ),
    ),
    "TB-CONFORMED-DIM": JudgingGuide(
        ref="4.4.9",
        shape="ratio",
        labels=("shared_or_unique", "duplicated_across_stores"),
        compliant="shared_or_unique",
        evidence="table-detail",
        classify=(
            "This workspace holds tables in more than one store - the 'store='\n"
            "prefix on each record says which. For each DIMENSION, decide whether\n"
            "the same business concept has been rebuilt in another store.\n"
            "\n"
            "  shared_or_unique        - one home for this concept, or the second\n"
            "                            copy is a legitimately different thing.\n"
            "  duplicated_across_stores - the same concept exists twice, so two\n"
            "                            reports can disagree about a customer.\n"
            "\n"
            "Use 'undetermined' for anything that is not a dimension.\n"
            "\n"
            "A medallion layer is NOT duplication. The same concept appearing in\n"
            "bronze, silver and gold is one pipeline, not two masters, and the\n"
            "rule counts it as duplication - that is the main thing to correct.\n"
            "Nor is a staging copy a duplicate.\n"
            "\n"
            "The rule compares names with noise words stripped, so it also\n"
            "misses a genuine duplicate held under two different names. Compare\n"
            "the columns."
        ),
    ),

    # ---- audit trail --------------------------------------------------------
    "TB-CONFIG-SINGLE-STORE": JudgingGuide(
        ref="4.6.2",
        shape="ratio",
        labels=("in_the_main_config_store", "config_kept_elsewhere",
                "not_configuration"),
        compliant="in_the_main_config_store",
        out_of_scope=("not_configuration",),
        evidence="table-detail",
        classify=(
            "Some tables here hold CONFIGURATION THE LOAD FRAMEWORK READS -\n"
            "watermarks, ingestion driver rows, control parameters, schedules,\n"
            "settings a run looks up before it does its work. Work out which\n"
            "store holds most of them, then label each one.\n"
            "\n"
            "  in_the_main_config_store - configuration, sitting in that store.\n"
            "  config_kept_elsewhere    - configuration in a different store, so\n"
            "                             a change has to be made in two places.\n"
            "  not_configuration        - business data, a fact or dimension, a\n"
            "                             log, a report table. Most tables are\n"
            "                             this.\n"
            "\n"
            "Use 'undetermined' only for a table you believe IS configuration but\n"
            "whose store you cannot tell - the record shows 'store=?'. Keeping\n"
            "that apart from 'not_configuration' is what lets the report say\n"
            "whether the estate was assessable.\n"
            "\n"
            "The 'store=' prefix on each record says where it lives.\n"
            "\n"
            "Rulings, so two readers agree:\n"
            "  * A run-history or telemetry table - job_id, status, start_time,\n"
            "    duration, error_message - is a LOG, not configuration. It\n"
            "    records what happened; config says what should happen.\n"
            "  * A business-domain lookup - a name-to-email mapping, a product\n"
            "    reference list, an ontology definition - is not the load\n"
            "    framework's configuration, even though something reads it.\n"
            "  * Judge from the columns, not the table name. A table whose\n"
            "    columns are config_key / config_value, or watermark_column /\n"
            "    watermark_value, is configuration whatever it is called.\n"
            "\n"
            "The rule decides what is configuration from a fixed name vocabulary\n"
            "that contains the bare word 'job' but not the abbreviation 'ctl' -\n"
            "so on one estate it counted a job-telemetry table, missed sixteen\n"
            "ctl_*_ingestion driver tables, and named the wrong store as the\n"
            "config home on the strength of that one false match."
        ),
    ),
    "TB-AUDIT-QUERYABLE": JudgingGuide(
        ref="4.6.8",
        shape="ratio",
        labels=("queryable", "not_queryable"),
        compliant="queryable",
        evidence="table-detail",
        classify=(
            "Some tables here are AUDIT OR LOG tables - they record what a\n"
            "process did rather than what the business did. For each one, decide\n"
            "whether someone investigating an incident could actually query it.\n"
            "\n"
            "  queryable     - it has a timestamp, something identifying the run\n"
            "                  or row, and its content is in typed columns.\n"
            "  not_queryable - the detail is buried in a blob: a json, struct or\n"
            "                  binary column that has to be parsed before any\n"
            "                  question can be asked, or there is no timestamp.\n"
            "\n"
            "Use 'undetermined' for every table that is not an audit or log\n"
            "table.\n"
            "\n"
            "Column types are shown, which is what settles this. The rule picks\n"
            "the population from a name vocabulary, so a log table named for its\n"
            "subject is never examined and a business table with 'error' in its\n"
            "name is."
        ),
    ),
    "NB-AUDIT-IMMUTABLE": JudgingGuide(
        ref="4.6.5",
        shape="binary",
        labels=("append_only", "rewrites_history"),
        compliant="append_only",
        evidence="notebook-code",
        classify=(
            "This notebook writes to an audit or log table. Decide whether the\n"
            "record it leaves can be TRUSTED LATER.\n"
            "\n"
            "  append_only      - it only adds rows.\n"
            "  rewrites_history - it updates, deletes, truncates or overwrites\n"
            "                     the audit table, so what happened yesterday can\n"
            "                     change today.\n"
            "\n"
            "Use 'undetermined' when the notebook writes no audit or log table,\n"
            "or when the target is held in a variable you cannot resolve.\n"
            "\n"
            "Rewriting a staging or working table is fine and is not this check.\n"
            "Only the audit trail must be immutable.\n"
            "\n"
            "The rule recognises an audit target only when the table name matches\n"
            "its vocabulary and is a literal string, so a destructive rewrite of\n"
            "a log table named for its subject is invisible. Follow the write."
        ),
    ),
    "PL-AUDIT-IMMUTABLE": JudgingGuide(
        ref="4.6.5",
        shape="binary",
        labels=("append_only", "rewrites_history"),
        compliant="append_only",
        evidence="pipeline-definition",
        classify=(
            "This pipeline writes to an audit or log table. Decide whether the\n"
            "record it leaves can be TRUSTED LATER.\n"
            "\n"
            "  append_only      - it only adds rows.\n"
            "  rewrites_history - a Script activity, a Copy pre-copy script or an\n"
            "                     overwriting sink replaces what was already\n"
            "                     recorded.\n"
            "\n"
            "Use 'undetermined' when the pipeline writes no audit or log table,\n"
            "or when the work happens inside a stored procedure you cannot see.\n"
            "\n"
            "Rewriting a staging table is fine and is not this check.\n"
            "\n"
            "The rule only scores targets whose name matches its audit\n"
            "vocabulary, so a TRUNCATE of a log table named for its subject is\n"
            "missed. Read the SQL and the sink settings."
        ),
    ),
    "WS-RUN-HISTORY-EXPORT": JudgingGuide(
        ref="10.1.1",
        shape="best",
        labels=("writes_full_run_history", "writes_partial_run_history",
                "writes_no_run_history"),
        bands=(3, 2, 0),
        evidence="code-and-pipelines",
        classify=(
            "Fabric keeps run history only for a retention window. Decide, for\n"
            "each pipeline and notebook, whether it persists its own run record\n"
            "somewhere that outlives that window.\n"
            "\n"
            "  writes_full_run_history    - it writes a durable row carrying WHICH\n"
            "                               run it was, WHETHER it succeeded, and\n"
            "                               WHEN or HOW LONG it took.\n"
            "  writes_partial_run_history - it writes a run record missing one of\n"
            "                               those three.\n"
            "  writes_no_run_history      - it writes none.\n"
            "\n"
            "The strongest label wins, because one artefact doing this properly\n"
            "means the estate has run history. Most notebooks will not write one\n"
            "and that is normal - label them 'writes_no_run_history' rather than\n"
            "'undetermined'; only use 'undetermined' when the definition could\n"
            "not be read.\n"
            "\n"
            "The rule searches for a fixed set of table names and field words\n"
            "anywhere in the corpus, so a run log written to a table named for\n"
            "the solution is missed. Follow the write and read what it stores."
        ),
    ),

    # ---- semantic models and items -----------------------------------------
    "R-DAX-VAR": JudgingGuide(
        ref="14.1.4",
        shape="graded",
        labels=("well_written", "hard_to_maintain_or_slow", "broken"),
        bands=(3, 1, 0),
        evidence="model-measures",
        classify=(
            "Judge each model's SUBSTANTIAL measures and label the MODEL by how\n"
            "they read overall.\n"
            "\n"
            "A measure is SUBSTANTIAL when it does more than aggregate: it\n"
            "branches, manipulates filter context, declares VAR/RETURN, or\n"
            "composes several steps. A bare aggregation is not substantial\n"
            "however long it is - SUM('Some Very Long Table Name'[Amount]) is\n"
            "still a bare SUM. Ignore the character count; the rule uses a\n"
            "60-character threshold and so assesses long-named simple measures\n"
            "while skipping short complex ones.\n"
            "\n"
            "  well_written             - substantial measures name their\n"
            "                             intermediate results with VAR instead\n"
            "                             of repeating them, and do not repeat\n"
            "                             the same block across several measures.\n"
            "  hard_to_maintain_or_slow - the same substantial expression is\n"
            "                             pasted more than once, EITHER inside\n"
            "                             one measure OR across several measures\n"
            "                             in the model; or the same number is\n"
            "                             computed two different ways; or\n"
            "                             iterators are nested inside one\n"
            "                             another; or CALCULATE wraps FILTER over\n"
            "                             an entire table where a column filter\n"
            "                             would do.\n"
            "  broken                   - a measure cannot work at all: a syntax\n"
            "                             error, or a reference to a table or\n"
            "                             column the model does not contain.\n"
            "                             This outranks the other two - do not\n"
            "                             call a model well_written because its\n"
            "                             broken measure happens to use VAR.\n"
            "\n"
            "Cross-measure duplication counts, and is usually the bigger problem:\n"
            "five measures pasted from one another drift apart, and the drift is\n"
            "the defect. A shared bare SUM is not duplication.\n"
            "\n"
            "Use 'undetermined' for a model with no substantial measures, one\n"
            "whose definition could not be read, and one whose 'measures' are\n"
            "really a translation table - a SWITCH over USERCULTURE returning\n"
            "static strings computes nothing and must not be judged as DAX.\n"
            "\n"
            "Length alone is not a fault: a genuinely complex calculation may be\n"
            "long and clear. Read the DAX, and ignore the measure's name."
        ),
    ),
    "SM-COLUMN-SHAPE": JudgingGuide(
        ref="14.2.3",
        shape="ratio",
        labels=("compresses_well", "carries_expensive_columns"),
        compliant="compresses_well",
        evidence="model-columns",
        classify=(
            "A semantic model stores each column compressed, and compression\n"
            "works on repeated values. A column with a different value in every\n"
            "row cannot compress, and on a large table that is where the memory\n"
            "goes. Decide whether this model carries such columns unnecessarily.\n"
            "\n"
            "  compresses_well           - no per-row-unique columns beyond the\n"
            "                              ones the model needs to join on.\n"
            "  carries_expensive_columns - it holds columns that are unique per\n"
            "                              row and nothing joins on them.\n"
            "\n"
            "Use 'undetermined' when no columns are declared.\n"
            "\n"
            "THE QUESTION IS CARDINALITY, NOT TYPE. There is no data type that\n"
            "says 'unique per row' - an order id and a region id are both\n"
            "int64. Judge what the column identifies:\n"
            "\n"
            "  * A key identifying a TRANSACTION - an order, an invoice, a\n"
            "    ticket, an event - has one value per row. Expensive.\n"
            "  * A key identifying a DIMENSION member - a product, a customer, a\n"
            "    region, a date - repeats across many rows. NOT expensive, even\n"
            "    when no relationship joins on it. A dangling dimension key is a\n"
            "    modelling gap, not a compression cost, and this check is about\n"
            "    compression.\n"
            "  * A GUID is expensive whatever it identifies.\n"
            "  * A timestamp with a time component repeats far less than a date.\n"
            "    Expensive on a large fact; a column literally named 'date' on a\n"
            "    reference table is not, whatever type it was given.\n"
            "  * Unbounded free text - prose, JSON, XML - is expensive. Note\n"
            "    that varchar(8000) and nvarchar(16777216) are just the Fabric\n"
            "    and Snowflake defaults for 'a string', not evidence of length.\n"
            "  * ETL audit columns - load_timestamp, batch_id, inserted_on - do\n"
            "    NOT count. They are deliberate, and the check's own remediation\n"
            "    says not to act on them.\n"
            "\n"
            "A column marked [in-relationship] is load-bearing - never expensive.\n"
            "A column marked [is_key] is the model DECLARING it identifies a row,\n"
            "so treat it as a claim to check rather than proof: a 'Month' column\n"
            "marked is_key has twelve values and costs nothing.\n"
            "\n"
            "Markers appear together, as [in-relationship, hidden]. Read the\n"
            "whole bracket.\n"
            "\n"
            "The rule decides mainly from the column name's last word and from\n"
            "is_key, so it accuses a model of waste for declaring a low-cardinality\n"
            "column as its key, and counts every unjoined dimension key as a\n"
            "per-row identifier. Judge by what the column identifies."
        ),
    ),
    "WS-MONITOR-TREND": JudgingGuide(
        ref="10.4.4",
        shape="best",
        labels=("trends_over_time", "date_table_only", "current_state_only"),
        bands=(3, 1, 0),
        evidence="model-measures",
        classify=(
            "Decide whether each model can answer 'is this GETTING BETTER OR\n"
            "WORSE' rather than only 'what is it now'.\n"
            "\n"
            "  trends_over_time   - it has a date table AND measures that compare\n"
            "                       periods - year to date, same period last\n"
            "                       year, a moving window, a period-over-period\n"
            "                       difference.\n"
            "  date_table_only    - a date table exists but nothing compares\n"
            "                       periods.\n"
            "  current_state_only - neither.\n"
            "\n"
            "The strongest label wins: one model that trends properly means the\n"
            "estate can trend.\n"
            "\n"
            "Use 'undetermined' when a model's definition could not be read.\n"
            "\n"
            "The rule finds the date table only by the words date or calendar and\n"
            "looks for a fixed list of time-intelligence function names, so a\n"
            "date table called 'Periods' is missed and a hand-written period\n"
            "comparison is not counted. Read the measures."
        ),
    ),
    "WS-GOLD-FRESHNESS": JudgingGuide(
        ref="5.4.7",
        shape="ratio",
        labels=("serving_and_fresh", "serving_but_stale", "not_a_serving_item"),
        compliant="serving_and_fresh",
        out_of_scope=("not_a_serving_item",),
        evidence="workspace-items",
        classify=(
            "Some items here are SERVING items - what reports and consumers read\n"
            "from, the end of the pipeline rather than the middle. For each one,\n"
            "decide whether it has been refreshed recently enough to be trusted.\n"
            "\n"
            "  serving_and_fresh  - a serving item whose last run is recent:\n"
            "                       within about two days, or a few days for a\n"
            "                       daily batch. Data several days old is stale.\n"
            "  serving_but_stale  - a serving item last refreshed long enough ago\n"
            "                       that a consumer is reading out-of-date\n"
            "                       figures.\n"
            "  not_a_serving_item - a store, a load job, a scratch or training\n"
            "                       artefact, a report, a duplicate. Most items\n"
            "                       in a workspace are this, and it is a\n"
            "                       judgment, not a gap - use it rather than\n"
            "                       'undetermined'.\n"
            "\n"
            "Use 'undetermined' ONLY for an item you believe IS serving but whose\n"
            "last run cannot be read. A missing timestamp is absence of evidence,\n"
            "not evidence of staleness, and must not count against the estate.\n"
            "Keeping that separate from 'not_a_serving_item' is what lets the\n"
            "report say whether the estate was assessable.\n"
            "\n"
            "'read by N report(s)' is the strongest signal available: a model a\n"
            "report reads is a serving surface whatever it is called, and a model\n"
            "nothing reads is not. Use it before you use the name.\n"
            "\n"
            "If NOTHING here is a serving item - a training or sandbox estate\n"
            "with no production layer - label everything 'not_a_serving_item'.\n"
            "That produces no score and the rule's verdict stands, which is the\n"
            "honest outcome. Do not manufacture a stale finding to avoid it.\n"
            "\n"
            "The rule picks serving items from name words like gold, curated and\n"
            "mart - and 'semantic', which every SemanticModel matches by virtue\n"
            "of its own item type, so it selects things called 'TestSemanticModel'\n"
            "and 'EmptySemanticModel'. It also matches a training exercise called\n"
            "'gold_sales'. Judge from what the item is for and who reads it."
        ),
    ),
    "WS-DDM": JudgingGuide(
        ref="6.2.3",
        shape="ratio",
        labels=("sensitive_all_masked", "sensitive_left_in_clear",
                "nothing_sensitive"),
        compliant="sensitive_all_masked",
        out_of_scope=("nothing_sensitive",),
        evidence="table-detail",
        classify=(
            "Decide, for each table, whether the data a person could be harmed\n"
            "by is hidden from someone who should not see it.\n"
            "\n"
            "  sensitive_all_masked    - holds sensitive columns and every one is\n"
            "                            marked [MASKED].\n"
            "  sensitive_left_in_clear - holds sensitive columns and at least one\n"
            "                            is not masked.\n"
            "  nothing_sensitive       - holds no personal data. Most tables are\n"
            "                            this, and it is a judgment rather than a\n"
            "                            gap.\n"
            "\n"
            "Use 'undetermined' for a table whose columns could not be read, and\n"
            "for any table NOT in a Warehouse - masking is a Warehouse feature,\n"
            "so a Lakehouse table cannot have it and must not be failed for that.\n"
            "The 'store=' prefix gives the store and its kind.\n"
            "\n"
            "What counts as sensitive, so two readers agree:\n"
            "  * A person's NAME is sensitive on its own - customer_name,\n"
            "    employee name, supervisor.\n"
            "  * A person's contact details, identifiers, health or pay data.\n"
            "  * A person's address IS. A supplier's trading address, a\n"
            "    warehouse address, or a geography dimension of cities and\n"
            "    postcodes is a business fact and is NOT.\n"
            "  * An account or reference number identifying a THING - a GL code,\n"
            "    a product code, an order number - is not personal data.\n"
            "  * Precise location tied to an individual journey is sensitive even\n"
            "    in a public sample dataset.\n"
            "\n"
            "The rule reads column names against a fixed vocabulary. On a real\n"
            "estate it found five genuinely sensitive columns and missed four\n"
            "tables entirely - 'Employee Name' and 'CustomerName' match nothing\n"
            "in its list, and 'PostalCode' fails because the pattern expects an\n"
            "underscore. Its hits are usually right; what it cannot do is find\n"
            "the ones named differently, so read every column rather than\n"
            "checking its work."
        ),
    ),
    "OPS-MONITOR-REFRESH": JudgingGuide(
        ref="10.4.2",
        shape="best",
        labels=("monitoring_hourly_or_better", "monitoring_several_times_a_day",
                "monitoring_daily_or_slower"),
        bands=(3, 1, 0),
        evidence="workspace-items",
        classify=(
            "Decide which of these items carry MONITORING DATA - telemetry,\n"
            "run logs, metrics, SLA or heartbeat data that someone watches to\n"
            "know the estate is healthy - and then read off how often each one\n"
            "actually refreshes.\n"
            "\n"
            "Each record states 'median gap between runs = N h', computed from\n"
            "the observed run history. You do not calculate it - read it.\n"
            "\n"
            "  monitoring_hourly_or_better    - monitoring data, gap about 1 hour\n"
            "                                   or less.\n"
            "  monitoring_several_times_a_day - monitoring data, gap of a few\n"
            "                                   hours.\n"
            "  monitoring_daily_or_slower     - monitoring data, gap of a day or\n"
            "                                   more, so a problem can go unseen\n"
            "                                   for a working day.\n"
            "\n"
            "Use 'undetermined' for every item that is NOT monitoring data, and\n"
            "for monitoring data whose cadence could not be measured. Most items\n"
            "in a workspace are not monitoring data.\n"
            "\n"
            "The strongest label wins: one properly refreshed monitoring item\n"
            "means the estate is observable.\n"
            "\n"
            "The rule selects items by name words - monitor, telemetry, audit,\n"
            "log - so a config table called 'monitor_config' is measured as if\n"
            "it were monitoring data, and in a Data Logs workspace with no\n"
            "matching name it falls back to measuring EVERY item. Judge what the\n"
            "item is for."
        ),
    ),
    "WS-METADATA-WRITE": JudgingGuide(
        ref="4.6.3",
        shape="ratio",
        labels=("framework_identity", "named_individual"),
        compliant="framework_identity",
        evidence="workspace-roles",
        classify=(
            "These are the people and identities who can write to this\n"
            "workspace. If it holds a METADATA STORE - the config, control,\n"
            "watermark and schedule tables a framework reads - then only the\n"
            "framework's own identity should be able to change them, because a\n"
            "hand edit to a watermark silently changes what every later run\n"
            "loads.\n"
            "\n"
            "First decide whether there IS a metadata store. Each record lists\n"
            "the config-shaped tables found. If none of them is really a\n"
            "metadata store, label EVERY assignment 'undetermined' - there is\n"
            "nothing here to protect and the workspace must not be marked down.\n"
            "\n"
            "Otherwise label each assignment:\n"
            "\n"
            "  framework_identity - a service principal, managed identity or\n"
            "                       application: something that runs the\n"
            "                       pipeline rather than someone who logs in.\n"
            "                       A team security group counts here too.\n"
            "  named_individual   - a specific person, who can hand-edit the\n"
            "                       control tables.\n"
            "\n"
            "Read-only roles are not a risk - label them 'undetermined'.\n"
            "\n"
            "The rule decides a metadata store exists whenever a table name\n"
            "matches a vocabulary that includes the word 'job', so a fact table\n"
            "of job applications convinces it. That is the judgment being asked\n"
            "of you; who holds which role is read from Fabric and is reliable."
        ),
    ),

    # ---- validation in code -------------------------------------------------
    # These refs read as needing row-level data - "whether FK values resolve",
    # "record-count reconciliation" - and that is what the checklist POINT is
    # about. The checks are not. Each asks whether the code CONTAINS the
    # validation, and the code is in the knowledge base, so each is judgeable.
    "NB-FK-INTEGRITY": JudgingGuide(
        ref="5.3.2",
        shape="binary",
        labels=("checks_the_join_resolved", "assumes_the_join_resolved"),
        compliant="checks_the_join_resolved",
        evidence="notebook-code",
        classify=(
            "This notebook joins one table to another on a key. Decide whether\n"
            "it CHECKS that the join found anything.\n"
            "\n"
            "  checks_the_join_resolved  - it counts or inspects the rows that\n"
            "                              did not match - an anti-join, a null\n"
            "                              check on the joined column, a\n"
            "                              comparison of row counts before and\n"
            "                              after - and does something about it.\n"
            "  assumes_the_join_resolved - it joins and moves on, so silently\n"
            "                              dropped or unmatched rows go unnoticed.\n"
            "\n"
            "Use 'undetermined' when the notebook performs no such join.\n"
            "\n"
            "An inner join that silently drops unmatched rows is the failure this\n"
            "is about. Logging a count is enough to pass - the practice is\n"
            "noticing, not fixing.\n"
            "\n"
            "The rule matches vocabulary in the code, so validation written with\n"
            "unremarkable variable names is missed. Read what the code does."
        ),
    ),
    "NB-FACT-DIM-RI": JudgingGuide(
        ref="4.5.12",
        shape="binary",
        labels=("validates_dimension_keys", "loads_unvalidated"),
        compliant="validates_dimension_keys",
        evidence="notebook-code",
        classify=(
            "This notebook loads a fact table whose rows carry keys into\n"
            "dimensions. Decide whether it PROVES those keys point at real\n"
            "dimension rows before writing.\n"
            "\n"
            "  validates_dimension_keys - it checks for keys with no matching\n"
            "                             dimension row, and rejects, quarantines\n"
            "                             or substitutes an unknown member.\n"
            "  loads_unvalidated        - it writes the fact and whether the keys\n"
            "                             resolve is never established.\n"
            "\n"
            "Use 'undetermined' when the notebook loads no fact table, or does no\n"
            "fact-to-dimension join.\n"
            "\n"
            "The rule needs dim_/fact_ naming in the code to look at all, so a\n"
            "correctly modelled estate using 'customers' and 'sales' is never\n"
            "examined. Judge from what the join joins."
        ),
    ),
    "NB-RECON-COUNT": JudgingGuide(
        ref="5.2.5",
        shape="binary",
        labels=("reconciles_counts", "writes_without_counting"),
        compliant="reconciles_counts",
        evidence="notebook-code",
        classify=(
            "This notebook writes data. Decide whether it CHECKS THAT WHAT WENT\n"
            "IN CAME OUT.\n"
            "\n"
            "  reconciles_counts       - it compares a count read against a count\n"
            "                            written, or against an expected figure,\n"
            "                            and reacts when they differ.\n"
            "  writes_without_counting - it writes and never compares, so losing\n"
            "                            half the rows would look like success.\n"
            "\n"
            "Use 'undetermined' when the notebook writes no data.\n"
            "\n"
            "Printing a count is not reconciliation - two numbers must be\n"
            "COMPARED. But logging a mismatch counts: the practice is detecting\n"
            "it, not halting.\n"
            "\n"
            "The rule matches count-comparison vocabulary, so a reconciliation\n"
            "written plainly is missed and a stray '.count()' can pass. Read the\n"
            "comparison."
        ),
    ),
    "NB-CROSS-RECON": JudgingGuide(
        ref="5.3.6",
        shape="binary",
        labels=("reconciles_across_sources", "combines_without_checking"),
        compliant="reconciles_across_sources",
        evidence="notebook-code",
        classify=(
            "This notebook reads from more than one source and combines them.\n"
            "Decide whether it CHECKS THE SOURCES AGREE.\n"
            "\n"
            "  reconciles_across_sources - it compares them - counts, totals, key\n"
            "                              sets, a control figure - and reacts to\n"
            "                              a mismatch.\n"
            "  combines_without_checking - it unions or joins them and assumes\n"
            "                              they agree.\n"
            "\n"
            "Use 'undetermined' when the notebook reads only one source.\n"
            "\n"
            "Reading a lookup table is not a second source in this sense. This is\n"
            "about two systems that should tell the same story.\n"
            "\n"
            "The rule matches vocabulary, so a comparison written with ordinary\n"
            "variable names is missed. Read the comparison."
        ),
    ),
    "NB-MERGE-VALID": JudgingGuide(
        ref="5.3.9",
        shape="binary",
        labels=("validates_the_merge", "merges_blind"),
        compliant="validates_the_merge",
        evidence="notebook-code",
        classify=(
            "This notebook runs a MERGE or upsert. Decide whether it CHECKS WHAT\n"
            "THE MERGE DID.\n"
            "\n"
            "  validates_the_merge - it reads back how many rows were inserted,\n"
            "                        updated or deleted - from the operation's\n"
            "                        own metrics, or by counting before and after\n"
            "                        - and compares that with what was expected.\n"
            "  merges_blind        - it merges and carries on, so a condition\n"
            "                        that updated every row would look normal.\n"
            "\n"
            "Use 'undetermined' when the notebook performs no merge or upsert.\n"
            "\n"
            "A merge with a wrong join condition can quietly rewrite an entire\n"
            "table, which is why reading the operation's metrics counts as\n"
            "validation even without an explicit assertion.\n"
            "\n"
            "The rule matches merge and count vocabulary. Read the code."
        ),
    ),
    "NB-LAYER-RECON": JudgingGuide(
        ref="5.4.6",
        shape="binary",
        labels=("reconciles_the_hop", "promotes_without_checking"),
        compliant="reconciles_the_hop",
        evidence="notebook-code",
        classify=(
            "This notebook promotes data from one layer to the next - raw to\n"
            "cleaned, cleaned to serving, however this estate names them. Decide\n"
            "whether it CHECKS THE TWO LAYERS STILL AGREE.\n"
            "\n"
            "  reconciles_the_hop        - it compares the layers across the hop:\n"
            "                              row counts, key sets, a control total,\n"
            "                              accounting for rows deliberately\n"
            "                              filtered.\n"
            "  promotes_without_checking - it reads one layer, writes the next,\n"
            "                              and nothing verifies the result.\n"
            "\n"
            "Use 'undetermined' when the notebook is not a layer promotion.\n"
            "\n"
            "A drop in row count is not automatically wrong - filtering is often\n"
            "the point. The question is whether anything CHECKS.\n"
            "\n"
            "The rule needs two medallion layer words near each other in the\n"
            "code, so an estate naming its layers raw / curated / publish is\n"
            "never examined. Judge from what is read and what is written."
        ),
    ),
    "NB-RECONCILE": JudgingGuide(
        ref="7.2.6",
        shape="binary",
        labels=("reconciles_source_to_target", "no_reconciliation"),
        compliant="reconciles_source_to_target",
        evidence="notebook-code",
        classify=(
            "This notebook moves data that may be financial. Decide whether it\n"
            "PROVES THE TARGET MATCHES THE SOURCE.\n"
            "\n"
            "  reconciles_source_to_target - it compares a control figure between\n"
            "                                source and target - a row count, a\n"
            "                                sum of amounts, a hash total - and\n"
            "                                reacts to a difference.\n"
            "  no_reconciliation           - it moves the data and nothing checks.\n"
            "\n"
            "Use 'undetermined' when the notebook moves no data.\n"
            "\n"
            "For money, a total that does not tie is the failure that matters -\n"
            "an amount sum is stronger evidence than a row count, but either\n"
            "counts.\n"
            "\n"
            "The rule matches reconciliation vocabulary, so a comparison written\n"
            "plainly is missed. Read the comparison."
        ),
    ),
    "PL-RECONCILE": JudgingGuide(
        ref="7.2.6",
        shape="binary",
        labels=("reconciles_source_to_target", "no_reconciliation"),
        compliant="reconciles_source_to_target",
        evidence="pipeline-definition",
        classify=(
            "This pipeline moves data that may be financial. Decide whether it\n"
            "PROVES THE TARGET MATCHES THE SOURCE.\n"
            "\n"
            "  reconciles_source_to_target - a Lookup, Script or condition\n"
            "                                compares a control figure across the\n"
            "                                move - a row count, a sum of\n"
            "                                amounts - and the pipeline reacts to\n"
            "                                a difference.\n"
            "  no_reconciliation           - it copies and nothing checks.\n"
            "\n"
            "Use 'undetermined' when the pipeline moves no data, or when the work\n"
            "happens inside a notebook or stored procedure you cannot see.\n"
            "\n"
            "A Lookup that reads a count but feeds nothing is not a\n"
            "reconciliation - the comparison has to happen and matter.\n"
            "\n"
            "The rule matches vocabulary in the definition, so a step named\n"
            "'Step_4' that does exactly this is missed. Follow the activities."
        ),
    ),
    "NB-MONEY-PRECISION": JudgingGuide(
        ref="5.5.2",
        shape="graded",
        labels=("fixed_point_and_currency_checked", "fixed_point_only",
                "floating_point_money"),
        bands=(3, 2, 0),
        evidence="notebook-code",
        classify=(
            "This notebook handles monetary values. Decide how it TYPES them.\n"
            "\n"
            "  fixed_point_and_currency_checked - money is decimal / fixed-point,\n"
            "                                     and currency codes are\n"
            "                                     validated against an allowed\n"
            "                                     set or pattern.\n"
            "  fixed_point_only                 - money is fixed-point but\n"
            "                                     currency is unchecked.\n"
            "  floating_point_money             - money is cast to double or\n"
            "                                     float, where binary floating\n"
            "                                     point cannot hold cents exactly\n"
            "                                     and the error compounds across\n"
            "                                     a sum.\n"
            "\n"
            "Use 'undetermined' when the notebook handles no monetary values, or\n"
            "when it never casts them so the type is decided elsewhere.\n"
            "\n"
            "Decide whether the value IS money. The rule matches column names\n"
            "like amount / price / cost / rate, so a conversion 'rate' or a\n"
            "'count_amount' is treated as currency. Read what the value means."
        ),
    ),
    "NB-KEY-QUALITY": JudgingGuide(
        ref="5.5.6",
        shape="binary",
        labels=("validates_keys", "writes_unvalidated_keys"),
        compliant="validates_keys",
        evidence="notebook-code",
        classify=(
            "This notebook writes a table with a key. Decide whether it CHECKS\n"
            "THE KEY IS SOUND before writing.\n"
            "\n"
            "  validates_keys          - it checks the key for nulls, for\n"
            "                            duplicates, or both - a distinct count\n"
            "                            against a total, a dropDuplicates, a\n"
            "                            null filter or assertion on the key.\n"
            "  writes_unvalidated_keys - it writes and the key's soundness is\n"
            "                            never established.\n"
            "\n"
            "Use 'undetermined' when the notebook writes no table, or when what\n"
            "it writes has no meaningful key - an append-only event log does not\n"
            "need one.\n"
            "\n"
            "A dropDuplicates on the key counts: enforcing uniqueness is as good\n"
            "as verifying it.\n"
            "\n"
            "The rule matches validation vocabulary in the code, so a check\n"
            "written with ordinary variable names is missed. Read the code."
        ),
    ),
    "NB-GRAIN-UNIQUE": JudgingGuide(
        ref="5.4.9",
        shape="binary",
        labels=("grain_enforced", "duplicates_possible"),
        compliant="grain_enforced",
        evidence="notebook-code",
        classify=(
            "This notebook writes a fact table. Decide whether it PREVENTS TWO\n"
            "ROWS FOR THE SAME THING.\n"
            "\n"
            "  grain_enforced      - it dedupes on the grain, merges on the grain\n"
            "                        rather than appending, or checks the\n"
            "                        distinct count against the row count.\n"
            "  duplicates_possible - it appends without any of that, so a rerun\n"
            "                        or a late-arriving batch doubles rows and\n"
            "                        every total silently inflates.\n"
            "\n"
            "Use 'undetermined' when the notebook writes no fact table.\n"
            "\n"
            "Overwriting the whole table also prevents duplicates - it is a\n"
            "legitimate way to enforce the grain.\n"
            "\n"
            "The rule needs dim_/fact_ naming to identify the fact write, so a\n"
            "correctly modelled estate is never examined. Judge from what the\n"
            "table holds."
        ),
    ),
    "SM-FK-SURROGATE": JudgingGuide(
        ref="5.4.1",
        shape="binary",
        labels=("relationships_defined", "tables_not_wired_together"),
        compliant="relationships_defined",
        evidence="model-columns",
        classify=(
            "A semantic model has to declare how its tables relate, or every\n"
            "report has to do the joining itself and no two reports need agree.\n"
            "Decide whether this model is WIRED UP.\n"
            "\n"
            "  relationships_defined     - its tables are connected by declared\n"
            "                              relationships covering the ones that\n"
            "                              need to join.\n"
            "  tables_not_wired_together - it has several tables that ought to\n"
            "                              join and few or no relationships, so\n"
            "                              they are islands.\n"
            "\n"
            "Use 'undetermined' for a single-table model - there is nothing to\n"
            "relate - and when the definition could not be read.\n"
            "\n"
            "Columns marked [in-relationship] are the wired ones. A model where\n"
            "almost no column carries that marker is not wired, however many\n"
            "key-shaped columns it has.\n"
            "\n"
            "The rule counts declared relationships against table count, and\n"
            "cannot tell that some tables are unrelated BY DESIGN - a disconnected\n"
            "slicer, a parameter table, a what-if table, a calculation group.\n"
            "Those are correct modelling and must not read as missing wiring.\n"
            "Judge from the column names whether a table was meant to join."
        ),
    ),
}


def guide_for(check_id: str) -> JudgingGuide | None:
    """The judging guide for a check, or ``None`` when it has none yet."""
    return GUIDES.get(check_id)
