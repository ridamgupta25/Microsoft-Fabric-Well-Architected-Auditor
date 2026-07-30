# Architecture

How the system is organised, what depends on what, and exactly what happens when
an audit runs.

---

## 1. Vocabulary

Five terms carry the whole domain.

| Term | Meaning |
|------|---------|
| **Project** | One engagement. Spans **one or more** Fabric workspaces. Defined by a YAML file. |
| **Workspace** | A Fabric workspace, audited as a unit. |
| **Layer** | What a workspace is *for*: `Data Prep`, `Data Storage`, `Data Logs`, `Data Operations`, `Reporting / Semantic`, `Mixed`. Your "inner pillars". |
| **Pillar** | One of seven quality attributes: `Security`, `Governance & Compliance`, `Operations & Reliability`, `Performance & Capacity`, `Cost & Resource Optimization`, `Data Management & Quality`. Plus `Foundation` — cross-cutting, informational, never scored. |
| **Scope** | The kind of object a check inspects: `workspace`, `pipeline`, `notebook`, and (reserved) `lakehouse`, `semantic_model`, `report`, `eventhouse`. |
| **Automation** | How a check's verdict is reached: `automated` (verified now), `roadmap` (automatable but needs data the provider does not yet fetch — reported as an attestation), `manual` (never machine-verifiable). |

Every check also carries a **`ref`** — a dotted string like `2.4.1`. It points at
a line item in the 13-area deep-dive checklist and is the key used to look up
remediation text. It is the traceability spine between the automated tool and the
manual audit instrument.

All of these are enums in [`core/enums.py`](../backend/src/auditfast/core/enums.py),
defined exactly once. Previously pillars were bare strings and layer roles were
re-declared in three places.

---

## 2. Layers

```mermaid
flowchart TD
    UI[React SPA<br/>separate deployable]
    API[api/v1<br/>FastAPI routers]
    CLI[cli.py]
    MCP[mcp/server.py]
    SVC[services/<br/>orchestration]
    CORE[core/<br/>engine · checks · scoring]
    CLIENTS[clients/<br/>Fabric · fixture]
    REP[reporting/]
    DB[(database/<br/>job store)]
    FAB[(Microsoft Fabric<br/>REST, read-only)]

    UI -->|REST/JSON| API
    API --> SVC
    CLI --> SVC
    MCP --> SVC
    API --> DB
    SVC --> CORE
    SVC --> CLIENTS
    SVC --> REP
    CORE --> CLIENTS
    CLIENTS --> FAB
```

**The dependency rule: arrows point inward. `core/` imports nothing outward.**

`core/` is pure domain logic — no FastAPI, no `requests`, no database.
`services/` orchestrates but imports no web framework. Verify it:

```powershell
# Returns nothing — the audit engine has no web dependency.
Select-String -Path backend/src/auditfast/core/*.py,backend/src/auditfast/services/*.py -Pattern "fastapi|flask"
```

That property is why the REST API, the CLI, and the MCP server produce identical
results: one implementation, three front doors.

---

## 3. Module map

