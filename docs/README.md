# Documentation

A rule-based platform that audits **Microsoft Fabric** workspaces against the
Well-Architected pillars, read-only and fully deterministic — every check is a
fixed rule with a fixed threshold and pre-written remediation, so the same input
always produces the same score.

---

## Start here

| # | Document | Read it when you want to… |
|---|----------|---------------------------|
| 1 | **[getting-started.md](getting-started.md)** | **Set the project up and run it.** Prerequisites, install, both servers, first audit, troubleshooting |
| 2 | **[how-to-use.md](how-to-use.md)** | **Use the tool, screen by screen.** Sign in, run an audit, read the report, use the CLI — the guide to share with colleagues |
| 3 | [architecture.md](architecture.md) | Understand the layers, the runtime flow, and the contracts everything hangs off |
| 4 | [checks.md](checks.md) | See every check, or add a new one |
| 5 | [managing-checks.md](managing-checks.md) | **Step-by-step**: add / remove checks, and run automated-only vs. the self-assessed questionnaire |
| 6 | [scoring.md](scoring.md) | Understand how 0–3 scores roll up into pillar percentages |
| 7 | [api.md](api.md) | Call the API from a client, a script, or a test |
| 8 | [migration.md](migration.md) | See what changed from the Flask original, what was reused, and why |
| 9 | [scalability.md](scalability.md) | Plan for scale, Azure deployment, and the future AI layer |
| 10 | [advisory-ai.md](advisory-ai.md) | Understand the AI-assisted advisory report for non-deterministic checks |

New to the codebase? **getting-started.md** then **architecture.md**. The rest
assumes their vocabulary.

---

## The system in one paragraph

A *project* spans one or more Fabric *workspaces*, each tagged with a **layer**
(Data Prep, Data Storage, Data Logs, Data Operations, Reporting / Semantic). A
**provider** reads each workspace into a normalized snapshot — from the live
Fabric REST API or an offline fixture. A registry of small pure functions, the
**checks**, each inspect that snapshot and return a verdict scored 0–3. Those
roll up into an overall score, a per-pillar scorecard, a per-workspace breakdown,
and a **pillar × layer matrix**, then render as JSON for the React app and as
Markdown and Excel files.

```mermaid
flowchart LR
    A[Fabric REST<br/>read-only] --> B[CachingProvider<br/>+ on-disk KB]
    B --> C[Engine<br/>runs selected checks]
    C --> D[CheckResult list]
    D --> E[Scoring<br/>weighted roll-up]
    E --> F[REST API<br/>Markdown · Excel]
    F --> G[React SPA]
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind, Axios |
| API | FastAPI, Pydantic v2, uvicorn |
| Domain | Pure Python — no framework dependency |
| Auth | MSAL, read-only delegated Entra scopes |
| Reports | openpyxl (Excel), Markdown |
| Tests | pytest + FastAPI TestClient — 189 passing (7 skipped), fully offline |

Three adapters sit over one service layer: the **REST API**, the **CLI**, and an
**MCP server**. They cannot disagree, because there is one implementation with
three front doors.

---

## Current coverage

**148 checks** — 64 verified today (`automated`), 84 automatable-but-not-yet (`roadmap`),
across seven pillars. The self-assessed (`interactive`) questionnaire machinery
remains, but no interactive points are registered today.

| Pillar | Checks |
|--------|-------:|
| Data Management & Quality | 53 |
| Operations & Reliability | 33 |
| Performance & Capacity | 23 |
| Security | 16 |
| Cost & Resource Optimization | 15 |
| Governance & Compliance | 7 |
| Foundation (informational, unscored) | 1 |

Automated today: workspace-level, Data Pipeline, and Spark/Delta **notebook**
checks. **Interactive** checks are scored from the reviewer's answer to a question
asked during the run (skipping records N/A, never a low score). `roadmap` checks
need a Fabric API the provider does not yet call (capacity metrics, audit logs,
SQL-endpoint column schemas) and are reported as attestations until promoted. The
`Scope` members for Lakehouse, semantic models, and Eventhouse exist so adding
them requires no engine change.

**AI stays out of the scoring path.** New in this release: a **checklist-intake**
layer (`ai/matching.py` dedup + `ai/authoring.py` proposal drafting) behind
`POST /api/v1/checklist/assess` and the Checklist page, plus the `.github/`
agentic authoring loop. The optional model **advisory** (`AUDITFAST_AI_ENABLED`,
off by default) only enriches that checklist flow — scoring stays deterministic
and reproducible.

---

## Design documents

The original specification, the 13-area deep-dive checklist, and the scoring
rubric live in `Local/` at the repository root, which is **excluded from git**
because the checklist contains findings from a real engagement. Several source
files cite them by name; those references will not resolve in a fresh clone.

Moving a scrubbed copy under `docs/design/` is an open task.
