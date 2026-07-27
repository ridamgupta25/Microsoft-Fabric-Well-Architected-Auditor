# Scoring

How a check's verdict becomes a 0–3 score, and how those roll up into pillar
percentages and a rating. All of this lives in
[`core/scoring.py`](../backend/src/auditfast/core/scoring.py) — pure functions, no
I/O, trivially unit-testable.

---

## 1. The 0–3 scale

Every scored check lands on the same four-point scale, taken from the audit
rubric:

| Score | Label | Meaning |
|-------|-------|---------|
| 0 | Not Implemented | Capability absent; critical gap |
| 1 | Partially Implemented | Exists but with major gaps |
| 2 | Implemented | Functional, minor issues |
| 3 | Best Practice | Fully aligned |
| `None` | Not Applicable / informational | Excluded from scoring entirely |

---

## 2. Getting to a score

Which builder a check uses determines how its score is derived.

**Binary** — `binary()`. Pass or fail, nothing between:

```
ok=True  →  score 3, coverage 1.0
ok=False →  score 0, coverage 0.0
```

**Coverage** — `covered(n, total)`. For *N of M objects comply*. The ratio is
clamped to `0.0–1.0` and banded:

| Coverage | Score |
|----------|------:|
| 100% | 3 |
| 80–99% | 2 |
| 50–79% | 1 |
| below 50% | 0 |

Note the top band is exact: 99% of activities having a retry policy scores 2, not
3. This is deliberate — "almost everywhere" is not best practice.

**Graded** — `graded(score)`. The check supplies `0`–`3` directly, for rules with
genuine middle ground. `WS-LEASTPRIV` and `PL-PARAM` are the two today.

**Informational** — `note()`. Sets `score=None` and `scored=False`, so the result
appears in reports but touches no arithmetic.

**Not applicable** — `not_applicable()`. Also unscored, but semantically
different from `note()`: it means the data needed to judge could not be read.
A network failure must not be recorded as a misconfiguration.

### Score to status

```
score >= 3  →  PASS
score >= 1  →  PARTIAL
otherwise   →  FAIL
```

### Severity

Severity is not independent of outcome. Every builder forces it:

```
PASS      →  Informational
otherwise →  the check's declared severity
```

So severity in a report always means *the severity of this finding*, never *the
importance of this check in the abstract*.

---

## 3. Rolling up

`aggregate()` produces every number the UI and the reports display.

The core calculation, applied at each level:

```
percentage = Σ(score × weight) / (3 × Σ weight) × 100
```

Only results where `scored is True` **and** `score is not None` are counted.
Informational rows, `N/A` results, and `WS-ACCESS` errors are excluded from every
denominator.

| Output | What it aggregates |
|--------|--------------------|
| `overall` | Every scored result in the run |
| `by_pillar[p]` | `{pct, count}` for each of the five scored pillars |
| `by_workspace[w]` | `{role, layer, pct, count, by_pillar}` per workspace |
| `by_layer[l]` | `{pct, count}` per architecture layer |
| `matrix[p][l]` | **Pillar × layer** — the "inner pillars" view |
| `layers` | The layers actually present in this run |
| `counts` | Number of results per `Status` |
| `total_scored` | Size of the scored set |

`by_pillar` iterates `Pillar.scored()`, so `Foundation` never appears — it is
cross-cutting and informational only. A pillar with no checks yields `pct: None`,
which every consumer renders as "not assessed" rather than as zero. **Not
assessed and scored zero are different things**, and the code is careful about it
throughout.

---

## 4. Rating bands

`rating(pct)` converts a percentage into a **risk** label:

| Percentage | Rating | |
|-----------|--------|---|
| 91–100 | Excellent | 🔵 |
| 76–90 | Good | 🟢 |
| 61–75 | Medium | 🟡 |
| 41–60 | High | 🟠 |
| 0–40 | Critical | 🔴 |
| `None` | Not assessed | ⚪ |

> **Read these as risk, not achievement.** A "Critical" rating means *critical
> risk* — a score of 40% or below. "High" means high risk, i.e. a bad score of
> 41–60%. The label runs opposite to the number, which surprises people reading a
> report for the first time.

---

## 5. Weighting, and what is still untuned

### The mechanism exists; the values do not

`01-scoring-rubric.md` specifies a **weighted** overall score:

```
Overall Score = Σ (Area Score × Area Weight)
```

with per-area weights — Security 12%, Compliance 12%, Cost 7%, Documentation 4%.

`CheckSpec` now carries a `weight`, and `aggregate()` applies it. **Every check
is currently `weight = 1.0`**, so the arithmetic reduces exactly to the flat mean
the tool has always produced, and the numbers are unchanged.

What that means today: influence still follows check *count*, not importance. A
single `Data Prep` workspace containing ten pipelines produces:

| Pillar | Scored checks | Share of overall |
|--------|--------------:|-----------------:|
| Reliability | 40 | 44% |
| Operational Excellence | 35 | 38% |
| Security | 14 | 15% |
| Cost Optimization | 2 | **2%** |

So a naming-convention violation still counts as much toward the overall score as
a hardcoded secret. The difference from before is that fixing this is now a
one-line change per check rather than a rewrite:

```python
@check(id="PL-SECRETS", ..., weight=3.0)
```

Choosing the actual weights is a scoring-policy decision for the audit owners,
not a refactor, so they are deliberately left at 1.0 until that call is made.

### Resolved

Two problems documented in earlier versions of this page are fixed:

| Was | Now |
|-----|-----|
| `Status.NA` existed but was never produced, so a failed Fabric read scored as a failure | Providers record unreadable resources in `WorkspaceContext.unavailable`; affected checks return `not_applicable()` and are excluded from scoring |
| `aggregate()` and the API computed different failure counts when a workspace was unreadable, so the Markdown report and the UI disagreed | Access errors are separated from results at the service boundary; every consumer reads one aggregate |

## 6. Testing the maths

The scoring functions are pure, so they test directly with no fixtures — see
[`tests/test_engine.py`](../backend/tests/test_engine.py):

```python
def test_bands():
    assert band_from_coverage(1.0) == 3
    assert band_from_coverage(0.85) == 2
    assert band_from_coverage(0.6) == 1
    assert band_from_coverage(0.2) == 0

def test_rating():
    assert rating(95)[0] == "Excellent"
    assert rating(30)[0] == "Critical"
    assert rating(None)[0] == "Not assessed"
```

If you change a band boundary, these fail first — which is the point.
