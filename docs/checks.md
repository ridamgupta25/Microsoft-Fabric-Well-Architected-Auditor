# Checks

The check library is the heart of the tool. This page lists every check and
explains how to add one.

---

## What a check is

A small **pure function** that inspects one implemented object and returns a
`Verdict` — a score plus the evidence for it. No AI, no network calls of its own,
no side effects: it is handed data and returns a judgement.

```python
@check(
    id="WS-GIT", ref="11.1.2", title="Git integration enabled",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE,
    severity=Severity.MEDIUM, requires=[Resource.GIT],
)
def git_connected(ctx: CheckContext) -> Verdict:
    """The workspace is connected to Git so its items are source-controlled."""
    ok = ctx.workspace.git_connected
    return binary(ok, "Workspace is connected to Git" if ok
                  else "Workspace is not connected to Git")
```

Everything else — id, ref, title, pillar, severity, weight, scope, workspace
name, object name, and the remediation lookup — comes from the registered
`CheckSpec` and the run context. That is why check bodies stay short.

---

## Catalog

### Workspace checks — 12

In [`core/checks/workspace/`](../backend/src/auditfast/core/checks/workspace/),
one module per pillar. These are the **common** checks: they run for every
workspace in every project.

| Check ID | Ref | Title | Pillar | Severity |
|----------|-----|-------|--------|----------|
| `WS-NAME` | 1.1.7 | Workspace naming convention | Operational Excellence | Low |
| `WS-GIT` | 11.1.2 | Git integration enabled | Operational Excellence | Medium |
| `WS-DEPLOY` | 11.2 | Deployment pipeline configured | Operational Excellence | Medium |
| `WS-LAYER-CONTENT` | 1.1.2 | Contains the expected items for its layer | Operational Excellence | Medium |
| `WS-LAYER-SEP` | 1.1.2 | Free of other layers' concerns | Operational Excellence | Medium |
| `WS-ROLES-GROUPS` | 6.1.2 | Roles assigned to security groups, not individuals | Security | High |
| `WS-LEASTPRIV` | 6.1.8 | Least-privilege admin grants | Security | High |
| `WS-GUESTS` | 6.1 | No unmanaged external / guest access | Security | High |
| `WS-LABELS` | 6.2.4 | Sensitivity labels applied to items | Security | Medium |
| `WS-CAPACITY` | 12.1 | Capacity assigned | Cost Optimization | High |
| `WS-ORPHAN` | 12.3.4 | No orphaned / stale items | Cost Optimization | Low |
| `WS-INVENTORY` | 1.1 | Item inventory | Foundation | — informational |

Notable logic:

- **`WS-LEASTPRIV`** is graded, not binary: `3` at or under `max_admins`, `1` up
  to two over, `0` beyond.
- **`WS-ORPHAN`** treats an item as stale when `lastRunUtc` is missing,
  unparseable, or older than `orphan_days` — unprovable use *is* the cost problem.
- **`WS-LAYER-CONTENT` / `WS-LAYER-SEP`** are the layer-aware pair, comparing the
  workspace's item types against `LAYER_ITEM_TYPES`. A role with no entry —
  `Mixed`, or untagged — yields an informational result instead.

### Pipeline checks — 8

In [`core/checks/pipeline/`](../backend/src/auditfast/core/checks/pipeline/).
Each inspects one pipeline definition and verifies the *implemented* pipeline
follows best practice; none trace where data comes from or goes.

| Check ID | Ref | Title | Pillar | Severity |
|----------|-----|-------|--------|----------|
| `PL-NAME` | 2.1.1 | Pipeline naming convention | Operational Excellence | Low |
| `PL-DESC` | 2.1.6 | Descriptions / annotations populated | Operational Excellence | Low |
| `PL-PARAM` | 2.1.2 | Parameterized — no hardcoded endpoints | Operational Excellence | Medium |
| `PL-RETRY` | 2.4.1 | Retry policy configured on activities | Reliability | High |
| `PL-FAILPATH` | 2.4.3 | On-failure path defined | Reliability | Medium |
| `PL-NOTIFY` | 2.4.5 | Failure notification present | Reliability | Medium |
| `PL-TIMEOUT` | 2.4 | Explicit activity timeouts set | Reliability | Low |
| `PL-SECRETS` | 6.4.2 | No hardcoded secrets in pipeline | Security | **Critical** |

Notable logic:

- **`PL-PARAM`** is three-valued: `0` if a hardcoded endpoint literal is found,
  `3` if parameters are declared and nothing hardcoded, `1` if neither.
- **`PL-TIMEOUT`** only counts a timeout that is *not* one of Fabric's defaults —
  `7.00:00:00` and friends mean "nobody set this".
- **`PL-SECRETS`** reports only the *number* of matching patterns, never the
  matched text: an audit report must not become a second copy of the secret.

### Synthetic result

| Check ID | Meaning |
|----------|---------|
| `WS-ACCESS` | The workspace could not be read at all. Never scored; surfaced by the API in a separate `errors` array. |

### Coverage

| Pillar | Checks |
|--------|-------:|
| Operational Excellence | 8 |
| Security | 5 |
| Reliability | 4 |
| Cost Optimization | 2 |
| **Performance Efficiency** | **0** — not yet automated |

