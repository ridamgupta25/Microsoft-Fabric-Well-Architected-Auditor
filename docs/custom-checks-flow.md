# Custom Checklist — Architecture & Current Flow

How the **Custom Checks** pipeline is *actually* implemented today, node by node, set
against the intended design — so the gaps (including the "workspace has notebooks but
the check said there are none" problem) are explicit and traceable to code.

> **TL;DR of the gaps**
> - The pipeline is a **plain Python state machine**, not LangGraph.
> - Semantic search uses an **in-memory cosine store**, not Chroma/Qdrant (those are a documented future swap).
> - The **missing-KB fetch is NOT AI-generated code** — it is fixed, read-only strategy functions. So there is *no generated fetch code* to store, and *no second guardrail pass* on fetch code. Only the **audit check** is AI-generated.
> - The custom-checks report is a **separate Markdown section**, not merged into the deterministic report or saved to audit history.
> - The "no notebooks" false pass was a **KB-shape bug in the generated code** (now fixed for new generations).

---

## 1. Intended design (your model)

1. User adds a custom checklist + AI model details (API key, model, base URL).
2. **Guardrail AI** filters unsafe/invalid checks (security). Dropped ones are reported with reasons.
3. **Semantic search (Chroma/Qdrant)** compares custom checks to the default catalog; duplicates are skipped and reported.
4. **Knowledge Base node**: understand each check's requirement → see if the KB has the metadata → build a plan → **generate REST-fetch code** → run it through Guardrail again (must not write to Fabric) → execute → update the KB. Re-check up to 3 times; still-missing checks are skipped with prepared reasons.
5. **Generate the check code**, run it (sandboxed), produce pass/fail/dropped/skipped with recommendation + reason + evidence.
6. User reviews → approves → merges into the **final report** → saved to **history**.

Plus: the AI-generated fetch code and check code should be stored per run in a
gitignored folder, like the KB snapshots are.

---

## 2. Current implementation (what the code does)

Orchestrated by [`ai/orchestrator/pipeline.py`](../backend/src/auditfast/ai/orchestrator/pipeline.py)
`run_check()` — a **plain function sequence**, not LangGraph (the module comment says
so explicitly). Entry point for the API is
[`services/custom_checks_service.py`](../backend/src/auditfast/services/custom_checks_service.py)
`run_custom_checks()`.

```mermaid
flowchart TD
    IN([Custom checks + AI key]) --> SVC[custom_checks_service.run_custom_checks<br/>load KB snapshots for selected workspaces]
    SVC --> SEED[kb_source.seed_session<br/>shared_kb = { workspace_id: snapshot }]
    SEED --> N1

    subgraph PIPE[pipeline.run_check — per check]
        N1[Node 1 · guardrails_agent.screen<br/>regex gate + optional Guardrails-AI seam]
        N2[Node 2 · semantic_router.route<br/>keyword match → embed+cosine → LLM critic]
        N3A[Node 3a · kb_identifier_agent.plan<br/>which KB field? present + valid?]
        N3B[Node 3b · kb_updater_agent.augment<br/>3 fixed read-only strategies via FetchProvider]
        N4[Node 4 · code_gen_agent.generate<br/>LLM writes BaseAuditCheck, 3-attempt loop]
    end

    N1 -- unsafe --> DROP([DROPPED_GUARDRAIL])
    N1 -- safe --> N2
    N2 -- duplicate --> RD([ROUTED_DEFAULT])
    N2 -- unique --> N3A
    N3A -- data present --> N4
    N3A -- data missing --> N3B
    N3B -- fetched from snapshot --> N4
    N3B -- all strategies fail --> KF([KB_FETCH_FAILED + diagnostic])
    N4 --> RUN[Node 5 · local_runner.load_and_run<br/>sandboxed exec at report time]
    RUN --> N6[Node 6 · approve/reject → render_report Markdown]
    N6 --> ARCH[custom_checks_archive.save_run<br/>custom-checks-runs/run_*/]
```

### Node 1 — Guardrails
[`ai/agents/guardrails_agent.py`](../backend/src/auditfast/ai/agents/guardrails_agent.py) `screen(check)`
- **Active today:** a deterministic **regex** gate — length bound, prompt-injection/jailbreak
  patterns, and a write-intent detector (with negation/"is enabled" neutralisers) that
  enforces zero-write.
- **Seam (optional):** [`_guardrails_ai.py`](../backend/src/auditfast/ai/agents/_guardrails_ai.py)
  builds the full Guardrails-AI Guard (ValidLength → DetectJailbreak → DetectPromptInjection →
  custom FabricZeroWriteValidator → DetectPII → SecretsPresent → RestrictToTopic). It
  **auto-activates only when the `guardrails` extra is installed** (torch/transformers +
  Hub token). Not installed here → regex floor runs.
- Dropped checks are tagged `DROPPED_GUARDRAIL` with the failing validator + reason,
  surfaced to the user in the "Not evaluated" area.

### Node 2 — Semantic Router (dedupe against default checks)
[`ai/rag/semantic_router.py`](../backend/src/auditfast/ai/rag/semantic_router.py) `route(check)`
- **Stage 1 (always on):** deterministic keyword/reference match (`match_point`, threshold 0.45).
- **Stage 2 (AI on):** `embed(prompt)` via **FastEmbed** (`BAAI/bge-small-en-v1.5`, 384-dim) →
  nearest in an **in-memory `VectorStore`** (pure-Python cosine), threshold ~0.70 to gather
  candidates.
- **Stage 3 (AI on):** an **LLM Intent Critic** confirms same-intent (so "enable X" is not
  deduped against "disable X"); falls back to cosine ≥ 0.85 when the critic is unavailable.
- Duplicates → `ROUTED_DEFAULT` (reported as reused). **Gap vs design:** the store is
  in-memory, **not Chroma/Qdrant** — those are named as a future swap behind the same
  `index()/nearest()` wrapper, but not wired.

### Node 3a — KB Identifier
[`ai/agents/kb_identifier_agent.py`](../backend/src/auditfast/ai/agents/kb_identifier_agent.py) `plan(check, session)`
- Decides, by meaning (embeddings) + keyword fallback, **which KB field** the check needs,
  from a curated catalog ([`kb_field_catalog.py`](../backend/src/auditfast/ai/rag/kb_field_catalog.py)).
- If present in `shared_kb` and valid → `PROCESSED_CUSTOM` (straight to code-gen).
- If absent → builds a `FetchPlan{field, resource, endpoint}` and leaves the check `PENDING`
  for Node 3b.

### Node 3b — KB Updater (the big design gap)
[`ai/agents/kb_updater_agent.py`](../backend/src/auditfast/ai/agents/kb_updater_agent.py) `augment(check, provider, session)`
- Tries **3 fixed, read-only strategies in order** — `item_rest`, `git_artifact`,
  `workspace_bundle` — through a `FetchProvider` protocol. `429` honours `Retry-After`
  (same strategy); success = `200` + non-empty + schema-valid + JSON-shaped; a `200` with
  junk advances to the next strategy. Success is deep-merged (by item id) into `shared_kb`
  (copy-on-write; the default snapshot is never mutated).
- **In this offline mode**, the provider is
  [`kb_source.SnapshotFetchProvider`](../backend/src/auditfast/ai/orchestrator/kb_source.py):
  it answers every strategy by **reading the already-crawled snapshot** — 200 with the value,
  or 404 when the field wasn't captured. **No new Fabric calls.**
- On total failure → `KB_FETCH_FAILED` with a diagnostic class
  (`INSUFFICIENT_PERMISSIONS` / `ITEM_TYPE_NOT_SUPPORTED` / `RATE_LIMITED` /
  `METADATA_UNAVAILABLE` / `TRANSIENT`), a root cause, a remediation, and a feasibility label.

> **Design vs implementation — read this.** Your model has the AI **generate REST-fetch code**,
> run it through the guardrail a second time, then execute it to enrich the KB. **That is not
> what happens.** The fetch is done by these fixed strategy functions, and offline they only
> re-read the crawl snapshot. Consequently:
> - There is **no AI-generated fetch code** — so nothing of that kind is produced or stored.
> - There is **no second guardrail pass** on fetch code (there is no fetch code).
> - "Fetching missing KB" offline can only surface what the **crawl already captured**; it
>   cannot reach back to Fabric for something the crawl skipped.

### Node 4 — Code Generator (this is the only AI-generated code)
[`ai/agents/code_gen_agent.py`](../backend/src/auditfast/ai/agents/code_gen_agent.py) `generate(check, session)`
- LLM emits a `BaseAuditCheck` subclass whose `evaluate(self, kb)` returns
  `{status, score 0-100, findings, recommendations}`.
- **Bounded 3-attempt loop:** Stage 1 static/safety (AST allow-list), Stage 2 functional
  (load + smoke-run against `shared_kb`), Stage 3 AI review. Feedback from a failed stage is
  fed into the next attempt. Success → `generated_code` set + `FULLY_FEASIBLE`; AI off →
  `AI_REQUIRED`; exhausted → `NOT_FEASIBLE`.

### Node 5 — Local Runner (sandbox)
[`ai/custom_runtime/local_runner.py`](../backend/src/auditfast/ai/custom_runtime/local_runner.py) `load_and_run(code, kb)`
- Runs the generated code under an **AST allow-list**, a **restricted builtins namespace**
  (no `os`/`sys`/`socket`/file/network), and a **thread-watchdog timeout**. Executed at
  report-render time and (for the UI) once per run to show the score before approval.

### Node 6 — HITL + report
[`pipeline.py`](../backend/src/auditfast/ai/orchestrator/pipeline.py) `approve/reject`, `render_report`
- User approves/rejects each generated check. `render_report` produces a **Markdown**
  custom-checks report: a status summary, the ledger, the 0–100 results for approved checks,
  and a "Not evaluated" section (dropped / fetch-failed / AI-required, each with its reason).
- **Gap vs design:** this report is **standalone Markdown**. It is **not folded into the
  deterministic `reporting/` engine, not written to `output/`, and not saved to the audit
  History.** Custom scores (0–100) are deliberately kept out of the deterministic 0–3 scorecard.

---

## 3. The "workspace has notebooks but the check said none" bug

**Root cause.** The KB handed to a generated check is
`kb = { "<workspace-id>": { …snapshot… }, … }` — a dict **keyed by workspace id**, with the
item collections *inside* each workspace. Early generations read `kb.get("notebooks")` at the
**top level**, found nothing, and then the model's own logic did
`if total == 0: score = 100` → a **false pass** ("no notebooks"). The same top-level mistake
hit other artifact types (semantic models, pipelines, …).

**Fix (applied).** The code-gen system prompt in
[`code_gen_agent.py`](../backend/src/auditfast/ai/agents/code_gen_agent.py) now documents the
exact KB shape and requires the model to:
- iterate workspaces with `for ws in kb.values():`;
- read the **name-keyed dicts** (`notebooks`, `semantic_models`, `pipelines`, `environments`,
  `tables`, `refresh_schedules`, `warehouse_audit`, `activators`) and the **lists**
  (`items`, `reports`, `role_assignments`, `connections`, `sql_views`) correctly;
- return **`N/A` with an explicit "none found" finding** when a workspace has zero of the
  relevant objects — instead of silently scoring 100.

**Residual risk.** The fix changes *future* generations only — a check generated before the
fix must be **re-run** to pick up the corrected code. And if a workspace genuinely has no
notebooks captured (crawl scope), the honest result is `N/A`, not a pass.

**Correct shape reference** — one workspace snapshot's keys (from
[`core/models.py`](../backend/src/auditfast/core/models.py) `WorkspaceContext.to_dict`):

