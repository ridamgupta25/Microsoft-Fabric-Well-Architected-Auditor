# MCP servers used by the authoring agents

The agents in [`../agents`](../agents) research and validate checks through two
MCP servers, configured in [`.mcp.json`](../../.mcp.json) at the repo root.

## 1. `fabric-well-architected-auditor` (local, this repo)
Stdio server — `python -m auditfast.mcp.server` (needs the `mcp` extra:
`pip install -e "backend[mcp]"`). A thin adapter over `auditfast.services`, so what
an agent sees equals what the REST API returns. Read-only.

**Catalog tools** (no tenant, no sign-in — safe to call freely):
`list_pillars`, `list_layers`, `list_checks`, `describe_check`, `catalog_summary`.
Use these to find existing coverage and match conventions before writing a check.

**Audit tools** (need a Fabric bearer token): `list_workspaces`, `run_check`,
`run_audit`, `summarize_findings`. Use `run_check` to see one rule's live verdict.

**FabricIQ tools** (need a Power BI-audience token): `discover_artifacts`,
`resolve_report_id_from_url`, `get_report_metadata`, `get_semantic_model_schema`,
`value_search`, `execute_query`.

## 2. `FabricIQ` (hosted, Microsoft)
The hosted FabricIQ MCP endpoint for Power BI data exploration. Read-only.

## Notes
- No tool returns the token it was given.
- The catalog tools are the cheapest way to answer "does the tool already cover
  this?" — but the canonical dedup path is `POST /api/v1/checklist/assess`.
- The vendored [`fabric-skills/`](../../fabric-skills) collection is the reference
  for Fabric REST semantics the agents cite when choosing `requires=`.
