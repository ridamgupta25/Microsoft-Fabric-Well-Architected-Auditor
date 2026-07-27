# Getting started

Everything needed to set the project up from a fresh clone and get it running.

**Mock mode works completely offline** — no Microsoft Fabric tenant, no sign-in,
no Azure account. Start there; it exercises the entire stack against a bundled
fixture.

---

## 1. Prerequisites

| Tool | Version | Check with | Needed for |
|------|---------|------------|------------|
| **Python** | 3.10+ | `python --version` | Backend API, engine, CLI |
| **Node.js** | 18+ | `node --version` | Frontend |
| **npm** | 9+ | `npm --version` | Frontend |
| **Git** | any | `git --version` | Cloning |

Optional:

| Tool | Needed for |
|------|-----------|
| **Azure CLI** (`az`) | The easiest live sign-in — `winget install -e --id Microsoft.AzureCLI` |

You do **not** need Docker, a database, or an Azure subscription to run this
locally.

---

## 2. Clone

```powershell
git clone <repository-url>
cd Fabric-Well-Architected-Auditor
```

---

## 3. Backend setup

From the **repository root**:

```powershell
# Create and populate a virtual environment
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
```

> **Why `-e "backend[dev]"`** and not `requirements.txt`: the backend is a real
> installable package. Installing it editable puts `auditfast` on the import
> path, so tests and tooling work from any directory without `sys.path` hacks.
> `[dev]` adds pytest, httpx, and ruff.

**Verify:**

```powershell
.\.venv\Scripts\python.exe -c "from auditfast.core.checks import REGISTRY; print(len(REGISTRY), 'checks loaded')"
```

Expected: `20 checks loaded`. If this prints `0`, the check modules failed to
import and every audit would silently score nothing.

### Run the tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
```

Expected: **52 passed**. The suite is fully offline and deterministic.

---

## 4. Frontend setup

From the **repository root**:

```powershell
cd frontend
npm install
```

**Verify:**

```powershell
npm run build
```

Expected: `✓ built in …` with no TypeScript errors.

---

## 5. Run it

The backend and frontend are separate processes. Use **two terminals**.

### Terminal 1 — API

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --port 8000
```

You should see:

```
API      http://127.0.0.1:8000
Docs     http://127.0.0.1:8000/docs
```

### Terminal 2 — frontend

```powershell
cd frontend
npm run dev
```

Open **http://localhost:5173**.

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`, so the browser
makes same-origin requests and no CORS is involved locally.

### First audit

1. Leave **Mode** on **Mock (offline)** in the header.
2. Go to **Run audit** — all three fixture workspaces are pre-selected.
3. Click **Run audit**.
4. You land on the report: overall **57.9%**, a pillar scorecard, the pillar ×
   layer matrix, and 27 findings.

If you see that, the whole stack is working.

---

## 6. Useful URLs

| URL | What |
|-----|------|
| http://localhost:5173 | The React app |
| http://127.0.0.1:8000/docs | **Swagger UI** — try any endpoint interactively |
| http://127.0.0.1:8000/redoc | ReDoc API reference |
| http://127.0.0.1:8000/openapi.json | Machine-readable API schema |
| http://127.0.0.1:8000/api/v1/health | Health, including how many checks loaded |

Swagger UI is the quickest way to explore the backend on its own — every
endpoint is documented with request/response schemas and a **Try it out**
button.

---

## 7. Using the CLI (no frontend needed)

```powershell
cd backend

# Run an audit and write reports
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --mock

# Only some pillars
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --pillars Security,Reliability

# Browse the rule library
..\.venv\Scripts\python.exe -m auditfast checks
..\.venv\Scripts\python.exe -m auditfast checks --pillar Security
```

Reports are written to `backend/output/`:
`audit-report.md` and `audit-report.xlsx` (Scorecard / Checks / Risk Register).

---

## 8. Live mode (auditing a real Fabric tenant)

Everything is **read-only**. The tool only issues GET calls plus the read-only
`getDefinition`, and never writes to your tenant.

### Easiest path — Azure CLI

```powershell
az login
```

Then in the app: switch **Mode** to **Live**, click **Sign in required** in the
header, and choose **Sign in with Azure CLI**.

### Email sign-in

Switch to Live → **Sign in** → enter your email → **Sign in with Microsoft**. A
browser window opens *on the machine running the API*. With no client id
configured, Microsoft's first-party Azure CLI client is used, so no app
registration is needed.

### With your own app registration

If Conditional Access blocks the above, register a Microsoft Entra **public
client** app with delegated, read-only Fabric scopes:

- `Workspace.Read.All`
- `Item.Read.All`

Then copy the example project and fill it in:

```powershell
cp backend/config/project.example.yaml backend/config/my-client.yaml
```

```yaml
auth:
  tenant_id: "<your-tenant-guid>"
  client_id: "<your-app-client-id>"