| Key | Type | Notes |
|-----|------|-------|
| `display_name`, `layer`, `git_connected`, `deployment_pipeline` | scalar | workspace attributes |
| `notebooks`, `pipelines`, `semantic_models`, `environments`, `tables`, `refresh_schedules`, `warehouse_audit`, `activators` | **dict keyed by name** | iterate `.items()` / `.values()` |
| `items`, `reports`, `role_assignments`, `connections`, `sql_views` | **list of dicts** | iterate directly |

---

## 4. Where artifacts are stored (per-run archive)

Mirroring the KB archive, every custom-checks run now writes a **gitignored** folder:
[`services/custom_checks_archive.py`](../backend/src/auditfast/services/custom_checks_archive.py)
→ `backend/custom-checks-runs/run_<YYYYMMDD_HHMMSS>/`:

```
run_<stamp>/
├── manifest.json              prompts, workspaces, per-check summary (status, score, feasibility)
├── generated/<check_id>.py    the AI-generated AUDIT code (Node 4) + header
├── fetch/<check_id>.json       Node 3b record: fetch_plan + kb_update (strategies, endpoints, fields, provenance, diagnostic)
└── updated_kb/<workspace>.json the shared KB AFTER augmentation, per workspace
```

Settings: `custom_checks_archive_enabled` (default true), `custom_checks_archive_dir`
(`custom-checks-runs`). Added to `.gitignore`.

