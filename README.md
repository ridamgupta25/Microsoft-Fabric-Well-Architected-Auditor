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
- **No AI.** An AI-assisted layer is scaffolded but deliberately unimplemented;
  scoring must stay reproducible.

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
..\.venv\Scripts\python.exe -m pytest      # 53 passed, fully offline against a recorded test fixture
```

---

## Architecture

```
backend/src/auditfast/
  core/       engine · checks · scoring · models   ← depends on nothing
  clients/    the read-only Fabric REST provider
  services/   orchestration; the single audit path, framework-free
  api/v1/     FastAPI routers, versioned
  cli.py      mcp/    two further adapters over the same services
  schemas/    config/  database/  reporting/  security/
  ai/         scaffolding only — nothing implemented
frontend/src/
  pages/  components/  layouts/  services/  hooks/  context/  types/
```

The dependency rule is one-way: **`core/` imports nothing outward.** The REST
API, the CLI, and the MCP server are three front doors onto one service layer, so
they cannot produce different numbers.

Audits are **fire-and-poll**: `POST /api/v1/audit` returns an id immediately and
the work runs in the background, because a tenant-wide run can take minutes.

---

## What it checks

| Level | Checks |
|-------|--------|
| **Workspace** (12) | naming, roles use security groups, least-privilege admins, no guest access, sensitivity labels, Git enabled, deployment pipeline, capacity assigned, orphaned items, item inventory, layer content, layer separation |
| **Pipeline** (8, each) | naming, descriptions, parameterization, retry policy, on-failure path, failure notification, explicit timeouts, no hardcoded secrets |

| Pillar | Checks |
|--------|-------:|
| Operational Excellence | 8 |
| Security | 5 |
| Reliability | 4 |
| Cost Optimization | 2 |
| Performance Efficiency | **0** — not yet automated |

Notebooks, Lakehouse/Delta, semantic models, and Eventhouse are not yet
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
