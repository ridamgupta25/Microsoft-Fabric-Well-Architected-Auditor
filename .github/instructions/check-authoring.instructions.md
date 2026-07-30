---
description: "Use when adding or editing a Well-Architected check under backend/src/auditfast/core/check. Encodes the determinism invariants, the N/A-not-FAIL rule, the verdict helpers, and the promotion checklist."
applyTo: "backend/src/auditfast/core/check/**"
---
# Authoring a check

- A check is a **pure function** of its `CheckContext`. No network, no clock, no randomness, no LLM. Same input → same score.
- Return `not_applicable(...)` when the data needed was unavailable (`not ctx.workspace.has(Resource.X)`). **Never FAIL on missing data** — "could not determine" ≠ "misconfigured". This is what keeps a new check from turning into a "could not fetch" failure.
- Build verdicts with the helpers only (`core/check/helpers.py`): `binary(ok, ev)` → 3/0 · `covered(n, total, ev)` → banded ratio · `graded(0..3, ev)` · `note(ev)` → INFO, unscored · `not_applicable(ev)` → N/A, unscored.
- Register with `@check(...)`; declare `requires=[...]` so the engine fetches only what the selected checks need.
- Leaf `automated.py` / `roadmap.py` / `manual.py` are auto-imported for their `@check` side effect. Modules starting with `_` (e.g. `_spark.py`, `_notebook.py`) are shared helpers and are **not** auto-loaded.
- Every `ref` must have remediation text in `backend/config/remediation.yaml` (a test enforces this).
- After adding a check, update the pinned counts: `checks_registered` in `tests/test_api.py`; `EXPECTED_*` in `tests/conftest.py`; evaluated counts in `tests/test_engine.py`.
- Run the harness before claiming done: `..\.venv\Scripts\python.exe -m pytest -q` and `..\.venv\Scripts\python.exe -m ruff check src` (from `backend/`).
