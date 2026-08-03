# Managing checks — add, remove, and run automated vs. self-assessed

A practical playbook for adding or removing checks, and for running an audit
**with only the automated checks** (skipping the self-assessed questionnaire) or
with both. Written to be shared as-is with colleagues.

> **Reference companion:** [checks.md](checks.md) is the full catalog + design
> reference. This page is the step-by-step *how-to*.

---

## 0. The two kinds of check (pick one)

| I want the tool to… | Kind | File to edit | Registered with |
|---|---|---|---|
| **Verify it automatically** from the workspace/pipeline/notebook data | **Automated** | `automated.py` | `@check(...)` |
| **Ask the reviewer** a scored question (Azure Well-Architected Review style) | **Interactive** (self-assessed / manual user-input) | `questionnaire.py` | `questionnaire_check(...)` |

Both live under `backend/src/auditfast/core/check/<pillar>/<layer>/`, e.g.
`cost_resource_optimization/data_operations/`.

- **Pillar folders:** `security`, `governance_compliance`, `operations_reliability`,
  `performance_capacity`, `cost_resource_optimization`, `data_management_quality`,
  `foundation` (foundation has no layer subfolder).
- **Layer folders:** `data_prep`, `data_storage`, `data_logs`, `data_operations`,
  `reporting_semantic`.

> The folder is just where the file lives. The **real** pillar comes from the
> `pillar=` argument and the applicable layers from `layers=`.

---

## 1. Add an **automated** check

**Where:** the `automated.py` under the pillar × layer it belongs to.

### Steps

1. **Write the check** in `automated.py`:

   ```python
   @check(
       id="WS-DF-GEN1", ref="1.2.3",
       title="No deprecated Dataflow Gen1 items",
       pillar=Pillar.DATA, scope=Scope.WORKSPACE,
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

   Rules that matter:
   - **`id`** must be unique. **`ref`** is the checklist reference and the
     remediation key.
   - **`requires=[...]`** declares the data it reads — it drives fetching. A check
     that reads data it did not declare sees empty values.
   - **Guard unreadable data** with `ctx.workspace.has(...)` → `not_applicable()`.
     *"Could not read"* must be **N/A, never FAIL.**
   - **Return a verdict builder**, never a raw result: `binary(ok, ev)` ·
     `covered(n, total, ev)` · `graded(0–3, ev)` · `note(ev)` · `not_applicable(ev)`.
   - **Evidence is a fact with numbers** — `"3 of 12 items…"`, not `"labels are bad"`.

2. **Add remediation text** (required for automated checks) in
   `backend/config/remediation.yaml`, keyed by `ref`:

   ```yaml
   "1.2.3": "Migrate Dataflow Gen1 items to Gen2 and retire the originals."
   ```

   A missing entry yields an empty recommendation — a test fails if you skip this.

3. **Restart the API** and **re-pin the tests** (see §6).

That's it — no `__init__.py` edit. The loader auto-discovers every `automated.py`.

---

## 2. Add an **interactive** (self-assessed / manual user-input) check

**Where:** the `questionnaire.py` under the pillar × layer. Create the file if it
does not exist (name it exactly `questionnaire.py`).

### Steps

1. **Write the question** with scored options:

   ```python
   from auditfast.core.check import Option, questionnaire_check
   from auditfast.core.enums import Layer, Pillar

   questionnaire_check(
       id="Q-COST-TAGGING", ref="Q-COST-3",
       title="Capacities are tagged for cost attribution",
       pillar=Pillar.COST,
       layers=(Layer.ANY,),   # ANY = ask on every workspace
       question="Are Fabric capacities tagged so cost can be attributed to owners?",
       options=(
           Option("tagged", "All capacities tagged and reported", 3),
           Option("partial", "Some capacities tagged", 1,
                  guidance="Extend tagging to every capacity and automate the report."),
           Option("none", "No tagging", 0,
                  guidance="Tag capacities by team/workload and report cost back."),
       ),
   )
   ```

   Rules that matter:
   - **`id`** unique; **`ref`** conventionally `Q-<PILLAR>-<n>`.
   - **`options`** are ordered best→worst; each has a **score `0–3`**. Give every
     non-top option **`guidance`** — it becomes the finding's recommendation.
   - **`layers=`** controls which workspaces are asked. `Layer.ANY` = all;
     otherwise e.g. `(Layer.STORAGE, Layer.REPORTING)`.
   - **No `remediation.yaml` entry needed** — the guidance lives on the options.

2. **Restart the API** and **re-pin the tests** (see §6).

The reviewer answers this during the audit; a chosen option scores `0–3`,
**skipping records N/A (never a low score)**, and the answer is merged into the
report for every applicable workspace.

---

## 3. Remove a check

1. **Delete** the `@check` function (in `automated.py`) or the
   `questionnaire_check(...)` call (in `questionnaire.py`). If a `questionnaire.py`
   becomes empty, delete the file.
2. **Automated only:** optionally remove its `ref` line from
   `backend/config/remediation.yaml` (leaving it is harmless).
3. **Restart the API** and **re-pin the tests** (see §6) — counts go **down**.

> To remove an auto-generated **`roadmap`** point instead, see
> [checks.md § Promoting](checks.md#5-promoting-a-roadmap-point-to-automated) —
> `roadmap.py` files are generated, not hand-edited.

---

## 4. Run automated only, or include the self-assessed questionnaire

The automated score is **always** computed from automated checks only — the audit
engine skips interactive checks entirely. Interactive points are a **separate,
optional questionnaire**; they only affect the score **if the reviewer answers
them**. So "test only automated, not manual" simply means *don't answer the
questionnaire*.

### Option A — CLI (cleanest automated-only path)

The CLI never shows the questionnaire. It runs the engine and writes the report,
which is purely the automated result.

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast run --project config\project.example.yaml
# add --pillars "Security,Cost & Resource Optimization" to narrow it
```

