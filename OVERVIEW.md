# Fabric Well‑Architected Auditor — Overview

A **rule‑based (no‑AI)** tool that signs in to Microsoft Fabric **read‑only**, runs
deterministic best‑practice checks across a project's workspaces, scores them
against the five Well‑Architected pillars, and produces a rated report.

## What it does
- Reads a Fabric project (offline **mock** fixture or **live** read‑only OAuth2) and scores each check **0–3**.
- Rolls checks up into a **pillar scorecard** + **per‑workspace breakdown**.
- Lists **findings with pre‑written remediation**; exports **Markdown + Excel** reports and a **risk register**.
- Best‑practice / architecture level only — it does **not** profile data rows or review code line by line.
- Fully **deterministic — no AI**, so the same input always gives the same score.

## Pillars & checks (Phase 1)
- **Pillars scored:** Reliability, Security, Cost Optimization, Operational Excellence. *(Performance = Phase 2.)*
- **Workspace checks:** naming convention, roles use security groups, least‑privilege admins, no guest access,
  sensitivity labels, Git enabled, deployment pipeline, capacity assigned, orphaned items, layer content/separation.
- **Pipeline checks (per pipeline):** naming, descriptions, parameterization (no hardcoded endpoints), retry policy,
  on‑failure path, failure notification, explicit timeouts, no hardcoded secrets.

## Tech stack
- **Backend:** Python + **Flask** (app factory + blueprints); read‑only OAuth2 via **MSAL**.
- **Frontend:** vanilla **HTML/CSS/JS (ES modules)** — no build step, no framework.
- **Tests:** **pytest** + Flask test client.

## Architecture (folders)
```
backend/auditfast/
  core/        models, scoring, engine, checks     (pure domain — no AI)
  clients/     fabric_client                        (read-only Fabric adapter)
  services/    audit_service, auth_service          (orchestration)
  reporting/   markdown / excel / console
  security/    device_flow                          (read-only OAuth)
  web/         create_app() + routes/               (Flask JSON API)
frontend/
  index.html, css/styles.css
  js/  core/  features/  ui/  main.js
```

Request flow: **browser → Flask JSON API → services → core (rules + scoring) / clients (read Fabric) → results**.

## Run it (from a fresh clone)
```powershell
# 1) create a virtual env and install deps
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt

# 2) start the web UI (Mock mode works fully offline)
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --project config/project.example.yaml
# open http://127.0.0.1:8000

# 3) run the tests
..\.venv\Scripts\python.exe -m pytest -q
```

For **live** mode, sign in read‑only in the UI (email / Azure CLI) and load your Fabric workspaces.

## Status
- **Phase 1 working:** workspace + pipeline checks, mock + live modes, web UI, reports, risk register.
- **No AI** in this release; an AI‑assisted *summary/remediation* layer is a planned later phase.
- Deep‑dive items (data profiling, notebooks, Delta, semantic models, reports) are Phase 2 / handled via the Excel checklist.
