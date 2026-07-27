# Checks

The check library is the heart of the tool. This document lists every check that
exists today and explains how to add one.

---

## What a check is

A small **pure function** that inspects one implemented object and returns a
`CheckResult` with a fixed status, a fixed 0–3 score, and a pre-written
recommendation. No AI, no network calls of its own, no side effects — it is
handed data and returns a verdict.

Two kinds exist today:

| Kind | Signature | Runs |
|------|-----------|------|
| **Workspace check** | `fn(ws: dict, settings: dict)` | Once per workspace |
| **Pipeline check** | `fn(ws: dict, name: str, definition: dict, settings: dict)` | Once per pipeline, per workspace |

`ws` is the [workspace context](architecture.md#5-contract-1--the-workspace-context).
`settings` is the `project:` block of the project YAML.

---

## Catalog

### Workspace checks — 12

Registered in [`workspace_checks.py`](../backend/auditfast/core/checks/workspace_checks.py).
These are the **common** checks: they run for every workspace in every project
regardless of source system.

| Check ID | Ref | Title | Pillar | Shape | Severity on failure |
|----------|-----|-------|--------|-------|---------------------|
| `WS-NAME` | 1.1.7 | Workspace naming convention | Operational Excellence | binary | Low |
| `WS-ROLES-GROUPS` | 6.1.2 | Roles assigned to security groups, not individuals | Security | coverage | High |
| `WS-LEASTPRIV` | 6.1.8 | Least-privilege admin grants | Security | scored | High |
| `WS-GUESTS` | 6.1 | No unmanaged external / guest access | Security | binary | High |
| `WS-LABELS` | 6.2.4 | Sensitivity labels applied to items | Security | coverage | Medium |
| `WS-GIT` | 11.1.2 | Git integration enabled | Operational Excellence | binary | Medium |
| `WS-DEPLOY` | 11.2 | Deployment pipeline configured | Operational Excellence | binary | Medium |
| `WS-CAPACITY` | 12.1 | Capacity assigned | Cost Optimization | binary | High |
| `WS-ORPHAN` | 12.3.4 | No orphaned / stale items | Cost Optimization | coverage | Low |
| `WS-INVENTORY` | 1.1 | Item inventory | Foundation | info | — never scored |
| `WS-LAYER-CONTENT` | 1.1.2 | Contains the items its layer role expects | Operational Excellence | binary | Medium |
| `WS-LAYER-SEP` | 1.1.2 | Free of other layers' item types | Operational Excellence | binary | Medium |

Notable logic:

- **`WS-LEASTPRIV`** is graded, not binary: `3` at or under `max_admins`, `1` up to
  two over, `0` beyond that.
- **`WS-ORPHAN`** treats an item as stale when `lastRunUtc` is missing, unparseable,
  or older than `orphan_days`.
- **`WS-LAYER-CONTENT` / `WS-LAYER-SEP`** are the only role-aware checks. They
  compare the workspace's item types against `EXPECTED_TYPES`
  ([`workspace_checks.py:16-22`](../backend/auditfast/core/checks/workspace_checks.py#L16-L22)).
  A workspace whose role has no entry there — including `Mixed` and untagged —
  returns an informational result instead.

### Pipeline checks — 8

Registered in [`pipeline_checks.py`](../backend/auditfast/core/checks/pipeline_checks.py).
Each inspects one pipeline definition. They verify that the *implemented*
pipeline follows best practice; they never trace where data comes from or goes.

| Check ID | Ref | Title | Pillar | Shape | Severity on failure |
|----------|-----|-------|--------|-------|---------------------|
| `PL-NAME` | 2.1.1 | Pipeline naming convention | Operational Excellence | binary | Low |
| `PL-DESC` | 2.1.6 | Descriptions / annotations populated | Operational Excellence | coverage | Low |
| `PL-PARAM` | 2.1.2 | Parameterized — no hardcoded endpoints | Operational Excellence | scored | Medium |
| `PL-RETRY` | 2.4.1 | Retry policy configured on activities | Reliability | coverage | High |
| `PL-FAILPATH` | 2.4.3 | On-failure path defined | Reliability | binary | Medium |
| `PL-NOTIFY` | 2.4.5 | Failure notification present | Reliability | binary | Medium |
| `PL-TIMEOUT` | 2.4 | Explicit activity timeouts set | Reliability | coverage | Low |
| `PL-SECRETS` | 6.4.2 | No hardcoded secrets in pipeline | Security | binary | **Critical** |

Notable logic:

- **`PL-PARAM`** is three-valued: `0` if a hardcoded endpoint literal is found,
  `3` if parameters are declared and nothing hardcoded, `1` if neither.
- **`PL-TIMEOUT`** only counts a timeout that is *not* one of Fabric's defaults —
  `7.00:00:00` and friends are treated as "not explicitly set".
- **`PL-SECRETS`** and **`PL-PARAM`** regex-scan the serialized definition JSON, so
  they can produce false positives on parameter names that merely look like
  secrets. Pattern lists are at the top of the module.
- **`PL-NOTIFY`** matches on either activity type (`Teams`, `Office365Outlook`,
  `SendEmail`, `WebHook`) or an activity name matching `notif|alert|email|teams`.

### Synthetic result

| Check ID | Ref | Meaning |
|----------|-----|---------|
| `WS-ACCESS` | — | The workspace could not be read at all. Emitted by [`engine.py`](../backend/auditfast/core/engine.py#L13-L24), never scored, surfaced by the API in a separate `errors` array. |

### Coverage by pillar

| Pillar | Checks | Gap |
|--------|-------:|-----|
| Operational Excellence | 8 | — |
| Security | 5 | — |
| Reliability | 4 | — |
| Cost Optimization | 2 | Thin — capacity and orphans only |
| **Performance Efficiency** | **0** | Entirely Phase 2. The UI ships its checkbox unticked by default. |

By object type, everything today is either workspace-level or pipeline-level.
Lakehouse / Delta, notebooks, semantic models, and Eventhouse have **no automated
checks** and are handled by the manual Excel checklist.

---

## Which checks run

Two filters apply, and they behave differently.

**Layer role** gates pipeline checks only, in the engine:

```python
PIPELINE_ROLES = {"Data Prep", "Data Operations", "Mixed", ""}
```

A workspace tagged `Data Storage`, `Data Logs`, or `Reporting / Semantic` gets the
12 workspace checks but no pipeline checks, even if it contains pipelines.

**Pillar selection** is applied to the *results*, after everything has run
([`audit_service.py:109-111`](../backend/auditfast/services/audit_service.py#L109-L111)).
Deselecting a pillar therefore saves no work — in live mode you still pay for
every Fabric API call. Unscored results always survive the filter, so
informational rows and access errors are never hidden.

---

## Result builders

Never construct a `CheckResult` by hand. Four builders in
[`base.py`](../backend/auditfast/core/checks/base.py) cover every shape and
handle severity downgrading and remediation lookup for you.

| Builder | Use when | Score |
|---------|----------|-------|
| `binary_result(ok=...)` | It is either done or not | `3` or `0` |
| `coverage_result(coverage=...)` | *N of M* objects comply | banded from the ratio — see [scoring.md](scoring.md) |
| `scored_result(score=...)` | You need graded judgement | you supply `0`–`3` |
| `info_result()` | Reporting a fact, not a verdict | `None`, `scored=False` |

All four automatically:

- derive `status` from the score,
- set `severity` to `Informational` when the check passes, and your
  `fail_severity` otherwise,
- look up `recommendation` from `remediation.yaml` by `ref` — **only on failure**.

---

## Adding a check

### 1. Write the function

In [`workspace_checks.py`](../backend/auditfast/core/checks/workspace_checks.py)
or [`pipeline_checks.py`](../backend/auditfast/core/checks/pipeline_checks.py):

```python
@workspace_check
def ws_dataflow_gen1(ws, s):
    """Flag deprecated Dataflow Gen1 items."""
    items = ws.get("items", [])
    gen1 = [i for i in items if i.get("type") == "Dataflow"]
    return binary_result(
        check_id="WS-DF-GEN1", ref="1.2.3",
        title="No deprecated Dataflow Gen1 items",
        pillar=OPEX, ok=not gen1,
        workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"{len(gen1)} Gen1 dataflow(s) found",
        fail_severity=Severity.MEDIUM,
    )
```

Rules to follow:

- **Read defensively.** The context is an untyped dict and live data is missing
  fields constantly. Always `.get()` with a default.
- **Never divide by zero.** The established idiom is `x / len(items) if items else 1.0`
  — an empty collection is vacuously compliant.
- **Write evidence as a fact, not a verdict.** `"3 of 12 items carry a label"`,
  not `"labels are bad"`. Evidence lands verbatim in the report and the risk
  register.
- **Return, don't raise.** An exception aborts the whole workspace. Degrade to a
  failing or informational result instead.
- A check may return a **list** of results; the engine flattens it.

### 2. Add the remediation text

Keyed by `ref` in [`config/remediation.yaml`](../backend/config/remediation.yaml):

```yaml
"1.2.3": "Migrate Dataflow Gen1 items to Gen2 and retire the originals."
```

A missing key is not an error — `rec()` returns `""` and the finding renders with
an empty recommendation column. Easy to miss in review.

### 3. Make sure it is imported

If you added the check to an existing module, you are done. **If you created a new
module, add it to [`checks/__init__.py`](../backend/auditfast/core/checks/__init__.py)**:

```python
from . import my_new_checks  # noqa: F401
```

Registration is an import side effect. A module nobody imports registers nothing,
raises nothing, and its checks simply never run.

### 4. Add fixture data and a test

Give the check something to fail against in
[`sample_data/tenant.json`](../backend/sample_data/tenant.json), then assert on it
in [`tests/test_smoke.py`](../backend/tests/test_smoke.py):

```python
def test_gen1_dataflow_flagged():
    results = _run()
    assert any(r.check_id == "WS-DF-GEN1" and r.status == Status.FAIL
               for r in results)
```

Note that `test_checks_registered` asserts registry counts, so adding a check may
require bumping those numbers.

### 5. Update this document

Add the row to the catalog above. The table is maintained by hand — there is
currently no way to generate it, because check metadata does not exist until the
check runs.

---

## Checklist for a new check

- [ ] Decorated with `@workspace_check` or `@pipeline_check`
- [ ] Uses a result builder, not a raw `CheckResult`
- [ ] `check_id` is unique; `ref` points at a real checklist item
- [ ] Reads the context defensively; no zero-division on empty collections
- [ ] Evidence states a fact and includes the numbers
- [ ] Remediation text added to `remediation.yaml` under the same `ref`
- [ ] Module imported in `checks/__init__.py` if it is new
- [ ] Fixture data added so the check both passes and fails somewhere
- [ ] Test added; registry-count assertions updated
- [ ] Catalog table in this document updated
