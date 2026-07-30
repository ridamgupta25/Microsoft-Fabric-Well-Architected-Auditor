---
description: "Reference map for check authoring — which vendored fabric-skills, common/ core docs, and MCP tools to cite for each Fabric surface when researching a check's requires[] and verifying its data is fetchable. Auto-attached when editing core/check/**."
applyTo: "backend/src/auditfast/core/check/**"
---
# Fabric-skills reference map (for check authoring)

When you research a checklist point (see the **Check Researcher** agent and
[check-authoring.instructions.md](check-authoring.instructions.md)), you must
confirm the data is **fetchable today** and pick the right `requires=` `Resource`
set. The vendored [`fabric-skills/`](../../fabric-skills) collection
(`microsoft/skills-for-fabric`) is the canonical reference for Fabric REST
semantics, item `getDefinition` payloads, and per-workload behaviour.

This file is the **index**: given the Fabric surface a check targets, it points at
the exact skill folder, the `common/*-CORE.md` doc, and the MCP tools to use. Cite
these paths in your research brief. **Reading them does not change determinism** —
they inform the check's `requires[]` and evidence, never its runtime.

> Rule of thumb: the check body only ever reads what the provider already fetches
> into `WorkspaceContext`. Use these skills to decide **whether** a signal is
> reachable and **which item definition / REST shape** carries it — then declare
> it in `requires[]` and return `not_applicable(...)` when absent.

## How to use this map
1. Identify the check's **scope** (`workspace`, `pipeline`, `notebook`,
   `lakehouse`, `semantic_model`, `report`, `eventhouse`, …) and **Fabric
   workload**.
2. Open the matching **skill** (`fabric-skills/skills/<name>/SKILL.md`) for the
   end-to-end operations, and the **`common/*-CORE.md`** for the REST/definition
   contract.
3. Confirm the signal is in an item **definition** (`getDefinition`) or a **REST
   list/metadata** call the provider makes — see
   [`common/ITEM-DEFINITIONS-CORE.md`](../../fabric-skills/common/ITEM-DEFINITIONS-CORE.md).
4. Reach for **MCP** ([`../mcp/README.md`](../mcp/README.md)) to spot-check a live
   shape: catalog tools (`list_checks`, `describe_check`) for dedup; audit tools
   (`run_check`) for a live verdict; FabricIQ tools for Power BI model/report
   metadata.

## Surface → skill → core doc → MCP

