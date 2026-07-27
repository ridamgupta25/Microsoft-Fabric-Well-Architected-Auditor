# Development guide

Setting the project up, running it, testing it, and configuring a new engagement.

---

## Setup

From a fresh clone, at the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Dependencies ([`requirements.txt`](../backend/requirements.txt)): Flask +
flask-cors (web), msal (OAuth2), requests (Fabric REST), openpyxl (Excel),
PyYAML (config), rich (console), pytest (dev).

Mock mode needs almost none of these at runtime — `msal` and `requests` are
imported lazily inside the functions that need them, so the offline path works
even if the live-mode dependencies are missing.

---

## Running

Most commands run from the `backend/` directory, because the default config
paths are relative to it.

### Web UI

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --project config/project.example.yaml
```

Opens `http://127.0.0.1:8000` in a browser. Mock mode works fully offline.

| Flag | Default | |
|------|---------|---|
| `--project` | `config/project.example.yaml` | Project YAML the UI opens with |
| `--port` | `8000` | |
| `--no-browser` | off | Do not auto-open a browser |

The server runs `threaded=True` — required, because the interactive sign-in poll
has to be serviced while a sign-in is in progress. The reloader is off so there
is a single clean process holding the auth sessions.

### CLI

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --mock
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --mock --pillars Security,Reliability
```

| Flag | Default | |
|------|---------|---|
| `--project` | *required* | |
| `--mock` / `--live` | `--mock` | Mutually exclusive |
| `--out` | `output` | Report directory |
| `--pillars` | all | Comma-separated subset |

`--live` triggers the device-code flow: a URL and code are printed, you sign in
in a browser, and the run continues. It reads `tenant_id` / `client_id` from the
project YAML's `auth:` block.

Outputs: a console scorecard, `output/audit-report.md`, and
`output/audit-report.xlsx` (Scorecard / Checks / Risk Register sheets).

### Other entry points

```powershell
python run.py serve --project config/project.example.yaml   # no -m needed
waitress-serve --listen=127.0.0.1:8000 wsgi:app             # production-style
```

[`wsgi.py`](../backend/wsgi.py) reads the project from the `AUDITFAST_PROJECT`
environment variable. Note that sign-in **will not work reliably under multiple
workers** — see [architecture.md](architecture.md#8-authentication).

---

## Testing

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

| File | Covers |
|------|--------|
| [`test_smoke.py`](../backend/tests/test_smoke.py) | Engine end-to-end against the fixture, scoring bands, rating bands, specific check outcomes |
| [`test_api.py`](../backend/tests/test_api.py) | The HTTP layer via Flask's test client — no server, no browser |

`test_smoke.py` also has a built-in runner for when pytest is unavailable:

```powershell
..\.venv\Scripts\python.exe tests/test_smoke.py
```

Both test files begin with a `sys.path.insert` shim because there is no
`pyproject.toml` and the package is not installed. Adding packaging would remove
those two lines and let the tests run from anywhere.

Everything runs against [`sample_data/tenant.json`](../backend/sample_data/tenant.json),
so the full suite is offline and deterministic. The fixture is deliberately
imperfect — it contains a badly named workspace, hardcoded secrets, and missing
retry policies — so failing paths are exercised, not just passing ones.

---

## Project configuration

One YAML file defines an engagement. Template:
[`config/project.example.yaml`](../backend/config/project.example.yaml).

```yaml
project:
  name: "Sales Analytics - Fabric Migration"
  client: "Contoso"
  naming_convention: '^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$'
  pipeline_naming_convention: '^PL_[A-Za-z0-9_]+$'
  orphan_days: 90
  max_admins: 2

mock:
  tenant_file: "sample_data/tenant.json"

remediation: "config/remediation.yaml"

workspaces:
  - id: "ws-prep-01"
    role: "Data Prep"
  - id: "ws-store-01"
    role: "Data Storage"

