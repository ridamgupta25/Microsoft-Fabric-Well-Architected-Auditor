"""The advisory checklist - the single source of truth for the advisory flag.

A check lands here when its deterministic verdict is a weak proxy, for one of
two reasons:

* **The data is not there.** Row-level reconciliation, key matching and value
  precision need rows, which the tool never profiles. Nobody - rule or reader -
  can answer these from the knowledge base; the honest verdict is often "cannot
  determine".
* **The data is there but the rule reads it as text.** The check matches a name,
  an identifier or a keyword and cannot tell what the thing *is*: a date
  dimension called ``DimTime``, a Key Vault reference that looks like a
  password, a plant named Mobile that looks like a phone number. A reader with
  the same evidence does better.

These refs are kept **out of the deterministic scorecard** and routed to a
separate Advisory report. The deterministic score stays reproducible.

**This module is the only place to edit.** Each entry carries the ref's judging
theme and why its rule is weak; both the split and the themed bundle read from
here, so a ref cannot be advisory-but-unthemed or themed-but-not-advisory. A
test asserts every ref is a real registered check's ref, so a typo fails fast.
"""
from __future__ import annotations

#: Shown for a check whose ref is in the advisory checklist.
ADVISORY_LABEL = "Advisory review"

#: ``theme -> the one question every check in it asks``. A judging pass covers
#: one theme, so a reviewer holds a single question in mind rather than
#: switching between grain, DAX style and PII masking.
THEMES: dict[str, str] = {
    "referential-integrity": "Do fact foreign keys actually resolve to dimension rows?",
    "reconciliation": "Are counts and totals reconciled between source, layers and target?",
    "dimensional-modelling": "Is the model shaped the way a star schema should be?",
    "data-quality": "Are keys, grain and values sound at the record level?",
    "code-quality": "Is the code and DAX written the way a reviewer would want?",
    "pipeline-reliability": "When a pipeline fails, does it retry, alert, quarantine and stop?",
    "load-patterns": "Is data loaded incrementally and staged, or bluntly reloaded?",
    "security-privacy": "Are secrets and personal data handled the way they must be?",
    "audit-lineage": "Is there an audit trail, and is it protected and queryable?",
    "monitoring": "Is the estate observable - monitoring data fresh and trendable?",
}

