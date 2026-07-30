---
description: "The complete check-authoring cookbook — every Pillar, Layer, Scope, Resource, verdict helper, context field, shared detector, id/ref convention, and worked example needed to write a deterministic @check. Auto-attached when editing core/check/**."
applyTo: "backend/src/auditfast/core/check/**"
---
# Check-authoring cookbook (the complete reference)

Everything GitHub Copilot needs to add a deterministic `@check` **without reading
the whole engine**. Pair this with the concise invariants in
[check-authoring.instructions.md](check-authoring.instructions.md) and the Fabric
data map in [fabric-skills-reference.instructions.md](fabric-skills-reference.instructions.md).

A check is a **pure function** `CheckContext -> Verdict`. Same input → same score.
**No network, no clock, no randomness, no LLM, no `import auditfast.ai`.**

---

## 1. The 8 steps to add a check

1. **Dedup** — `POST /api/v1/checklist/assess` or MCP `assess_checklist_point`. If `covered`, stop.
2. **Research the data** — is the signal in a resource the provider already fetches? (§4, §5, and the fabric-skills map). If it needs an API not fetched yet → make it a **roadmap/gated** attestation (§9), not a guess.
3. **Pick pillar · layer · scope** (§2, §3).
4. **Declare `requires=[...]`** — only what the check reads (§4/§5 mapping).
5. **Write the verdict** with a helper (§6) reading the context API (§7) and shared detectors (§8).
6. **Add remediation** for the `ref` in `backend/config/remediation.yaml` (§10) — a test enforces it.
7. **Register** — put the `@check` in the right auto-loaded leaf module (§3, §11).
8. **Validate** — the harness + pinned counts (§12).

---

## 2. Pillars — enum, folder, scored?

`Pillar` in [core/enums.py](../../backend/src/auditfast/core/enums.py). The folder
is `backend/src/auditfast/core/check/<folder>/`.

| `Pillar` member | `.value` | Folder | Scored? |
|---|---|---|---|
| `Pillar.SECURITY` | `Security` | `security/` | yes |
| `Pillar.GOVERNANCE` | `Governance & Compliance` | `governance_compliance/` | yes |
| `Pillar.OPERATIONS` | `Operations & Reliability` | `operations_reliability/` | yes |
| `Pillar.PERFORMANCE` | `Performance & Capacity` | `performance_capacity/` | yes |
| `Pillar.COST` | `Cost & Resource Optimization` | `cost_resource_optimization/` | yes |
| `Pillar.DATA` | `Data Management & Quality` | `data_management_quality/` | yes |
| `Pillar.FOUNDATION` | `Foundation` | `foundation/` | **never** (informational only) |

`Foundation` is unscored — use it only for inventory / access-error / informational
`note(...)` checks, and it has **no layer subfolder** (checks live in `foundation/automated.py`).

---

## 3. Layers — enum, folder, and where a check module lives

`Layer` in [core/enums.py](../../backend/src/auditfast/core/enums.py). A check's
module path is `core/check/<pillar-folder>/<layer-folder>/<automated|roadmap|manual>.py`.

| `Layer` member | `.value` | Folder |
|---|---|---|
| `Layer.PREP` | `Data Prep` | `data_prep/` |
| `Layer.STORAGE` | `Data Storage` | `data_storage/` |
| `Layer.LOGS` | `Data Logs` | `data_logs/` |
| `Layer.OPERATIONS` | `Data Operations` | `data_operations/` |
| `Layer.REPORTING` | `Reporting / Semantic` | `reporting_semantic/` |
| `Layer.MIXED` | `Mixed` | `mixed/` |
| `Layer.ANY` | `*` | (sentinel — never a folder) |

- `layers=` on the `@check` controls **which workspaces it runs on**. A workspace
  tagged `MIXED` runs *every* check (`CheckSpec.applies_to` returns True for MIXED),
  and `Layer.ANY` in `layers=` means "runs on every layer".
- The **folder** is just where the source lives (organisational). Put a check in the
  layer folder matching the workspace role it targets; use shared layer tuples like
  `NOTEBOOK_LAYERS` / `PIPELINE_LAYERS` / `TABLE_LAYERS` (§8) for the `layers=` value.