By object type everything is workspace- or pipeline-level. Lakehouse/Delta,
notebooks, semantic models and Eventhouse have no automated checks yet; the
`Scope` members exist so adding them needs no engine change.

Browse it live: `GET /api/v1/catalog/checks`, or `auditfast checks --pillar Security`.

---

## Which checks run

Two filters, both applied **before** execution.

**Layer** — a check declares which layers it applies to. Pipeline checks use:

```python
PIPELINE_LAYERS = (Layer.PREP, Layer.OPERATIONS, Layer.MIXED)
```

So a `Data Storage` workspace gets the 12 workspace checks and no pipeline
checks, even if it contains pipelines. Its layer-separation check will still flag
those pipelines as foreign — that *is* the finding.

**Pillar** — narrows the set before anything is fetched, so deselecting a pillar
genuinely skips its Fabric API calls.

Because selection precedes fetching, the engine unions the `requires` of the
selected checks and asks the provider for only that. A run with no pipeline
checks never pays for `getDefinition` — one call per pipeline.

---

## Verdict builders

Never construct a `CheckResult`. Return a `Verdict` from one of these
([`core/checks/helpers.py`](../backend/src/auditfast/core/checks/helpers.py)):

| Builder | Use when | Score |
|---------|----------|-------|
| `binary(ok, evidence)` | It is either done or not | `3` or `0` |
| `covered(n, total, evidence)` | *N of M* objects comply | banded from the ratio |
| `graded(score, evidence)` | Genuine middle ground | you supply `0`–`3` |
| `note(evidence)` | Reporting a fact, not a verdict | unscored, `INFO` |
| `not_applicable(evidence)` | The data could not be read | unscored, `N/A` |

`covered()` handles the empty population (an empty collection is vacuously
compliant), removing the `x / len(items) if items else 1.0` idiom every coverage
check used to repeat — along with its zero-division risk.

`not_applicable()` matters: *"we could not determine this"* must not score the
same as *"this is not configured"*.

---

## Adding a check

### 1. Write it

Pick the module matching its scope and pillar, e.g.
[`core/checks/workspace/cost.py`](../backend/src/auditfast/core/checks/workspace/cost.py):

```python
@check(
    id="WS-DF-GEN1", ref="1.2.3",
    title="No deprecated Dataflow Gen1 items",
    pillar=Pillar.OPEX, scope=Scope.WORKSPACE,
    severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
)
def no_dataflow_gen1(ctx: CheckContext) -> Verdict:
    """Dataflow Gen1 items have been migrated to Gen2."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    gen1 = [i for i in ctx.workspace.items if i.type == "Dataflow"]
    return binary(not gen1, f"{len(gen1)} Gen1 dataflow(s) found")
```

Rules:

- **Declare `requires`.** It drives fetching. A check reading data it did not
  declare will see empty values.
- **Guard unavailable data** with `ctx.workspace.has(...)` → `not_applicable()`.
- **Write evidence as a fact, with numbers** — `"3 of 12 items carry a label"`,
  not `"labels are bad"`. Evidence lands verbatim in the report and risk register.
- **Return, don't raise.** A crash is caught and converted to an unscored `N/A`
  result, but that is a safety net, not a design.

### 2. Add remediation text

Keyed by `ref` in [`config/remediation.yaml`](../backend/config/remediation.yaml):

```yaml
"1.2.3": "Migrate Dataflow Gen1 items to Gen2 and retire the originals."
```

A missing key silently yields an empty recommendation, so a test asserts every
scoreable check's ref has text. It will fail if you skip this.

### 3. Make sure the module is imported

Adding to an existing module: done. **Creating a new module?** Import it in
[`core/checks/__init__.py`](../backend/src/auditfast/core/checks/__init__.py):

```python
from .workspace import my_new_module as _ws_new
```

Registration is an import side effect. A module nobody imports registers nothing
and raises nothing.

### 4. Add fixture data and tests

Give the check something to fail against in
[`tests/fixtures/tenant.json`](../backend/tests/fixtures/tenant.json), then
assert on it in [`tests/test_engine.py`](../backend/tests/test_engine.py). That
fixture is test-only infrastructure — see
[migration.md § Test strategy](migration.md#test-strategy) for why it is not
part of the shipped product.

`test_registry_is_fully_populated` pins the registry counts and the parity tests
pin the overall score — both will fail, correctly, and both need updating with
the new expected numbers.

### 5. Update the catalog table above

Maintained by hand. `auditfast checks` prints the live list to copy from.

---

## Checklist

- [ ] Decorated with `@check(...)`, unique `id`, real checklist `ref`
- [ ] Returns a `Verdict` from a builder, never a raw `CheckResult`
- [ ] `requires` declares every resource it reads
- [ ] Guards unavailable data with `not_applicable()`
- [ ] Evidence states a fact and includes the numbers
- [ ] Remediation text added under the same `ref`
- [ ] New module imported in `core/checks/__init__.py`
- [ ] Fixture data added so it both passes and fails somewhere
- [ ] Tests added; registry counts and parity values updated
- [ ] Catalog table in this document updated