> Note: `generated/` holds the **audit** code. There is **no** `fetch/*.py` because, as
> above, the fetch is not AI-generated — `fetch/*.json` is the *record* of what the fixed
> strategies did.

The generated audit code also lives **in memory** on `CustomCheck.generated_code` and is
returned in the API ledger row (that is what the UI "View generated code" panel shows); the
archive is a durable copy, not the runtime source.

---

## 5. Design-vs-implementation gap table

| # | Intended | Current | Impact | To close it |
|---|----------|---------|--------|-------------|
| 1 | LangGraph state machine | Plain Python function sequence (`pipeline.py`) | Low — behaviour equivalent | Optional LangGraph wrapper with `interrupt_before` |
| 2 | Chroma/Qdrant vector store | In-memory pure-Python cosine `VectorStore` | Low–med — fine at this scale, not durable/concurrent | Swap behind the existing `index()/nearest()` wrapper |
| 3 | AI generates REST-fetch code, re-guardrailed, executed to enrich KB | Fixed strategies via `FetchProvider`; offline = re-read crawl snapshot | **High** — cannot fetch what the crawl didn't capture; no fetch-code artifact | Add a live `FetchProvider` (real REST reads) and/or an AI-authored, guardrailed, sandboxed fetch step |
| 4 | Guardrail AI (PII, secrets, jailbreak, topic) | Deterministic regex floor; Guardrails-AI seam present but package not installed | Med — PII/secrets/topic not enforced live | `pip install guardrails-ai` + Hub validators |
| 5 | Merge approved checks into final report + save to history | Standalone Markdown report; not in `reporting/`, `output/`, or History | Med — results not persisted with the audit | Fold into the reporting engine + history store |
| 6 | Generated code false-passing "no notebooks" | Fixed via KB-shape prompt (new generations only) | Was **High**, now low | Re-run old checks; optionally validate the runner's result against KB counts |
| 7 | Store generated code per run in gitignore | Done — `custom-checks-runs/` (audit code + fetch record + updated KB) | Resolved | — |