```

Serve with it:

```powershell
..\.venv\Scripts\python.exe -m auditfast serve --project config/my-client.yaml
```

> Files matching `backend/config/*.yaml` are gitignored (except the two
> examples), so real engagement configs containing tenant ids, client ids, and
> production workspace GUIDs are never committed.

### If a live audit returns less than expected

Use **Diagnose access** on the sign-in page. It reports per-resource HTTP status
codes, distinguishing a bad token from missing permission on one sub-resource
(for example: items readable, role assignments 403).

---

## 9. Configuring a project

A project spans one or more Fabric workspaces, each tagged with its layer role.
See [`backend/config/project.example.yaml`](../backend/config/project.example.yaml).

```yaml
project:
  name: "Sales Analytics - Fabric Migration"
  naming_convention: '^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$'
  pipeline_naming_convention: '^PL_[A-Za-z0-9_]+$'
  orphan_days: 90
  max_admins: 2

workspaces:
  - id: "ws-prep-01"
    role: "Data Prep"
  - id: "ws-store-01"
    role: "Data Storage"
```

The `role` matters: it decides which checks run. A `Data Storage` workspace gets
the 12 workspace checks but no pipeline checks, and is assessed on whether it
correctly contains storage items and *only* storage items.

| Key | Effect |
|-----|--------|
| `project.naming_convention` | Regex for `WS-NAME`. Matched with `re.match`, so anchored at the start |
| `project.pipeline_naming_convention` | Regex for `PL-NAME` |
| `project.orphan_days` | Staleness threshold for `WS-ORPHAN` |
| `project.max_admins` | Threshold for `WS-LEASTPRIV` |
| `mock.tenant_file` | Fixture path used in mock mode |
| `remediation` | Path to the remediation text file |
| `workspaces[].id` | Workspace GUID (live) or fixture id (mock) |
| `workspaces[].role` | Layer role — gates pipeline checks, drives the layer checks |
| `auth.*` | Live mode only. Values wrapped in `<…>` are treated as unset |

The whole `project:` block is passed to every check as `settings`, so adding a
tunable is a new key plus a `ctx.setting()` call in the check.

**Path resolution:** relative paths in the YAML resolve against the directory two
levels up from the file — for `backend/config/project.example.yaml` that is
`backend/`, so `sample_data/tenant.json` means `backend/sample_data/tenant.json`.

### Remediation text

[`backend/config/remediation.yaml`](../backend/config/remediation.yaml) maps a
checklist `ref` to the advice shown for a failing check. Edit it to tune guidance
without touching code. A missing key silently yields an empty recommendation, so
a test asserts every scoreable check's ref has text.

---

## 10. Environment variables

All optional — every setting has a working default. Prefix is `AUDITFAST_`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUDITFAST_DEFAULT_PROJECT` | `config/project.example.yaml` | Project the API opens with |
| `AUDITFAST_OUTPUT_DIR` | `output` | Where reports are written |
| `AUDITFAST_ENVIRONMENT` | `local` | `local` / `dev` / `staging` / `prod` |
| `AUDITFAST_LOG_LEVEL` | `INFO` | Logging verbosity |
| `AUDITFAST_LOG_JSON` | `false` | Structured JSON logs — enable when hosted |
| `AUDITFAST_CORS_ORIGINS` | `["http://localhost:5173"]` | Origins allowed to call the API |

Frontend (see [`frontend/.env.example`](../frontend/.env.example)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_API_BASE_URL` | *(empty)* | API origin in production. Empty in dev to use the proxy |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev proxy forwards `/api` |

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `ModuleNotFoundError: auditfast` | The package is not installed. Run `pip install -e "backend[dev]"` from the repo root |
| `0 checks loaded` / health says `degraded` | A check module failed to import. Run `python -c "import auditfast.core.checks"` to see the real error |
| Frontend shows **API unreachable** | The backend is not running, or is on a different port. Start it, or set `VITE_API_PROXY_TARGET` |
| `Port 8000 is already in use` | `--port 8001`, and set `VITE_API_PROXY_TARGET` to match |
| `npm run build` fails on `Cannot find type definition file for 'node'` | `npm install` was not re-run after a dependency change |
| Live audit returns 401 | The session expired. Sign in again |
| Live audit returns 403 for a workspace | The signed-in user needs at least Viewer on it. Confirm with **Diagnose access** |
| `az` sign-in fails | Run `az login` first, on the machine running the API — not your laptop if the API is remote |
| Tests fail with import errors | Install with the `[dev]` extra: `pip install -e "backend[dev]"` |

---

## 12. Project layout

```
Fabric-Well-Architected-Auditor/
├─ backend/
│  ├─ pyproject.toml           # package + tooling config
│  ├─ config/                  # project + remediation YAML
│  ├─ sample_data/tenant.json  # offline fixture
│  ├─ tests/                   # 50 tests, fully offline
│  └─ src/auditfast/
│     ├─ core/                 # engine, checks, scoring — depends on nothing
│     ├─ clients/              # read-only Fabric + fixture providers
│     ├─ services/             # orchestration; the single audit path
│     ├─ api/v1/               # FastAPI routers
│     ├─ schemas/              # Pydantic request/response models
│     ├─ config/               # settings + logging
│     ├─ database/             # job store (in-memory today)
│     ├─ ai/                   # scaffolding only — nothing implemented
│     ├─ mcp/                  # MCP adapter (optional extra)
│     ├─ cli.py  main.py
│     └─ reporting/  security/
└─ frontend/
   └─ src/
      ├─ pages/ components/ layouts/
      ├─ services/             # Axios API layer
      ├─ hooks/ context/ types/ utils/
      └─ App.tsx  main.tsx
```

Next: [architecture.md](architecture.md) explains how the pieces fit together.
