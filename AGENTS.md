# AGENTS.md — context for AI agents and new engineers

> Read this first. It is the single orientation file for anyone — human or
> model — who opens this repository and needs to understand *what this is*, *how
> it works*, and *how to change it safely*. Deeper detail lives in [`docs/`](docs/README.md).

---

## 1. What this project is

The **Microsoft Fabric Well-Architected Auditor** is a **rule-based, read-only,
fully deterministic** platform that audits Microsoft Fabric workspaces against a
7-pillar Well-Architected model. Every check is a fixed rule with a fixed
threshold and pre-written remediation, so **the same input always produces the
same score**. There is no LLM in the scoring path and scoring must stay
reproducible.

- **Best-practice level, not a deep dive.** It judges whether the *implemented*
  workspaces, pipelines, and notebooks follow Fabric best practices. It does not
  trace data lineage, profile rows, or review business logic.
- **Multi-workspace per project.** A *project* spans one or more workspaces, each
  tagged with a *layer* role; results roll up into one score plus a per-pillar,
  per-workspace, and **pillar × layer** breakdown.
- **Read-only.** The tool only issues GET calls plus the read-only
  `getDefinition`. It never writes to a tenant.

---

## 2. TL;DR for an agent working here

**Invariants — do not break these:**

- `core/` imports **nothing outward** — no FastAPI, no `requests`, no database.
  The engine is pure. Verify with a grep for `fastapi|flask` under
  `core/` and `services/` (must return nothing).
- The **REST API, CLI, and MCP server are three front doors over one service
  layer** — they must never produce different numbers. Put logic in `core/` or
  `services/`, never in a router.
- Scoring is **deterministic**. No randomness, no clock-dependent logic, no LLM
  in a check body. A check is a **pure function** of its `CheckContext`.
- A check reports **N/A**, never FAIL, when the data could not be read. "We could
  not determine this" ≠ "this is misconfigured".
- Checks are registered by an **import side effect** (the `@check` decorator). A
  new check module that is not auto-imported registers nothing and silently never
  runs. See §7.

**Where things go:**

- New rule → a `@check` function under `backend/src/auditfast/core/check/<pillar>/<layer>/`.
- New data source → one `Provider.fetch` implementation in `clients/`; `core/`
  does not change.
- New artifact type → add a `Scope` member + a provider that yields it; the
  engine does not change.

**Always run the tests after a change** (§9). Expected: **171 passed**, offline.

---

## 3. Repository layout

The product lives entirely under `auditfast-core/`. (The rest of the workspace is
audit-methodology documents and checklists that seeded the rules.)