| Path | Responsibility |
|------|----------------|
| [`core/enums.py`](../backend/src/auditfast/core/enums.py) | Pillar, Layer, Scope, Resource, Status, Severity |
| [`core/models.py`](../backend/src/auditfast/core/models.py) | `WorkspaceContext` (+ `read_failures`, `is_complete`), `CheckContext`, `CheckSpec`, `CheckResult` |
| [`core/scoring.py`](../backend/src/auditfast/core/scoring.py) | Bands, ratings, weighted roll-up, pillar×layer matrix |
| [`core/engine.py`](../backend/src/auditfast/core/engine.py) | Generic scope-driven dispatch |
| [`core/check/registry.py`](../backend/src/auditfast/core/check/registry.py) | The single registry (`REGISTRY`) and the `@check` decorator |
| [`core/check/helpers.py`](../backend/src/auditfast/core/check/helpers.py) | `Verdict` and the builders |
| [`core/check/<pillar>/<layer>/`](../backend/src/auditfast/core/check/) | 148 checks — `automated`, `manual`, and generated `roadmap` modules per pillar × layer |
| [`clients/`](../backend/src/auditfast/clients/) | `LiveFabricProvider` (the only shipped provider) and the `Provider` protocol |
| [`services/audit_service.py`](../backend/src/auditfast/services/audit_service.py) | The one audit path; builds the caching provider |
| [`services/context_store.py`](../backend/src/auditfast/services/context_store.py) | The KB: `ContextStore` (disk cache) + `CachingProvider` + `KBArchive` + `ArchivingProvider` (permanent timestamped archive) |
| [`services/audit_runner.py`](../backend/src/auditfast/services/audit_runner.py) | Background execution, concurrency limits, background KB refresh |
| [`services/catalog_service.py`](../backend/src/auditfast/services/catalog_service.py) | Catalog questions; no I/O |
| [`services/project.py`](../backend/src/auditfast/services/project.py) | Project YAML → `ProjectConfig` |
| [`services/auth_service.py`](../backend/src/auditfast/services/auth_service.py) | Read-only Entra sign-in |
| [`services/intake_service.py`](../backend/src/auditfast/services/intake_service.py) | Checklist-intake: dedup a point vs the registry, draft a proposal (token-free, never mutates `REGISTRY`) |
| [`api/v1/`](../backend/src/auditfast/api/v1/) | 9 routers (incl. `checklist`), the REST surface — `POST /checklist/assess` added |
| [`api/deps.py`](../backend/src/auditfast/api/deps.py) | Dependency injection |
| [`api/errors.py`](../backend/src/auditfast/api/errors.py) | Exception → HTTP mapping |
| [`schemas/`](../backend/src/auditfast/schemas/) | Pydantic request/response models |
| [`config/`](../backend/src/auditfast/config/) | Settings, structured logging |
| [`database/`](../backend/src/auditfast/database/) | Job model + repository pattern |
| [`ai/matching.py`](../backend/src/auditfast/ai/matching.py) | Deterministic checklist-point → existing-check matcher (dedup) |
| [`ai/authoring.py`](../backend/src/auditfast/ai/authoring.py) | Draft a `@check` proposal from a plain-language point |
| [`ai/orchestrator/`](../backend/src/auditfast/ai/orchestrator/) | Optional Azure OpenAI advisory — off unless `ai_enabled`; never in the scoring path |
| [`mcp/server.py`](../backend/src/auditfast/mcp/server.py) | MCP tools over the same services (catalog, audit, FabricIQ) |

---

## 4. What happens during an audit

```mermaid
sequenceDiagram
    participant UI as React
    participant API as api/v1/audit
    participant R as AuditRunner
    participant S as audit_service
    participant E as core/engine
    participant P as CachingProvider

    UI->>API: POST /api/v1/audit
    API->>API: resolve token (live only)
    API->>R: submit(...)
    R-->>API: audit_id
    API-->>UI: 202 {audit_id, status}
    Note over R: background task
    R->>S: run_audit (in a worker thread)
    S->>E: run(caching provider, targets, settings)
    loop each workspace
        E->>E: select checks for (pillars, layer)
        E->>P: fetch(id, layer, required resources)
        alt fresh snapshot in KB (age ≤ TTL)
            P-->>E: cached WorkspaceContext (no Fabric call)
        else miss or past TTL
            P->>P: crawl Fabric live, save snapshot to kb-cache/
        end
        loop each scope, each object
            E->>E: run the checks for that scope
        end
    end
    E-->>S: list[CheckResult]
    S->>S: aggregate + write reports
    S-->>R: AuditRun
    UI->>API: GET /api/v1/audit/{id} (poll)
    UI->>API: GET /api/v1/reports/{id}
```

**Why fire-and-poll:** a tenant-wide audit issues many sequential Fabric calls
and can take minutes. A synchronous endpoint would tie up a worker and time out
at any gateway in front of it.

**Why a worker thread:** `run_audit` is synchronous and I/O-bound. Calling it
directly from an async handler would block the event loop and stall every other
request. `AuditRunner` dispatches it via `asyncio.to_thread` behind a semaphore
that caps concurrent audits.

