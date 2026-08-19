# Microsoft Fabric Well-Architected Auditor

A **read-only** tool that scans your Microsoft Fabric workspaces, scores them
against the Microsoft **Well-Architected** best practices, and hands you an
evidence-backed report with clear fix-it guidance. It is **fully deterministic** —
every check is a fixed rule with a fixed threshold, so the same input always
produces the same score.

> **Safe by design.** The tool only ever *reads* your tenant (GET requests plus
> the read-only `getDefinition`). It never creates, edits, or deletes anything in
> Fabric.

> **Development:** The `dev` branch contains the latest in-progress changes.
> Use `main` for the stable version of the auditor.

**New here? Pick your path:**

- **Setting it up on your machine?** You're in the right place — follow
  **[Setup in 4 steps](#setup-in-4-steps)** below. It takes about 10 minutes.
- **Already running and want to learn the tool?** Read the
  **[How-to-use guide »](docs/how-to-use.md)** — a full, screen-by-screen
  walkthrough of signing in, running an audit, and reading every part of the
  report.

---

## What it does

- **Audits against best practices.** Checks whether your workspaces, pipelines,
  and notebooks follow Fabric best practices across seven Well-Architected
  pillars (Security, Reliability, Performance, Cost, and more).
- **One project, many workspaces.** Register the workspaces that make up a
  project, tag each with its layer role, and get one overall score plus a
  per-workspace and **pillar × layer** breakdown.
- **Evidence + remediation, every time.** Each finding shows what was observed,
  the affected item, a severity, and a pre-written recommendation.
- **Fast repeat runs.** The first run crawls Fabric into an on-disk cache; later
  runs are near-instant and only re-read Fabric when the data goes stale.
- **Exportable reports.** Download every result as Markdown or Excel (Scorecard /
  Checks / Risk Register) to share with your team.

---

## Before you begin — prerequisites

Install these once. On Windows, the quickest way is `winget` (copy-paste each
line into PowerShell). **Close and reopen your terminal after installing** so the
new commands are found.

| Tool | Version | Install on Windows | Used for |
|------|---------|--------------------|----------|
| **Python** | 3.10 or newer | `winget install -e --id Python.Python.3.12` | Backend, engine, CLI |
| **Node.js** (includes npm) | 18 LTS or newer | `winget install -e --id OpenJS.NodeJS.LTS` | Frontend |
| **Git** | any | `winget install -e --id Git.Git` | Getting the code |
| **Azure CLI** (recommended) | any | `winget install -e --id Microsoft.AzureCLI` | The easiest sign-in |
| **ODBC Driver 18 for SQL Server** (optional) | 18 | `winget install -e --id Microsoft.msodbcsql.18` | Only for lakehouse / warehouse **column-level** checks — see [Optional — enable lakehouse & warehouse column checks](#optional--enable-lakehouse--warehouse-column-checks) |
| **A Fabric workspace** | — | — | You need at least **Viewer** access to one workspace to run a real audit |

You do **not** need Docker, a database, or your own app registration — sign-in
falls back to Microsoft's built-in Azure CLI client.

**Check everything is installed:**

```powershell
python --version   # 3.10+
node --version     # v18+
npm --version      # 9+
git --version
az version         # only if you installed Azure CLI
```

---

## Setup in 4 steps

### 1. Get the code

**Windows only — enable long paths first.** This repo contains some deeply
nested files that exceed Windows' default 260-character path limit. If you skip
this, some files may fail to check out during clone. Enable long path support in
Git (no admin needed):

```powershell
git config --global core.longpaths true
```

> To also enable long paths system-wide (optional, requires an **Administrator**
> PowerShell):
>
> ```powershell
> Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -Value 1
> ```

Clone the repository:

```powershell
git clone <repository-url>
```

Then move into the project folder — the one that contains `backend\` and
`frontend\` (this README lives there):

```powershell
cd auditfast-core
```

> Shared as a **.zip** instead? Unzip it, then `cd` into the folder that contains
> the `backend\` and `frontend\` sub-folders.

### 2. Set up the backend (Python)

Run these from the **project root** (the folder with `backend\` and `frontend\`),
one command at a time.

Create the virtual environment:

```powershell
py -m venv .venv
```

Upgrade `pip` inside it:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Install the backend package (the `[dev]` extra adds the test and lint tools):

```powershell
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
```

> **Important:** the virtual environment (`.venv`) is created **at the project
> root**, and the backend is always launched with `..\.venv\Scripts\python.exe`
> from inside the `backend\` folder. Keep the `.venv` where it is — this is the
> single most common thing people trip on.

**Verify the backend loaded correctly:**

```powershell
.\.venv\Scripts\python.exe -c "from auditfast.core.check.registry import REGISTRY; print(len(REGISTRY), 'checks loaded')"
```

Expected: a line like `… checks loaded` with a count **greater than 0**. The
exact number grows as checks are added, so the value itself doesn't matter here —
only that it isn't `0`. If it prints `0 checks loaded`, the check modules failed
to import; see [Troubleshooting](#troubleshooting).

### 3. Set up the frontend (Node.js)

Move into the frontend folder:

```powershell
cd frontend
```

Install its dependencies:

```powershell
npm install
```

(Optional) Confirm it builds — expect `built in …` with no TypeScript errors:

```powershell
npm run build
```

Go back to the project root, ready for the next step:

```powershell
cd ..
```

### Optional — enable lakehouse & warehouse column checks

A small group of checks inspect **column-level** details of your lakehouse and
warehouse tables — column names, data types, surrogate keys, SCD2 audit columns,
star-schema shape, and warehouse row-level security. That information is **not**
in the Fabric REST API: it lives only behind the **SQL analytics endpoint** that
Fabric provisions for every lakehouse and warehouse, reached over TDS (port
1433). To read it, the machine running the backend needs two extra things:

1. the **`pyodbc`** Python package, and
2. a **SQL Server ODBC driver** (Driver 18 recommended).

> **This step is completely optional.** If you skip it, those column-level checks
> simply report **N/A** (never counted, never failed) and every other check runs
> exactly as normal. Set it up only if you want lakehouse/warehouse column-level
> coverage.

**1. Install the ODBC driver** (needs a normal terminal; reopen the terminal
after it finishes so the driver is found):

```powershell
winget install -e --id Microsoft.msodbcsql.18
```

**2. Install `pyodbc` into the project's virtual environment** (run from the
project root — the folder with `backend\` and `frontend\`):

```powershell
.\.venv\Scripts\python.exe -m pip install pyodbc
```

**3. Make sure outbound TCP port 1433 is open** from this machine — corporate
firewalls sometimes block it. Nothing else is required: the tool discovers each
endpoint's address automatically and signs the SQL connection with the same
read-only session you already sign in with, so there is no connection string and
no extra sign-in.

**Verify the driver is visible to Python:**

```powershell
.\.venv\Scripts\python.exe -c "import pyodbc; print([d for d in pyodbc.drivers() if 'SQL Server' in d])"
```

Expected: a list containing something like `ODBC Driver 18 for SQL Server`. An
empty list `[]` means the driver isn't installed yet — repeat step 1 and reopen
the terminal. `ModuleNotFoundError: pyodbc` means step 2 didn't run in this
`.venv`.

> The SQL reads are **on by default** and stay silent if any piece is missing. To
> turn them off entirely, set `AUDITFAST_SQL_ENDPOINT_ENABLED=false` before you
> start the backend.

### 4. Configuration (optional — skip for a first run)

Nothing needs configuring to start. Every setting has a working default and
sign-in uses your Azure CLI session. When you're ready to customise, see the
[How-to-use guide](docs/how-to-use.md#configure-a-project-yaml) and
[docs/getting-started.md](docs/getting-started.md).

---

## Start the tool

The backend and frontend run as two separate processes, so use **two terminals**.

**Terminal 1 — backend API** (from the project root).

Go into the backend folder:

```powershell
cd backend
```

Start the API:

```powershell
..\.venv\Scripts\python.exe -m auditfast serve --port 8000
```

You should see `API http://127.0.0.1:8000` and `Docs …/docs`.

> The backend does **not** auto-reload. If you change backend code, stop it
> (`Ctrl+C`) and start it again. The frontend *does* hot-reload.

**Terminal 2 — frontend** (from the project root).

Go into the frontend folder:

```powershell
cd frontend
```

Start the dev server:

```powershell
npm run dev
```


Now open **http://localhost:5173** in your browser.

---

## Your first audit

1. Click **Connect to Fabric** (top-right). The fastest sign-in: run `az login`
   once in any terminal, then choose **Reuse my Azure CLI session**.
2. Go to the **Run audit** tab — every workspace your account can see is already
   selected.
3. Click **Run audit** and wait for it to finish.
4. Read the report: an overall score, a pillar scorecard, the pillar × layer
   matrix, and every finding. If you see that, your whole setup is working.

**Want the full walkthrough of every screen and option?** See the
**[How-to-use guide »](docs/how-to-use.md)**.

---

## Handy URLs

| URL | What it is |
|-----|------------|
| http://localhost:5173 | The web app |
| http://127.0.0.1:8000/docs | **Swagger UI** — try any API endpoint in the browser |
| http://127.0.0.1:8000/api/v1/health | Health check + how many checks are loaded |

---

## Optional: run from the command line (no browser)

Go into the backend folder:

```powershell
cd backend
```

Run an audit (signs you in via a device code first):

```powershell
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml
```

Or just browse the rule library — no sign-in needed:

```powershell
..\.venv\Scripts\python.exe -m auditfast checks --pillar Security
```

Reports land in `backend/output/` as Markdown and Excel (Scorecard / Checks /
Risk Register). Full CLI reference is in the
[How-to-use guide](docs/how-to-use.md#using-the-command-line-no-browser).

---

## Optional: run the tests

Go into the backend folder:

```powershell
cd backend
```

Run the test suite:

```powershell
..\.venv\Scripts\python.exe -m pytest
```

Expected: **189 passed, 7 skipped**. The suite is fully offline — it runs against
a recorded fixture, needs no credentials, and never touches a real tenant.

---

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `python` / `node` / `npm` / `az` **not recognized** | The tool isn't installed, or the terminal was open before you installed it. Install it (see [prerequisites](#before-you-begin--prerequisites)) and **reopen the terminal**. |
| `ModuleNotFoundError: auditfast` | The package isn't installed. From the **project root**, run `.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"`. |
| Verify step prints `0 checks loaded` | A check module failed to import. Run `.\.venv\Scripts\python.exe -c "import auditfast.core.check"` to see the real error. |
| `'..\.venv\Scripts\python.exe' is not recognized` | You're in the wrong folder, or the `.venv` isn't at the project root. Recreate it: from the project root run `py -m venv .venv`, then launch the backend from inside `backend\`. |
| Frontend shows **API unreachable** | The backend isn't running, or it's on a different port. Start Terminal 1, or set `VITE_API_PROXY_TARGET`. |
| `Port 8000 is already in use` | Start with `--port 8001` and set `VITE_API_PROXY_TARGET=http://127.0.0.1:8001` in `frontend/.env`. |
| An audit is rejected with **401** before it starts | You're not signed in (or the session expired). Reconnect via **Connect to Fabric**. |
| An audit returns **403** for a workspace | The signed-in user needs at least **Viewer** on it. Use **Troubleshoot access** on the sign-in page to confirm. |
| `az` sign-in fails | Run `az login` first, on the same machine that runs the backend. |
| Lakehouse / warehouse **column** checks all show **N/A** | The SQL analytics endpoint couldn't be read. Install `pyodbc` and **ODBC Driver 18**, make sure outbound **port 1433** is open (see [Optional — enable lakehouse & warehouse column checks](#optional--enable-lakehouse--warehouse-column-checks)), then re-run. These checks degrade to N/A by design — they never fail the audit. |

More detail: **[docs/getting-started.md](docs/getting-started.md)**.

---

## Architecture

```
backend/src/auditfast/
  core/       engine · check/ · scoring · models   ← depends on nothing
  clients/    the read-only Fabric REST provider (classifies read failures)
  services/   the single audit path + KB cache, archive, and checklist-intake, framework-free
  api/v1/     FastAPI routers, versioned (audit, catalog, checklist, …)
  cli.py      mcp/    two further adapters over the same services
  schemas/    config/  database/  reporting/  security/
  ai/         checklist-intake: matching + authoring (+ optional advisory, off by default)
frontend/src/
  pages/  components/  layouts/  services/  hooks/  context/  types/   (incl. ChecklistPage)
.github/      agentic authoring layer (agents, skills, instructions, harness, mcp)
intake/  output/   inputs the authoring agents read · generated reports + KB archive
```

The dependency rule is one-way: **`core/` imports nothing outward.** The REST
API, the CLI, and the MCP server are three front doors onto one service layer, so
they cannot produce different numbers.

Audits are **fire-and-poll**: `POST /api/v1/audit` returns an id immediately and
the work runs in the background, because a tenant-wide run can take minutes.

---

## What it checks

The auditor applies a growing library of best-practice checks across seven
Well-Architected pillars. **Coverage is under active development, so the number
of checks changes over time** — the current count is always shown live in the
app's health badge and at `GET /api/v1/health`.

Checks apply at three object scopes:

| Scope | What it looks at |
|-------|------------------|
| **Workspace** | naming, roles via security groups, least-privilege admins, guest access, sensitivity labels, Git, deployment pipeline, capacity, orphaned items, layer content / separation, inventory |
| **Pipeline** | naming, descriptions, parameterization, retry, on-failure path, failure notification, timeouts, no hardcoded secrets |
| **Notebook** | Delta MERGE / OPTIMIZE / VACUUM / Z-ORDER / V-ORDER, table properties, retention, Spark env & pinned libraries, shuffle / cache / repartition, `SELECT *` |
| **Lakehouse / Warehouse** | table & column naming, data types, surrogate keys, SCD2 audit columns, star-schema / fact-grain shape, warehouse schemas and row-level security (the **column-level** checks need the [SQL-endpoint setup](#optional--enable-lakehouse--warehouse-column-checks)) |

The seven Well-Architected pillars it scores:

- Data Management & Quality
- Operations & Reliability
- Performance & Capacity
- Security
- Cost & Resource Optimization
- Governance & Compliance
- Foundation (informational)

Lakehouse and warehouse **table & column** checks need the optional SQL-endpoint
setup ([see above](#optional--enable-lakehouse--warehouse-column-checks));
semantic models and Eventhouse are not yet fully automated. The engine dispatches
on object *scope*, so adding more requires no engine change.

---

## Documentation

| Document | Read it when you want to… |
|----------|---|
| **[How to use the tool](docs/how-to-use.md)** | **Learn the app screen by screen — sign in, run an audit, read the report, use the CLI** |
| **[Getting started](docs/getting-started.md)** | Set up and run it — prerequisites, install, configuration, troubleshooting |
| [Architecture](docs/architecture.md) | Understand the layers, runtime flow, and the core contracts |
| [Checks](docs/checks.md) | See the full catalog, and how to add one |
| [Scoring](docs/scoring.md) | Understand how 0–3 scores become pillar percentages |
| [API](docs/api.md) | Call the REST API — reference and design notes |
| [Migration](docs/migration.md) | See what changed from the Flask original, and what was reused |
| [Scalability](docs/scalability.md) | Plan for scale and Azure deployment |

**Adding a check?** Start at
[docs/checks.md → Adding a check](docs/checks.md#adding-a-check).
