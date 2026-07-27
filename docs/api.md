# JSON API reference

The Flask app serves the single-page front end at `/` and a JSON API under
`/api/`. Every route is a thin adapter — it parses the request, calls a service
function, and serializes the result. The audit logic lives in
[`services/`](../backend/auditfast/services/) and
[`core/`](../backend/auditfast/core/).

Base URL when running `auditfast serve`: `http://127.0.0.1:8000`

---

## Conventions

**Errors** return `{"error": "<message>"}` with a non-200 status. The messages are
written for end users, not developers — they are rendered directly in the UI.

**CORS** is open to all origins for `/api/*`
([`web/__init__.py:44`](../backend/auditfast/web/__init__.py#L44)) so a separately
hosted front end can call the API.

> **The API is unauthenticated.** There is no API key, session cookie, or origin
> check. The `session` values below authenticate *you to Fabric*, not the caller
> to this service — and anyone who can reach the port can use a session another
> user created. Bind to localhost only, which is what `serve` does.

---

## Endpoints at a glance

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | [`/api/config`](#get-apiconfig) | Pillars and auth defaults for app startup |
| `POST` | [`/api/auth/login`](#post-apiauthlogin) | Interactive browser sign-in |
| `POST` | [`/api/auth/azcli`](#post-apiauthazcli) | Reuse an existing `az login` |
| `POST` | [`/api/auth/start`](#post-apiauthstart) | Begin a device-code sign-in |
| `POST` | [`/api/auth/poll`](#post-apiauthpoll) | Poll a sign-in for completion |
| `GET` | [`/api/workspaces`](#get-apiworkspaces) | Workspaces from the fixture or project file |
| `POST` | [`/api/workspaces/live`](#post-apiworkspaceslive) | Every workspace the signed-in user can see |
| `POST` | [`/api/diag`](#post-apidiag) | Probe what the token can actually read |
| `POST` | [`/api/run`](#post-apirun) | **Run an audit** |
| `GET` | [`/api/download/{kind}`](#get-apidownloadkind) | Download the generated report |

---

## GET /api/config

Called once at startup. Drives the pillar checkboxes and pre-fills the sign-in
form.

```json
{
  "project": "config/project.example.yaml",
  "project_name": "project.example.yaml",
  "pillars": ["Reliability", "Security", "Cost Optimization",
              "Operational Excellence", "Performance Efficiency"],
  "auth": { "tenant_id": "", "client_id": "" }
}
```

Placeholder values like `<TENANT_ID>` are blanked out before being returned.
`pillars` is the server's `PILLARS` constant, so adding a pillar server-side
flows to the UI with no front-end change.

---

## POST /api/auth/login

Opens the Microsoft sign-in in a browser. **Returns immediately** — MSAL runs on a
background thread. Poll for completion.

```json
{ "email": "user@contoso.com", "tenant_id": null, "client_id": null }
```

```json
{ "session": "3f2a…", "message": "A browser window is opening for user@…" }
```

With no `client_id` configured, the service falls back to Microsoft's first-party
Azure CLI public client so the user can sign in with just an email. Tenants that
block this via Conditional Access require a real app registration.

---

## POST /api/auth/azcli

Reuses an existing `az login` session. No app registration needed. Synchronous —
the returned session is already `done`.

```json
{ "session": "9c1b…", "status": "done", "message": "Signed in via Azure CLI." }
```

Returns 400 if the Azure CLI is not installed or not signed in.

---

## POST /api/auth/start

Device-code flow, for headless environments.

```json
{ "tenant_id": "…", "client_id": "…", "scopes": ["…"] }
```

```json
{
  "session": "7e4d…",
  "user_code": "F8K3NPQR",
  "verification_uri": "https://microsoft.com/devicelogin",
  "message": "To sign in, use a web browser to open…",
  "expires_in": 900
}
```

Both `tenant_id` and `client_id` are required here.

---

## POST /api/auth/poll

```json
{ "session": "3f2a…" }
```

```json
{ "status": "pending" }
```

`status` is `pending`, `done`, or `error` (with an `error` field). Always
HTTP 200, including for `error` — check the body, not the status code. An unknown
session id also reports `error`.

The UI polls this on an interval until it stops returning `pending`.

---

## GET /api/workspaces

Query parameters: `project` (optional, defaults to the server's project) and
`mode` (`mock` or `live`).

```json
[
  { "id": "ws-prep-01", "name": "Sales-Prod-DataPrep",
    "role": "Data Prep", "items": 6, "pipelines": 2 }
]
```

In `mock` mode this reads the tenant fixture and returns real item counts. In
`live` mode it can only echo what the project YAML declares — `name` equals `id`
and both counts are `null`, because enumerating contents needs a token. Use
`/api/workspaces/live` instead once signed in.

---

## POST /api/workspaces/live

Enumerates every workspace the signed-in user can access, regardless of the
project file.

```json
{ "session": "3f2a…" }
```

Same row shape as above, with `role: ""` and null counts — the user assigns layer
roles in the UI. Returns 400 if the session has no token.

---

## POST /api/diag

Connectivity probe. Useful when a live run returns nothing and you need to see
whether the problem is the token, the permissions, or the workspace.

```json
{
  "list_status": 200,
  "count": 14,
  "samples": [
    { "name": "Sales-Prod-DataPrep", "items_status": 200,
      "items": 6, "pipelines": 2, "roles_status": 403 }
  ],
  "error": null
}
```

Reports raw HTTP status codes per sub-resource for the first three workspaces, so
partial permissions are visible — the example above shows a token that can read
items but not role assignments.

---

## POST /api/run

The main endpoint.

### Request

```json
{
  "project": "config/project.example.yaml",
  "mode": "mock",
  "pillars": ["Security", "Reliability"],
  "workspaces": [
    { "id": "ws-prep-01", "role": "Data Prep", "name": "Sales-Prod-DataPrep" }
  ],
  "auth_session": null
}
```

| Field | Notes |
|-------|-------|
| `project` | Optional; defaults to the server's configured project |
| `mode` | `mock` or `live`. Live requires `auth_session` |
| `pillars` | Omit or leave empty to score all pillars |
| `workspaces` | Objects with `id` and `role`. A bare array of id strings is also accepted, and roles then come from the project file |
| `auth_session` | Session id from a sign-in endpoint. Required when `mode` is `live` |

The `role` you send matters: it decides whether pipeline checks run at all, and
it drives the layer-content and layer-separation checks. See
[checks.md](checks.md#which-checks-run).

### Response

```json
{
  "project_name": "Sales Analytics - Fabric Migration",
  "mode": "mock",
  "overall": 61.4,
  "by_pillar": {
    "Security": { "pct": 70.8, "count": 8 },
    "Performance Efficiency": { "pct": null, "count": 0 }
  },
  "by_workspace": {
    "Sales-Prod-DataPrep": {
      "role": "Data Prep", "pct": 58.3, "count": 24,
      "by_pillar": { "Security": 66.7, "Reliability": 50.0 }
    }
  },
  "counts": { "PASS": 14, "PARTIAL": 6, "FAIL": 9, "INFO": 1 },
  "total_scored": 29,
  "results": [ /* see below */ ],
  "errors": [ /* see below */ ],
  "files": { "markdown": "audit-report.md", "excel": "audit-report.xlsx" }
}
```

A `pct` of `null` means **not assessed**, not zero — render it differently.

Each entry in `results`:

```json
{
  "check_id": "PL-RETRY", "ref": "2.4.1",
  "title": "Retry policy configured on activities",
  "pillar": "Reliability", "status": "PARTIAL",
  "score": 1, "coverage": 0.5,
  "evidence": "2 of 4 activities have a retry policy",
  "recommendation": "Configure a retry policy (>= 1) on activities that…",
  "severity": "High",
  "workspace": "Sales-Prod-DataPrep", "workspace_role": "Data Prep",
  "obj": "PL_Bronze_Load", "scored": true, "common": false
}
```

`obj` is empty for workspace-level checks. `common` is `true` for those same
checks — they apply to every project regardless of source system.
`recommendation` is populated only when the check did not pass.

Workspaces that could not be read are **not** in `results`; they are separated
into `errors` so they read as warnings rather than as failing checks:

```json
[{ "workspace": "ws-old-01", "role": "Data Prep",
   "message": "Access denied (HTTP 403): the signed-in user does not have access…",
   "recommendation": "Confirm the workspace name/ID is correct and that…" }]
```

`files` holds **base names only**; fetch them from `/api/download/`.

### Status codes

| Code | Cause |
|------|-------|
| 200 | Ran. May still contain `errors` for individual workspaces |
| 400 | `mode` is `live` but no valid `auth_session` |
| 500 | Unhandled failure; the traceback is printed server-side |

---

## GET /api/download/{kind}

`kind` of `md` returns `audit-report.md`; **anything else** returns
`audit-report.xlsx` (the UI uses `xlsx`). Served as an attachment.

Returns 404 with `{"error": "run an audit first"}` when no report has been
generated yet.

Files are written to `OUT_DIR` (default `./output`) and **overwritten on every
run**. There is no run history — the endpoint always returns the most recent
audit, from whichever project last ran.

---

## Calling the API from tests

Use the Flask test client — no server, no browser. See
[`tests/test_api.py`](../backend/tests/test_api.py):

```python
from auditfast.web import create_app

app = create_app("config/project.example.yaml")
client = app.test_client()

r = client.post("/api/run", json={
    "mode": "mock", "pillars": ["Security"],
    "workspaces": [{"id": "ws-prep-01", "role": "Data Prep"}],
})
assert r.get_json()["overall"] is not None
```
