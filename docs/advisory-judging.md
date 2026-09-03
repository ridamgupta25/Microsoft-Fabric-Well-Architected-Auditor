# Advisory judging — how the AI half works

> A second report for the checks a fixed rule can only guess at. An AI reads the
> real workspace evidence and **labels** what it finds; **code** turns those
> labels into scores. **The deterministic audit score is never affected.**

---

## 1 · Why this exists

Checks fall into two kinds.

**Deterministic** — *"does this notebook call `OPTIMIZE`?"* A regex answers it the
same way every time. These produce the **score**.

**Advisory** — *"is this table a dimension?"* The rule matches `dim_` in the name,
so `DimTime` is missed and `fact_codes` is wrongly counted. The evidence to answer
properly is in the knowledge base; the rule just can't interpret it.

Advisory checks are **pulled out of the score** and given their own report. There,
an AI reads the same evidence and judges it.

Two families end up on the list, for different reasons:

| | Why it's advisory | Can AI help? |
|---|---|---|
| The data isn't there | Row-level reconciliation, key matching, value precision | Not really — it's equally blind, and should answer *undetermined* |
| The rule reads text | A name, an identifier, a keyword | **Yes** — this is the point |

---

## 2 · The core idea

**The AI classifies. Code scores.**

Asking a model *"how many of these 537 tables are dimensions?"* invites a
plausible number from a model that saw 40 of them. So we never ask.

```
AI is asked:    "what is this table?"        →  "dimension"
Code computes:  43 dimensions, 18 facts      →  both present  →  score 3
```

Three consequences:

- **A count can't be invented** — the model never handles one.
- **Scoring is reproducible** — the same labels always give the same score.
- **Drift is measurable** — diff two runs' labels and you can see exactly where
  the model wobbled. Impossible when it hands back a number.

---

## 3 · The pieces

| File | Responsibility |
|---|---|
| `core/advisory.py` | **Which refs are advisory**, their theme and why. One line each. Also `SCORING_GUIDE`, used by the bundle path only. |
| `core/judging.py` | **Judging guides** — the instruction sheet per **check id**: what to look at, what labels to use, which evidence family. Also `LABELLING_RULES`, the job path's equivalent. |
| `ai/evidence.py` | Builds **derived facts** per object — the shape a check reasons about, not raw data. |
| `ai/jobs.py` | Writes **one job file per check**, with every object, chunked. |
| `ai/classify.py` | Turns labels into a score. The four shapes and the engine's bands. |

---

## 4 · The flow

```
Audit runs
  ├─ deterministic checks ──→ audit-report.xlsx      ★ THE SCORE, done
  └─ advisory checks ────────→ advisory-report.xlsx  (rule verdicts)
                          └──→ jobs/<ref>.json       one per check
                          └──→ jobs/<ref>-labels.csv pre-filled with object ids

Agent takes one check at a time
  read the guide → label every object, chunk by chunk → write the CSV

advisory-apply
  labels → group by finding → apply shape → score → rebuild the report
```

### The work unit is the *check*, not the finding

A theme mixes five questions across hundreds of findings, and switching between
them is where a reader starts to drift. One check per pass means one question
held in mind throughout — and if the rule misfires the same way repeatedly, that
becomes obvious rather than being rediscovered each time.

### Nothing is sampled

Objects are split into ~18 KB chunks. A 537-table estate becomes ~5 chunks; a
5,000-table estate becomes ~50. **Every object is judged in both directions** —
the ones the rule wrongly flagged *and* the ones it wrongly missed. A sample can
only ever catch the first.

A large notebook or pipeline gets a chunk to itself rather than being dropped. A
genuinely enormous one is cut with a visible marker and an instruction to answer
`undetermined` rather than assume the practice is absent. Truncating *inside* one
object is recoverable, because the reader knows it happened; skipping objects is
not.

### The evidence families

