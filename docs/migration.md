# Migration: Flask → FastAPI, vanilla JS → React

What changed, what was reused, and why. The governing constraint was **preserve
the working audit logic** — this was a restructure, not a rewrite.

> **Historical record.** This page describes the original Flask → FastAPI
> migration, when the tool had **20 checks** under `core/checks/`. The codebase
> has since grown to **164 checks** under `core/check/<pillar>/<layer>/` across
> seven pillars (including 16 interactive, self-assessed points), plus an on-disk
> knowledge-base cache. Figures and paths below are preserved as they were at
> migration time; see [architecture.md](architecture.md) and
> [checks.md](checks.md) for the current state.

---

## 1. Proof the business logic survived

The strongest claim available, made first.

Before touching anything, a full baseline was captured from the original
implementation: every check result for the fixture tenant then bundled with the
product as `sample_data/tenant.json`, with status, score, coverage, pillar,
severity, and evidence text. After the restructure the same audit produces:

| Measure | Before | After |
|---------|--------|-------|
| Overall score | `57.89473684210527` | `57.89473684210527` |
| Scored checks | 57 | 57 |
| Result rows | 60 | 60 |
| Status counts | 30 / 9 / 18 / 3 | 30 / 9 / 18 / 3 |
| Per-field diffs across all 60 rows | — | **0** |

Bit-exact, not "close". Verified at three levels: the engine directly, through
the FastAPI TestClient, and over real HTTP against a running uvicorn server. The
value is pinned in [`tests/conftest.py`](../backend/tests/conftest.py) so any
future change to a check, a band, or the roll-up fails loudly.

---

## 2. Strategy

Sequenced so the system was working at every step:

| Phase | Change | Risk |
|-------|--------|------|
| 0 | `pyproject.toml`, `src/` layout, remove `sys.path` hacks | none |
| 1 | Enums + `CheckSpec` registry; port all 20 checks mechanically | low — parity test pins the scores |
| 2 | Generic engine + providers with resource-driven fetching | medium |
| 3 | Weighted scoring + pillar × layer matrix | low — weights default to 1.0, numbers unchanged |
| 4 | Replace the web layer with FastAPI; add CLI and MCP adapters | low — services were already framework-free |
| 5 | Replace the vanilla UI with React | isolated — API contract unchanged |

**Why this order.** Phases 0–3 touch only code that has no web dependency, so the
web layer could not break them. By phase 4 the only Flask-coupled code left was
~200 lines of route files, which made the framework swap nearly free.

**The key enabling property**, which already existed: `services/` imported no web
framework. `service.run_audit()` was already the single entry point both the CLI
and the web layer called. The migration preserved that shape and added two more
callers.

---

## 3. Reused without modification

Logic that carried across unchanged in substance — moved and re-typed, but the
rules and thresholds are identical.

| Component | Now at | Note |
|-----------|--------|------|
| **All 20 check rules** | `core/checks/{workspace,pipeline}/` | Same thresholds, same regexes, same evidence strings — the parity test proves it |
| **Scoring bands** | `core/scoring.py` | 100→3, 80–99→2, 50–79→1, <50→0 |
| **Rating bands** | `core/scoring.py` | Critical/High/Medium/Good/Excellent |
| **Markdown report** | `reporting/markdown.py` | Import lines only |
| **Excel report** | `reporting/excel.py` | Import lines only |
| **Console report** | `reporting/console.py` | Import lines only |
| **MSAL sign-in flows** | `services/auth_service.py` | Interactive, Azure CLI, device code — logic untouched |
| **Device-code flow** | `security/device_flow.py` | Unchanged |
| **Fabric REST endpoints** | `clients/live.py` | Same URLs, same read-only calls |
| **Project + remediation YAML** | `config/` | Same schema; one missing key added |

---

## 4. Refactored, and why

