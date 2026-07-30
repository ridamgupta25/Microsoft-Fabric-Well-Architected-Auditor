# `.github/` — the agentic authoring layer

This folder is the **brain** that lets GitHub Copilot extend the auditor: given a
new checklist point, it researches, writes, and validates a real deterministic
`@check`. It is **additive** — none of it runs during an audit, and none of it can
change a score. The deterministic engine under `backend/src/auditfast/core` is
untouched.

## How a checklist point becomes a check

```
User point ──▶ POST /api/v1/checklist/assess ──▶ covered?
                          │                          │
                    (intake_service)            yes ─┴─▶ run the existing check
                          │
                          no
                          ▼
        checklist-author (orchestrator agent)
          ├─ check-researcher   (read-only: fabric-skills + MCP catalog + docs)
          ├─ check-implementer  (writes @check + remediation.yaml)
          └─ check-reviewer     (runs the harness, updates pinned counts)
                          ▼
        merged deterministic @check  ──▶  runs like every other check
```

The dedup + proposal step is served by the backend
[`intake_service`](../backend/src/auditfast/services/intake_service.py) and the
[`ai/`](../backend/src/auditfast/ai) matcher/author; the authoring loop is the
agents here.

## Running a whole custom checklist (the batch path)

Separately from authoring one point, a user can bring **their own checklist**
(CSV / JSON / Markdown) and run it through the auditor in bulk:

```
Checklist file ──▶ checklist_batch.parse_checklist ──▶ per point: assess (dedup)
                                                          │
                                    covered (automated) ──┴─▶ evaluate over the
                                                              offline KB (kb-cache/)
                                                              ↳ live fallback if no snapshot
                                    not_covered ───────────▶ draft proposal → /add-check
```

Surfaced everywhere the single-point tool is:
`POST /api/v1/checklist/batch`, the CLI `auditfast checklist <file>`, the MCP
`assess_checklist_batch` tool, and the **Checklist** page's *Run a custom
checklist* upload. Driven by the [`/run-checklist`](prompts/run-checklist.prompt.md)
prompt. It is **additive** — it never registers a check and never changes a score.

## Contents (maps to the target architecture)

| Folder | Role |
|--------|------|
| [`agents/`](agents) | The multi-agent authoring workflow: `checklist-author` → `check-researcher` → `check-implementer` → `check-reviewer`. |
| [`skills/`](skills) | `check-authoring` — the end-to-end workflow, with links to `fabric-skills/` and the MCP tools. |
| [`prompts/`](prompts) | `add-check` — a `/add-check` command that authors one point; `run-checklist` — a `/run-checklist` command that runs a whole custom checklist. |
| [`mcp/`](mcp) | Which MCP servers the agents use. Wired for VS Code in [`.vscode/mcp.json`](../.vscode/mcp.json) (local auditor + hosted FabricIQ). |
| [`harness/`](harness) | The validate-a-generated-check gate — an **executable** [`validate_check.py`](harness/validate_check.py) plus pytest + ruff + registry-count. |
| [`instructions/`](instructions) | `check-authoring` invariants + a `check-authoring-cookbook` (the complete Pillar/Layer/Scope/Resource/helper enumeration) + a `fabric-skills-reference` surface→skill→MCP map, all auto-attached when editing `core/check/**`. |

## The one rule
Everything here obeys AGENTS.md: **scoring stays deterministic and reproducible.**
A generated check only ever runs after a human reviews and merges it, and it must
report **N/A, never FAIL, on missing data** — so extending coverage can never make
an existing run start failing with "could not fetch".