---

## 6. Where to look in code

| Concern | File · symbol |
|---------|---------------|
| Orchestration | [`ai/orchestrator/pipeline.py`](../backend/src/auditfast/ai/orchestrator/pipeline.py) `run_check`, `render_report` |
| Service entry / archive | [`services/custom_checks_service.py`](../backend/src/auditfast/services/custom_checks_service.py) `run_custom_checks`, `_archive_run` |
| KB seeding + offline fetch | [`ai/orchestrator/kb_source.py`](../backend/src/auditfast/ai/orchestrator/kb_source.py) `seed_session`, `SnapshotFetchProvider` |
| Node 1 | [`ai/agents/guardrails_agent.py`](../backend/src/auditfast/ai/agents/guardrails_agent.py) · [`_guardrails_ai.py`](../backend/src/auditfast/ai/agents/_guardrails_ai.py) |
| Node 2 | [`ai/rag/semantic_router.py`](../backend/src/auditfast/ai/rag/semantic_router.py) · [`embeddings.py`](../backend/src/auditfast/ai/rag/embeddings.py) · [`vector_store.py`](../backend/src/auditfast/ai/rag/vector_store.py) |
| Node 3a / 3b | [`ai/agents/kb_identifier_agent.py`](../backend/src/auditfast/ai/agents/kb_identifier_agent.py) · [`kb_updater_agent.py`](../backend/src/auditfast/ai/agents/kb_updater_agent.py) |
| Node 4 / 5 | [`ai/agents/code_gen_agent.py`](../backend/src/auditfast/ai/agents/code_gen_agent.py) · [`ai/custom_runtime/local_runner.py`](../backend/src/auditfast/ai/custom_runtime/local_runner.py) |
| Archive | [`services/custom_checks_archive.py`](../backend/src/auditfast/services/custom_checks_archive.py) |
| UI | [`frontend/src/pages/CustomChecksPage.tsx`](../frontend/src/pages/CustomChecksPage.tsx) |

See also the single-source design in
[`local/Planning/custom-checks-pipeline-plan.md`](../../local/Planning/custom-checks-pipeline-plan.md).