---

## 4. Scopes — what the check iterates, and what `ctx.obj` is

`Scope` decides how the engine dispatches: it asks the workspace for objects of that
scope and runs the check once per object.

| `Scope` | One verdict per… | `ctx.obj` is | Typical `requires=` |
|---|---|---|---|
| `Scope.WORKSPACE` | workspace | the `WorkspaceContext` itself | `WORKSPACE`, `ITEMS`, `ROLE_ASSIGNMENTS`, `GIT`, `TABLE_SCHEMAS` |
| `Scope.PIPELINE` | pipeline | the parsed pipeline definition `dict` | `PIPELINE_DEFINITIONS` |
| `Scope.NOTEBOOK` | notebook | the ipynb-style definition `dict` | `NOTEBOOK_DEFINITIONS` |
| `Scope.LAKEHOUSE` | lakehouse/warehouse item | an `Item` | `TABLE_SCHEMAS`, `SHORTCUTS` |
| `Scope.SEMANTIC_MODEL` | semantic model item | an `Item` | `SEMANTIC_MODEL_DEFINITIONS` |
| `Scope.REPORT` | report item | an `Item` | *(reserved — definitions not fetched yet)* |
| `Scope.EVENTHOUSE` | eventhouse item | an `Item` | *(reserved)* |

**Key pattern — aggregate collections are WORKSPACE-scoped.** Table checks
(`TB-*`) are `Scope.WORKSPACE`, `requires=[Resource.TABLE_SCHEMAS]`, and iterate
`ctx.workspace.tables` to emit **one** `covered(n, total, …)` verdict per workspace.
Use `Scope.PIPELINE` / `Scope.NOTEBOOK` only when you want **one verdict per object**.

---

## 5. Resources — what to put in `requires=`

`Resource` is the data the provider fetches. The engine unions `requires=` across the
*selected* checks and fetches only that, so declaring the wrong/too-broad set makes a
run pay for data it never reads. Access via `ctx.workspace.<field>` after checking
`ctx.workspace.has(Resource.X)`.

| `Resource` | `.value` | Unlocks (`ctx.workspace.…`) |
|---|---|---|
| `Resource.WORKSPACE` | `workspace` | `capacity_id`, `deployment_pipeline`, `display_name`, `layer` |
| `Resource.ITEMS` | `items` | `items` (`list[Item]`), `item_types()` |
| `Resource.ROLE_ASSIGNMENTS` | `roleAssignments` | `role_assignments` (`list[RoleAssignment]`) |
| `Resource.GIT` | `git` | `git_connected`, `git_details` |
| `Resource.PIPELINE_DEFINITIONS` | `pipelineDefinitions` | `pipelines` (dict) — and `Scope.PIPELINE` `ctx.obj` |
| `Resource.NOTEBOOK_DEFINITIONS` | `notebookDefinitions` | `notebooks` (dict) — and `Scope.NOTEBOOK` `ctx.obj` |
| `Resource.TABLE_SCHEMAS` | `tableSchemas` | `tables` (dict keyed by table name) |
| `Resource.SHORTCUTS` | `shortcuts` | `shortcuts` (dict) |
| `Resource.SEMANTIC_MODEL_DEFINITIONS` | `semanticModelDefinitions` | `semantic_models` (dict) |

---

## 6. Verdict helpers (build the return value — never construct `CheckResult`)

From [core/check/helpers.py](../../backend/src/auditfast/core/check/helpers.py). All
take an optional `obj=` to override the reported object name.

| Helper | Signature | Score | Use for |
|---|---|---|---|
| `binary` | `binary(ok: bool, evidence)` | 3 or 0 | done / not-done |
| `covered` | `covered(compliant: int, total: int, evidence)` | banded ratio (empty total ⇒ vacuously 3) | *N of M* objects comply |
| `graded` | `graded(score: int, evidence)` | you supply 0–3 | genuine middle ground |
| `note` | `note(evidence)` | none, `INFO`, unscored | report a fact, don't judge |
| `not_applicable` | `not_applicable(evidence)` | none, `N/A`, unscored | **data unavailable — the N/A-not-FAIL rule** |