```
auditfast-core/
  backend/                     Python: the engine, API, CLI, MCP
    src/auditfast/
      core/                    PURE domain — imports nothing outward (never imports ai/)
        enums.py               Pillar, Layer, Scope, Automation, Resource, Status, Severity
        models.py              WorkspaceContext (+ read_failures, is_complete), CheckContext, CheckSpec, CheckResult
        engine.py              Generic, scope-driven dispatch; emits WS-READ-INCOMPLETE warnings
        scoring.py             0–3 bands → weighted roll-up → pillar × layer matrix
        check/                 The check library (see §6)
          registry.py          REGISTRY + the @check decorator
          helpers.py           Verdict builders: binary/covered/graded/note/not_applicable
          _notebook.py _pipeline.py ...  shared detectors (underscore = not auto-loaded)
          <pillar>/<layer>/{automated,manual,roadmap}.py
      clients/                 LiveFabricProvider (the only shipped provider) + Provider protocol
        live.py                Fabric REST reads (GET + read-only getDefinition; classifies failures)
        powerbi.py             Power BI REST reads for the FabricIQ tools
      services/                Orchestration; framework-free
        audit_service.py       The one audit path: build_provider → run_engine → aggregate
        audit_runner.py        Background execution, concurrency semaphore, background KB refresh
        context_store.py       The KB: ContextStore + CachingProvider + KBArchive + ArchivingProvider (§5)
        intake_service.py      Checklist-intake: dedup a point vs REGISTRY, draft a proposal (§11)
        auth_service.py        Read-only Entra sign-in (token stays server-side)
        catalog_service.py fabriciq_service.py project.py
      ai/                      Additive AI layer — core/ never imports it (§11)
        matching.py            Deterministic checklist-point → existing-check matcher
        authoring.py           Draft a @check proposal from a plain-language point
        orchestrator/          Optional Azure OpenAI advisory (off unless ai_enabled)
        agents/                authoring_task() steps for the design-time agents
      api/v1/                  FastAPI routers (audit, catalog, checklist, authentication, workspaces, reports, …)
      schemas/                 Pydantic request/response models (catalog, checklist, …)
      config/settings.py       pydantic-settings, AUDITFAST_ env prefix (cache + kb-archive + ai)
      reporting/               Markdown + Excel writers (Markdown has Crawl-completeness + N/A sections)
      database/                Job store: protocol + in-memory implementation
      mcp/ cli.py              Two more adapters over services
    tests/                     pytest, fully offline against a recorded tenant fixture
    config/                    project.example.yaml, remediation.yaml
    kb-cache/                  On-disk KB cache — one snapshot per workspace, TTL'd (git-ignored)
    Fabric workspace kb/       Permanent, timestamped KB archive — one dated folder per run (git-ignored)
    output/                    Generated audit-report.md / .xlsx
  frontend/                    React 18 + TypeScript + Vite + Tailwind + Axios (separate deployable)
    src/{pages,components,services,hooks,context,types}/   (pages include ChecklistPage)
  .github/                     Agentic authoring layer: agents/ skills/ instructions/ harness/ mcp/ (§11)
  .vscode/mcp.json             Wires the auditfast + FabricIQ MCP servers into VS Code
  intake/                      Inputs the authoring agents read (manual CSV, diagrams, domain reference)
  docs/                        The long-form documentation (start at docs/README.md)
```

---

## 4. Domain model (the vocabulary)

All of these are enums in [`core/enums.py`](backend/src/auditfast/core/enums.py),
defined once.

| Term | Meaning |
|------|---------|
| **Project** | One engagement; spans one or more workspaces; defined by a YAML file. |
| **Workspace** | A Fabric workspace, audited as a unit. |
| **Layer** | What a workspace is *for*: `Data Prep`, `Data Storage`, `Data Logs`, `Data Operations`, `Reporting / Semantic`, `Mixed`. (`*`/`ANY` is a check-side sentinel meaning "every layer".) |
| **Pillar** | One of **7**: `Security`, `Governance & Compliance`, `Operations & Reliability`, `Performance & Capacity`, `Cost & Resource Optimization`, `Data Management & Quality`, and `Foundation` (cross-cutting, informational, **never scored**). |
| **Scope** | The object a check inspects: `workspace`, `pipeline`, `notebook` (plus reserved `lakehouse`, `semantic_model`, `report`, `eventhouse`). |
| **Automation** | How a check's verdict is reached: `automated` (verified now), `roadmap` (automatable but needs data not yet fetched — reported as an attestation), `manual` (never machine-verifiable). |
| **Resource** | A unit of data a check needs the provider to fetch. Checks declare `requires=`; the engine fetches only the union of the selected checks' needs. |
| **`ref`** | A dotted string like `3.3.1` pointing at the deep-dive checklist; the key used to look up remediation text. The traceability spine. |

---

## 5. How an audit runs (and the knowledge-base cache)

Audits are **fire-and-poll**: `POST /api/v1/audit` returns an `audit_id`
immediately (202); the work runs in a background worker thread (bounded by a
semaphore); the client polls `GET /api/v1/audit/{id}` until a terminal state.

The provider handed to the engine is a **`CachingProvider`** that reads each
workspace through an **on-disk knowledge base (KB)** under `kb-cache/`, one JSON
snapshot per workspace:

1. **Cache hit** (snapshot age ≤ hard TTL, default 24h) → served from disk, no
   Fabric call. If the snapshot is past the *soft* window (default 1h), a
   **background daemon thread** refreshes it while the current run still uses the
   cached copy. The run is flagged `kb.served_from_cache = true`.
