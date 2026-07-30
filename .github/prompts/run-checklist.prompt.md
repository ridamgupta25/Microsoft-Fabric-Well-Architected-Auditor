---
description: "Run a user-supplied custom checklist (CSV / JSON / Markdown) through the auditor: parse the points, dedup each against the catalog, and evaluate the covered automated checks over the offline knowledge base — with a live fallback for workspaces that have no cached snapshot."
argument-hint: "Path to a checklist file, e.g. intake/manual/checklist-points.example.csv"
---
Run this custom checklist through the auditor: ${input:file}

This is the **batch** path — it fires only when the user brings *their own*
checklist, and it is **additive**: it never registers a check and never changes an
audit score. It is deterministic and offline by default (reads cached snapshots in
`backend/kb-cache/`), using a live Fabric read only for a workspace with no
snapshot yet.

Do the work through the existing `checklist_batch` service — do **not** hand-roll
parsing or run checks ad hoc:

1. **Parse.** The file may be CSV (`point[,pillar,scope,notes]`), JSON (array of
   strings or `{point,…}` objects), or Markdown / plain text (one point per
   line). `checklist_batch.parse_checklist(content, filename=…)` auto-detects the
   format.
2. **Assess + run.** Call it end-to-end. Pick the surface that fits:
   - **CLI (offline, token-free):** from `backend/`, run
     `..\.venv\Scripts\python.exe -m auditfast checklist <file>` — writes
     `output/checklist-report.md` and `.json`.
   - **REST:** `POST /api/v1/checklist/batch` with `{ "content": "<file text>",
     "filename": "<name>", "run_checks": true }` (add `auth_session` only for the
     live fallback).
   - **MCP:** the auditfast `assess_checklist_batch` tool (offline).
   - **Python:** `checklist_batch.run_checklist(points, run_checks=True)`.
3. **Read the result.** For each point: `covered` → the matched check id(s) and
   its per-workspace verdict from the KB (`kb`/`live`/`none` source); or
   `not_covered` → a draft proposal to author later. `summary` rolls up
   total/covered/not_covered/evaluated and per-status verdicts.

To **author** any `not_covered` point into a real check, hand it to the
`/add-check` prompt (the checklist-author loop) — that is the separate authoring
path.

Report: points parsed, covered vs not_covered counts, the per-workspace verdicts
for covered checks, and the list of not_covered points worth authoring.
