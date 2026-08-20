# API reference

The backend returns **JSON, files, and status codes only** — never HTML. The
React app is a separate deployable that consumes it.

**Interactive docs are generated from the code and are always current:**

| URL | What |
|-----|------|
| http://127.0.0.1:8000/docs | **Swagger UI** — try any endpoint in the browser |
| http://127.0.0.1:8000/redoc | ReDoc reference |
| http://127.0.0.1:8000/openapi.json | Machine-readable schema |

Treat Swagger UI as the source of truth. This page explains the *design* — the
shapes and the reasoning — which a generated schema cannot.

Base path: **`/api/v1`**.

---

## Conventions

**Versioned.** Everything sits under `/api/v1`. A breaking change ships as
`/api/v2` alongside it so existing clients keep working while they migrate.

**One error shape**, whatever fails:

```json
{
  "detail": "Not signed in — complete the Microsoft sign-in first.",
  "code": "authentication_error",
  "correlation_id": "3f2a9c14"
}
```

`code` is stable and machine-readable; `detail` is written for a user. The
`correlation_id` matches `X-Correlation-Id` on the response and the server's log
lines for that request.

| `code` | Status | Meaning |
|--------|--------|---------|
| `validation_error` | 422 | Request did not match the schema |
| `authentication_error` | 401 | Not signed in, or the session expired |
| `workspace_access_denied` | 403 | The user cannot read that workspace |
| `audit_error` | 400 | Run could not start — missing token, unknown check |
| `not_found` / `http_error` | 404 | No such resource |
| `provider_error` | 502 | Upstream Fabric problem; retryable |
| `internal_error` | 500 | Unexpected. Detail is logged, not returned |

**Headers on every response:** `X-Correlation-Id`, `X-Response-Time-ms`. An
inbound `X-Correlation-Id` is honoured, so a trace started in the frontend
continues through the backend.

> **The API is unauthenticated.** The `session` values authenticate *you to
> Fabric*, not the caller to this service. Bind to localhost, or put a gateway in
> front before exposing it. See [scalability.md](scalability.md#security).

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health, version, checks loaded |
| `GET` | `/health/live` | Liveness probe (204, touches nothing) |
| `GET` | `/health/ready` | Readiness probe |
| `POST` | `/login` | Start interactive sign-in |
| `POST` | `/login/azure-cli` | Reuse an existing `az login` |
| `POST` | `/login/device-code` | Start device-code sign-in |
| `GET` | `/login/{session}` | Poll a sign-in |
| `POST` | `/logout` | Discard a session |
| `GET` | `/workspaces` | Workspaces available for selection |
| `GET` | `/workspaces/live` | Every workspace the signed-in user can see |
| `GET` | `/workspaces/diagnostics` | Probe what the token can read |
| `GET` | `/catalog/pillars` | Pillars and their check counts |
| `GET` | `/catalog/layers` | Layer roles |
| `GET` | `/catalog/checks` | The rule library, filterable |
| `GET` | `/catalog/checks/{id}` | One check's metadata |
| `GET` | `/catalog/summary` | Coverage by pillar and scope |
| `POST` | `/audit` | **Submit an audit** (202) |
| `GET` | `/audit/{id}` | Poll status; includes the report when done, plus the interactive questionnaire |
| `POST` | `/audit/{id}/answers` | Submit the reviewer's answers to the self-assessed questionnaire |
| `POST` | `/audit/check` | Run one check synchronously |
| `GET` | `/reports/{id}` | The finished scorecard |
| `GET` | `/reports/{id}/download/{kind}` | Markdown or Excel file |
| `GET` | `/recommendations/{id}` | Findings with remediation, worst first |
| `POST` | `/checklist/assess` | Assess a checklist point: already covered by a check, or a draft proposal (token-free) |
| `GET` | `/history` | Past runs, paged |

---

## The audit lifecycle

Every audit reads the live tenant — there is no offline mode, and
`auth_session` is required. The core flow is three calls.

### 1. Submit

```http
POST /api/v1/audit
```

```json
{
  "pillars": ["Security", "Reliability"],
  "workspaces": [{ "id": "ws-prep-01", "role": "Data Prep" }],
  "auth_session": "3f2a9c14"
}
```

Returns **202 Accepted** immediately:

```json
{
  "audit_id": "81fe3389df6b42d7",
  "status": "running",
  "submitted_at": "2026-07-27T09:03:00Z"
}
```

| Field | Notes |
|-------|-------|
| `pillars` | Empty means all. Deselecting genuinely skips work and Fabric calls |
| `workspaces` | Empty means whatever the project file declares |
| `auth_session` | Completed sign-in session id. Resolved to a token *before* scheduling, so a missing or expired session fails fast with 401 rather than as a dead background job |

The `role` matters: it decides whether pipeline checks run at all, and drives the
layer-content and layer-separation checks.

### 2. Poll

```http
GET /api/v1/audit/{audit_id}
```

`status` moves `queued` → `running` → `succeeded` | `failed`. Poll until it is
terminal; the full report is embedded once it succeeds.

The poll response also carries the run's **interactive questionnaire** — the
self-assessed points to answer while the crawl runs (see below) — and
`answers_submitted`, which flips to `true` once they are recorded:

```json
{
  "audit_id": "81fe3389df6b42d7",
  "status": "running",
  "answers_submitted": false,
  "questionnaire": [
    {
      "id": "Q-OPS-DR",
      "ref": "Q-OPS-1",
      "title": "Disaster-recovery / restore plan documented and tested",
      "pillar": "Operations & Reliability",
      "scope": "workspace",
      "severity": "High",
      "layers": ["*"],
      "question": "Is there a documented, restore-tested DR plan for this workspace?",
      "options": [
        { "value": "tested", "label": "Documented and restore-tested within the last year", "score": 3, "guidance": "" },
        { "value": "documented", "label": "Documented but never tested", "score": 1, "guidance": "Run a restore drill…" },
        { "value": "none", "label": "No DR plan", "score": 0, "guidance": "Document a recovery plan…" }
      ],
      "required": true,
      "automation": "interactive"
    }
  ]
}
```

### 2a. Answer the self-assessed questionnaire

Some Well-Architected points can't be read from a workspace (a *tested* DR plan, a
documented cost review) but a reviewer can attest to them — the **Azure
Well-Architected Review** model. The `questionnaire` above lists those points,
filtered to the run's selected pillars and the audited workspaces' layers. Submit
the reviewer's choices at any time (even while the audit is still running):

