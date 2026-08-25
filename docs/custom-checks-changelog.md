# Custom Checks — change log

A running record of the work on the Custom Checks pipeline, so a large change stays
reviewable and reversible. Newest first. Follows
[coding-standards.md](coding-standards.md).

> **Invariant honoured throughout:** the validated deterministic checks in
> `core/check/` are **never** altered. Every change here is additive AI/UI work.

---

## 2026-08-24 — cross-run AI memory + generated-code reuse

- New [`services/custom_checks_memory.py`](../backend/src/auditfast/services/custom_checks_memory.py)
  `CustomChecksMemory` — a gitignored JSON store (`custom-checks-runs/memory.json`)
  keyed by `check_id`, holding non-secret metadata, the last approve/reject decision,
  and the validated generated code.
- **Generated-code reuse:** `code_gen_agent.generate` accepts a `code_cache`; a check
  whose code is remembered **reuses it (skips the LLM)** — but the reused code is still
  re-validated (AST) and smoke-run, so safety is unchanged. Threaded through
  `pipeline.run_custom_checks → run_batch → run_check → generate` (all default `None`,
  so existing callers are unaffected).
- Service records the ledger to memory after each run and loads the cache before it
  ([`custom_checks_service.py`](../backend/src/auditfast/services/custom_checks_service.py)).
- Settings: `custom_checks_memory_enabled` (default true), `custom_checks_memory_file`.
- Tests: `tests/test_custom_checks_memory.py`. Full custom-checks + code-gen suites
  stay green; deterministic checks untouched.
- **CrewAI decision:** not adopted. It would add a heavy second orchestration model
  over a working, tested pipeline for no behavioural gain. The valuable half —
  durable memory — is implemented above.

## 2026-08-24 — UI: merge Custom Checks onto the Checks page

- New [`frontend/src/pages/ChecksPage.tsx`](../frontend/src/pages/ChecksPage.tsx) — a
  single **Checks** page with two in-page tabs, **Default checks** (the existing
  catalog) and **Custom checks** (the existing run flow). Both tabs **reuse their
  existing page components unchanged**, so neither flow's behaviour is altered.
- Routing: `/catalog` → `ChecksPage`; `/custom-checks` → `ChecksPage` with the custom
  tab preselected (deep link preserved). Removed the separate **Custom checks** nav
  item ([`App.tsx`](../frontend/src/App.tsx), [`MainLayout.tsx`](../frontend/src/layouts/MainLayout.tsx)).
- Frontend typecheck clean.

## 2026-08-24 — REST-fetch code generation + generated-code artifacts

**Goal:** make the AI-generated code visible and stored per run (both the
*missing-KB REST fetch* code and the *audit check* code), in gitignored folders —
and close the "no notebooks" false-pass bug.

Added / changed:

- **Node 3b fetch-code generation** — new
  [`ai/agents/fetch_code_gen_agent.py`](../backend/src/auditfast/ai/agents/fetch_code_gen_agent.py):
  `generate_fetch_code(check, *, ai)` asks the model for **read-only** REST-fetch
  code for a check's `FetchPlan`, validates it (AST allow-list + write-verb screen),
  and stores it on `check.fetch_code`. Generation-and-validation only; execution
  stays on the safe fixed strategies (see "Deferred" below).
- **State** — `CustomCheck.fetch_code` field + serialised in `to_dict`
  ([`ai/orchestrator/state.py`](../backend/src/auditfast/ai/orchestrator/state.py)).
- **Pipeline** — `run_check` now generates fetch code when a `FetchPlan` exists and
  AI is on ([`ai/orchestrator/pipeline.py`](../backend/src/auditfast/ai/orchestrator/pipeline.py)).
- **Per-run archive** — folders renamed/added so both kinds of generated code are
  visible ([`services/custom_checks_archive.py`](../backend/src/auditfast/services/custom_checks_archive.py)):
  - `generated_checks/<check_id>.py` — the AI audit code (was `generated/`)
  - `generated_fetch/<check_id>.py` — the AI REST-fetch code (new)
  - `fetch/<check_id>.json`, `updated_kb/<ws>.json`, `manifest.json` (unchanged)
  Still gitignored under `custom-checks-runs/`.
- **"No notebooks" bug fix** — the code-gen system prompt now documents the exact KB
  shape (`kb = {workspace_id: snapshot}`; name-keyed dicts vs lists) and requires
  `N/A` + evidence when a workspace has zero of the relevant objects, instead of a
  silent 100. Applies to **new** generations; re-run old checks to pick it up.
- **Docs** — [`coding-standards.md`](coding-standards.md),
  [`custom-checks-flow.md`](custom-checks-flow.md), this changelog.
- **Tests** — `tests/test_fetch_code_gen_agent.py`. Existing suites stay green
  (deterministic checks untouched).

**Decision — LangGraph/LangChain:** we keep the lightweight, fully-tested custom
orchestrator rather than swapping to LangChain agents. Rationale: a full swap would
touch validated flows and add heavy dependencies for no behavioural gain — the
industry-standard principles that matter (structured output, schema validation,
bounded retry, HITL interrupt) are already implemented. A thin optional LangGraph
`StateGraph` wrapper remains a documented future option (see Deferred).

---

## 2026-08-24 — earlier in the day (context)

- Per-run archive first version (`custom-checks-runs/`), workspace picker + empty
  state on the Custom Checks page, richer result cards (score/evidence/reco/
  diagnostics), FastEmbed activated for live semantic matching, Guardrails-AI seam
  wired (`_guardrails_ai.py`), fixed a corrupted `core/enums.py` block, VS Code
  debug config + `debugging.md`.

---

## Deferred / tracked backlog (bigger, needs its own change)

| Item | Why deferred | Risk |
|------|--------------|------|
| **Execute** generated REST-fetch code live | Needs a live, read-only Fabric client + auth session (offline mode has none) and a hardened network sandbox; executing generated network code is a large security surface | High — build behind a gated live provider + guardrail, off by default |
| **Merge Custom Checks into the deterministic checks page** (one page, not a separate tab) | UI/IA change spanning `RunAuditPage`/report views | Medium |
| **Fold custom report into `reporting/` + save to History** | Touches the reporting engine and history store | Medium |
| **Install Guardrails-AI Hub validators** (PII/secrets/jailbreak/topic) | Heavy deps (torch/transformers) + Hub token | Low (seam ready) |
| **Optional LangGraph `StateGraph` wrapper** (`interrupt_before` for HITL) | Additive wrapper over existing nodes | Low |
| **Swap in-memory vector store → Qdrant** | Durability/concurrency at scale | Low (wrapper isolates it) |
