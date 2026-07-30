# MCP servers used by the authoring agents

The agents in [`../agents`](../agents) research and validate checks through two
MCP servers, configured in [`.mcp.json`](../../.mcp.json) at the repo root.

## 1. `fabric-well-architected-auditor` (local, this repo)
Stdio server — `python -m auditfast.mcp.server` (needs the `mcp` extra:
`pip install -e "backend[mcp]"`). A thin adapter over `auditfast.services`, so what
an agent sees equals what the REST API returns. Read-only. No tool returns the token
it was given.

### Complete tool reference
`✓ token-free` = safe to call with no sign-in. `⚿ needs token` = pass a Fabric
(or Power BI-audience) bearer token.

| Tool | Signature | Token | What it does |
|---|---|---|---|
| `list_pillars` | `()` | ✓ | The 7 pillars + check counts. |
| `list_layers` | `()` | ✓ | The layer roles + counts. |
| `list_checks` | `(pillar?, scope?, layer?)` | ✓ | Filter the catalog. **Dedup step 1.** |
| `describe_check` | `(check_id)` | ✓ | One check's full spec (`ref`, `requires`, `severity`, `automation`, doc). |
| `catalog_summary` | `()` | ✓ | Totals by pillar and by scope. |
| `assess_checklist_point` | `(point)` | ✓ | Is a point already covered? Draft proposal if not. **Dedup step 2.** |
| `assess_checklist_batch` | `(points?, content?, filename?, workspace_ids?, run_checks?)` | ✓ | Assess a whole checklist + run covered checks over the offline KB. |
| `list_declared_workspaces` | `(project?)` | ✓ | Workspaces the project YAML declares. |
| `list_workspaces` | `(token)` | ⚿ | Every workspace the token can see. |
| `run_check` | `(check_id, workspace_id, token, layer?, project?)` | ⚿ | **Run one rule live** — the fastest way to see a new check's verdict. |
| `run_audit` | `(token, pillars?, workspaces?, project?)` | ⚿ | Full scorecard (results truncated to 50). |
| `summarize_findings` | `(token, project?)` | ⚿ | Roll-up of the latest findings. |

**FabricIQ / Power BI tools** (⚿ Power BI-audience token): `resolve_report_id_from_url(url)`,
`discover_artifacts(...)`, `get_report_metadata(...)`, `get_semantic_model_schema(...)`,
`value_search(...)`, `execute_query(...)` — for inspecting a semantic model's schema
or a report's metadata when researching a `SEMANTIC_MODEL`/`REPORT`-scoped check.

### The two calls you make most
```jsonc
// 1. Dedup a point before authoring
assess_checklist_point({ "point": "Delta tables are OPTIMIZE-compacted after large writes" })
// 2. See a just-written check's live verdict on one workspace
run_check({ "check_id": "DELTA-OPTIMIZE", "workspace_id": "<guid>", "token": "<bearer>" })
```

## 2. `FabricIQ` (hosted, Microsoft)
The hosted FabricIQ MCP endpoint for Power BI data exploration. Read-only.

## Notes
- No tool returns the token it was given.
- The catalog tools are the cheapest way to answer "does the tool already cover
  this?" — but the canonical dedup path is `POST /api/v1/checklist/assess`.
- The vendored [`fabric-skills/`](../../fabric-skills) collection is the reference
  for Fabric REST semantics the agents cite when choosing `requires=`.