```http
POST /api/v1/audit/{audit_id}/answers
```

```json
{ "answers": { "Q-OPS-DR": "tested", "Q-COST-REVIEW": "__skip__" } }
```

Each answer maps an interactive check id to a chosen option `value`. Use
`"__skip__"` (or simply omit an id) to skip a point. Scoring folds the answers
into the report as soon as the automated crawl finishes, **fanned out to every
audited workspace whose layer the check applies to**; each option contributes its
`score` (0–3), while a **skip records N/A and never lowers the score**. The merge
is idempotent and re-applied after the KB background refresh, so answers are never
double-counted or lost.

### 3. Read the report

```http
GET /api/v1/reports/{audit_id}
```

```json
{
  "audit_id": "81fe3389df6b42d7",
  "project_name": "Sales Analytics - Fabric Migration",
  "overall": 57.89473684210527,
  "by_pillar": {
    "Security": { "pct": 48.9, "count": 15 },
    "Governance & Compliance": { "pct": null, "count": 0 }
  },
  "by_layer": { "Data Prep": { "pct": 61.7, "count": 27 } },
  "matrix": {
    "Security": { "Data Prep": 61.1, "Data Storage": 83.3, "Data Operations": 6.7 }
  },
  "layers": ["Data Prep", "Data Storage", "Data Operations"],
  "by_workspace": { "...": {} },
  "counts": { "PASS": 30, "PARTIAL": 9, "FAIL": 18, "INFO": 3 },
  "total_scored": 57,
  "results": [],
  "errors": [],
  "kb": { "served_from_cache": false, "refreshing": false },
  "files": { "markdown": "audit-report.md", "excel": "audit-report.xlsx" }
}
```

**`pct: null` means *not assessed*, not zero.** Render it differently — a pillar
with no checks has not failed.

**`matrix`** is the pillar × layer view: how each architecture layer scores
against each pillar. This is the "inner pillars" model.

**`errors`** is separate from `results` on purpose. A workspace that could not be
read (`WS-ACCESS`) **or a partial crawl** (`WS-READ-INCOMPLETE` — some
`getDefinition`/table reads were blocked or throttled) is a warning, not a
failing check — *we could not look* is a different finding from *we looked and it
was misconfigured*, and it must not drag the score down:

```json
[
  {
    "workspace": "ws-old-01",
    "role": "Data Prep",
    "message": "Access denied (HTTP 403): the signed-in user does not have access…",
    "recommendation": "Confirm the workspace name/ID is correct and that…"
  },
  {
    "workspace": "Explore Fabric - NOIDA",
    "role": "Mixed",
    "message": "42 of 138 notebook definitions could not be read — 42 forbidden (HTTP 401/403). Re-sign-in with Item.ReadWrite.All…",
    "recommendation": ""
  }
]
```

The Markdown/Excel report surfaces the same information in a **Crawl completeness**
section, so a permission/throttle gap is visible instead of hiding behind a low
score.

Status codes: **404** no such audit; **409** it exists but has not finished (so a
polling client can tell "not ready" from "never existed").

---

## Running a single check

```http
POST /api/v1/audit/check
```

```json
{ "check_id": "WS-GIT", "workspace_id": "ws-prep-01", "auth_session": "3f2a9c14", "layer": "Data Prep" }
```

Synchronous, because it only fetches the resources that one check declares and
returns in well under a second. The fastest way to iterate on a rule.

Only possible because checks carry metadata — there was previously no way to
address one by id.

---

