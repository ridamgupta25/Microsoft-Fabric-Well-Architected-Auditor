# AuditFAST Core — Fabric Well-Architected Auditor

A **rule-based (no-AI)** tool that signs in to Microsoft Fabric **read-only**, runs
deterministic best-practice checks across the workspaces that make up a project,
scores them against the Well-Architected pillars, and produces a rated report.

- **Best-practice level, not a deep-dive.** It checks whether the *implemented*
  workspaces and pipelines follow Fabric best practices. It does not trace data
  flow, profile rows, or review code line by line.
- **Multi-workspace per project.** Register the workspaces that make up a project,
  tag each with its layer role (Data Prep / Storage / Logs / Operations /
  Reporting), and get one aggregated score plus a per-workspace breakdown.
- **Fully deterministic.** Every check is a fixed rule with a fixed threshold and a
  pre-written recommendation, so the same input always gives the same score. An
  AI-assisted layer is planned for a later phase.

---

## Quick start

Works fully offline in **mock** mode — no Fabric tenant or sign-in needed.

```powershell
# 1. Install (from the repository root)
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt

# 2. Launch the web UI
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --project config/project.example.yaml
```

Your browser opens `http://127.0.0.1:8000`. In the app you can:

- pick **which workspaces** to audit and tag each with its layer role,
- pick **which pillars** to assess,
- click **Run Audit** for a live pillar scorecard, per-workspace breakdown, and
  colour-coded findings,
- download the **Markdown** and **Excel** reports.

### Command line (headless / CI)

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --mock

# optional: score only some pillars
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml --mock --pillars Security,Reliability
```

Outputs a console scorecard plus `output/audit-report.md` and
`output/audit-report.xlsx` (Scorecard / Checks / Risk Register sheets).

### Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

The whole suite runs offline against `sample_data/tenant.json`.

---

## Live mode (read-only OAuth2)

1. Register a Microsoft Entra **public client** app with delegated, read-only
   Fabric scopes: `Workspace.Read.All`, `Item.Read.All`.
2. Copy `backend/config/project.example.yaml` and fill in `tenant_id`,
   `client_id`, and your real workspace IDs with their layer roles.
3. Sign in from the web UI, or run the CLI with `--live` for a device-code
   sign-in.

No app registration? The UI can sign you in with just your email using
Microsoft's first-party client, or reuse an existing `az login`. Neither needs an
admin.

The tool only ever issues read calls (and the read-only `getDefinition`). It
never writes, and tokens are never stored on disk.

---

## What it checks today

| Level | Checks |
|-------|--------|
| **Workspace** (12) | naming convention, roles use security groups, least-privilege admins, no guest access, sensitivity labels, Git enabled, deployment pipeline, capacity assigned, orphaned items, item inventory, layer content, layer separation |
| **Pipeline** (8, per pipeline) | naming, descriptions/annotations, parameterization (no hardcoded endpoints), retry policy, on-failure path, failure notification, explicit timeouts, no hardcoded secrets |

Coverage by pillar:

| Pillar | Checks |
|--------|-------:|
| Operational Excellence | 8 |
| Security | 5 |
| Reliability | 4 |
| Cost Optimization | 2 |
| Performance Efficiency | **0** — Phase 2 |

Other technologies — notebooks, Delta/Lakehouse internals, semantic models,
reports — are Phase 2 and currently covered by the manual Excel checklist.

---

## Layout

```
backend/
  auditfast/
    core/        models, scoring, engine, checks   (pure domain — no AI)
    clients/     read-only Fabric adapters (mock + live)
    services/    orchestration shared by CLI and web
    reporting/   markdown / excel / console
    security/    read-only OAuth device flow
    web/         create_app() + routes/            (Flask JSON API)
  config/        project.example.yaml, remediation.yaml
  sample_data/   tenant.json (offline fixture)
  tests/
frontend/        index.html, css/, js/ (vanilla ES modules — no build step)
docs/            architecture, checks, scoring, API, development
```

Request flow: **browser → Flask JSON API → services → core (rules + scoring) /
clients (read Fabric) → results**.

---

## Documentation

| Document | |
|----------|---|
| [docs/architecture.md](docs/architecture.md) | Layers, runtime flow, and the data contracts everything hangs off |
| [docs/checks.md](docs/checks.md) | Every check in the catalog, and how to add one |
| [docs/scoring.md](docs/scoring.md) | How 0–3 scores roll up into pillar percentages and ratings |
| [docs/api.md](docs/api.md) | JSON API reference |
| [docs/development.md](docs/development.md) | Setup, testing, and full project-YAML configuration |
| [OVERVIEW.md](OVERVIEW.md) | Short project overview |

**Adding a check?** Start with
[docs/checks.md → Adding a check](docs/checks.md#adding-a-check).