auth:
  tenant_id: "<TENANT_ID>"
  client_id: "<ENTRA_APP_CLIENT_ID>"
  scopes:
    - "https://api.fabric.microsoft.com/Workspace.Read.All"
    - "https://api.fabric.microsoft.com/Item.Read.All"
```

| Key | Effect |
|-----|--------|
| `project.naming_convention` | Regex for `WS-NAME`. Matched with `re.match`, so it is anchored at the start only |
| `project.pipeline_naming_convention` | Regex for `PL-NAME` |
| `project.orphan_days` | Staleness threshold for `WS-ORPHAN` |
| `project.max_admins` | Threshold for `WS-LEASTPRIV` |
| `mock.tenant_file` | Fixture path for mock mode |
| `remediation` | Path to the remediation text file |
| `workspaces[].id` | Workspace GUID (live) or fixture id (mock) |
| `workspaces[].role` | Layer role — gates pipeline checks and drives the layer checks |
| `auth.*` | Live mode only. Values wrapped in `<…>` are treated as unset |

The whole `project:` block is handed to every check as `settings`, so a new
tunable is just a new key plus a `settings.get()` in the check.

**Path resolution:** relative paths in the YAML resolve against the directory
*two levels up* from the file — for `backend/config/project.example.yaml` that is
`backend/`. So `sample_data/tenant.json` means `backend/sample_data/tenant.json`.
See `_resolve()` in
[`audit_service.py`](../backend/auditfast/services/audit_service.py#L22-L28).

### Remediation text

[`config/remediation.yaml`](../backend/config/remediation.yaml) maps a checklist
`ref` to the advice shown for a failing check:

```yaml
"2.4.1": "Configure a retry policy (>= 1) on activities that call external systems."
```

Edit these to tune guidance without touching code. A missing key silently yields
an empty recommendation column.

---

## Setting up live mode

1. Register a Microsoft Entra **public client** app with delegated, read-only
   Fabric scopes: `Workspace.Read.All`, `Item.Read.All`.
2. Copy `project.example.yaml`, fill in `tenant_id`, `client_id`, and the real
   workspace GUIDs with their layer roles.
3. Run with `--live`, or sign in through the web UI.

No app registration? The UI's email sign-in falls back to Microsoft's first-party
Azure CLI client, and the "Azure CLI" button reuses an existing `az login`.
Neither needs an admin. Some tenants block both via Conditional Access.

If a live run returns less than expected, call `/api/diag` (the **Diagnose**
button) — it reports per-resource HTTP status codes so you can see whether the
token can read items but not role assignments, and so on.

---

## Known setup issues

### ⚠ Engagement configs are not gitignored

[`.gitignore`](../.gitignore) contains:

```
config/*.yaml
!config/project.example.yaml
```

A pattern containing a slash is anchored to the `.gitignore`'s own directory, so
this matches `<root>/config/` — a directory that no longer exists. The real
config directory is `backend/config/`, and it is **not** covered:

```console
$ git check-ignore -v backend/config/my-client.yaml
(no output — the file would be committed)
```

The rule predates the `backend/` restructure. A real engagement file contains the
client name, tenant id, client id, and production workspace GUIDs, so this should
be fixed before anyone adds one:

```diff
-config/*.yaml
-!config/project.example.yaml
-!config/remediation.yaml
+backend/config/*.yaml
+!backend/config/project.example.yaml
+!backend/config/remediation.yaml
```

`output/` and the virtualenv are correctly ignored at any depth.

### The root README is out of date

[`README.md`](../README.md) describes the pre-restructure layout — `webapp.py`,
`service.py`, and `engine.py` at the package root, and a claim that the UI uses
"the Python standard library only, no web framework dependency". Neither is true
now. Its install path and its "Extending" section point at files that have moved.

Use [`OVERVIEW.md`](../OVERVIEW.md) and this folder instead until the README is
rewritten.

### Reports have no run history

Both report files are written to a fixed name in `OUT_DIR` and overwritten on
every run, by every project. `/api/download/` always returns the most recent one.