## Assessing a checklist point

```http
POST /api/v1/checklist/assess
```

```json
{ "point": "Delta tables are OPTIMIZE-compacted after large writes" }
```

**Token-free** — it answers from the registered catalog (and an optional model),
never contacting Fabric, so it always returns and never fails on a read. The
response says whether the point is already `covered` (with the matching checks and
a confidence score) or `not_covered` (with a draft `@check` proposal — inferred
pillar/scope/severity, a ready-to-edit code skeleton, and the steps to promote
it). It **never registers a check**, so the catalog and the score cannot move.
See [AGENTS.md §11](../AGENTS.md) and [`.github/`](../.github/README.md) for the
agentic authoring loop that turns a proposal into a merged check.

---

## The catalog

Answers from registered metadata alone: no tenant, no sign-in, no audit run. Safe
to call on app load, and the quickest way to review coverage.

```http
GET /api/v1/catalog/checks?pillar=Security
```

```json
[{
  "id": "PL-SECRETS",
  "ref": "6.4.2",
  "title": "No hardcoded secrets in pipeline",
  "pillar": "Security",
  "scope": "pipeline",
  "severity": "Critical",
  "layers": ["Data Operations", "Data Prep", "Mixed"],
  "requires": ["pipelineDefinitions"],
  "weight": 1.0,
  "automation": "automated",
  "interactive": false,
  "question": "",
  "options": [],
  "description": "No credential literal appears anywhere in the pipeline definition."
}]
```

`requires` is what drives resource-aware fetching — see
[architecture.md](architecture.md#resource-driven-fetching).

An **interactive** check (`automation: "interactive"`, `interactive: true`) carries
a non-empty `question` and a list of scored `options` — `{ value, label, score,
guidance }` — instead of reading a resource. Those are the points the reviewer
self-assesses during an audit (see [The audit lifecycle § 2a](#2a-answer-the-self-assessed-questionnaire)).

---

## Sign-in

All flows are read-only and asynchronous. **The Fabric token never reaches the
browser**; the client holds an opaque session id.

```http
POST /api/v1/login          → { "session": "3f2a…", "status": "pending" }
GET  /api/v1/login/{session} → { "status": "pending" | "done" | "error" }
```

Polling always returns **HTTP 200**, including for `status: "error"` — a failed
*sign-in* is a valid answer about the session's state, not a failed request. Read
the body.

`POST /login/azure-cli` is synchronous: the returned session is already `done`.

### Diagnostics

```http
GET /api/v1/workspaces/diagnostics?session=…
```

Reports raw HTTP status per sub-resource for the first few workspaces, so partial
permissions are visible:

```json
{
  "list_status": 200,
  "count": 14,
  "samples": [{ "name": "Sales-Prod-DataPrep", "items_status": 200,
                "items": 6, "pipelines": 2, "roles_status": 403 }]
}
```

That example shows a token that can read items but not role assignments — which
would otherwise look like clean passes.

---

## History

```http
GET /api/v1/history?limit=25&offset=0
```

Summaries only; report bodies are large and this endpoint is polled by
dashboards. `limit` is capped at 100 — an unbounded list endpoint is a future
outage.

---

## Downloads

```http
GET /api/v1/reports/{audit_id}/download/markdown
GET /api/v1/reports/{audit_id}/download/excel
```

`kind` is a whitelist, so a path fragment in the URL can never read an arbitrary
file. Excel follows the client-approved audit structure: Summary, Area Detail,
Checklist, Findings, Risk Register, and Invent. Repeated asset verdicts are
consolidated by control for stakeholder reporting; the deterministic asset-level
results remain the scoring basis. `Invent` assigns each audited workspace a
report-local sequential Workspace ID (`WS1`, `WS2`, ...); `Checklist` uses the
same IDs as matrix columns and shows each workspace's weighted raw score on the
engine's native 0-3 scale for every consolidated control. Percentage scores
remain in the summary views.

> **Current limitation:** report files are written to a fixed filename in the
> output directory and overwritten by each run, so a download returns the most
> recent audit's file regardless of `audit_id`. Fix tracked in
> [scalability.md](scalability.md#report-storage).

---

## Calling the API from tests

No server, no browser — the TestClient drives the real app, middleware
included. Because every audit needs a token, tests patch provider construction
to return a recorded-tenant double instead of calling live Fabric — see
[`backend/tests/conftest.py`](../backend/tests/conftest.py) for the fixture that
does this (`client`) and its `AUTHENTICATED_SESSION` constant:

```python
from fastapi.testclient import TestClient
from auditfast.main import create_app

with TestClient(create_app()) as client:  # here, "client" is patched per conftest.py
    audit_id = client.post(
        "/api/v1/audit", json={"auth_session": AUTHENTICATED_SESSION}
    ).json()["audit_id"]
    # poll /api/v1/audit/{audit_id} until terminal
    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert report["overall"] == 57.89473684210527
```

See [`backend/tests/test_api.py`](../backend/tests/test_api.py).
