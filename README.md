# AuditFAST Core — Fabric Well-Architected Auditor (Phase 1)

A **rule-based (no-AI)** tool that signs in to Microsoft Fabric (read-only), runs
deterministic **best-practice** checks across a project's workspaces, scores them
against the five Well-Architected pillars, and produces a rated report.

- **Best-practice level, not a deep-dive.** It checks whether the *implemented*
  pipelines and workspaces follow Fabric best practices — it does **not** trace
  data flow, profile rows, or review code line by line.
- **Multi-workspace per project.** Register the workspaces that make up a project
  (Data Prep / Data Storage / Data Logs / Data Operations / Reporting) and get one
  aggregated score plus a per-workspace breakdown.
- **No AI in this release** (an AI-assisted layer is planned later).

Design doc: [`../09-fabric-well-architected-auditor-draft.md`](../09-fabric-well-architected-auditor-draft.md)

---

## Quick start — interactive web UI (recommended)

Run from the repository root (`auditfast-core/`). The dependencies live in
`backend/requirements.txt`, and the app is started from the `backend/` folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt

cd backend
python -m auditfast serve --project config/project.example.yaml
```

Your browser opens `http://127.0.0.1:8000`. In the app you can:

- pick **which workspaces** to audit (each shows its layer role: Data Prep /
  Storage / Logs / Ops / Reporting),
- pick **which pillars** to assess (Reliability, Security, Cost, Operational
  Excellence, Performance),
- click **Run Audit** to see a live pillar scorecard, per-workspace breakdown,
  colour-coded findings, an "all checks" view (workspace checks are flagged as
  *common to every project*), and
- download the **Markdown** and **Excel** reports.

The backend is a small **Flask** app (app factory + blueprints); the front end is
plain **HTML/CSS/JS (ES modules)** served as static files, with no build step.

## Command-line run (headless / CI)

```powershell
cd backend
python -m auditfast run --project config/project.example.yaml --mock
# optional: only score some pillars
python -m auditfast run --project config/project.example.yaml --mock --pillars Security,Reliability
```

Outputs:
- Console pillar scorecard + per-workspace breakdown
- `output/audit-report.md` — WAF-style Markdown report
- `output/audit-report.xlsx` — Scorecard / Checks / Risk Register sheets

Run the tests (from `backend/`):

```powershell
cd backend
python -m pytest -q      # 10 tests: rule engine + API (Flask test client)
```

## Live mode (read-only OAuth2)

1. Register a Microsoft Entra app (public client) with **delegated, read-only**
   Fabric scopes (`Workspace.Read.All`, `Item.Read.All`).
2. Put the `tenant_id`, `client_id`, real workspace IDs, and layer roles into a
   copy of `config/project.example.yaml`.
3. Run:

   ```powershell
   cd backend
   python -m auditfast run --project config/my-project.yaml --live
   ```

   You'll be prompted with a device-code URL to sign in. The tool only ever
   issues read calls (and the read-only `getDefinition`); it never writes.

## What Phase 1 checks

| Level | Checks |
|-------|--------|
| **Workspace** | naming convention, roles use security groups, least-privilege admins, no guest access, sensitivity labels, Git enabled, deployment pipeline, capacity assigned, orphaned items, inventory |
| **Pipeline** (per pipeline) | naming, descriptions/annotations, parameterized (no hardcoded endpoints), retry policy, on-failure path, failure notification, explicit timeouts, no hardcoded secrets |

Pillars scored: **Reliability, Security, Cost Optimization, Operational Excellence.**
**Performance Efficiency** and other technologies (notebooks, Delta, semantic
models, reports) are Phase 2 / handled via the Excel checklist.

## Project layout

```
auditfast-core/
├─ backend/
│  ├─ auditfast/
│  │  ├─ cli.py            # `auditfast run ...` and `auditfast serve`
│  │  ├─ core/             # domain: models, scoring, engine, checks/ (no AI)
│  │  ├─ clients/          # fabric_client — read-only Fabric adapter (mock + live)
│  │  ├─ services/         # audit_service + auth_service (orchestration)
│  │  ├─ reporting/        # markdown / excel / console reports
│  │  ├─ security/         # device_flow — MSAL read-only OAuth2
│  │  └─ web/              # create_app() Flask factory + routes/ (JSON API)
│  ├─ config/             # project.example.yaml + remediation.yaml
│  ├─ sample_data/        # tenant.json (offline demo)
│  ├─ tests/              # pytest: rule + API tests
│  ├─ requirements.txt
│  ├─ wsgi.py             # production WSGI entry (waitress / gunicorn)
│  └─ run.py
└─ frontend/
   ├─ index.html
   ├─ css/styles.css
   └─ js/  core/  features/  ui/  main.js
```

## Extending

- **Add a check:** write a function in `backend/auditfast/core/checks/workspace_checks.py`
  or `pipeline_checks.py`, decorate it with `@workspace_check` / `@pipeline_check`,
  and add its remediation text to `backend/config/remediation.yaml`.
- **Tune conventions:** edit `naming_convention`, `pipeline_naming_convention`,
  `orphan_days`, `max_admins` in the project YAML.