**The one rule that must never break:** when the data you need was not fetched,
return `not_applicable(...)`, never a 0/`FAIL`. "Could not determine" ≠ "misconfigured".

```python
if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
    return not_applicable("Notebook definitions could not be read from Fabric")
```

A check may also return a **list** of verdicts (one per sub-object) — set `obj=` on each.

---

## 7. The context API (what you read inside a check)

`CheckContext` (from [core/models.py](../../backend/src/auditfast/core/models.py)):
- `ctx.workspace` → `WorkspaceContext`
- `ctx.obj` → the object under inspection (see §4)
- `ctx.obj_name` → its display name
- `ctx.settings` → the project YAML `project:` block (a dict)
- `ctx.setting(key, default=None)` → read one tunable (e.g. `ctx.setting("orphan_days", 90)`)

`WorkspaceContext`:
- `.has(Resource) -> bool` — **gate every read with this**
- `.name`, `.display_name`, `.id`, `.layer`, `.capacity_id`
- `.git_connected`, `.deployment_pipeline`, `.git_details`
- `.role_assignments: list[RoleAssignment]`
- `.items: list[Item]`, `.item_types() -> set[str]`
- `.pipelines`, `.notebooks`, `.tables`, `.shortcuts`, `.semantic_models` (dicts)
- `.unavailable: set[Resource]`, `.is_complete: bool`
- `.objects(scope) -> Iterator[(name, obj)]`

`Item`: `.id`, `.type`, `.display_name`, `.sensitivity_label`, `.last_run_utc`.
`RoleAssignment`: `.principal_type`, `.display_name`, `.role`, `.principal_id`,
`.is_guest`, `.is_individual`.

---

## 8. Shared detectors (`_`-prefixed modules — import, don't re-implement)

Underscore modules are **not** auto-loaded (no checks); they hold reusable parsing.
Put any new reusable detector in one of these, or a new `_yourhelper.py`.

- `_notebook.py`: `NOTEBOOK_LAYERS`; `notebook_code(defn) -> str` (all code cells joined);
  `has_parameters_cell(defn) -> bool`; `markdown_sources(defn) -> list[str]`.
- `_pipeline.py`: `PIPELINE_LAYERS`; `activities(defn) -> list[dict]`.
- `_tables.py`: `TABLE_LAYERS`, `AUDIT_COLUMNS`; `columns(t)`, `col_names(t)`,
  `is_snake_case(n)`, `is_fact(n)`, `is_dimension(n)`.
- `_gated.py`: `Requirement` enum + `gated(...)` factory for roadmap attestations (§9).

---

## 9. The gated / roadmap pattern (data not fetchable yet)

If the point is automatable *in principle* but needs an API the provider does not
call (tenant admin, capacity metrics, an item definition not yet crawled), do **not**
write a live check that guesses. Register it as a roadmap/gated attestation that
returns `not_applicable(...)` with the specific `Requirement` (see `_gated.py`:
`ADMIN_SCANNER`, `ADMIN_ACTIVITY`, `ADMIN_TENANT`, `ITEM_DEFINITION`, `GIT_REPO`,
`CAPACITY_METRICS`). Set `automation=Automation.ROADMAP`. These appear in the catalog
so no checklist point is silently missing, and are promoted later.

---

## 10. Remediation (`backend/config/remediation.yaml`)

Keyed by `ref` (a string), value = one-line fix. A test asserts every `ref` used by a
check has an entry. Reuse an existing `ref`'s text if you reuse the `ref`.

```yaml
"3.3.2": "Run OPTIMIZE after write-heavy operations to compact small Delta files."
```

---

## 11. Registration & auto-loading (registration is an import side effect)

- The package loader imports every leaf **`automated.py` / `roadmap.py` / `manual.py`**;
  the `@check` runs at import and populates `REGISTRY`. A module that is not imported
  registers nothing and raises nothing.
