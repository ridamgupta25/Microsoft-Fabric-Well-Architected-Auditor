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
| `audit_error` | 400 | Run could not start — bad mode, unknown check |
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
| `GET` | `/audit/{id}` | Poll status; includes the report when done |
| `POST` | `/audit/check` | Run one check synchronously |
| `GET` | `/reports/{id}` | The finished scorecard |
| `GET` | `/reports/{id}/download/{kind}` | Markdown or Excel file |
| `GET` | `/recommendations/{id}` | Findings with remediation, worst first |
| `GET` | `/history` | Past runs, paged |

---

## The audit lifecycle

The core flow. Three calls.

### 1. Submit

```http
POST /api/v1/audit
```

```json
{
  "mode": "mock",
  "pillars": ["Security", "Reliability"],
  "workspaces": [{ "id": "ws-prep-01", "role": "Data Prep" }],
  "auth_session": null
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
| `mode` | `mock` or `live`. Live requires `auth_session` |
| `pillars` | Empty means all. Deselecting genuinely skips work and Fabric calls |
| `workspaces` | Empty means whatever the project file declares |
| `auth_session` | Session id from a sign-in. Validated *before* scheduling, so a bad session fails fast with 401 |

The `role` matters: it decides whether pipeline checks run at all, and drives the
layer-content and layer-separation checks.

### 2. Poll

```http
GET /api/v1/audit/{audit_id}
```

`status` moves `queued` → `running` → `succeeded` | `failed`. Poll until it is
terminal; the full report is embedded once it succeeds.

### 3. Read the report

```http
GET /api/v1/reports/{audit_id}
```

```json
{
  "audit_id": "81fe3389df6b42d7",
  "project_name": "Sales Analytics - Fabric Migration",
  "mode": "mock",
  "overall": 57.89473684210527,
  "by_pillar": {
    "Security": { "pct": 48.9, "count": 15 },
    "Performance Efficiency": { "pct": null, "count": 0 }
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
  "files": { "markdown": "audit-report.md", "excel": "audit-report.xlsx" }
}
```

**`pct: null` means *not assessed*, not zero.** Render it differently — a pillar
with no checks has not failed.

**`matrix`** is the pillar × layer view: how each architecture layer scores
against each pillar. This is the "inner pillars" model.

**`errors`** is separate from `results` on purpose. A workspace that could not be
read is a warning, not a failing check — *we could not look* is a different
finding from *we looked and it was misconfigured*, and it must not drag the score
down:

```json
[{
  "workspace": "ws-old-01",
  "role": "Data Prep",
  "message": "Access denied (HTTP 403): the signed-in user does not have access…",
  "recommendation": "Confirm the workspace name/ID is correct and that…"
}]
```

Status codes: **404** no such audit; **409** it exists but has not finished (so a
polling client can tell "not ready" from "never existed").

---

## Running a single check

```http
POST /api/v1/audit/check
```

```json
{ "check_id": "WS-GIT", "workspace_id": "ws-prep-01", "mode": "mock", "layer": "Data Prep" }
```

Synchronous, because it only fetches the resources that one check declares and
returns in well under a second. The fastest way to iterate on a rule.

Only possible because checks carry metadata — there was previously no way to
address one by id.

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
  "description": "No credential literal appears anywhere in the pipeline definition."
}]
```

`requires` is what drives resource-aware fetching — see
[architecture.md](architecture.md#resource-driven-fetching).

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
file. Excel has three sheets: Scorecard, Checks, Risk Register.

> **Current limitation:** report files are written to a fixed filename in the
> output directory and overwritten by each run, so a download returns the most
> recent audit's file regardless of `audit_id`. Fix tracked in
> [scalability.md](scalability.md#report-storage).

---

## Calling the API from tests

No server, no browser — the TestClient drives the real app, middleware included:

```python
from fastapi.testclient import TestClient
from auditfast.main import create_app

with TestClient(create_app()) as client:
    audit_id = client.post("/api/v1/audit", json={"mode": "mock"}).json()["audit_id"]
    # poll /api/v1/audit/{audit_id} until terminal
    report = client.get(f"/api/v1/reports/{audit_id}").json()
    assert report["overall"] == 57.89473684210527
```

See [`backend/tests/test_api.py`](../backend/tests/test_api.py).