| Component | Change | Reason |
|-----------|--------|--------|
| **Check registration** | Two bare lists → one registry with `CheckSpec` metadata | Pillar/ref/severity lived inside function bodies and did not exist until a check ran. No catalog, no run-one-by-id, no filtering before execution |
| **Check signatures** | `fn(ws, s)` and `fn(ws, name, defn, s)` → `fn(ctx)` | Every new object type invented a new signature |
| **Check bodies** | ~10 lines → ~3 | Each repeated its own id, ref, pillar, workspace name and role. The engine now supplies them from the spec |
| **Engine** | Per-type branches → generic `Scope` dispatch | Adding lakehouse/semantic-model checks meant a new registry and a new engine branch each time |
| **Workspace context** | Untyped `dict` → `WorkspaceContext` dataclass | Every check did `.get()` with defensive defaults; no field was discoverable |
| **Pillar / layer names** | Bare strings in 3 places → enums in `core/enums.py` | Had to be edited in sync by hand |
| **Data fetching** | Always fetch everything → driven by `requires` | Live runs paid for one `getDefinition` per pipeline even when no pipeline check was selected |
| **Pillar filtering** | After the run → before it | Deselecting a pillar saved no work |
| **Remediation** | Module-global + `set_remediation()` mutator | Two concurrent audits would trample each other |
| **Project config** | 4-tuple return → `ProjectConfig` dataclass | Callers had to remember positional order |
| **Web layer** | Flask blueprints → FastAPI routers | Adds OpenAPI/Swagger, Pydantic validation, DI, async |
| **Audit execution** | Synchronous request → background job + polling | A tenant-wide run would time out at any gateway |
| **Frontend** | DOM string concatenation → React + TypeScript | `results.js` was 79 lines of string building; the matrix view would not have scaled |

---

## 5. Removed

| File | Why |
|------|-----|
| `backend/wsgi.py` | Imported `auditfast.web`, which no longer exists — **it was broken**. WSGI is also wrong for an ASGI app |
| `backend/requirements.txt` | Still listed `flask` and `flask-cors`, contradicting `pyproject.toml`. Two competing dependency sources |
| `backend/run.py` | A `sys.path` shim, superseded by the installed `auditfast` console script |
| `OVERVIEW.md` | Duplicated `README.md`; the two were drifting apart |
| `frontend/js/`, `frontend/css/` | Replaced by the React app |
| `core/checks/base.py` | Split into `registry.py` (registration) and `helpers.py` (verdicts) |

---

## 6. Bugs found while migrating

Each surfaced because the restructure forced the code to be re-read, and each is
now covered by a test.

**1. Falsy-registry bug.** `CheckRegistry` defines `__len__`, so an *empty*
registry is falsy. `registry or REGISTRY` therefore fell through to the global
registry, meaning a caller passing a fresh registry silently polluted the real
catalog. Found by a test that got 0 results where it expected 3.

```python
# wrong — an empty registry is falsy
(registry or REGISTRY).register(spec)
# right
(registry if registry is not None else REGISTRY).register(spec)
```

**2. Failed reads scored as failures.** The old client swallowed every exception
to `None`, so a network blip on `git/connection` was indistinguishable from "Git
is not connected" and scored `0`. Providers now record unreadable resources in
`WorkspaceContext.unavailable`, and affected checks return `N/A` — excluded from
scoring entirely.

**3. Two different failure counts.** `aggregate()` counted access errors as
failures; the API recomputed without them. The Markdown report and the browser
disagreed by one on any run with an unreadable workspace. Access errors are now
separated at the service boundary, so every consumer reads one aggregate.

**4. `.gitignore` protected the wrong path.** `config/*.yaml` is anchored to the
file's own directory, so it matched a top-level `config/` that no longer existed
after the `backend/` split. Real engagement files — tenant ids, client ids,
production workspace GUIDs — were tracked:

```console
$ git check-ignore -v backend/config/my-client.yaml
(no output — would have been committed)
```

**5. A check with no remediation text.** `WS-LAYER-SEP` (ref `1.1.2`) had no
entry in `remediation.yaml`, so it rendered findings with an empty
recommendation. A missing key is silent by design; a test now asserts every
scoreable check's ref has text.

---

## 7. What the frontend gained

The API contract was designed first, so the React app was built against a stable
target:

- **`by_layer` and `matrix`** — the pillar × layer heatmap, which the old UI had
  no data for.
- **Typed contract** — `src/types/api.ts` mirrors the Pydantic schemas, so a
  field rename is a compile error rather than a runtime `undefined`.
- **One error path** — every API failure normalises to `ApiRequestError`, so
  components never inspect Axios internals.
- **Audit history** — previously there was none; reports were overwritten.

---

## 8. Behaviour that intentionally did **not** change

So nobody "fixes" these thinking they are oversights:

- **Scores are identical.** Weights exist but are all `1.0`.
- **Evidence strings are identical**, character for character. They appear in the
  Excel risk register, which clients read.
- **Read-only.** Still only GETs plus the read-only `getDefinition`.
- **Deterministic.** Still no AI anywhere in the scoring path.
- **`Data Storage` workspaces still fail layer separation** when they contain
  pipelines. That is a correct finding, not a regression.

---

## 9. Removing mock mode as a product feature

A follow-up change, after the FastAPI/React migration above had landed. The
product initially shipped **two** ways to run an audit — `mock` (an offline
fixture tenant, selectable from the UI and the CLI) and `live` (a real Fabric
tenant). Mock mode was removed entirely: no mode selector, no fixture data
reachable from the browser, no `--mock` CLI flag, no `mode` field anywhere in
the API contract. **Every audit now reads the live tenant.**

### Why

The mode toggle was surfacing fixture data as if it were real: the run-audit
page defaulted to `mode="mock"` and listed the fixture tenant's three
workspaces regardless of whether the user had signed in, which read as the
tool showing "your workspaces" when it was actually showing sample data. The
fix was not a UI tweak — it was removing the concept of a non-live source from
the product entirely, so that ambiguity cannot recur.

### What changed

| Layer | Change |
|-------|--------|
| `clients/` | `MockProvider` deleted. `LiveFabricProvider` is the only shipped provider |
| `services/audit_service.py` | `build_provider()` always requires a token; `MOCK`/`LIVE` constants and every `mode` parameter removed |
| `cli.py` | `--mock`/`--live` flags removed; `run` always signs in via the device-code flow first |
| `schemas/audit.py` | `AuditMode` enum removed; `mode` dropped from every request/response schema |
| `database/models.py` | `AuditJob.mode` removed |
| `mcp/server.py` | Tools that need Fabric data now take an explicit `token` argument — MCP has no browser to sign in through |
| `config/project.example.yaml` | `mock:` section removed |
| Frontend | Mode dropdown removed from the header; `RunAuditPage` only fetches workspaces once signed in, and shows a connect gate otherwise |

### Test strategy

Removing mock mode from the product does not remove the need for deterministic,
offline tests — hitting live Fabric from CI would be slow, non-reproducible,
and require tenant credentials in a build pipeline. The fixture data and the
provider that serves it moved to `backend/tests/fixtures/` instead of being
deleted:

* `tests/fixtures/tenant.json` — the same recorded tenant, relocated.
* `tests/fixtures/provider.py` — `RecordedProvider`, satisfying the same
  `Provider` protocol `LiveFabricProvider` does. It is never imported by
  `auditfast.*`; only `tests/conftest.py` constructs it.

For the engine and scoring tests (`test_engine.py`), this is a drop-in
replacement — they take a `provider` fixture and never cared which
implementation it was.

For the API lifecycle tests (`test_api.py`), removing the `mode` field meant
`auth_session` became required for every audit, which needed a second fixture:
the `client` fixture in `conftest.py` monkeypatches
`auth_service.token_for` (only a known test session id resolves to a token —
anything else still 401s, so the negative tests are unaffected) and
`audit_service.build_provider` (always returns `RecordedProvider`, so no
network call is ever made). Every test that submits an audit passes
`AUTHENTICATED_SESSION` from `conftest.py` where it previously passed
`mode: "mock"`.

Net effect: the parity numbers in Section 1 above are unchanged, the suite
still runs fully offline, and the word "mock" no longer appears anywhere in
`src/auditfast`.