| Family | Sends | Used by |
|---|---|---|
| `table-shape` | derived counts — keys / numeric / descriptive — plus column names | table and model checks |
| `table-names` | the name and column count only | `WS-STAGING` |
| `pipeline-definition` | the definition itself, pruned of empties, long strings capped | the 10 pipeline checks |
| `notebook-code` | code **and markdown, comments kept** | the 7 notebook checks |

Deriving works for tables because the check reasons about counts, so a count is
the whole of what it needs. It does **not** work for pipelines: ten checks read
ten different parts of one definition — activity `type`, `dependsOn` conditions,
`typeProperties`, Script bodies, pipeline parameters — and any field a summary
omits is a question the reader silently cannot answer. Those families send the
artefact.

Notebook comments are kept even though the rules strip them. Stripping is right
for a regex, which cannot tell `# we hash the email here` from code that hashes
it. A reader can, and that difference is most of why these checks are advisory.

---

## 5 · A job file

```json
{
  "check_id": "TB-STARSCHEMA",
  "ref": "4.5.1",
  "question": "Is the model shaped the way a star schema should be?",
  "rule": "<the check's own docstring>",
  "why_advisory": "Star schema falls back to fact_/dim_ prefixes...",
  "instruction": "For each table decide whether it is a FACT, a DIMENSION...",
  "labels": ["fact", "dimension", "neither"],
  "undetermined_label": "undetermined",
  "how_it_is_scored": "Code scores 3 if both 'fact' and 'dimension' appear...",
  "findings": {
    "(workspace)": {
      "pillar": "Data Management & Quality", "severity": "Medium",
      "layer": "Mixed", "scope": "workspace", "weight": 1.0, "scored": true,
      "obj": "", "recommendation": "...",
      "deterministic": { "status": "PASS", "score": 3, "evidence": "..." }
    }
  },
  "objects": 18,
  "chunks": [ { "chunk": 1, "of": 1, "objects": [
      { "id": "dbo.DimCustomer",
        "finding": "(workspace)",
        "facts": "10 cols (keys=3, numeric=0, descriptive=7): customer_key, city, ...",
        "rule_says": "dimension" } ] } ]
}
```

`rule_says` is deliberate: the reader sees what the rule concluded and is asked
to say where it disagrees. **That disagreement is the finding worth having** — it
tells you the check has a bug, not just that one workspace scored differently.

`findings` is one entry per report row, and every object carries the `finding`
its label scores. A workspace-scoped check has a single finding fed by every
object; a notebook- or pipeline-scoped check has one per object, each scored on
its own. Without that split, judging a notebook check would collapse forty rows
into one and silently drop thirty-nine the deterministic report still has.

### Keyed by check id, not ref

Guides, job files and label CSVs are all keyed by **`check_id`**. The 48
advisory refs resolve to **55 checks** — seven refs carry two, and five of those
pairs differ in scope:

| ref | checks |
|---|---|
| `5.1.9` | `PL-DQ-GATE` (pipeline) + `NB-DQ-HALT` (notebook) |
| `7.2.6` | `PL-RECONCILE` (pipeline) + `NB-RECONCILE` (notebook) |
| `4.6.5` | `NB-AUDIT-IMMUTABLE` + `PL-AUDIT-IMMUTABLE` |
| `5.4.1` | `SM-FK-SURROGATE` (semantic model) + `SM-FK-RI-DATA` (workspace) |
| `5.4.6`, `4.4.9`, `9.3.4` | a notebook check + a cross-workspace `XW-*` group check |

A ref-keyed guide would hand a pipeline instruction sheet to a notebook check,
and ref-named job files would collide so one silently overwrote the other.
`is_advisory` stays keyed by ref, because *being advisory* is a property of the
checklist point. Each key answers the question it is for.

---

## 6 · The five scoring shapes