- Modules starting with `_` are **skipped** — helpers only.
- `id=` is globally unique. Convention by scope/topic:
  `WS-*` workspace · `PL-*` pipeline · `NB-*` notebook · `SPARK-*` Spark config ·
  `DELTA-*` Delta table · `TB-*` lakehouse table · `R-*` report/semantic.
- `ref=` follows the checklist number and is the remediation key. Rough map:
  **1.x** foundation/workspace · **2.x** pipelines (2.1 authoring, 2.2 load, 2.4
  reliability, 2.6 copy) · **3.1–3.2** notebook authoring/Spark perf · **3.3** Delta ·
  **3.4–3.5** Spark config/perf · **4.x** tables/model · **6.x** security (6.1 access,
  6.2 labels, 6.4 secrets) · **11.x** ops (git/deploy) · **12.x** cost (capacity/orphans).

---

## 12. Validate (from `backend/`, PowerShell — use `;` not `&&`)

```powershell
# one-shot: registered? remediation present? N/A-not-FAIL on missing data?
..\.venv\Scripts\python.exe ..\.github\harness\validate_check.py <NEW-ID>
# suite (offline) + lint
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src
```

Adding **one automated** check moves pinned counts — update them to the true value
(never weaken a test):
- `checks_registered == 148` → +1 in [tests/test_api.py](../../backend/tests/test_api.py)
- `EXPECTED_OVERALL`, `EXPECTED_SCORED_CHECKS = 98`, `EXPECTED_RESULT_ROWS = 211`
  in [tests/conftest.py](../../backend/tests/conftest.py)
- the evaluated-count `== 64` in [tests/test_engine.py](../../backend/tests/test_engine.py)

A **roadmap/manual** attestation moves `checks_registered` and result-row counts but
**not** `EXPECTED_OVERALL` / `EXPECTED_SCORED_CHECKS` (it is never scored).

---

## 13. Worked examples

### Workspace scope (a fact + N/A guard)
```python
@check(id="WS-DEPLOY", ref="11.2", title="Deployment pipeline configured",
       pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
       requires=[Resource.WORKSPACE])
def deployment_pipeline(ctx: CheckContext) -> Verdict:
    if not ctx.workspace.has(Resource.WORKSPACE):
        return not_applicable("Workspace metadata could not be read")
    ok = ctx.workspace.deployment_pipeline
    return binary(ok, "Assigned to a deployment pipeline" if ok
                  else "No deployment pipeline assigned")
```

### Workspace scope aggregating a collection (the `covered` pattern)
```python
@check(id="WS-LABELS", ref="6.2.4", title="Sensitivity labels applied to items",
       pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
       requires=[Resource.ITEMS])
def labels(ctx: CheckContext) -> Verdict:
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Item inventory could not be read")
    items = ctx.workspace.items
    labelled = [i for i in items if i.sensitivity_label]
    return covered(len(labelled), len(items),
                   f"{len(labelled)} of {len(items)} items carry a sensitivity label")
```

### Notebook scope (per-object, using a shared detector + N/A on the collection empty)
```python
from auditfast.core.check._notebook import NOTEBOOK_LAYERS, notebook_code

@check(id="NB-IMPORTS", ref="3.2.7", title="Explicit imports (no wildcard)",
       pillar=Pillar.DATA, scope=Scope.NOTEBOOK, severity=Severity.LOW,
       layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS])
def no_wildcard_imports(ctx: CheckContext) -> Verdict:
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")
    code = notebook_code(ctx.obj)
    ok = "import *" not in code
    return binary(ok, "No wildcard imports" if ok else "Uses `from x import *`")
```

### Pipeline scope (per-object, reading the definition via a shared detector)
```python
from auditfast.core.check._pipeline import PIPELINE_LAYERS, activities

@check(id="PL-RETRY", ref="2.4.1", title="Retry policy configured on activities",
       pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE, severity=Severity.MEDIUM,
       layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS])
def retry(ctx: CheckContext) -> Verdict:
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")
    acts = activities(ctx.obj)
    with_retry = [a for a in acts if (a.get("policy") or {}).get("retry")]
    return covered(len(with_retry), len(acts),
                   f"{len(with_retry)} of {len(acts)} activities set a retry policy")
```