**Why a knowledge-base cache:** a full live crawl issues one `getDefinition` per
notebook and per pipeline and can take minutes. The `CachingProvider` serves each
workspace from an on-disk snapshot (the KB) so repeat runs are near-instant, and
re-crawls Fabric only on a cache miss or once a snapshot ages past its TTL. When
a run is served from cache, `AuditRunner` re-runs it with `refresh=True` in the
background and updates the stored report — "show cached now, refresh silently".
See [§6a](#6a-the-knowledge-base-cache).

---

## 5. The engine is generic

The engine knows nothing about any specific check, pillar, or artifact type. It
dispatches purely on `Scope`:

```python
for workspace_id, layer in targets:
    specs = registry.select(pillars=..., layer=layer)
    resources = registry.required_resources(specs)   # only fetch what's needed
    workspace = provider.fetch(workspace_id, layer, resources)

    for scope in registry.scopes(specs):
        for obj_name, obj in workspace.objects(scope):
            for spec in specs_for(scope):
                emit(spec.fn(CheckContext(workspace, settings, obj_name, obj)))
```

Adding a new artifact type — lakehouses, semantic models, notebooks — means:

1. add a `Scope` member,
2. teach a provider to yield those objects,
3. write checks tagged with that scope.

[`engine.py`](../backend/src/auditfast/core/engine.py) does not change.

---

## 6. Contract 1 — the workspace context

The most important interface in the system. Every provider emits this, which is
why checks run unmodified against live data or an offline fixture.

```python
@dataclass
class WorkspaceContext:
    id: str
    display_name: str
    layer: Layer
    capacity_id: str | None
    git_connected: bool
    deployment_pipeline: bool
    role_assignments: list[RoleAssignment]
    items: list[Item]
    pipelines: dict[str, dict]        # name -> parsed pipeline definition
    notebooks: dict[str, dict]        # name -> parsed .ipynb
    tables: dict[str, dict]           # lakehouse table name -> {type, format, columns}
    shortcuts: dict[str, list]        # lakehouse -> OneLake shortcuts
    semantic_models: dict[str, dict]  # name -> parsed TMSL facts
    git_details: dict                 # provider/org/repo/branch/dir when Git-connected
    unavailable: set[Resource]        # what could NOT be read at all
    read_failures: dict[str, dict]    # per-resource partial-read counts (see below)
```

`unavailable` is what separates *"we could not determine this"* from *"this is
not configured"*. A check whose data landed there reports **N/A** instead of
failing — a network error must not look like a misconfiguration.

`read_failures` records **partial** one-per-item read failures (notebooks,
pipelines, tables, semantic models): for each resource, how many were
`attempted`, `read`, and `failed`, split into `forbidden` (401/403) and
`transient` (429/5xx/timeout). `is_complete` (a property) is false when any
`read_failures` exist or `items`/`role_assignments` are `unavailable` — the
signal the `CachingProvider` uses to refuse to serve a partial snapshot.

The context is JSON-serializable via `to_dict()` / `from_dict()`, which is what
the knowledge-base cache persists to disk ([§6a](#6a-the-knowledge-base-cache)).

**Adding a data source** means writing one method:

```python
def fetch(self, workspace_id, layer, resources) -> WorkspaceContext: ...
```

Nothing in `core/` changes.

### Resource-driven fetching

Checks declare what they need via `requires=`. The engine unions the
requirements of the *selected* checks and passes that set to the provider, so a
run that scores no pipeline checks never pays for `getDefinition` — one call per
pipeline, the most expensive operation in a live audit.

| Resource | Live cost |
|----------|-----------|
| `WORKSPACE` | 1 call, always made |
| `ITEMS` | 1 call |
| `ROLE_ASSIGNMENTS` | 1 call |
| `GIT` | 1 call |
| `PIPELINE_DEFINITIONS` | **one call per pipeline** |
| `NOTEBOOK_DEFINITIONS` | **one `getDefinition` per notebook** (a long-running operation) |
| `TABLE_SCHEMAS` | one list-tables call per lakehouse (columns need the SQL endpoint; left empty) |
| `SHORTCUTS` | one call per lakehouse |
| `SEMANTIC_MODEL_DEFINITIONS` | one `getDefinition` per semantic model (TMSL) |

---

## 6a. The knowledge-base cache

A full live crawl is expensive — the `getDefinition` calls above dominate. So the
provider the engine actually receives is a **`CachingProvider`**
([`services/context_store.py`](../backend/src/auditfast/services/context_store.py))
wrapping the live provider and a **`ContextStore`** (one JSON snapshot per
workspace under `kb-cache/`).

```mermaid
flowchart TD
    F[CachingProvider.fetch] --> Q{snapshot in KB?}
    Q -- "age ≤ TTL (24h)" --> S[serve from disk · served_from_cache=true]
    Q -- "miss / past TTL" --> C[crawl Fabric live · ALL resources · save snapshot]
    S --> H{age > soft (1h)?}
    H -- yes --> B[background daemon thread refreshes snapshot]
    H -- no --> D[return]
    C --> D
    B --> D
```

- **Granularity is per workspace, not per check.** On a refresh the provider
  crawls *all* resources at once and stores one snapshot; on a hit it returns
  that snapshot and no check calls Fabric itself.
- **Freshness** is governed by two windows: a hard **TTL** (`AUDITFAST_CACHE_TTL_SECONDS`,
  default 24h — older snapshots are re-crawled inline) and a soft window
  (`AUDITFAST_CACHE_SOFT_SECONDS`, default 1h — older snapshots are served at once
  and refreshed in the background).
- **Completeness-aware.** Each per-item `getDefinition`/table read is classified
  as `forbidden` (401/403 — won't recover with the same token) or `transient`
  (429/5xx/timeout — may recover), and the counts are recorded on
  `WorkspaceContext.read_failures`. `WorkspaceContext.is_complete` is false when
  any definition/table read failed or `items`/`role_assignments` were
  unavailable, and the **`CachingProvider` never serves an incomplete snapshot**
  — it re-crawls, so a permission/throttle gap is not frozen for the TTL. The
  engine surfaces the gap as a `WS-READ-INCOMPLETE` warning (the report's *Crawl
  completeness* section and the audit `errors[]`).
- **Permanent archive.** An **`ArchivingProvider`** wraps whatever provider
  serves a run and writes a fresh, timestamped snapshot **every run** to
  `Fabric workspace kb/<workspace>/<workspace>_<YYYYMMDD_HHMMSS>/`
  (`workspace.json` + `summary.json`). It never overwrites, so the full crawl
  history is kept — separate from the single-file cache.
- **Config:** `AUDITFAST_CACHE_ENABLED` (default true; set false to always crawl
  live), `AUDITFAST_CACHE_DIR` (`kb-cache`), the two windows above, plus
  `AUDITFAST_KB_ARCHIVE_ENABLED` (default true) and `AUDITFAST_KB_ARCHIVE_DIR`
  (`Fabric workspace kb`).

---

## 7. Contract 2 — checks carry metadata

A check declares everything about itself at registration:

```python
@check(
    id="PL-RETRY", ref="2.4.1",
    title="Retry policy configured on activities",
    pillar=Pillar.OPERATIONS, scope=Scope.PIPELINE,
    severity=Severity.HIGH, layers=PIPELINE_LAYERS,
    requires=[Resource.PIPELINE_DEFINITIONS],
)
def retry_policy(ctx: CheckContext) -> Verdict:
    """Activities that call external systems retry before failing."""
    acts = activities(ctx.obj)
    with_retry = [a for a in acts if (a.get("policy") or {}).get("retry", 0) >= 1]
    return covered(len(with_retry), len(acts),
                   f"{len(with_retry)} of {len(acts)} activities have a retry policy")
```

The check returns a small `Verdict` — a score plus evidence. The engine combines
it with the registered `CheckSpec` to build the full `CheckResult`. That split is
why a check body is three lines instead of ten: id, ref, title, pillar, severity,
weight, scope, workspace name, and object name all come from elsewhere.

Because metadata exists before execution, the system can:

- list the catalog without running an audit (`GET /catalog/checks`),
- run one check by id (`POST /audit/check`),
- filter *before* running, so deselecting a pillar skips its Fabric calls.

See [checks.md](checks.md) for the full catalog and how to add one.

---

## 8. Registration is an import side effect

The `@check` decorator registers at import time. [`core/check/__init__.py`](../backend/src/auditfast/core/check/__init__.py)
**auto-discovers** them by walking the package tree and importing every leaf
module named `automated`, `manual`, or `roadmap` — so a new
`<pillar>/<layer>/automated.py` is picked up with no `__init__.py` edit. Helper
modules whose name starts with `_` (e.g. `_spark.py`) are skipped.

> **Gotcha:** a check module *not* named `automated` / `manual` / `roadmap` (or
> hidden behind a `_` prefix) registers nothing, raises nothing, and its checks
> silently never run. `registered_modules()` exists so a test can assert the
> tree was fully imported, and `/api/v1/health` reports `checks_registered` so
> an empty catalog is visible rather than silent.

---

## 9. Authentication

Read-only throughout. Three ways in, all yielding a delegated bearer token:

| Flow | Used by |
|------|---------|
| Interactive browser sign-in | Web UI, primary |
| Existing `az login` | Web UI, no app registration needed |
| Device code | CLI / headless |

MSAL runs on a background thread; the caller receives a session id and polls.
**The token never leaves the server** — the browser holds only an opaque session
id, so a compromised browser cannot yield a Fabric access token.

**Normal delegated access is the design target.** Most reads — workspace, items,
role assignments, git connection, and notebook/pipeline/semantic-model
definitions via `getDefinition` — work with ordinary workspace access;
tenant-admin is **not** required. Checks whose data genuinely needs
tenant-admin, capacity-metrics, or audit-log APIs are marked `roadmap` and
report an attestation rather than guessing. When a definition cannot be read the
affected checks degrade to **N/A**, never FAIL.

> **Known limitation:** sessions live in a process-local dict, so sign-in does
> not survive a restart and is not shared across replicas. See
> [scalability.md](scalability.md#session-storage).

---

## 10. Frontend

React 18 + TypeScript + Vite + Tailwind + Axios, a **completely separate
deployable**. The API returns JSON only and renders no HTML.

| Path | Role |
|------|------|
| `src/services/` | The only modules that call the API; one Axios instance |
| `src/types/api.ts` | The API contract, mirrored from the Pydantic schemas |
| `src/hooks/useAsync.ts` | Shared async state: data, loading, error, reload |
| `src/context/AuditContext.tsx` | Mode, sign-in session, most recent audit |
| `src/pages/` | Dashboard, Run audit, Report, Catalog, History, Sign in |
| `src/components/` | `PillarMatrix`, `FindingsTable`, shared UI primitives |
| `src/utils/format.ts` | Rating bands, percentage and duration formatting |

In development Vite proxies `/api` to the backend, so the browser makes
same-origin requests and no CORS is involved. In production the app is built to
static files and points at the API origin via `VITE_API_BASE_URL`.

The Run-audit page **polls `GET /audit/{id}` until a terminal state with no
client-side timeout** — a tenant-wide crawl can take minutes, and the KB plus
background refresh keep repeat runs fast. (The former 600s cap was removed.)

---

## 11. Cross-cutting concerns

| Concern | Where | Note |
|---------|-------|------|
| **Settings** | `config/settings.py` | pydantic-settings, `AUDITFAST_` prefix |
| **Logging** | `config/logging.py` | Structured JSON when hosted; correlation id on every record |
| **Correlation ids** | `api/middleware.py` | Generated or honoured from the request, echoed in the response |
| **Errors** | `api/errors.py` | Every failure returns the same JSON shape with a correlation id |
| **Validation** | `schemas/` | Pydantic; invalid requests fail before reaching a service |
| **Persistence** | `database/repositories/` | Protocol + in-memory implementation |

Internal exception messages are never returned to clients — they can carry
workspace ids and file paths. The client gets a correlation id; the detail goes
to the log.

---

## 12. Known limitations

Honest list, with pointers.

| Limitation | Impact |
|------------|--------|
| Job store is in-memory | History dies with the process; not shared across replicas |
| Auth sessions are process-local | Breaks under multi-worker or multi-replica deployment |
| Reports written to a fixed filename | Concurrent audits overwrite each other's files |
| All check weights are 1.0 | Roll-up is effectively unweighted; the mechanism exists but is untuned |
| AI is advisory-only | The checklist-intake layer (`ai/matching`, `ai/authoring`) is real, but the optional model advisory is off by default and **never** in the scoring path |
| KB cache is per-workspace, not per-check | A partial crawl records `read_failures` and is flagged incomplete, so the `CachingProvider` re-crawls rather than serving it; the granularity is still whole-workspace |
| Crawl is sequential | `getDefinition` is read one item at a time with a 60s timeout, so a 1000+ item workspace can take many minutes on the first crawl — parallelisation is the open item |
| KB snapshots live on local disk | Not shared across replicas (the permanent archive too) |

All are addressed in [scalability.md](scalability.md).