| Shape | How a score is computed |
|---|---|
| `ratio` | compliant ÷ judged, then the engine's bands: `1.00 → 3` · `≥0.80 → 2` · `≥0.50 → 1` · `<0.50 → 0` |
| `binary` | any compliant → 3, else 0 |
| `pair` | both labels present → 3 · one → 1 · neither → 0 |
| `graded` | each label carries its own 0–3 band; the weakest one assigned wins |
| `worst` | the weakest label sets the score, banded by its position in the vocabulary |

The bands mirror `core/scoring.band_from_coverage` exactly, so **an advisory 2
means what a deterministic 2 means**.

`graded` exists because `worst` derives a band from *where* a label sits, so its
third label can only ever score 1. `NB-DQ-HALT` scores a notebook that carries on
past bad data **0** — and 1 would claim the practice is partly met when it is
absent. Stating the band per label keeps the gap the deterministic `graded()`
helper gives it.

### `undetermined`

Always available, never declared by a guide. An object the reader can't judge
**leaves the denominator** rather than counting as a failure — the N/A-not-FAIL
rule applied per object. If nothing at all could be judged, no score is produced
and the deterministic verdict stands.

---

## 7 · What protects the score

- Advisory refs are **absent from the collection that gets scored** — a list
  split, not a weighting.
- The verdict import **refuses any ref not in `ADVISORY_CHECKLIST`**, so a
  hand-edited file can't reach a scored check.
- **Low confidence is not applied** — the deterministic verdict is kept.
- Finding IDs **hash the evidence**, so a verdict can't land on data that changed
  after it was judged.
- Every AI-touched row is marked `source="advisory-offline"` and its evidence is
  prefixed `[offline - <who> - <confidence> confidence]`.

---

## 8 · Adding a check to the AI half

1. Add the ref to `ADVISORY_CHECKLIST` in `core/advisory.py` — `(theme, why)`.
2. Add a `JudgingGuide` in `core/judging.py`, **keyed by check id** — its `ref`,
   labels, shape, instruction.
3. If it needs evidence no builder produces yet, add one to `ai/evidence.py`.

Two tests catch the ways this goes silently wrong. A mistyped check id makes
`guide_for` return `None`, so the check quietly falls through to the bundle and
looks like a coverage gap rather than a typo — `test_every_guide_points_at_a_real_advisory_check`
asserts the id is registered, its ref matches, and that ref is advisory. A guide
naming an evidence family that doesn't exist yields zero objects, so `build_job`
returns `None` and the job is never written —
`test_every_guide_names_an_evidence_family_that_exists` catches that.

**A check with no guide is still advisory** — it reaches the report with its
deterministic verdict and simply isn't AI-judged. So guides can be added one at a
time without stranding anything.

### Writing the instruction: verify every claim about the rule

A guide's closing paragraph names what the deterministic rule gets wrong, so the
reader knows where to look. **Read the rule's source before writing that
sentence.** Four of the first twenty guides described a failure mode the rule
does not have, and each one sent a judging agent somewhere useless:

| guide | what it claimed | what the code does |
|---|---|---|
| `PL-FAILURE-ALERT` | "never checks the notifier and the failure are connected" | `failure_names & notifiers` — a set intersection. **Stricter**, not looser. |
| `TB-SCD2` | "`effective_from` / `isactive` read as non-standard" | Both are in the frozensets and match. The real miss is suffixed names like `valid_from_instant`. |
| `SM-COLUMN-SHAPE` | "a column called `grid_ref` is flagged" | It isn't — `_is_technical_key_name` requires the *trailing* word to be a key word. |
| `WS-DDM` | "flagged a warehouse address, a GL account number and a product code" | Those were `NB-PII-TOKENISED`'s false positives, transplanted. All five of `WS-DDM`'s hits were true positives. |

The pattern is reasoning about a rule instead of reading it, and transplanting a
real finding from one check onto another. Both produce a confident, specific,
wrong sentence — which is worse than saying nothing, because a reader will act
on it.