2. **Cache miss / past TTL** → the live provider **crawls the whole workspace**
   (all resources at once), saves the snapshot, and returns it.
3. After a cache-served job finishes, `AuditRunner` re-runs the audit with
   `refresh=True` **in the background**, rebuilds the KB, and updates the stored
   report — "show cached now, refresh silently".

Independently of the cache, an **`ArchivingProvider`** wraps whatever provider
serves a run and writes a **permanent, timestamped snapshot every run** to the KB
archive at `Fabric workspace kb/<workspace>/<workspace>_<YYYYMMDD_HHMMSS>/`
(`workspace.json` + `summary.json`). It never overwrites, so the full crawl
history is kept on disk.

Config (env, `AUDITFAST_` prefix): `CACHE_ENABLED` (default true),
`CACHE_DIR` (`kb-cache`), `CACHE_TTL_SECONDS` (86400), `CACHE_SOFT_SECONDS`
(3600), `CACHE_BACKGROUND_REFRESH` (true), `KB_ARCHIVE_ENABLED` (true),
`KB_ARCHIVE_DIR` (`Fabric workspace kb`). Set `AUDITFAST_CACHE_ENABLED=false`
to bypass the cache and always crawl live.

> **Crawl completeness.** A per-item `getDefinition`/table read is classified as
> `forbidden` (401/403 — will not recover with the same token) or `transient`
> (429/5xx/timeout — may recover), and the counts land on
> `WorkspaceContext.read_failures`. The engine emits a visible
> `WS-READ-INCOMPLETE` warning ("42 of 138 notebook definitions could not be
> read…"), routed to the report's **Crawl completeness** section and the audit
> `errors[]`. `WorkspaceContext.is_complete` is false when any definition/table
> read failed or items/role-assignments were unavailable, and the
> **`CachingProvider` never serves an incomplete snapshot** — it re-crawls, so a
> permission/throttle gap is not frozen for the TTL.

> **Known caveat (performance).** The crawl reads `getDefinition` **one item at a
> time, sequentially, with a 60s per-call timeout**, so a 1000+ item workspace
> can take many minutes on the first (uncached) crawl. Parallelising the per-item
> reads is the main open scalability improvement — see
> [docs/scalability.md](docs/scalability.md).

The engine itself is generic ([`core/engine.py`](backend/src/auditfast/core/engine.py)):

```python
for workspace_id, layer in targets:
    specs = registry.select(pillars=..., layer=layer)      # filter before fetch
    resources = registry.required_resources(specs)         # only what's needed
    workspace = provider.fetch(workspace_id, layer, resources)   # KB or live
    for scope in registry.scopes(specs):
        for name, obj in workspace.objects(scope):
            for spec in specs_for(scope):
                emit(spec.fn(CheckContext(workspace, settings, name, obj)))
```

---

## 6. The check system

**Current coverage: 148 checks** — 64 `automated`, 84 `roadmap`, 0 `manual`.

| Pillar | Checks |
|--------|-------:|
| Data Management & Quality | 53 |
| Operations & Reliability | 33 |
| Performance & Capacity | 23 |
| Security | 16 |
| Cost & Resource Optimization | 15 |
| Governance & Compliance | 7 |
| Foundation (unscored) | 1 |

By scope: workspace 107, pipeline 12, notebook 29. Browse the live catalog with
`GET /api/v1/catalog/checks` or `auditfast checks --pillar Security` — that is the
source of truth, not a hand-maintained list.

**A check** is a pure function returning a `Verdict` (a score + evidence):

```python
@check(id="DELTA-OPTIMIZE", ref="3.3.2", title="Delta tables are OPTIMIZE-compacted",
       pillar=Pillar.PERFORMANCE, scope=Scope.NOTEBOOK, severity=Severity.MEDIUM,
       layers=NOTEBOOK_LAYERS, requires=[Resource.NOTEBOOK_DEFINITIONS])
def delta_optimize(ctx: CheckContext) -> Verdict:
    code = notebook_code(ctx.obj)
    if not writes_delta(code):
        return not_applicable("Notebook does not write Delta tables")
    return binary(bool(OPTIMIZE.search(code)), "OPTIMIZE present" if ... else "no OPTIMIZE")
```

**Verdict builders** ([`core/check/helpers.py`](backend/src/auditfast/core/check/helpers.py)):
`binary(ok, ev)` → 3/0 · `covered(n, total, ev)` → banded ratio · `graded(score, ev)`
→ you supply 0–3 · `note(ev)` → INFO, unscored · `not_applicable(ev)` → N/A, unscored.

### Adding / promoting a check

- The `automated`, `manual`, and `roadmap` leaf modules under each
  `core/check/<pillar>/<layer>/` are auto-imported by the loader. Modules whose
  name starts with `_` (e.g. `_spark.py`) are **skipped** — use them for shared
  helpers.
- `roadmap.py` files are **generated** by [`build-manual-checks.py`](../build-manual-checks.py)
  (repo root). To **promote** a roadmap point to a real automated check:
  1. write the evaluator in the pillar/layer `automated.py` with its `ref`,
  2. add that `ref` to the `AUTOMATED` set in `build-manual-checks.py`,
  3. re-run `python build-manual-checks.py` to regenerate the `roadmap.py`
     modules (the promoted refs drop out),
  4. add remediation text for the `ref` in [`config/remediation.yaml`](backend/config/remediation.yaml)
     (enforced by tests),
  5. update the pinned counts in `tests/` (registry totals, score, row counts).

---

## 7. Registration is an import side effect

`@check` registers into the module-level `REGISTRY` at import time; the check
package's loader imports every leaf `automated`/`manual`/`roadmap` module for that
side effect. **A module not imported registers nothing and raises nothing.**
`/api/v1/health` reports `checks_registered`, and a test asserts the registry
count has not drifted, so an empty catalog is visible instead of silent.

---

## 8. Authentication & access model

Read-only throughout, using a **delegated OAuth2 bearer token** (MSAL). Sign-in
options: interactive browser, existing `az login` (no app registration needed),
or device code (CLI/headless). **The token never leaves the server** — the
browser holds only an opaque session id.

**Normal delegated access is the design target** — most reads (workspace, items,
role assignments, git connection, notebook/pipeline definitions via
`getDefinition`) work with ordinary workspace access; **tenant-admin is not
required**. Checks whose data genuinely needs tenant-admin/capacity-metrics/
audit-log APIs are marked `roadmap` and report an attestation rather than
guessing. `getDefinition` needs an appropriate item scope on the token; when a
definition can't be read the affected checks degrade to N/A, not FAIL.

> Sessions are a process-local dict today — sign-in does not survive a restart or
> span replicas. See [docs/scalability.md](docs/scalability.md#session-storage).

---

## 9. Build, run, and test (Windows PowerShell)

The venv interpreter is `auditfast-core/.venv/Scripts/python.exe`. Do **not**
chain commands with `&&` in PowerShell; use `;`.

```powershell
# Install (from auditfast-core/)
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"

# Run the API
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --port 8000     # http://127.0.0.1:8000/docs

# Run the frontend (separate terminal, from auditfast-core/frontend)
npm install ; npm run dev                                       # http://localhost:5173

# Tests — expected: 171 passed, fully offline
cd backend
..\.venv\Scripts\python.exe -m pytest -q

# Lint
..\.venv\Scripts\python.exe -m ruff check src

# Sanity: how many checks registered (expected 148)
..\.venv\Scripts\python.exe -c "from auditfast.core.check.registry import REGISTRY; print(len(REGISTRY), 'checks')"
```

> **Restart the API after backend changes.** `auditfast serve` does not
> hot-reload; a running server keeps its old code (and its old registry / no KB)
> until restarted. The frontend (Vite) *does* hot-reload.

---

## 10. Current status, limitations, and caveats

| Area | State |
|------|-------|
| Automated checks | 64 today (workspace + pipeline + Spark/Delta notebook checks). 84 more are `roadmap`. |
| Foundation pillar | Informational only — never scored (inventory, access errors, `WS-READ-INCOMPLETE` warnings). |
| Crawl completeness | `getDefinition`/table failures are tracked (`read_failures`, forbidden vs transient), surfaced as `WS-READ-INCOMPLETE` + a report section, and an incomplete snapshot is never cached (§5). |
| Crawl performance | `getDefinition` is read one item at a time (60s timeout) — a 1000+ item workspace can take many minutes on first crawl. Parallelisation is the open scalability item. |
| KB archive | Every run writes a permanent, timestamped snapshot to `Fabric workspace kb/` (git-ignored); never overwritten. |
| Checklist intake | `POST /api/v1/checklist/assess` + the Checklist page dedup a point vs the registry and draft a proposal; the `.github/` agents author + test new checks (§11). Never mutates the registry. |
| Job store | In-memory — history dies with the process, not shared across replicas. |
| Auth sessions | Process-local — breaks under multi-worker/replica. |
| Report files | Fixed filename on local disk — concurrent audits overwrite. |
| Check weights | All 1.0 — roll-up is effectively unweighted (mechanism exists, untuned). |
| AI advisory | Optional (`AUDITFAST_AI_ENABLED`, off by default); enriches the checklist advisory only. **Never in the scoring path** — scoring stays reproducible. |
| Poll timeout | The frontend polls with **no client-side timeout** — a hung/slow backend crawl spins; rely on the KB + background refresh. |

---

## 11. The checklist-intake & agentic authoring layer (additive)

A separate, **additive** capability beside the deterministic engine — it never
registers a check at runtime, never touches a score, and is safe by construction.
Two halves:

**Runtime (the tool).** `POST /api/v1/checklist/assess` and the frontend
**Checklist** page take a plain-language best-practice point and answer,
*token-free*, from the registered catalog:

- [`services/intake_service.py`](backend/src/auditfast/services/intake_service.py)
  → [`ai/matching.py`](backend/src/auditfast/ai/matching.py) deterministically
  ranks the existing checks (dedup). A strong match means the point is **already
  covered** and the existing check is returned.
- Otherwise [`ai/authoring.py`](backend/src/auditfast/ai/authoring.py) drafts a
  **proposal** (inferred pillar/scope/severity + a ready-to-edit `@check`
  skeleton + a remediation stub); when `AUDITFAST_AI_ENABLED` is on,
  `ai/orchestrator/` adds an optional advisory. The proposal is **never
  auto-registered**, so the pinned check count and the score cannot move.

**Design-time (Copilot).** The `.github/` folder drives GitHub Copilot to turn a
proposal into a real, merged, deterministic `@check`:

- `agents/` — a multi-agent workflow: `checklist-author` orchestrates
  `check-researcher` (read-only) → `check-implementer` (writes the check +
  remediation) → `check-reviewer` (runs the harness, tests it live).
- `skills/check-authoring/` — the end-to-end workflow · `instructions/` — the
  invariants, auto-attached when editing `core/check/**` · `harness/` — an
  executable `validate_check.py` (registered? remediation? N/A-not-FAIL?) plus
  pytest + ruff · `mcp/` + `.vscode/mcp.json` — the auditfast + FabricIQ MCP
  servers the agents use (e.g. `run_check` to test a new check live).

The guarantee matches the engine's: a generated check only runs after a human
reviews and merges it, and it must report **N/A, not FAIL, on missing data** — so
extending coverage can never make an existing run start failing.

---

## 12. Read next

| Doc | For |
|-----|-----|
| [docs/getting-started.md](docs/getting-started.md) | Setup, sign-in, running, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | Layers, runtime flow, the KB cache, the core contracts |
| [docs/checks.md](docs/checks.md) | The check taxonomy and how to add/promote one |
| [docs/scoring.md](docs/scoring.md) | How 0–3 scores become pillar percentages |
| [docs/api.md](docs/api.md) | REST reference and design notes |
| [docs/scalability.md](docs/scalability.md) | Scale, Azure deployment, the KB and future AI layer |