| Check surface / scope | Primary skill(s) `fabric-skills/skills/…` | Core contract `fabric-skills/common/…` | MCP tools |
|---|---|---|---|
| **Spark / notebooks** (notebook config, session tags, pool, autotune) | `spark-authoring-cli`, `spark-notebook-authoring` via `common/notebook-authoring/`, `spark-operations-cli` | `SPARK-AUTHORING-CORE.md`, `SPARK-NOTEBOOK-AUTHORING-CORE.md`, `SPARK-CONSUMPTION-CORE.md`, `SPARK-MONITORING-CORE.md` | auditfast `run_check` |
| **Lakehouse / Delta tables** (OPTIMIZE, V-Order, partitioning, schema) | `spark-authoring-cli`, `e2e-medallion-architecture` | `SPARK-AUTHORING-CORE.md`, `ITEM-DEFINITIONS-CORE.md` | auditfast `run_check` |
| **Data pipelines** (retries, activities, triggers, staging) | `pipeline-migration`, `dataflows-consumption-cli` | `ITEM-DEFINITIONS-CORE.md` | auditfast `run_check` |
| **Dataflows Gen2** (staging, refresh, query folding) | `dataflows-authoring-cli`, `dataflows-consumption-cli`, `dataflows-save-as-authoring-cli` | `DATAFLOWS-AUTHORING-CORE.md`, `DATAFLOWS-CONSUMPTION-CORE.md` | auditfast `run_check` |
| **Semantic models** (Direct Lake, RLS/OLS, aggregations, refresh, DAX) | `semantic-model-authoring`, `semantic-model-consumption` | `ITEM-DEFINITIONS-CORE.md` | FabricIQ `get_semantic_model_schema`, `execute_query`; auditfast `run_check` |
| **Power BI reports** (visuals, bookmarks, themes, accessibility, layout) | `powerbi-report-authoring`, `powerbi-report-design`, `powerbi-report-planning`, `powerbi-report-management` | `ITEM-DEFINITIONS-CORE.md` | FabricIQ `get_report_metadata`, `resolve_report_id_from_url`, `discover_artifacts` |
| **Warehouse (SQL DW)** (indexing, stats, distribution, T-SQL) | `sqldw-authoring-cli`, `sqldw-consumption-cli`, `sqldw-operations-cli` | `SQLDW-AUTHORING-CORE.md`, `SQLDW-CONSUMPTION-CORE.md` | auditfast `run_check` |
| **SQL database (Fabric SQL DB)** | `sqldb-authoring-cli`, `sqldb-consumption-cli`, `sqldb-operations-cli` | `SQLDB-AUTHORING-CORE.md`, `SQLDB-CONSUMPTION-CORE.md` | auditfast `run_check` |
| **Eventhouse / KQL** (retention, caching, update policies) | `eventhouse-authoring-cli`, `eventhouse-consumption-cli` | `EVENTHOUSE-AUTHORING-CORE.md`, `EVENTHOUSE-CONSUMPTION-CORE.md` | auditfast `run_check` |
| **Eventstream (RTI)** (sources, destinations, transforms) | `eventstream-authoring-cli`, `eventstream-consumption-cli` | `EVENTSTREAM-AUTHORING-CORE.md`, `EVENTSTREAM-CONSUMPTION-CORE.md` | auditfast `run_check` |
| **Activator / Data Activator** (alerts, rules) | `activator-authoring-cli`, `activator-consumption-cli` | `ITEM-DEFINITIONS-CORE.md` | auditfast `run_check` |
| **FabricIQ / ontology / semantic layer** | `fabriciq`, `fabriciq-ontology-authoring-cli`, `fabriciq-ontology-consumption-cli` | `ITEM-DEFINITIONS-CORE.md` | FabricIQ `discover_artifacts`, `value_search`, `execute_query` |
| **Mirrored catalogs / monitoring** | `azmon-mirroredcatalogs-operations-cli`, `mlv-operations-cli` | `COMMON-CORE.md` | auditfast `run_check` |
| **Search / index** | `search-consumption-cli` | `COMMON-CORE.md` | FabricIQ `value_search` |
| **Migration provenance** (Databricks/HDInsight/Synapse origins) | `databricks-migration`, `hdinsight-migration`, `synapse-migration` | `ITEM-DEFINITIONS-CORE.md` | — |
| **Any item's definition shape / CRUD** | (workload skill above) | `ITEM-DEFINITIONS-CORE.md`, `COMMON-CORE.md`, `COMMON-CLI.md` | auditfast `describe_check` |

## Pillar → likely surfaces (which checks live where)
The auditor's pillars map to `core/check/<pillar>/<layer>/`:

- **`foundation`** — workspace-level: Git integration, deployment pipelines,
  naming, capacity assignment → `COMMON-CORE.md`, `ITEM-DEFINITIONS-CORE.md`.
- **`performance_capacity`** (`data_prep`) — Spark/Delta/notebook tuning,
  Direct Lake → `SPARK-*-CORE.md`, `semantic-model-authoring`.
- **`data_management_quality`** (`data_prep` · `data_operations` · `data_storage`)
  — medallion, schemas, lineage, retention → `e2e-medallion-architecture`,
  `SPARK-AUTHORING-CORE.md`, `EVENTHOUSE-*`.
- **`cost_resource_optimization`** (`data_operations`) — capacity, autoscale,
  pause/resume, pipeline efficiency → `SPARK-CONSUMPTION-CORE.md`,
  `pipeline-migration`.
- **`security`** (`data_operations` · `data_storage`) — RLS/OLS, workspace roles,
  sensitivity labels, private endpoints → `semantic-model-authoring`,
  `COMMON-CORE.md`.
- **`operations_reliability`** (`data_prep` · `data_operations`) — retries,
  monitoring, alerting, DR → `SPARK-MONITORING-CORE.md`,
  `activator-authoring-cli`, `pipeline-migration`.

## Guardrails (do not break)
- These skills are **research references only**. A check body must stay pure —
  no network, no LLM, no clock, no randomness (AGENTS.md §core invariants).
- If a signal is only reachable via tenant-admin / capacity-metrics / audit-log
  APIs the provider does **not** call, classify the point `automation=ROADMAP`
  (an attestation) instead of writing a check that guesses.
- Whatever you cite here must end up as an explicit `Resource` in the check's
  `requires=`, and the check must return `not_applicable(...)` — never FAIL —
  when that resource is missing from `WorkspaceContext`.