### Coverage today

**51 of 55 advisory checks have a guide.** The remaining four:

- **3 cross-workspace `XW-*` group checks** — `Scope.GROUP`, comparing
  Dev/UAT/Prod for consistency rather than judging one workspace. Two of the
  three already reuse a per-workspace check that *is* guided.
- **`SM-FK-RI-DATA`** — reads measured orphan counts from `query_results`, so
  the verdict is arithmetic rather than judgment. (That field is never
  populated, so the check is permanently N/A — a separate defect.)

---

## 9 · What validating the guides found

Twenty-six of the guides have been judged by an AI agent against a real
1,080-item Fabric workspace. Every one produced a defect report, and **six were
bugs in the deterministic checks themselves** — which is why this feature's
change set touches `core/check/**` at all:

| check | defect |
|---|---|
| `NB-MARKDOWN` | Failed notebooks whose definition could not be read, with the evidence *"the logic is undocumented"* — a claim about a notebook nobody had seen. Also scored 3/3 for an **empty** markdown cell (`[""]` is truthy), and failed 28 empty stubs for not documenting logic they don't have. |
| `PL-NOTIFY` | Failed 11 pipelines with **no activities at all** for having no notifier. The sibling `PL-FAILURE-ALERT` already guarded this, which showed it was an oversight. |
| `PL-FAILURE-ALERT` | Scored the check **3/3 on a disabled Teams activity** — `"state": "Inactive"` with `onInactiveMarkAs: "Succeeded"`, so the pipeline reports green on the very path meant to raise the alarm. It was the only PASS in 56 pipelines. |
| `TB-SCD2` | Reported *"1 of 1 SCD2 table(s) carry the full trio" → PASS* on an estate with 17 half-built ones, because validity-pair tables are excluded from the population without being reported. |
| `NB-DQ-HALT` | `_DQ_ASSERTION` was byte-identical to the first alternative of `_DQ_HARD_STOP`, so any top-level `assert` was **both the sole trigger and an automatic 3** — `assert os.path.exists(path)` scored "DQ failures halt pipeline progression". |
| `NB-DQ-HALT` | Unreadable definitions reported as *"runs no data-quality evaluation"* — reading nothing and reporting an absence are different claims. |

Three of those are **N/A-not-FAIL violations**, the invariant this codebase is
built on. Each fix is guarded by a test, and every pinned engine count is
unchanged — they alter behaviour only in the conditions they target.

The AI layer also found things the rules structurally cannot see: two notebooks
substituting **random Age and Gender** for unmatched rows after a left join
(fabricated data indistinguishable from real), 16 `ctl_*_ingestion` control
tables the config check misses because its vocabulary has `control` but not
`ctl`, and roughly 90 staging tables where the rule finds one.

---

## 10 · Honest limitations

**The advisory report is not reproducible across runs.** Two judging passes can
label differently. The scoring is deterministic given labels, but the labels
aren't. This is inherent, and it's why the advisory half is kept out of the score.

**Nobody validates the AI's judgments automatically.** We have tests for the
plumbing, not for whether a verdict is right. The cheapest fix would be a small
labelled fixture — 20–30 objects with known-correct labels — to measure agreement
and catch regressions when a guide changes.

**Guides are the new single point of failure.** A vague instruction produces
inconsistent labels. They're written from the check docstrings and from a survey
of each rule's actual regex and failure mode, so every guide names the specific
mistake its rule makes. But **no guide has yet been read by a model**, so whether
the label vocabularies work in practice is untested. That is what the first
end-to-end run settles.

---

## 11 · Related

- [`advisory-ai.md`](advisory-ai.md) — the API-key route, which sends the same
  evidence to a configured gateway instead of writing job files.
- [`checks.md`](checks.md) — the deterministic check system.
- [`scoring.md`](scoring.md) — how 0–3 scores become pillar percentages.
