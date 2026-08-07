# How to use the Fabric Well-Architected Auditor

A complete, plain-language walkthrough of the tool — from signing in to reading
every part of the report. If you have not set the tool up yet, do that first
using the **[README setup guide](../README.md#setup-in-4-steps)**, then come back
here.

This guide assumes the tool is **already running**: the backend on Terminal 1,
the frontend on Terminal 2, and the app open at **http://localhost:5173**.

---

## Contents

1. [What the tool does for you](#1-what-the-tool-does-for-you)
2. [Before you start](#2-before-you-start)
3. [A tour of the screens](#3-a-tour-of-the-screens)
4. [Step 1 — Sign in to Fabric](#step-1--sign-in-to-fabric)
5. [Step 2 — Choose what to audit](#step-2--choose-what-to-audit)
6. [Step 3 — Run it and watch progress](#step-3--run-it-and-watch-progress)
7. [Step 4 — Read the report](#step-4--read-the-report)
8. [Understanding the score](#understanding-the-score)
9. [Layer roles explained](#layer-roles-explained)
10. [Browse the check library](#browse-the-check-library)
11. [Review past audits (History)](#review-past-audits-history)
12. [Download and share reports](#download-and-share-reports)
13. [Using the command line (no browser)](#using-the-command-line-no-browser)
14. [Configure a project (YAML)](#configure-a-project-yaml)
15. [Environment variables](#environment-variables)
16. [Caching and re-runs](#caching-and-re-runs)
17. [Tips and good practices](#tips-and-good-practices)
18. [Frequently asked questions](#frequently-asked-questions)
19. [Where to get more help](#where-to-get-more-help)

---

## 1. What the tool does for you

You point it at one or more Microsoft Fabric workspaces. It reads their
configuration (workspaces, roles, pipelines, notebooks, and more), compares what
it finds against its library of best-practice checks, and produces:

- an **overall score** and a **rating** (for example, *Good* or *Needs work*);
- a **scorecard per Well-Architected pillar** (Security, Reliability,
  Performance, Cost, and so on);
- a **pillar × layer matrix** showing how each part of your architecture scores;
- a **per-workspace breakdown**; and
- a list of **findings**, each with the evidence observed, a severity, and a
  recommended fix.

Everything is **read-only** and **deterministic** — the same input always gives
the same score, so results are repeatable and easy to defend.

---

## 2. Before you start

- The backend and frontend must both be running (see the
  [README](../README.md#start-the-tool)).
- You need at least **Viewer** access to one Fabric workspace to see a real
  audit. Workspaces you cannot read are reported separately, not scored low.
- The tool never changes your tenant. It only issues read calls.

Open **http://localhost:5173**. At the top you'll see the app name, a green
**health badge** (showing how many checks are loaded), the navigation tabs, and a
**Login** button.

> **Health badge:** it shows how many checks loaded. Green with a number means
> healthy. If it shows **Degraded** or **API unreachable**, the audit engine or
> the backend has a problem — see the README
> [Troubleshooting](../README.md#troubleshooting) table.

---

## 3. A tour of the screens

The navigation bar at the top has these tabs:

| Tab | What it's for |
|-----|---------------|
| **Dashboard** | The landing page: what the tool covers and your most recent runs. |
| **Run audit** | Pick workspaces and pillars, then run an audit and watch it live. |
| **Checks** | Browse the full rule library. No sign-in needed. |
| **History** | Every past audit, with a link back to each report. |

To the right of the tabs is the **account control**: a **Login** button when
you're signed out, or your name with a **Sign out** option when you're connected.
Choosing **Account & diagnostics** opens the sign-in page, where you can also
**Troubleshoot access**.

---

## Step 1 — Sign in to Fabric

Every audit reads a live tenant, so you must sign in first. There are three ways;
pick the first one that works for you. **All three are read-only, and your Fabric
token never reaches the browser — the session lives on the server.**

### A. Reuse your Azure CLI session (easiest)

1. In any terminal, run:

   ```powershell
   az login
   ```

2. In the app, open the sign-in page (**Login** / **Connect to Fabric**), expand
   **Running the app on this machine?**, and click **Reuse my Azure CLI session**.

That's it — no passwords typed into the app, no app registration.

### B. Open the sign-in window on the API host (same machine only)

Use this when your **browser and the backend (API) are on the same computer** —
the normal local setup from the README. It opens a Microsoft sign-in window on the
machine that hosts the API.

1. On the sign-in page, expand **Running the app on this machine?**
2. *(Optional)* Type your work email in the **you@contoso.com (optional)** box; it
   just pre-fills the Microsoft prompt, so you can leave it blank.
3. Click **Open the sign-in window on the API host**.
4. A Microsoft sign-in window opens on the API host — complete it there, and the
   app connects automatically.

No app registration is needed; the tool uses Microsoft's built-in client.

> Both this option and **Reuse my Azure CLI session** live under **Running the app
> on this machine?** and **only work when the browser and the API are on the same
> computer**.

### C. Your own app registration (only if the above are blocked)

If Conditional Access blocks options A and B, register a Microsoft Entra
**public client** app with these delegated, read-only scopes:

- `Workspace.Read.All`
- `Item.Read.All`

Then put its ids in a project file (see
[Configure a project](#configure-a-project-yaml)) and start the backend with
`--project config/my-client.yaml`.

### If an audit shows fewer results than expected

On the sign-in page, click **Troubleshoot access**. It reports the HTTP status
for each resource, so you can tell a bad token apart from a missing permission on
one sub-resource (for example, items readable but role assignments returning
403).

---

## Step 2 — Choose what to audit

Go to the **Run audit** tab. Once you're signed in, it lists **every workspace
your account can see**, all selected by default.

**Workspaces**

- **Select / deselect** the workspaces you want in this run.
- **Layer role** — each workspace has a role dropdown (for example *Data Prep*,
  *Data Storage*, *Data Logs*, *Data Operations*, *Reporting / Semantic*, or
  *Mixed*). The role decides **which checks run** and how the workspace is judged
  — see [Layer roles explained](#layer-roles-explained). If you're unsure, leave
  it as *Mixed*.
- **Add a workspace by ID** — paste a workspace GUID and pick a role to include
  one that wasn't listed.
- **Remove** any workspace you don't want in this run.

**Pillars**

- Tick the Well-Architected pillars you want to assess. A pillar with **no
  runnable checks yet** is left off by default (so you don't get an empty pillar
  in the report). For a first audit, leaving the defaults is fine.

When you're happy, click **Run audit**.

---

## Step 3 — Run it and watch progress

Audits are **fire-and-poll**: the moment you click **Run audit**, the work starts
on the server and the page shows **live status** rather than freezing.

- A tenant-wide run can take a few minutes on the **first** run, because each
  workspace is crawled from Fabric.
- **Later runs are near-instant** — the tool reads from its on-disk cache and
  only re-crawls Fabric when the data goes stale (see
  [Caching and re-runs](#caching-and-re-runs)).
- Results appear **workspace by workspace**. You can open the report while it's
  still running and use **Reload results** to fetch the latest.

When it finishes, you land on the full report.

---

## Step 4 — Read the report

The report page has several sections, top to bottom.

### Header

The title is the **workspace name** when a single workspace was audited, or a
neutral heading otherwise. Below it: how many workspaces were audited, and how
many were **skipped for access**.

### Overall score

- A big **percentage** and a **rating** badge (colour-coded).
- A one-line summary: **pass · partial · fail** counts and the total number of
  checks scored.
- Two buttons — **Markdown** and **Excel** — to download the report (see
  [Download and share reports](#download-and-share-reports)).

### Pillar scorecard

One card per pillar with a score bar and percentage. A pillar with no checks yet
shows **"Not assessed — no checks yet"** rather than a misleading 0%.

### Pillar by layer

A matrix (cross-tab) of **pillar × layer role**, so you can see, for example, how
your *Data Storage* workspaces score on *Security* versus your *Reporting* ones.

### Per-workspace breakdown

Each audited workspace with its own score, so you can spot which one is dragging
the number down.

### Findings

The heart of the report — a filterable table. Each finding shows:

- the **check** that ran and its **severity**;
- the **evidence observed** (what the tool actually saw);
- the **affected item**; and
- a **recommended fix** (pre-written remediation).

Use this list to plan the work: sort by severity and start at the top.

### Workspaces requiring additional access

If any workspace was skipped because your account couldn't read it, it's listed
here — **excluded from the scores**, with the reason and what to do (usually:
grant at least Viewer access, then re-run). This is the tool being honest: a
permission problem is never silently scored as non-compliance.

---

## Understanding the score

- Every check returns a verdict scored **0–3**, surfaced as **PASS**, **PARTIAL**,
  or **FAIL**.
- Those roll up into a **percentage per pillar** and an **overall percentage**,
  plus a **rating** label.
- A check the tool **couldn't evaluate** (for example, a resource it wasn't
  allowed to read) is recorded as **N/A** and **left out of the score** — it does
  not count against you.
- Scoring is **deterministic**: the same snapshot, settings, and rules always
  produce the same numbers, with no AI in the scoring path.

For the exact roll-up maths, see **[scoring.md](scoring.md)**.

---

## Layer roles explained

A real Fabric project usually splits work across several workspaces. Tagging each
with a **layer role** lets the tool apply the right checks and judge whether a
workspace contains the *right kind* of content.

| Layer role | Typical content | What the tool emphasises |
|------------|-----------------|--------------------------|
| **Data Prep** | Pipelines, notebooks, dataflows | Pipeline and notebook best practices, ingestion hygiene |
| **Data Storage** | Lakehouses / warehouses | That it holds storage items — and *only* storage items |
| **Data Logs** | Logging / monitoring items | Log separation and retention |
| **Data Operations** | Orchestration, ops items | Operational and reliability practices |
| **Reporting / Semantic** | Semantic models, reports | Reporting-layer separation |
| **Mixed** | Anything | All applicable checks; the safe default when unsure |

The role gates whole groups of checks — for example, **pipeline checks only run
where pipelines belong** — and drives the *layer separation* checks that flag a
workspace holding content that should live elsewhere.

---

## Browse the check library

Open the **Checks** tab. This page needs **no sign-in and no audit** — it lists
every rule the tool can apply, straight from its metadata.

- **Search** by keyword (id, title, reference, or description).
- **Filter** by pillar and by layer.
- Each row shows the check **ID**, its **reference**, the **pillar**, the
  **scope** (Workspace / Pipeline / Notebook), the **severity**, and the
  **title**.

Use it to understand what a finding means, or to see exactly what the tool covers
before you run anything.

---

## Review past audits (History)

Open the **History** tab for a list of every past run: its id, when it was
submitted, its status, and its score. Click an id to **reopen that report**. The
**Dashboard** also shows your five most recent runs.

---

## Download and share reports

From any report, use the **Markdown** and **Excel** buttons at the top.

- **Markdown** — a clean, shareable text version of the whole report.
- **Excel** — a workbook with three tabs:
  - **Scorecard** — the pillar and overall scores;
  - **Checks** — every check and its result;
  - **Risk Register** — the findings laid out as a ready-to-use risk register.

When you run from the [command line](#using-the-command-line-no-browser),
the same files are written to `backend/output/` as `audit-report.md` and
`audit-report.xlsx`. Separately, every run archives a permanent, timestamped
snapshot under `backend/Fabric workspace kb/` (see
[Caching and re-runs](#caching-and-re-runs)).

---

## Using the command line (no browser)

Prefer a terminal, or automating runs? The CLI calls the exact same engine as the
web app, so the scores are identical. Run these from the **`backend\`** folder.

```powershell
cd backend

# Run an audit. This signs you in via a device code first: it prints a URL and a
# short code — open the URL, enter the code, finish sign-in, and the run starts.
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml

# Only some pillars (use the full pillar name)
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --pillars Security

# Browse the rule library — no sign-in needed
..\.venv\Scripts\python.exe -m auditfast checks
..\.venv\Scripts\python.exe -m auditfast checks --pillar Security

# Start the API only (what the frontend talks to)
..\.venv\Scripts\python.exe -m auditfast serve --port 8000
```

Every run is live — there is no offline/demo mode. Reports are written to
`backend/output/` (`audit-report.md` and `audit-report.xlsx`).

---

## Configure a project (YAML)

A **project** groups one or more workspaces and sets a few thresholds. The
starting point is
[`backend/config/project.example.yaml`](../backend/config/project.example.yaml).
Copy it and edit your own:

```powershell
cp backend/config/project.example.yaml backend/config/my-client.yaml
```

```yaml
project:
  name: "Sales Analytics - Fabric Migration"
  naming_convention: '^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$'
  pipeline_naming_convention: '^PL_[A-Za-z0-9_]+$'
  orphan_days: 90
  max_admins: 2

workspaces:
  - id: "ws-prep-01"      # the Fabric workspace GUID
    role: "Data Prep"     # the layer role
  - id: "ws-store-01"
    role: "Data Storage"

# Optional — only needed for your own app registration (Step 1, option C)
auth:
  tenant_id: "<your-tenant-guid>"
  client_id: "<your-app-client-id>"
```

| Key | What it controls |
|-----|------------------|
| `project.name` | A label for the run |
| `project.naming_convention` | Regex a **workspace name** must match |
| `project.pipeline_naming_convention` | Regex a **pipeline name** must match |
| `project.orphan_days` | How many days of inactivity counts as an orphaned item |
| `project.max_admins` | The largest number of admins allowed (least-privilege) |
| `workspaces[].id` | The Fabric workspace GUID |
| `workspaces[].role` | The layer role (gates which checks run) |
| `auth.*` | Optional. Values wrapped in `<…>` are treated as unset and fall back to the built-in Azure CLI client |

Start the backend with your file:

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --project config/my-client.yaml
```

> Files matching `backend/config/*.yaml` are gitignored (except the examples), so
> real client configs with tenant ids and production workspace GUIDs are never
> committed.

The remediation text shown for failing checks lives in
[`backend/config/remediation.yaml`](../backend/config/remediation.yaml) — edit it
to tune guidance without touching code.

---

## Environment variables

All optional — every setting has a working default. Backend variables use the
`AUDITFAST_` prefix; copy `backend/.env.example` to `backend/.env` to set them, or
set them as real environment variables before `serve`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUDITFAST_DEFAULT_PROJECT` | `config/project.example.yaml` | Project the API opens with |
| `AUDITFAST_OUTPUT_DIR` | `output` | Where reports are written |
| `AUDITFAST_ENVIRONMENT` | `local` | `local` / `dev` / `staging` / `prod` |
| `AUDITFAST_LOG_LEVEL` | `INFO` | Logging verbosity |
| `AUDITFAST_LOG_JSON` | `false` | Structured JSON logs — enable when hosted |
| `AUDITFAST_CORS_ORIGINS` | `["http://localhost:5173"]` | Origins allowed to call the API |
| `AUDITFAST_CACHE_ENABLED` | `true` | Turn the knowledge-base cache on/off |
| `AUDITFAST_CACHE_TTL_SECONDS` | `86400` | How long a cached snapshot stays fresh |

Frontend variables live in `frontend/.env` (see `frontend/.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_API_BASE_URL` | *(empty)* | API origin in production. Leave empty in dev to use the proxy |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the dev proxy forwards `/api` |

---

## Caching and re-runs

- **Knowledge-base cache.** The first live crawl of a workspace is saved under
  `backend/kb-cache/`. Later audits read from it, so repeat runs are near-instant.
  The tool re-crawls Fabric only on a cache miss or once a snapshot ages past its
  TTL. An **incomplete crawl is never cached** — the tool keeps re-crawling until
  the reads succeed. To force a full fresh crawl, delete the `backend/kb-cache/`
  folder.
- **Permanent archive.** Separately, **every run** writes a timestamped snapshot
  to `backend/Fabric workspace kb/<workspace>/…`, so you keep a history of what
  each workspace looked like at audit time.
- **Backend doesn't hot-reload.** If you change backend code, stop the server
  (`Ctrl+C`) and start it again. The frontend hot-reloads automatically.

---

## Tips and good practices

- **Run `az login` before you open the app** for the smoothest sign-in.
- **Grant the account Viewer on every workspace** you want in scope, so none are
  skipped for access.
- **Start with all pillars**, then narrow later once you know what you care about.
- **Re-runs are cheap** thanks to caching — delete `backend/kb-cache/` only when
  you want a guaranteed-fresh crawl.
- **Share the Excel** with stakeholders; the **Risk Register** tab is a
  ready-made register you can hand over.
- **Fix by severity** — sort findings and work top-down.

---

## Frequently asked questions

**Does the tool change anything in my Fabric tenant?**
No. It only reads (GET requests plus the read-only `getDefinition`). It never
creates, edits, or deletes.

**Do I need my own Entra app registration?**
Usually no — the Azure CLI sign-in works for most people. You only need your own
app if Conditional Access blocks the built-in options.

**Why is a pillar showing 0% or "not assessed"?**
No checks are automated for that pillar yet. It's shown for completeness, not
counted against you.

**Why did I get fewer results than I expected?**
Almost always access. Use **Troubleshoot access** on the sign-in page, grant at
least Viewer on the missing workspaces, and re-run.

**Where are my report files?**
Download them from the report page (Markdown / Excel), or find them in
`backend/output/` after a CLI run. Every run is also archived under
`backend/Fabric workspace kb/`.

**Will I get the same score if I run it again?**
Yes — the scoring is deterministic. The same tenant state and settings always
produce the same numbers.

---

## Where to get more help

| Document | For |
|----------|-----|
| [README](../README.md) | Installing and starting the tool, plus setup troubleshooting |
| [Getting started](getting-started.md) | Detailed setup, sign-in options, and configuration |
| [Scoring](scoring.md) | Exactly how 0–3 scores become pillar percentages |
| [Checks](checks.md) | The full check catalog, and how to add one |
| [Architecture](architecture.md) | How the pieces fit together |
| [API](api.md) | Calling the REST API from a script or client |