### Option B — REST API (automated-only = don't submit answers)

```text
POST /api/v1/audit           → start the automated crawl (returns an audit_id)
GET  /api/v1/audit/{id}       → poll; the report here is AUTOMATED-ONLY
```

The response also carries a `questionnaire` array, but it is **optional**. If you
never call `POST /api/v1/audit/{id}/answers`, the report stays automated-only.
Submit answers only when you *want* the self-assessed points scored in.

### Option C — Web UI (skip the questionnaire)

1. Start the audit as usual.
2. While it crawls, the **Self-assessed checklist** panel appears.
3. To test **automated only**: click **"Submit answers & view report"** without
   choosing any option — or pick **"Skip this check"** on each. Skipped points are
   recorded as **N/A** and excluded from the score, so the score reflects the
   automated checks only.
4. To include them: choose an option per question, then submit.

> If no interactive points apply to the selected workspaces/pillars, the report
> opens automatically — there is nothing to skip.

### Permanently disable the questionnaire (optional)

If a team never wants the self-assessed points, **remove the `questionnaire.py`
modules** (§3). The automated audit is unaffected. Prefer *skipping* over deleting
unless you truly never want them.

---

## 5. Narrow a run to specific pillars

Deselecting a pillar skips **all** its checks (automated *and* interactive) and
even its Fabric calls.

- **UI:** untick pillars on the Run Audit page.
- **CLI:** `--pillars "Security,Operations & Reliability"`.
- **API:** `"pillars": ["Security", "Operations & Reliability"]` in the `POST /audit` body (empty/omitted = all).

To review coverage without running anything:

```powershell
..\.venv\Scripts\python.exe -m auditfast checks --pillar Security
# or GET /api/v1/catalog/checks?pillar=Security
```

---

## 6. After ANY change — do these three things

1. **Restart the API server.** `auditfast serve` does **not** hot-reload; a running
   server keeps the old checks until restarted. (The frontend hot-reloads on its
   own.)

   ```powershell
   # stop the old server (Ctrl+C in its terminal), then:
   cd backend
   ..\.venv\Scripts\python.exe -m auditfast serve --port 8000
   ```

2. **Verify what's registered:**

   ```powershell
   ..\.venv\Scripts\python.exe -c "from auditfast.core.check.registry import REGISTRY; print(len(REGISTRY), 'checks')"
   ```

3. **Re-pin the tests** (they fail on purpose when counts drift — that keeps a
   coverage change from being silent). Run them and update the pinned numbers to
   the values the failures report:

   ```powershell
   ..\.venv\Scripts\python.exe -m pytest -q
   ```

   | You changed | Update in |
   |---|---|
   | **Any** check added/removed | Nothing for `checks_registered` — `backend/tests/test_api.py` asserts `== len(REGISTRY)`, which self-adjusts |
   | An **automated** check | the automated count `== 64` in `backend/tests/test_engine.py`; `EXPECTED_OVERALL`, `EXPECTED_SCORED_CHECKS`, `EXPECTED_RESULT_ROWS` in `backend/tests/conftest.py` (the score/row counts shift) |
   | An **interactive** check | the interactive count `== 0` in `backend/tests/test_engine.py` (the automated score is **unchanged** — the engine skips it) |

   Then lint: `..\.venv\Scripts\python.exe -m ruff check src`.

---

## Cheat sheet

| Task | Do this |
|---|---|
| Add automated check | new `@check` in `automated.py` + remediation ref → restart → re-pin |
| Add self-assessed check | new `questionnaire_check` in `questionnaire.py` → restart → re-pin |
| Remove a check | delete the function/call (+ optional remediation line) → restart → re-pin |
| **Test automated only** | run via **CLI**, or in the **UI skip the questionnaire**, or via **API don't POST answers** |
| Include self-assessed | answer the questionnaire in the UI, or `POST /audit/{id}/answers` |
| Only some pillars | untick pillars (UI) · `--pillars` (CLI) · `"pillars": [...]` (API) |
| See coverage | `auditfast checks` · `GET /catalog/checks` |
