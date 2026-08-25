# Coding standards

The conventions every file in this repository follows. New and changed code must
match these so the codebase stays consistent, modular, and quick to read.

> Golden rule: **a reader should understand a file's purpose in seconds.** Every
> module opens with a docstring saying *what it does and why*; every non-trivial
> function says *what it guarantees*, not *how each line works*.

---

## 1. Structure & modularity

- **One responsibility per module.** A file does one job; if it grows two jobs,
  split it. The custom-checks pipeline is one node per file under `ai/agents/` and
  `ai/rag/`.
- **Small, pure functions.** Prefer pure functions that take inputs and return
  outputs. Side effects (I/O, disk, network) live at the edges (services, clients),
  not in domain logic.
- **Dependency direction is one-way.** `core/` never imports `ai/`. AI code never
  imports `core/`. Generated code runs in `ai/custom_runtime/`, never in `core/`.
  This keeps the deterministic score reproducible.
- **No duplication.** If the same logic appears twice, extract a helper. Reuse
  existing utilities (e.g. `validate_source`, `_WRITE_VERB`) rather than re-writing.

## 2. Documentation & comments

- **Module docstring** (required): 1–3 short paragraphs — what the module is, the
  key contract, and any non-obvious constraint.
- **Function docstring** (required for public functions): one line on what it
  returns/guarantees; add a short paragraph only when the contract is subtle.
- **Comments explain *why*, not *what*.** Do not restate the code. A single line
  that captures intent beats a paragraph. Never leave commented-out code.
- **Name things for the reader.** `router_reuse_threshold`, not `t1`.

## 3. Python

- **Target 3.12+.** `from __future__ import annotations` at the top of every module.
- **Type everything** at boundaries: function params and returns. Prefer `X | None`
  over `Optional[X]`, `list[X]` over `List[X]`.
- **Dataclasses** for state (`@dataclass(slots=True)`); Pydantic for API schemas.
- **Errors:** validate at system boundaries; do not add defensive checks for states
  that cannot occur. Catch narrow exceptions; never bare `except:`. Best-effort
  side effects (archiving, logging) use `except Exception` + `log.exception` and
  never break the request.
- **No hidden globals.** Thresholds and paths live in `config/settings.py`.
- **Imports:** stdlib, third-party, local — grouped, alphabetised within a group.
- **Style is enforced, not debated:** `ruff` for lint + format. Run before commit.

## 4. AI / LLM code

- **Deterministic-first, LLM-optional, fail-closed.** Everything works with the AI
  off; the LLM only *tightens* a decision, never loosens it. AI off → a labelled
  status (`AI_REQUIRED`), never a crash.
- **Structured output + validation + bounded retry.** Parse LLM output into a fixed
  shape, validate it, and retry a bounded number of times with feedback — never
  trust raw model text. (See `code_gen_agent.generate`'s 3-attempt loop.)
- **Treat fetched/tenant data as untrusted** when it feeds a prompt (indirect
  injection).
- **Never execute generated code unsandboxed.** AST allow-list + restricted
  builtins + timeout (`ai/custom_runtime/local_runner.py`). No `os`/`sys`/`socket`/
  file/network in the namespace.
- **Zero-write.** No generated or fixed code path may create/update/delete Fabric.

## 5. Frontend (React + TS)

- **Function components + hooks.** One component per concern; extract sub-components
  when a render block grows.
- **Types mirror the API** in `types/api.ts`; snake_case fields match the backend.
- **No `any`.** `npm run typecheck` must be clean (EXIT 0).
- **Services own I/O** (`services/*.ts`); components stay declarative.

## 6. Tests

- **Every behaviour has a test.** New logic ships with tests in the same PR.
- **Deterministic:** inject fakes (fake embedder, fake generator/reviewer) rather
  than calling a live model or network. Autouse fixtures reset shared registries.
- **Never weaken a validated check to make a test pass.** Check logic is validated
  and must not be tampered with.

## 7. Change hygiene

- **Track every change** in [`custom-checks-changelog.md`](custom-checks-changelog.md)
  during large work, so the diff is reviewable and reversible.
- **Small, focused commits.** One logical change per commit; keep unrelated edits
  out.
- **Keep validated logic sacred.** The deterministic checks in `core/check/` are
  validated; do not alter their behaviour. Additive AI features live beside them.
