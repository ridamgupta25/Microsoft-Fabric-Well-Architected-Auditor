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

## Contents (maps to the target architecture)

| Folder | Role |
|--------|------|
| [`agents/`](agents) | The multi-agent authoring workflow: `checklist-author` → `check-researcher` → `check-implementer` → `check-reviewer`. |
| [`skills/`](skills) | `check-authoring` — the end-to-end workflow, with links to `fabric-skills/` and the MCP tools. |
| [`prompts/`](prompts) | `add-check` — a `/add-check` slash command that runs the whole loop for one point. |
| [`mcp/`](mcp) | Which MCP servers the agents use. Wired for VS Code in [`.vscode/mcp.json`](../.vscode/mcp.json) (local auditor + hosted FabricIQ). |
| [`harness/`](harness) | The validate-a-generated-check gate — an **executable** [`validate_check.py`](harness/validate_check.py) plus pytest + ruff + registry-count. |
| [`instructions/`](instructions) | `check-authoring` invariants, auto-attached when editing `core/check/**`. |

## The one rule
Everything here obeys AGENTS.md: **scoring stays deterministic and reproducible.**
A generated check only ever runs after a human reviews and merges it, and it must
report **N/A, never FAIL, on missing data** — so extending coverage can never make
an existing run start failing with "could not fetch".
