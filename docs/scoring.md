# Scoring

How a check's verdict becomes a 0–3 score, and how those roll up into pillar
percentages and a rating. All of this lives in
[`core/scoring.py`](../backend/auditfast/core/scoring.py) — 83 lines, no I/O,
trivially unit-testable.

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
| `None` | Not Applicable | Excluded from scoring entirely |

---

## 2. Getting to a score

Which builder a check uses determines how its score is derived.

**Binary** — `binary_result()`. Pass or fail, nothing between:

```
ok=True  →  score 3, coverage 1.0
ok=False →  score 0, coverage 0.0
```

**Coverage** — `coverage_result()`. For *N of M objects comply*. The ratio is
clamped to `0.0–1.0` and banded:

| Coverage | Score |
|----------|------:|
| 100% | 3 |
| 80–99% | 2 |
| 50–79% | 1 |
| below 50% | 0 |

Note the top band is exact: 99% of activities having a retry policy scores 2, not
3. This is deliberate — "almost everywhere" is not best practice.

**Graded** — `scored_result()`. The check supplies `0`–`3` directly, for rules
with genuine middle ground. `WS-LEASTPRIV` and `PL-PARAM` are the two today.

**Informational** — `info_result()`. Sets `score=None` and `scored=False`, so the
result appears in reports but touches no arithmetic.

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
otherwise →  the check's declared fail_severity
```

So severity in a report always means *the severity of this finding*, never *the
importance of this check in the abstract*.

---

## 3. Rolling up

`aggregate()` produces every number the UI and the reports display.

The core calculation, applied at each level:

```
percentage = Σ scores / (3 × count of scored checks) × 100
```

Only results where `scored is True` **and** `score is not None` are counted.
Informational rows and `WS-ACCESS` errors are excluded from every denominator.

| Output | What it aggregates |
|--------|--------------------|
| `overall` | Every scored result in the run |
| `by_pillar[p]` | `{pct, count}` for each of the five pillars |
| `by_workspace[w]` | `{role, pct, count, by_pillar}` per workspace |
| `counts` | Number of results per `Status` |
| `total_scored` | Size of the scored set |

`by_pillar` iterates the `PILLARS` constant, so `Foundation` never appears — it is
cross-cutting and informational only. A pillar with no checks yields
`pct: None`, which the reports render as "Phase 2 / Excel" rather than as zero.
**Not assessed and scored zero are different things**, and the code is careful
about it.

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

## 5. Known divergences

Three places where the implementation and the specification disagree. Documented
rather than silently carried.

### Divergence from the rubric: no weights

`01-scoring-rubric.md` specifies a **weighted** overall score:

```
Overall Score = Σ (Area Score × Area Weight)
```

with per-area weights — Security 12%, Compliance 12%, Cost 7%, Documentation 4%,
and so on. The code computes a **flat unweighted mean over every scored check**
([`scoring.py:47-51`](../backend/auditfast/core/scoring.py#L47-L51)). There is no
`weight` field on `CheckResult`.

The practical effect is that influence follows check *count*, not importance. A
single `Data Prep` workspace containing ten pipelines produces:

| Pillar | Scored checks | Share of overall |
|--------|--------------:|-----------------:|
| Reliability | 40 | 44% |
| Operational Excellence | 35 | 38% |
| Security | 14 | 15% |
| Cost Optimization | 2 | **2%** |

So a naming-convention violation counts exactly as much toward the overall score
as a hardcoded secret, and Cost Optimization is almost invisible on any
pipeline-heavy project. Adding more checks to a pillar silently increases its
weight.

Adding a `weight` field (defaulting to `1.0`) is cheap now and expensive to
retrofit later.

### `Status.NA` is never produced

The enum member exists in [`models.py`](../backend/auditfast/core/models.py) and
the rubric relies on N/A to exclude inapplicable items — but no code path ever
emits it. Combined with the live client swallowing errors to `None`
(see [architecture.md](architecture.md#5-contract-1--the-workspace-context)),
"we could not determine this" currently scores the same as "this is not
configured": a `0`.

### Two different fail counts

`aggregate()["counts"]` tallies **all** results, including the `WS-ACCESS` errors
whose status is `FAIL`. The API's `to_json()` recomputes its own counts over the
list with `WS-ACCESS` removed
([`audit_service.py:148-152`](../backend/auditfast/services/audit_service.py#L148-L152)).

The Markdown report reads the former; the browser reads the latter. On a run
where a workspace could not be read, **the Markdown report shows one more failure
than the UI does**. The scores themselves agree — only the headline counts drift.

---

## 6. Testing the maths

The scoring functions are pure, so they test directly with no fixtures — see
[`tests/test_smoke.py`](../backend/tests/test_smoke.py):

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