#: ``ref -> (theme, why the deterministic verdict is weak)``.
#: THIS is the list to edit: add a ref to route it to the Advisory report,
#: delete it to send it back to the deterministic score.
ADVISORY_CHECKLIST: dict[str, tuple[str, str]] = {
    # -- the checklist point is data-level; the CHECK reads code ---------------
    # These read as needing row values, and the checklist *point* does. The
    # checks do not: each asks whether the notebook or pipeline CONTAINS the
    # validation, which is a question about code, and the code is in the KB.
    # The reason text says what the check does, so nobody concludes twice over
    # that these cannot be judged.
    "5.3.2":  ("referential-integrity", "Whether a join is verified is matched from code vocabulary."),
    "5.4.1":  ("referential-integrity", "Relationship coverage is judged structurally, not from the data."),
    "4.5.12": ("referential-integrity", "Fact-dimension validation is gated on dim_/fact_ naming in the code."),
    "5.2.5":  ("reconciliation", "Count reconciliation is matched from code vocabulary."),
    "5.3.6":  ("reconciliation", "Cross-source comparison is matched from code vocabulary."),
    "5.3.9":  ("reconciliation", "Post-merge validation is matched from code vocabulary."),
    "5.4.6":  ("reconciliation", "The layer hop is found by medallion words near each other."),
    "7.2.6":  ("reconciliation", "Source-to-target controls are matched by name, not followed."),
    "5.5.2":  ("data-quality", "Money columns are identified by name, so a 'rate' reads as currency."),
    "5.5.6":  ("data-quality", "Key validation is matched from code vocabulary."),
    "5.4.9":  ("data-quality", "The fact write is gated on dim_/fact_ naming in the code."),

    # -- the data is there, the rule reads it as text ---------------------------
    # Dimensional modelling: columns and relationships are in the KB.
    "4.5.2":  ("dimensional-modelling", "Grain is judged from column names, not declared anywhere."),
    "4.5.4":  ("dimensional-modelling", "Snowflake links are matched by name, so a table matches itself."),
    "4.5.8":  ("dimensional-modelling", "SCD strategy is inferred from marker column names."),
    "4.5.9":  ("dimensional-modelling", "SCD2 columns are matched by spelling; effective_from reads as non-standard."),
    "4.5.11": ("dimensional-modelling", "Degenerate and junk dimensions are a design judgment on columns."),
    "4.4.9":  ("dimensional-modelling", "Repeated concept names count as duplication, including medallion layers."),
    "4.2.5":  ("dimensional-modelling", "Audit columns are matched by name across a sampled population."),
    "4.5.1":  ("dimensional-modelling", "Star schema falls back to fact_/dim_ prefixes when no relationship is declared."),
    "4.5.7":  ("dimensional-modelling", "A date dimension is matched by name, so DimTime is missed."),

    # Pipelines: the definition is in the KB.
    "2.4.6":  ("pipeline-reliability", "Rerun-safety matches anywhere in the definition, so a batch_id column passes."),
    "2.4.4":  ("pipeline-reliability", "A dead-letter path is credited from an activity's display name."),
    "2.4.5":  ("pipeline-reliability", "Notification is matched on name, so Slack and PagerDuty are missed."),
    "10.1.4": ("pipeline-reliability", "Failure alerting uses the same name list, not the failure edge."),
    "5.1.9":  ("pipeline-reliability", "The DQ gate is located by activity name."),
    "9.3.4":  ("pipeline-reliability", "Post-failure integrity needs two literal layer words near each other."),
    "2.2.1":  ("load-patterns", "The full-reload exemption is inferred from the pipeline's name."),
    "2.2.2":  ("load-patterns", "The reload target is classified by name only."),
    "2.2.3":  ("load-patterns", "Historical-vs-incremental intent is read from names."),
    "2.2.5":  ("load-patterns", "A pipeline named pl_incremental_copy passes on its name alone."),
    "3.6.3":  ("load-patterns", "Staging is recognised only from stg/stage/staging."),

    # Notebooks: the code is in the KB.
    "5.5.4":  ("security-privacy", "Personal data is inferred from column-name vocabulary."),
    "6.4.2":  ("security-privacy", "A Key Vault reference matches the hardcoded-password pattern."),
    "6.2.3":  ("security-privacy", "Sensitive columns are matched by name, including address and compensation."),
    "5.4.4":  ("data-quality", "Unknown-member monitoring is gated on dim_/fact_ naming in the code."),
    "5.1.10": ("data-quality", "The quarantine sink is identified from DataFrame variable names."),
    "3.1.4":  ("code-quality", "Whether markdown explains the business logic is a semantic judgment."),
    "14.1.4": ("code-quality", "DAX quality is a heuristic judgment over expressions."),
    "14.2.3": ("code-quality", "Cardinality falls back to name words including 'at' and 'on'."),

    # Audit trail and monitoring: names decide the population.
    "4.6.5":  ("audit-lineage", "An audit table is recognised by name, so a destructive rewrite is invisible."),
    "4.6.2":  ("audit-lineage", "Config tables are found by a fixed vocabulary."),
    "4.6.3":  ("audit-lineage", "Whether a metadata store exists is decided by a config-name vocabulary."),
    "4.6.4":  ("audit-lineage", "Audit logging is matched from a fixed identifier vocabulary."),
    "4.6.8":  ("audit-lineage", "The audit-table population comes from a name vocabulary."),
    "10.1.1": ("audit-lineage", "Run-history persistence requires one of ten fixed table spellings."),
    "10.4.2": ("monitoring", "Monitoring items are selected by display-name keywords."),
    "10.4.4": ("monitoring", "The date table is found only by the words date/calendar."),
    "5.4.7":  ("monitoring", "Serving items are selected by name words like gold, curated, mart."),
}

