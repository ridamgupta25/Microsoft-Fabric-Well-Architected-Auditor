# Fabric Skills — vendored from `microsoft/skills-for-fabric`

This folder vendors the **Microsoft Fabric Skills** collection
([`microsoft/skills-for-fabric`](https://github.com/microsoft/skills-for-fabric),
MIT-licensed — see [LICENSE](LICENSE)) so the auditor's agents have the same
Fabric operating knowledge and the same FabricIQ MCP surface, offline.

Two things live here, and it matters which is which:

| Kind | What it is | Where it runs |
|------|-----------|---------------|
| **Skills** (`skills/`, `common/`) | Markdown AI-assistant instructions — Fabric APIs, query patterns, workload guidance. Not code. | Read by the agent as context |
| **MCP server** (FabricIQ) | Live Power BI data tools. The repo ships only *registration* scripts; the server is hosted by Microsoft. | Hosted, or re-created natively (below) |

## Skills (36)

All 36 skills are copied verbatim under [`skills/`](skills/), with their shared
references under [`common/`](common/) so every `../../common/...` link resolves.

Authoring · consumption · operations across the Fabric workloads:

- **Semantic models & Power BI** — `semantic-model-authoring`,
  `semantic-model-consumption`, `fabriciq`, `powerbi-report-authoring`,
  `powerbi-report-design`, `powerbi-report-management`, `powerbi-report-planning`
- **Spark & Lakehouse** — `spark-authoring-cli`, `spark-consumption-cli`,
  `spark-operations-cli`
- **SQL** — `sqldb-*`, `sqldw-*` (authoring / consumption / operations)
- **Eventhouse / Eventstream / Dataflows / Activator** — `eventhouse-*`,
  `eventstream-*`, `dataflows-*`, `dataflows-save-as-authoring-cli`, `activator-*`
- **Ontology & search** — `fabriciq-ontology-authoring-cli`,
  `fabriciq-ontology-consumption-cli`, `search-consumption-cli`
- **Operations** — `mlv-operations-cli`, `azmon-mirroredcatalogs-operations-cli`
- **Migration & end-to-end** — `databricks-migration`, `synapse-migration`,
  `hdinsight-migration`, `pipeline-migration`, `e2e-medallion-architecture`
- **Housekeeping** — `check-updates`

## MCP server functions (FabricIQ)

The FabricIQ MCP server exposes six read-only Power BI tools. They are available
to this project **two ways**:

1. **Hosted** — registered in the repo-root [`.mcp.json`](../.mcp.json) at
   `https://api.fabric.microsoft.com/v1/mcp/fabricaihub/integrations/m365`
   (header `X-VARIANTS: Fabric.Routing.PowerBIDataExploration`). See
   [`mcp-setup/`](mcp-setup/) for the registration scripts.
2. **Native** — re-created in the auditor's own MCP server so it owns the
   implementation and stays read-only. These call the Power BI REST API + DAX
   `executeQueries` directly:

   | FabricIQ tool | Native tool | Implementation |
   |---------------|-------------|----------------|
   | `DiscoverArtifacts` | `discover_artifacts` | `services/fabriciq_service.py` |
   | `ResolveReportIdFromUrl` | `resolve_report_id_from_url` | (pure URL parsing) |
   | `GetReportMetadata` | `get_report_metadata` | Power BI REST |
   | `GetSemanticModelSchema` | `get_semantic_model_schema` | DAX `INFO.VIEW.*` |
   | `ValueSearch` | `value_search` | DAX `SEARCH` over text columns |
   | `ExecuteQuery` | `execute_query` | Power BI `executeQueries` |

   Code: [`backend/src/auditfast/services/fabriciq_service.py`](../backend/src/auditfast/services/fabriciq_service.py)
   and [`backend/src/auditfast/clients/powerbi.py`](../backend/src/auditfast/clients/powerbi.py),
   surfaced in [`backend/src/auditfast/mcp/server.py`](../backend/src/auditfast/mcp/server.py).

   The native tools reproduce only the deterministic **data plane**. The hosted
   server's AI features — natural-language→DAX, verified answers, custom
   instructions — are intentionally left to the hosted endpoint; the
   [`fabriciq`](skills/fabriciq/SKILL.md) skill is the orchestration guidance for
   those.

### Token audience

The native Power BI tools need a bearer token for
`https://analysis.windows.net/powerbi/api` — a **different** audience from the
Fabric token the audit tools use:

```powershell
az login
az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv
```

## Updating

This is a point-in-time copy. To refresh, re-clone `microsoft/skills-for-fabric`
and replace `skills/`, `common/`, and `mcp-setup/`. See [CHANGELOG.md](CHANGELOG.md)
for the upstream version vendored.
