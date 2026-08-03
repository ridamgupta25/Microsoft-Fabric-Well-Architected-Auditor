# Microsoft Fabric Well-Architected Auditor

A **rule-based** platform that audits Microsoft Fabric workspaces against the
Well-Architected pillars, **read-only** and **fully deterministic** — every check
is a fixed rule with a fixed threshold and pre-written remediation, so the same
input always produces the same score.

- **Best-practice level, not a deep dive.** It checks whether the *implemented*
  workspaces and pipelines follow Fabric best practices. It does not trace data
  flow, profile rows, or review code line by line.
- **Multi-workspace per project.** Register the workspaces that make up a
  project, tag each with its layer role, and get one aggregated score plus a
  per-workspace and **pillar × layer** breakdown.
- **Automated + self-assessed.** Most checks are verified from the tenant; the
  points a machine can't read (a tested DR plan, a documented cost review) are
  **interactive** — the reviewer picks a scored option during the audit, Azure
  Well-Architected Review style, and skipping records N/A rather than a low score.
- **Cached & incremental.** Each workspace is crawled once into an on-disk
  knowledge base and re-read from it on later runs, so repeat audits are
  near-instant and re-crawl Fabric only when a snapshot goes stale.
- **AI stays out of scoring.** A deterministic **checklist-intake** layer — dedup
  a best-practice point against the catalog, or draft a `@check` proposal — is
  live; an optional model advisory is off by default, so scoring stays reproducible.

---

## Quick start

Every audit reads a **live** Microsoft Fabric tenant — sign-in is required. The
easiest path needs only `az login` and no app registration; see
[docs/getting-started.md § Signing in](docs/getting-started.md#8-signing-in).

```powershell
# 1. Backend (from the repository root)
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"

# 2. Frontend
cd frontend
npm install
cd ..
```

Then run both, in two terminals:

```powershell
# Terminal 1 — API
cd backend
..\.venv\Scripts\python.exe -m auditfast serve --port 8000

# Terminal 2 — web app
cd frontend
npm run dev
```

Open **http://localhost:5173**, click **Connect to Fabric** and sign in, then go
to **Run audit** and click **Run audit**.

> Full setup, troubleshooting, sign-in options, and configuration:
> **[docs/getting-started.md](docs/getting-started.md)**

| URL | What |
|-----|------|
| http://localhost:5173 | The web app |
| http://127.0.0.1:8000/docs | **Swagger UI** — try any endpoint |
| http://127.0.0.1:8000/api/v1/health | Health + how many checks loaded |

### Command line (no frontend)

```powershell
cd backend
..\.venv\Scripts\python.exe -m auditfast run --project config/project.example.yaml
..\.venv\Scripts\python.exe -m auditfast checks --pillar Security
```

`run` signs you in first (device-code flow) — every audit reads the live
tenant, so there is no `--mock`/`--live` flag to choose between.

Reports land in `backend/output/` as Markdown and Excel (Scorecard / Checks /
Risk Register).

### Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest      # 189 passed, 7 skipped, fully offline against a recorded test fixture
```

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

**148 checks** across seven pillars — 64 verified today (`automated`) and 84
automatable-but-pending (`roadmap`). The self-assessed (`interactive`) questionnaire
machinery remains, but no interactive points are registered today.

| Scope | Checks | Examples |
|-------|-------:|----------|
| **Workspace** | 107 | naming, roles via security groups, least-privilege admins, guest access, sensitivity labels, Git, deployment pipeline, capacity, orphaned items, layer content / separation, inventory |
| **Pipeline** | 12 | naming, descriptions, parameterization, retry, on-failure path, failure notification, timeouts, no hardcoded secrets |
| **Notebook** | 29 | Delta MERGE / OPTIMIZE / VACUUM / Z-ORDER / V-ORDER, table properties, retention, Spark env & pinned libraries, shuffle / cache / repartition, `SELECT *` |

| Pillar | Checks |
|--------|-------:|
| Data Management & Quality | 53 |
| Operations & Reliability | 33 |
| Performance & Capacity | 23 |
| Security | 16 |
| Cost & Resource Optimization | 15 |
| Governance & Compliance | 7 |
| Foundation (informational, unscored) | 1 |

Lakehouse / Delta storage, semantic models, and Eventhouse are not yet
automated. The engine dispatches on object *scope*, so adding them requires no
engine change.

---

## Documentation

| Document | |
|----------|---|
| **[Getting started](docs/getting-started.md)** | **Setup, running, configuration, troubleshooting** |
| [Architecture](docs/architecture.md) | Layers, runtime flow, the core contracts |
| [Checks](docs/checks.md) | Full catalog, and how to add one |
| [Scoring](docs/scoring.md) | How 0–3 scores become pillar percentages |
| [API](docs/api.md) | REST reference and design notes |
| [Migration](docs/migration.md) | What changed from the Flask original, and what was reused |
| [Scalability](docs/scalability.md) | Scale, Azure deployment, future AI layer |

**Adding a check?** Start at
[docs/checks.md → Adding a check](docs/checks.md#adding-a-check).