#: The set of advisory refs, derived from the checklist above.
ADVISORY_REFS: frozenset[str] = frozenset(ADVISORY_CHECKLIST)

#: The rubric a reader must apply, mirroring what the engine does in
#: :func:`auditfast.core.scoring.band_from_coverage` and the ``binary`` /
#: ``covered`` / ``not_applicable`` verdict helpers.
#:
#: The point is that scoring is **arithmetic, not taste**. A reader decides which
#: objects comply - that is the judgment, and it is where reading beats a regex -
#: but turning that count into a score is fixed. Without this, scores drift
#: between runs and cannot be compared with deterministic ones.
SCORING_GUIDE = """\
SCORING RULES - apply these exactly; they mirror the deterministic engine.

1. Decide the shape of the check first.

   RATIO  - "n of m objects comply" (most workspace and table checks).
            Count how many comply, divide by the total, then band it:
                ratio = 1.00          -> 3
                ratio >= 0.80         -> 2
                ratio >= 0.50         -> 1
                ratio <  0.50         -> 0
            State the count in your evidence, e.g. "7 of 9 comply".

   BINARY - "is this present or not" (most notebook and pipeline checks).
            Present -> 3. Absent -> 0. There is no middle value.
            Do not soften a 0 to a 1 because the object nearly qualifies.

2. You are shown a SAMPLE, not the whole estate. The evidence carries at most
   40 tables out of what may be hundreds, and says so in its header.

   So do not try to recount a large population yourself. Instead:

     a. Take the population count from the deterministic evidence. It says
        things like "156 of 442 solution tables have audit columns", and that
        count is reliable - counting is the one thing the rule does well.

     b. Use the sample to test whether the rule CLASSIFIED correctly. That is
        what it gets wrong: it matches a name and cannot tell what the thing
        is.

     c. If the sample shows the rule misclassified, adjust the count and show
        your working: "the rule counted 3 of 13; DimTime and dt_reference are
        date dimensions it missed, so 5 of 13 -> 0.38 -> 0".

     d. If the sample shows the rule classified correctly, keep its count and
        band it. Agreeing with the rule is a valid, useful answer.

   Never state a count you did not derive from either the deterministic
   evidence or the objects actually in front of you.

3. Judgment goes into deciding what counts as compliant - not into the
   arithmetic. Once you have the count, the score follows.

4. If the evidence does not let you decide, answer confidence=low and give
   your best score anyway. A low-confidence verdict is discarded and the
   deterministic verdict is kept, which is the correct outcome.

   Never invent a middle score to hedge. Uncertainty belongs in the
   confidence field, not in the score.

5. Missing data is not failure. If the evidence does not show whether the
   practice is met, that is confidence=low - not a score of 0. Scoring 0
   asserts the practice is genuinely absent.

6. Score the estate, not its naming. A workspace of personal sandboxes is a
   training estate, not a badly modelled one - say so rather than scoring 0.
"""


def is_advisory(ref: str) -> bool:
    """True when a check's ``ref`` is in the advisory checklist."""
    return ref in ADVISORY_CHECKLIST


def theme_of(ref: str) -> str:
    """The judging theme a ref belongs to; ``"other"`` when it is not advisory."""
    entry = ADVISORY_CHECKLIST.get(ref)
    return entry[0] if entry else "other"


def reason_for(ref: str) -> str:
    """Why this ref's deterministic verdict is weak; ``""`` when not advisory."""
    entry = ADVISORY_CHECKLIST.get(ref)
    return entry[1] if entry else ""


def question_for(theme: str) -> str:
    """The one question a theme's checks all ask."""
    return THEMES.get(theme, "Miscellaneous advisory checks")
