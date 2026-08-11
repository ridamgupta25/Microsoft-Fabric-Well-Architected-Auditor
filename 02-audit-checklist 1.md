# Audit Checklist — MLC Microsoft Fabric Data Solution

> **Instructions**: Score each item 0–3 per the [Scoring Rubric](01-scoring-rubric.md). Mark N/A with written justification. Record evidence/observations in the Notes column.
>
> **Legend**: Score 0 = Not Implemented | 1 = Partial | 2 = Implemented | 3 = Best Practice | N/A = Not Applicable
>
> **Solution profile**: 67 workspaces, 20 databases; domains (Finance, Sales, etc.) segregated as folders within each workspace. **Data Prep** (`MLC_DATAPREP_*`) holds Pipelines + Spark Notebooks **only — no Lakehouses or Warehouses**. **Data Store** (`MLC_DATASTORE_*`) holds the medallion: Bronze Lakehouse → Silver Lakehouse → **Gold Warehouse (DW)**. **Data Consumption** (`MLC_DATACONSUMPTION_*`) holds semantic models + Power BI reports (including cross-domain reports) that read the Gold Warehouse in Data Store (cross-workspace). Supporting artifacts: a **Metadata DB** (tracks every notebook run, data source changes, full audit trail & lineage) and **Audit Tables** (data quality logs, row counts, null checks, exceptions). Sources: IFS (Oracle Cloud → Azure SQL DB → Fabric), EAM (Oracle → JSON), Adage (SQL, historicals), LIMS (SQL, TBD). Git-integrated workspaces; 1 Service Principal (SPN) in the workspace access list.

---

## Audit Category Legend

> Every checklist item is assigned a **Category (Cat)** value indicating how the item will be assessed during the audit:
>
> | Cat | Label | Description |
> |-----|-------|-------------|
> | **1** | **Automated** | Can be assessed via script, AI, or Playwright-based automation. The auditor (with Viewer/Reader access) can run queries, scan code, inspect configurations, or use APIs/portal navigation to verify this item programmatically. |
> | **2** | **Admin Review** | Requires admin-level access the auditor does not have. The auditor will request the client's admin team to navigate to the relevant admin panel / portal setting and demonstrate or screenshot the configuration. The auditor fills in the score based on what is shown. |
> | **3** | **Client Documentation** | Requires documentation, policies, processes, or domain knowledge that resides with the client's development/operations team. The auditor will share these items with the client team; they will provide the evidence and fill in the details, then submit the checklist section for auditor review. |

---

## Area 1: Architecture & Design (Weight: 8%)

### 1.1 Solution Architecture

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 1.1.1 | Clear separation of concerns across the 67 workspaces (Data Prep / Data Store / Data Consumption × Dev / QA / Prod) | 1 | | |
| 1.1.2 | Layered workspace model is intentional and documented (Prep vs Store vs Consumption responsibilities do not bleed across workspaces) | 3 | | |
| 1.1.3 | Environment isolation enforced (Dev / QA / Prod workspaces have no shared mutable artifacts or cross-env dependencies) | 1 | | |
| 1.1.4 | Domain segregation via folders (Finance, Sales, etc.) is consistent and applied uniformly across Prep and Store workspaces | 1 | | |
| 1.1.5 | Medallion architecture properly implemented (Bronze Lakehouse → Silver Lakehouse → Gold Warehouse) with clear layer boundaries | 1 | | |
| 1.1.6 | Appropriate Fabric component selection per workload (Lakehouse for Bronze/Silver, Warehouse for Gold, DB for Metadata) with documented rationale | 3 | | |
| 1.1.7 | Architecture diagram exists and reflects the actual implementation across all 67 workspaces | 3 | | |
| 1.1.8 | Single source of truth principle — no duplicate data stores serving the same purpose across domains or layers | 1 | | |
| 1.1.9 | Data Prep workspaces (`MLC_DATAPREP_*`) contain only Pipelines and Notebooks — no Lakehouses or Warehouses (all storage resides in the Data Store workspace) | 1 | | |

### 1.2 Data Architecture

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 1.2.1 | Data flow lineage is traceable end-to-end from source system to Gold Warehouse and downstream semantic models | 1 | | |
| 1.2.2 | Each medallion layer has a clearly defined purpose and transformation responsibility | 3 | | |
| 1.2.3 | Bronze Lakehouse captures raw data with audit metadata (ingestion timestamp, source system, batch ID) | 1 | | |
| 1.2.4 | Bronze immutability strategy defined (append/raw capture vs. overwrite) and matches intended layer responsibility | 3 | | |
| 1.2.5 | Silver Lakehouse applies cleansing, deduplication, conforming, and type standardization | 1 | | |
| 1.2.6 | Gold Warehouse is consumption-ready, modeled (star schema), and serves the semantic layer in the Data Consumption workspace (cross-workspace reference) | 1 | | |
| 1.2.7 | Metadata DB role clearly defined (tracks every notebook run, data source changes, control/config/watermarks, full audit trail & lineage) and separated from business data | 3 | | |
| 1.2.8 | Audit Tables role clearly defined (data quality logs, row counts, null checks, exceptions) and separated from business data | 1 | | |

### 1.3 Integration Architecture

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 1.3.1 | IFS (Oracle Cloud) ingestion path via the Azure SQL DB Fabric connection is documented, supported, and stable | 3 | | |
| 1.3.2 | EAM (Oracle) JSON-based ingestion is well defined (schema, file/endpoint contract, parsing strategy) | 3 | | |
| 1.3.3 | Adage (SQL) historical load strategy is documented (one-time vs. periodic, cut-over, and reconciliation) | 3 | | |
| 1.3.4 | LIMS (SQL, TBD) integration approach is scoped with a defined decision/target date and interim handling | 3 | | |
| 1.3.5 | Connections use secure, non-personal identities (SPN / Workspace Identity) rather than individual accounts | 1 | | |
| 1.3.6 | All source connections inventoried (consolidated source inventory maintained outside pipeline metadata) | 1 | | |
| 1.3.7 | Connection credentials use secure storage (Key Vault / Fabric-managed) — not hardcoded | 1 | | |
| 1.3.8 | On-premises data gateway (if used for any SQL source) is properly configured, sized, and highly available | 2 | | |

### 1.4 Semantic Model & Reporting Design

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 14.1.1 | Star schema followed in the semantic model (single-direction relationships, no unnecessary bidirectional filters) | 1 | | |
| 14.1.2 | Relationships correctly defined (cardinality, active/inactive) with no ambiguous paths | 1 | | |
| 14.1.3 | Measures centralized (no duplicated calculation logic across reports) | 1 | | |
| 14.1.4 | DAX follows good practices (variables, no repeated sub-expressions, avoids expensive iterators where avoidable) | 1 | | |
| 14.1.5 | Shared/certified semantic model reused across domains rather than one-off models per report | 1 | | |
| 14.1.6 | Cross-domain model design governed (conformed dimensions, consistent grain across domains) | 3 | | |
| 14.1.7 | Unused columns/tables removed from the model to reduce size and confusion | 1 | | |
| 14.1.8 | Model naming and organization are consumer-friendly (display folders, hidden keys) | 1 | | |
| 14.1.9 | Date table is marked and consistently used for time-intelligence calculations | 1 | | |
| 14.1.10 | Auto Date/Time is disabled to avoid hidden model bloat and inconsistent calendars | 1 | | |
| 14.1.11 | Visuals use explicit measures (no implicit aggregations for business-critical KPIs) | 1 | | |
| 14.1.12 | Column and measure descriptions are maintained for discoverability and self-service use | 1 | | |
| 14.3.1 | Reports have a documented owner and data source of truth | 3 | | |
| 14.3.2 | Cross-domain reports have a clear ownership and certification model | 3 | | |
| 14.3.3 | Consistent KPI definitions across domains (no conflicting versions of the same metric) | 3 | | |
| 14.3.4 | Reports use the shared certified model, not private ad-hoc extracts | 1 | | |
| 14.3.5 | Report performance is acceptable (load time, interaction responsiveness) | 1 | | |
| 14.3.6 | Endorsement (Promoted/Certified) applied to trusted models and reports | 1 | | |
| 14.3.7 | Report distribution method documented (apps, workspaces, sharing) and access-governed | 3 | | |
| 14.3.8 | Semantic-model-to-report lineage is documented and validated against the implemented data flow | 1 | | |
| 14.3.9 | Reports follow organizational branding and design standards (theme, colors, typography, and layout consistency) | 3 | | |
| 14.3.10 | Report navigation and user experience are intuitive (bookmarks, drill-through, buttons, and page navigation work correctly) | 1 | | |
| 14.3.11 | Visualizations are appropriate for the data and do not misrepresent trends, scale, or comparisons | 3 | | |
| 14.3.12 | Reports comply with accessibility standards (alt text, color contrast, keyboard navigation, and tab order) | 1 | | |
| 14.3.13 | Production reports are published through governed workspaces/apps rather than personal workspaces | 1 | | |

---

## Area 2: Data Integration & Ingestion (Weight: 9%)

### 2.1 Pipeline Design

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.1.1 | Pipelines follow consistent naming conventions (including domain prefix/folder alignment) | 1 | | |
| 2.1.2 | Pipelines are parameterized (no hardcoded sources, targets, dates, or environment values) | 1 | | |
| 2.1.3 | Master/orchestrator pipeline pattern used for coordinating dependent domain pipelines | 1 | | |
| 2.1.4 | Pipeline activities are logically grouped, annotated, and self-documenting | 1 | | |
| 2.1.5 | Parallel execution used where possible (no unnecessary sequential execution) | 1 | | |
| 2.1.6 | Pipeline annotations/descriptions populated for pipelines and key activities | 1 | | |

### 2.2 Incremental Load Strategy

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.2.1 | Incremental load implemented where applicable (watermark, CDC, delta detection) for IFS/EAM/LIMS | 1 | | |
| 2.2.2 | Full load reserved only for small reference/dimension tables or initial loads | 1 | | |
| 2.2.3 | Adage historical load clearly separated from ongoing incremental patterns | 1 | | |
| 2.2.4 | Watermark / control values persisted reliably in the Metadata DB (not volatile locations) | 1 | | |
| 2.2.5 | Initial load vs. incremental load clearly separated or parameterized | 1 | | |

### 2.3 Source Change Data (I/U/D) Processing

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.3.1 | Source change capability documented per source (which sources expose I/U/D, which require derivation) | 3 | | |
| 2.3.2 | Operation type column/flag preserved in Bronze for auditability where the source provides it | 1 | | |
| 2.3.3 | All applicable operation types (I/U/D) handled correctly in the merge strategy | 1 | | |
| 2.3.4 | Insert records validated for uniqueness / business key before merge into target | 1 | | |
| 2.3.5 | Update strategy documented (full row replacement vs. changed-columns-only) | 3 | | |
| 2.3.6 | Delete strategy documented (soft-delete flag vs. hard-delete vs. tombstone) per table | 3 | | |
| 2.3.7 | Merge conflict resolution strategy defined (e.g., last-write-wins, source-timestamp priority) | 3 | | |
| 2.3.8 | Out-of-order / late-arriving change records handled without data corruption | 1 | | |

### 2.4 Error Handling & Retry

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.4.1 | All pipeline activities have appropriate retry policies configured (copy, notebook, lookup, web, ForEach) | 1 | | |
| 2.4.2 | Retry count and interval follow reasonable patterns (not infinite retries) | 1 | | |
| 2.4.3 | On-failure paths defined for critical activities | 1 | | |
| 2.4.4 | Failed records captured to dead-letter / quarantine area (not silently dropped or halting good records) | 1 | | |
| 2.4.5 | Pipeline failure triggers notification (Data Activator, email, Teams) | 1 | | |
| 2.4.6 | Idempotency ensured — re-running a failed pipeline does not produce duplicates | 1 | | |

### 2.5 Metadata-Driven Ingestion Framework

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.5.1 | Metadata DB drives ingestion (source list, load type, schedule, target mapping) rather than hardcoded pipelines | 1 | | |
| 2.5.2 | Adding a new source/table is configuration-driven (metadata entry) not a code change | 1 | | |
| 2.5.3 | Run control tables capture batch ID, status, row counts, start/end timestamps | 1 | | |
| 2.5.4 | Metadata schema is documented and version-controlled | 3 | | |
| 2.5.5 | Framework handles per-domain configuration cleanly (Finance, Sales, etc.) | 1 | | |
| 2.5.6 | Metadata changes are governed (who can change config, and how it is promoted across environments) | 3 | | |

### 2.6 Pipeline Performance

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 2.6.1 | Pipeline execution times monitored and baselined | 1 | | |
| 2.6.2 | Copy activities use appropriate parallelism (DIU, degree of copy parallelism) | 1 | | |
| 2.6.3 | Large data movements use bulk/batch patterns, not row-by-row | 1 | | |
| 2.6.4 | Pipeline scheduling avoids capacity contention (staggered across domains, not all at once) | 1 | | |
| 2.6.5 | IFS ingestion via Azure SQL DB is tuned (query folding, indexed source reads, batch sizing) | 1 | | |
| 2.6.6 | JSON ingestion (EAM) is efficient (streaming/partitioned parse, no oversized single-file bottlenecks) | 1 | | |

### 2.7 Semantic Model & Reporting Integration

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 14.4.1 | RLS defined on semantic models and tested per role | 1 | | |
| 14.4.2 | Cross-domain reports enforce combined security correctly (a user only sees permitted domains) | 1 | | |
| 14.4.3 | Object-Level Security applied where fields must be hidden from some audiences | 1 | | |
| 14.4.4 | Workspace/app access aligns with least privilege for report consumers | 2 | | |
| 14.4.5 | Sensitivity labels applied to reports/models containing sensitive data | 1 | | |
| 14.5.1 | Refresh strategy defined and aligned with upstream Gold load completion | 1 | | |
| 14.5.2 | Incremental refresh configured for large Import models where applicable | 1 | | |
| 14.5.3 | Refresh failures alert the owning team | 1 | | |
| 14.5.4 | Semantic models and reports are source-controlled and deployed via pipeline (Dev → QA → Prod) | 1 | | |
| 14.5.5 | Deprecated reports/models retired on a defined cadence | 3 | | |
| 14.5.6 | Report metadata (description, purpose, business owner, refresh information) is complete and maintained | 3 | | |
| 14.7.1 | Report values are validated against trusted source systems on a defined cadence | 3 | | |
| 14.7.2 | Filters, slicers, and calculations produce consistent and expected results across report interactions | 3 | | |

---

## Area 3: Data Processing & Transformation (Weight: 7%)

### 3.1 Spark Notebook Quality

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.1.1 | Notebooks follow a consistent structure (parameters → imports → config → logic → output) | 1 | | |
| 3.1.2 | Notebooks are parameterized using Fabric notebook parameters or widgets | 1 | | |
| 3.1.3 | No hardcoded paths, connection strings, secrets, or environment-specific values | 1 | | |
| 3.1.4 | Cell-level documentation (markdown cells) explains business logic, not just code | 1 | | |
| 3.1.5 | Functions are modular and reusable — not monolithic single-cell scripts | 1 | | |
| 3.1.6 | Notebooks avoid `display()` / `show()` in production execution paths | 1 | | |
| 3.1.7 | All notebooks have meaningful, consistent names aligned to domain/layer | 1 | | |
| 3.1.8 | Notebook execution timeout / max runtime configured to prevent runaway Spark sessions | 1 | | |

### 3.2 Spark Code Standards

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.2.1 | Consistent language approach (PySpark vs Spark SQL — one primary, not mixed ad-hoc) | 1 | | |
| 3.2.2 | DataFrame API used over RDD API | 1 | | |
| 3.2.3 | No unnecessary `collect()`, `toPandas()`, or `count()` on large datasets | 1 | | |
| 3.2.4 | Broadcast joins used for small-large table joins where appropriate | 1 | | |
| 3.2.5 | UDFs avoided where native Spark functions exist | 1 | | |
| 3.2.6 | Schema explicitly defined at read time for external sources (not inferred on CSV/JSON) | 1 | | |
| 3.2.7 | Explicit imports only (no `import *`) | 1 | | |
| 3.2.8 | Structured logging used in notebooks (severity/context) instead of ad-hoc `print()` statements | 1 | | |
| 3.2.9 | Exception handling is implemented for critical read/write operations with meaningful error context | 1 | | |
| 3.2.10 | Notebook runs are deterministic from a clean session (no hidden state dependency between cells/runs) | 1 | | |
| 3.2.11 | Notebook transformations are organized into reusable functions/modules rather than repeated inline logic | 1 | | |
| 3.2.12 | Data writes use explicit save mode and schema evolution behavior (no accidental overwrite/merge semantics) | 1 | | |
| 3.2.13 | Join keys and business-critical column names are explicitly selected/aliased to prevent ambiguous columns | 1 | | |
| 3.2.14 | Notebook code follows established coding standards, naming conventions, and formatting guidelines | 1 | | |
| 3.2.15 | Join conditions are validated to prevent duplicate, missing, or Cartesian records | 1 | | |
| 3.2.16 | SQL queries and DataFrame transformations are reviewed for correctness, efficiency, and alignment with business logic | 1 | | |
| 3.2.17 | Notebook execution is monitored for runtime errors, data-quality failures, and validation failures with logging/alerts | 1 | | |
| 3.2.18 | Notebook code undergoes peer review before deployment to production | 3 | | |
| 3.2.19 | Critical business rules and transformation logic are verified against functional requirements before deployment | 3 | | |
| 3.2.20 | Notebook entrypoint logic is separated from reusable transformation functions to improve testability and maintainability | 1 | | |
| 3.2.21 | Schema and key data-type assertions are executed before critical writes/merges | 1 | | |
| 3.2.22 | Join outputs are validated post-join (row-count and key uniqueness checks) to detect duplicate/missing records early | 1 | | |
| 3.2.23 | Caching is paired with explicit unpersist/cleanup to avoid memory pressure across long notebook runs | 1 | | |
| 3.2.24 | Spark configuration overrides in notebooks are centralized and consistent (no conflicting settings across cells) | 1 | | |
| 3.2.25 | Notebook exits with explicit failure status/messages when validations fail so orchestration state is accurate | 1 | | |
| 3.2.26 | External side effects are retry-safe (idempotent writes/calls) to prevent duplicate actions on rerun | 1 | | |
| 3.2.27 | Static checks/linting are run for notebook code before promotion to higher environments | 1 | | |

### 3.3 Delta Lake Best Practices (Bronze / Silver Lakehouse)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.3.1 | Single `MERGE INTO` handles I/U/D atomically — not separate sequential DELETE/INSERT/UPDATE | 1 | | |
| 3.3.2 | `OPTIMIZE` (bin-compaction) scheduled appropriately (not after every micro-batch) | 1 | | |
| 3.3.3 | `VACUUM` scheduled to clean up old Delta files | 1 | | |
| 3.3.4 | Z-ORDER / liquid clustering applied on high-cardinality filter columns | 1 | | |
| 3.3.5 | V-Order enabled where Fabric recommends for read-optimized workloads | 1 | | |
| 3.3.6 | Table properties set appropriately (optimizeWrite, autoCompaction) | 1 | | |
| 3.3.7 | Delta table history / log retention configured and monitored | 1 | | |

### 3.4 Environment & Spark Pool Configuration

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.4.1 | Fabric Environments used to manage Spark dependencies | 1 | | |
| 3.4.2 | Custom library versions pinned (not latest/floating) | 1 | | |
| 3.4.3 | Spark pool size appropriate for workload (not over- or under-provisioned) | 1 | | |
| 3.4.4 | Spark configuration tuned from defaults where justified (shuffle partitions, memory) | 1 | | |
| 3.4.5 | Python/Spark runtime version is current and supported | 1 | | |

### 3.5 Spark Performance & Optimization

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.5.1 | Spark UI reviewed for skew, spill, shuffle issues on key jobs | 1 | | |
| 3.5.2 | Partition count appropriate (not 200 default for small/medium data) | 1 | | |
| 3.5.3 | Caching (`persist`/`cache`) used judiciously, not indiscriminately | 1 | | |
| 3.5.4 | Write operations use appropriate partition strategy (coalesce vs repartition; right-sized files) | 1 | | |
| 3.5.5 | No full-table scans when partition pruning is possible | 1 | | |
| 3.5.6 | Long-running notebooks profiled and optimized | 1 | | |
| 3.5.7 | Predicate pushdown verified for shortcut/external reads | 1 | | |
| 3.5.8 | Unnecessary columns eliminated in reads (explicit select, not `SELECT *`) | 1 | | |

### 3.6 Gold Warehouse Load (T-SQL / Stored Procedures)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 3.6.1 | Load pattern into the Gold Warehouse is defined and consistent (COPY INTO / CTAS / pipeline copy / stored procedure) | 1 | | |
| 3.6.2 | Silver-to-Gold transformations are set-based T-SQL (no row-by-row cursors) | 1 | | |
| 3.6.3 | Staging tables/schema used for Warehouse loads before merge into final tables | 1 | | |
| 3.6.4 | Warehouse load procedures are idempotent and re-runnable | 1 | | |
| 3.6.5 | Transaction handling / error handling (TRY...CATCH) implemented in load procedures | 1 | | |
| 3.6.6 | Warehouse loads avoid unnecessary full reloads (incremental/delta merge where possible) | 1 | | |
| 3.6.7 | Statistics are updated after significant Warehouse loads | 1 | | |

### 3.7 Semantic Model & Reporting Code Quality

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 14.2.1 | Storage mode chosen deliberately (Direct Lake / Import / DirectQuery) with documented rationale | 1 | | |
| 14.2.2 | Direct Lake fallback behavior understood and monitored (no unexpected DirectQuery fallback) | 1 | | |
| 14.2.3 | Model size and column cardinality optimized (reduce high-cardinality columns where possible) | 1 | | |
| 14.2.4 | Aggregations / summarizations used for performance-critical reports where needed | 1 | | |
| 14.2.5 | Report query performance tested at expected concurrency | 1 | | |
| 14.2.6 | Gold Warehouse structured to serve the model efficiently (no expensive per-query transformations) | 1 | | |
| 14.2.7 | Performance Analyzer (or equivalent tooling) reviewed and major bottlenecks remediated | 1 | | |
| 14.2.8 | Visual count and complexity are optimized to minimize rendering time and improve responsiveness | 1 | | |
| 14.2.9 | Unused visuals, measures, and fields are removed or hidden to improve maintainability and performance | 1 | | |
| 14.6.1 | DAX measures use `VAR` to avoid repeated sub-expressions and improve maintainability | 1 | | |
| 14.6.2 | Measures use safe arithmetic patterns (e.g., `DIVIDE`) and explicit blank/zero handling where appropriate | 1 | | |
| 14.6.3 | Expensive iterator usage (`SUMX`/`FILTER` over large tables) is minimized unless justified by business logic | 1 | | |
| 14.6.4 | Time-intelligence measures follow a consistent pattern and rely on the marked date table | 1 | | |

---

## Area 4: Data Modeling & Storage (Weight: 8%)

### 4.1 Lakehouse Design (Bronze / Silver)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.1.1 | Lakehouse Tables (managed) used for structured data; Files section for raw/unstructured | 1 | | |
| 4.1.2 | OneLake used as the single data lake — no ungoverned shadow storage | 1 | | |
| 4.1.3 | Shortcuts (if used) don't create circular references or ungoverned data access paths | 1 | | |
| 4.1.4 | Clear separation of Bronze vs Silver Lakehouse responsibilities per domain | 1 | | |
| 4.1.5 | Bronze/Silver domain folders are consistent with the workspace folder taxonomy | 1 | | |

### 4.2 Table Design

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.2.1 | Tables use meaningful, consistent naming conventions (agreed standard) | 1 | | |
| 4.2.2 | Partitioning / clustering strategy defined for large tables | 1 | | |
| 4.2.3 | Column naming is consistent and self-documenting | 1 | | |
| 4.2.4 | Data types are appropriate (no stringly-typed dates, no oversized varchars) | 1 | | |
| 4.2.5 | Audit columns present (created_date, modified_date, source_system, batch_id) | 1 | | |

### 4.3 File Format & Organization

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.3.1 | Delta format used for all analytical Lakehouse tables | 1 | | |
| 4.3.2 | Raw files in Files section organized by source/date hierarchy | 1 | | |
| 4.3.3 | File sizes avoid the small-file problem (target 128MB–1GB per file) | 1 | | |
| 4.3.4 | Orphaned files cleaned up periodically (archiving/purging policy) | 1 | | |

### 4.4 Gold Warehouse Design

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.4.1 | Warehouse schema organization is logical (by domain schema, plus staging schema) | 1 | | |
| 4.4.2 | Table and column naming conventions are consistent across the Warehouse | 1 | | |
| 4.4.3 | Data types are appropriate and sized correctly (avoid oversized varchar, correct numeric precision) | 1 | | |
| 4.4.4 | Surrogate keys implemented appropriately for dimensions using a generated-key pattern (hash key, `ROW_NUMBER()`/window, or key table) — note: Fabric Warehouse does not support IDENTITY columns | 1 | | |
| 4.4.5 | Primary/foreign key constraints declared where supported (documented as not enforced in Fabric Warehouse) | 1 | | |
| 4.4.6 | Statistics maintenance strategy defined and automated | 1 | | |
| 4.4.7 | Views/stored procedures used to abstract the semantic-facing layer from physical tables | 1 | | |
| 4.4.8 | No business logic duplicated between Silver Lakehouse and Gold Warehouse | 1 | | |
| 4.4.9 | Cross-domain conformed dimensions shared (not duplicated per domain) in the Warehouse | 1 | | |
| 4.4.10 | Warehouse capacity/scale considerations documented (concurrency, query patterns) | 3 | | |

### 4.5 Dimensional Modeling

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.5.1 | Star schema design implemented (fact + dimension tables, not flat wide tables) | 1 | | |
| 4.5.2 | Fact table grain clearly defined and documented for each fact table | 1 | | |
| 4.5.3 | Fact tables contain only foreign keys and measures (no descriptive attributes) | 1 | | |
| 4.5.4 | Dimension tables are denormalized appropriately (star over snowflake unless justified) | 1 | | |
| 4.5.5 | Conformed dimensions shared across fact tables (no duplicate dimension versions) | 1 | | |
| 4.5.6 | Surrogate keys used for dimension tables (not business keys as PKs in facts) | 1 | | |
| 4.5.7 | Date/Time dimension exists with all required attributes (fiscal periods, quarter, holidays) | 1 | | |
| 4.5.8 | SCD strategy defined and implemented per dimension (Type 1 / Type 2 / Hybrid) | 1 | | |
| 4.5.9 | SCD Type 2 includes valid_from, valid_to, and is_current flag correctly maintained (where used) | 1 | | |
| 4.5.10 | Late-arriving dimensions and facts handled (unknown/inferred member pattern) | 1 | | |
| 4.5.11 | Degenerate and junk dimensions used where appropriate | 1 | | |
| 4.5.12 | Referential integrity validated (every FK in fact tables has a matching dimension record) | 1 | | |

### 4.6 Metadata DB & Audit Tables Design

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 4.6.1 | Metadata DB schema is documented (notebook-run tracking, data source changes, control/config/watermark/mapping tables, lineage) | 3 | | |
| 4.6.2 | Metadata DB is the single source of ingestion/orchestration configuration | 1 | | |
| 4.6.3 | Metadata DB access is restricted (only framework identities can write) | 1 | | |
| 4.6.4 | Audit Tables capture data quality logs, row counts, null checks, and exceptions | 1 | | |
| 4.6.5 | Audit records are immutable / append-only (no in-place overwrite of history) | 1 | | |
| 4.6.6 | Audit Tables and Metadata DB are separated from business data stores | 1 | | |
| 4.6.7 | Retention policy defined for audit and metadata history | 3 | | |
| 4.6.8 | Audit Tables support operational queries (structured, queryable schema) | 1 | | |

---

## Area 5: Data Quality Framework (Weight: 9%)

> **Approach**: Data quality is a cross-cutting framework. Validations vary by medallion stage (what to check) and by data type (how to check). This area assesses whether a systematic DQ framework exists and whether appropriate validations are applied at each layer, with results logged to the Audit Lakehouse.

### 5.1 DQ Framework & Governance

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 5.1.1 | Data quality framework formally defined with rules, ownership, and scoring methodology | 3 | | |
| 5.1.2 | DQ rules codified in code/config (not ad-hoc manual checks) | 1 | | |
| 5.1.3 | DQ KPIs defined: completeness, accuracy, timeliness, consistency, uniqueness, validity | 3 | | |
| 5.1.4 | DQ scores computed per table/dataset and trended over time (via Audit Lakehouse) | 1 | | |
| 5.1.5 | DQ remediation workflow exists (alert → investigate → fix → verify) | 3 | | |
| 5.1.6 | DQ data contracts defined between producer and consumer teams (per domain) | 3 | | |
| 5.1.7 | DQ tool/library standardized across the solution | 1 | | |
| 5.1.8 | DQ SLAs defined per data product / Gold table | 3 | | |
| 5.1.9 | DQ failures halt pipeline progression where critical (bad data does not silently flow downstream) | 1 | | |
| 5.1.10 | DQ quarantine pattern: failed records routed to error tables with failure reason | 1 | | |

### 5.2 Bronze Layer Validation (Raw Data)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 5.2.1 | Schema validation: incoming records match expected schema (column count, names, data types) — incl. EAM JSON | 1 | | |
| 5.2.2 | Completeness: all expected source files/batches received (no missing partitions or source tables) | 1 | | |
| 5.2.3 | Timeliness: data arrives within expected SLA window | 1 | | |
| 5.2.4 | Format validation: expected encoding (UTF-8), delimiters, and JSON structure for EAM | 1 | | |
| 5.2.5 | Record count reconciliation vs. source system control counts | 1 | | |
| 5.2.6 | Duplicate detection across batches | 1 | | |
| 5.2.7 | Null/empty handling: known nullable fields documented; unexpected nulls flagged | 1 | | |
| 5.2.8 | Source metadata captured: ingestion timestamp, source system, file name, batch ID | 1 | | |
| 5.2.9 | Corrupt/malformed records isolated to quarantine (pipeline does not fail entirely) | 1 | | |

### 5.3 Silver Layer Validation (Cleansed & Conformed)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 5.3.1 | Data type conformance: all columns cast to standard types (dates as DATE, correct numeric precision) | 1 | | |
| 5.3.2 | Referential integrity: FK values exist in corresponding dimension/lookup tables | 1 | | |
| 5.3.3 | Business rule validation: domain-specific rules applied (e.g., start_date ≤ end_date) | 1 | | |
| 5.3.4 | Deduplication verification: no duplicate business keys after merge/upsert | 1 | | |
| 5.3.5 | Standardization: consistent formatting (dates, codes, reference mappings) | 1 | | |
| 5.3.6 | Cross-source reconciliation: records from multiple sources reconciled correctly | 1 | | |
| 5.3.7 | Orphan detection: child records without matching parent records identified and handled | 1 | | |
| 5.3.8 | Historical consistency: row counts change as expected (no unexplained shrinkage) | 1 | | |
| 5.3.9 | Merge result validation: post-merge counts reconcile with source I/U/D counts | 1 | | |
| 5.3.10 | Null propagation check: no nulls introduced by failed joins or type casts | 1 | | |

### 5.4 Gold Warehouse Validation (Consumption-Ready)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 5.4.1 | Fact-dimension referential integrity: all FKs in fact tables match dimension surrogate keys | 1 | | |
| 5.4.2 | Measure validation: calculated measures produce expected results (spot-check vs. source of truth) | 1 | | |
| 5.4.3 | Aggregate consistency: sum of detail records equals aggregate totals (no data loss in rollup) | 1 | | |
| 5.4.4 | Completeness: all expected dimension members present; unknown/orphan member usage monitored | 1 | | |
| 5.4.5 | SCD validation: Type 2 dimensions have correct valid_from/valid_to ranges (no gaps/overlaps) | 1 | | |
| 5.4.6 | Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation) | 1 | | |
| 5.4.7 | Freshness validation: Gold tables updated within defined SLA | 1 | | |
| 5.4.8 | Business acceptance: Gold data matches known KPI values (sanity checks) | 1 | | |
| 5.4.9 | No duplicate grain: fact tables contain unique records per defined grain | 1 | | |

### 5.5 Data-Type-Specific Validation Rules

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 5.5.1 | **Dates**: Valid date ranges; consistent timezone handling; no invalid future dates where prohibited | 1 | | |
| 5.5.2 | **Numeric / Financial**: Precision preserved; no rounding errors; currency codes valid | 1 | | |
| 5.5.3 | **String / Text**: Encoding validated (UTF-8); max length respected; no silent truncation | 1 | | |
| 5.5.4 | **Sensitive data**: Masked/tokenized where required; format validation applied | 1 | | |
| 5.5.5 | **Categorical / Enum**: Values within expected domain; no invalid codes flowing to Gold | 1 | | |
| 5.5.6 | **Identifiers / Keys**: Uniqueness verified; format consistent; no nulls in key columns | 1 | | |
| 5.5.7 | **Boolean / Flag**: Only expected values present (not mixed formats across tables) | 1 | | |
| 5.5.8 | **JSON (EAM)**: Structure validated; required elements present; type coercion verified | 1 | | |

---

## Area 6: Security & Access Control (Weight: 12%)

### 6.1 Identity & Access Management

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 6.1.1 | Workspace roles follow least-privilege principle (Admin/Member/Contributor/Viewer used correctly) across all 67 workspaces | 2 | | |
| 6.1.2 | No individual user accounts for role assignments — security groups used | 2 | | |
| 6.1.3 | Service Principal used for automation; the single SPN is scoped least-privilege (not over-granted across all workspaces) | 2 | | |
| 6.1.4 | Single-SPN risk assessed (blast radius, rotation, and dependency if the one SPN is compromised or expires) | 2 | | |
| 6.1.5 | Workspace Identity used for Fabric data connections where supported (preferred over user-delegated auth) | 1 | | |
| 6.1.6 | Guest/external user access is explicitly governed | 2 | | |
| 6.1.7 | Regular access reviews scheduled and documented (per workspace and per domain folder) | 3 | | |
| 6.1.8 | Fabric tenant admin settings reviewed and hardened (export restrictions, external sharing, guest defaults) | 2 | | |
| 6.1.9 | Domain-folder access aligns with domain ownership (Finance data not writable by Sales team, etc.) | 2 | | |

### 6.2 Data Security

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 6.2.1 | Row-Level Security (RLS) implemented on the Gold Warehouse and/or semantic models where required | 1 | | |
| 6.2.2 | Column-Level Security / Object-Level Security applied for sensitive fields | 1 | | |
| 6.2.3 | Dynamic Data Masking applied in the Warehouse for sensitive columns where appropriate | 1 | | |
| 6.2.4 | Sensitive fields identified, classified, and protected (masking/encryption) | 2 | | |
| 6.2.5 | Sensitivity labels applied across Fabric items | 2 | | |
| 6.2.6 | OneLake data access controlled via workspace roles / OneLake data access roles (not open access) | 2 | | |
| 6.2.7 | Cross-domain report data access respects domain-level security (no leakage via shared models) | 1 | | |
| 6.2.8 | Data exfiltration controls configured (copy/export/download restrictions on sensitive workspaces) | 2 | | |

### 6.3 Network Security

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 6.3.1 | Connections to source systems use encrypted channels | 1 | | |
| 6.3.2 | Private endpoints / Private Link configured for Fabric capacity and Azure SQL DB (if applicable) | 2 | | |
| 6.3.3 | Azure SQL DB (IFS connection) firewall / network rules restrict access appropriately | 2 | | |
| 6.3.4 | API / source connections use TLS 1.2+ | 1 | | |
| 6.3.5 | Conditional Access policies applied to the Fabric tenant | 2 | | |

### 6.4 Secrets & Credentials

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 6.4.1 | Azure Key Vault used for all secrets, keys, and connection strings (incl. the SPN secret/certificate) | 2 | | |
| 6.4.2 | No secrets in notebook code, pipeline expressions, or Spark config | 1 | | |
| 6.4.3 | SPN credential rotation policy defined and automated (certificate preferred over client secret) | 2 | | |
| 6.4.4 | Gateway / source credentials managed securely (not shared personal accounts) | 2 | | |
| 6.4.5 | Managed Identity / Workspace Identity preferred over SAS tokens or account keys | 1 | | |
| 6.4.6 | Key Vault access is itself least-privilege (only the SPN/Workspace Identity can read required secrets) | 2 | | |

---

## Area 7: Compliance & Regulatory (Weight: 7%)

> **Note**: MLC's regulatory regime is **TBD**. This area captures generic controls plus a plug-in slot for the confirmed regime(s). Detailed control mappings are in [03-compliance-matrix.md](03-compliance-matrix.md). Increase this area's weight in the rubric once the regime is confirmed.

### 7.1 Regulatory Regime (Plug-in — TBD)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 7.1.1 | Applicable regulatory/compliance regime(s) identified and documented for MLC | 3 | | |
| 7.1.2 | In-scope regulated data categories identified and inventoried | 3 | | |
| 7.1.3 | Regime-specific control set mapped into the compliance matrix | 3 | | |
| 7.1.4 | Data residency / regional processing requirements identified and met | 3 | | |
| 7.1.5 | Required agreements in place (e.g., DPA/BAA/other) covering all Fabric and Azure services used | 3 | | |
| 7.1.6 | Breach / incident notification process documented (customer-side, not solely relying on Microsoft) | 3 | | |

### 7.2 Financial Data Controls (SOX-style ITGC)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 7.2.1 | Segregation of Duties enforced (developer ≠ deployer ≠ approver) | 2 | | |
| 7.2.2 | All changes to pipelines/notebooks/Warehouse go through formal change management | 3 | | |
| 7.2.3 | Audit trail for all data modifications in financial-relevant data (Finance domain) | 1 | | |
| 7.2.4 | Access control changes logged and reviewable | 2 | | |
| 7.2.5 | Data transformation logic documented and reproducible | 3 | | |
| 7.2.6 | Source-to-target reconciliation exists for financial data (completeness and accuracy) | 1 | | |
| 7.2.7 | Retention of historical financial data per policy | 3 | | |

### 7.3 Data Privacy (Generic Placeholder)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 7.3.1 | Personal data inventory exists with legal basis (if personal data is processed) | 3 | | |
| 7.3.2 | Data minimization applied — only necessary personal data ingested | 3 | | |
| 7.3.3 | Right-to-erasure / rectification technically achievable across layers (if in scope) | 3 | | |
| 7.3.4 | Data retention policies defined per data category | 3 | | |
| 7.3.5 | Consent / purpose tracking integrated where applicable | 3 | | |
| 7.3.6 | Cross-border transfer justified and documented (if applicable) | 3 | | |

### 7.4 Audit Trail & Logging

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 7.4.1 | Fabric Activity Log / Unified Audit Log enabled and exported (all environments incl. Prod) | 2 | | |
| 7.4.2 | Admin audit log captures workspace changes, permission changes, item deletions | 2 | | |
| 7.4.3 | Data access audit trail exists (who accessed what data, when) | 1 | | |
| 7.4.4 | Audit logs retained per compliance requirement | 2 | | |
| 7.4.5 | Logs stored in a tamper-resistant location (Audit Lakehouse / Eventhouse + backup) | 2 | | |
| 7.4.6 | Warehouse-level auditing enabled for sensitive schemas (Finance) where supported | 1 | | |

---

## Area 8: Data Governance (Weight: 5%)

### 8.1 Data Lineage & Cataloging

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 8.1.1 | Fabric lineage view used and accurate for all key data flows | 1 | | |
| 8.1.2 | End-to-end lineage visible from source system to Gold Warehouse and Power BI | 1 | | |
| 8.1.3 | Microsoft Purview (or equivalent) integrated for enterprise cataloging | 2 | | |
| 8.1.4 | Data assets tagged with business domain and data owner | 2 | | |
| 8.1.5 | Cross-domain data dependencies documented in lineage | 1 | | |

### 8.2 Data Ownership & Stewardship

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 8.2.1 | Every dataset/table has a defined data owner | 3 | | |
| 8.2.2 | Data stewards assigned per domain (Finance, Sales, etc.) | 3 | | |
| 8.2.3 | Ownership documented — not just "the team that built it" | 3 | | |
| 8.2.4 | Escalation path for data quality issues defined | 3 | | |
| 8.2.5 | Domain-level accountability aligns with the folder-based segregation model | 3 | | |

### 8.3 Metadata Management

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 8.3.1 | Data dictionary / glossary exists for Gold Warehouse tables | 3 | | |
| 8.3.2 | Technical metadata (schema, lineage) automatically captured | 1 | | |
| 8.3.3 | Business metadata (definitions, rules) manually curated and kept current | 3 | | |
| 8.3.4 | Metadata accessible to data consumers (self-service discovery) | 3 | | |
| 8.3.5 | The solution's own Metadata DB is documented and discoverable (not a black box) | 3 | | |
| 8.3.6 | Common/shared terminology consistent across domains (single business glossary) | 3 | | |

### 8.4 Cross-Domain Governance

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 8.4.1 | Cross-domain data sharing rules defined (who can consume another domain's data) | 3 | | |
| 8.4.2 | Conformed dimensions governed centrally (single owner) to keep domains consistent | 3 | | |
| 8.4.3 | Cross-domain reports have a documented data-ownership and certification model | 3 | | |
| 8.4.4 | Domain boundaries and interfaces (contracts) are documented | 3 | | |
| 8.4.5 | Change to a shared/conformed asset follows a governed, communicated process | 3 | | |

---

## Area 9: Reliability & Resilience (Weight: 5%)

### 9.1 Error Recovery

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 9.1.1 | Failed pipelines can be restarted from point of failure (not full re-run) | 1 | | |
| 9.1.2 | Transient failure handling: retries with backoff for pipelines and notebooks | 1 | | |
| 9.1.3 | Poison message / corrupt file handling (quarantine, not crash) | 1 | | |
| 9.1.4 | Manual intervention procedures documented for non-recoverable failures | 3 | | |

### 9.2 Disaster Recovery & Backup

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 9.2.1 | RTO (Recovery Time Objective) and RPO (Recovery Point Objective) defined per data product | 3 | | |
| 9.2.2 | OneLake BCDR strategy aligned with Fabric's region failover capabilities | 2 | | |
| 9.2.3 | Gold Warehouse backup / recovery approach defined and tested | 2 | | |
| 9.2.4 | Critical Gold-layer data has a secondary copy or export mechanism | 1 | | |
| 9.2.5 | DR plan documented and tested (at least a tabletop exercise) | 3 | | |
| 9.2.6 | DR capacity / failover approach provisioned or evaluated for disaster scenarios | 2 | | |
| 9.2.7 | DR test cadence defined and executed at least annually | 3 | | |

### 9.3 Idempotency & Data Integrity

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 9.3.1 | All pipelines and notebooks are idempotent (safe to re-run) | 1 | | |
| 9.3.2 | Merge/upsert patterns prevent duplicates on re-execution | 1 | | |
| 9.3.3 | Transaction boundaries defined for multi-step operations (incl. Warehouse loads) | 1 | | |
| 9.3.4 | Data integrity validated across layers after failures | 1 | | |

### 9.4 SLA Management

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 9.4.1 | Data freshness SLAs defined per data product / Gold table | 3 | | |
| 9.4.2 | Pipeline completion SLAs set and monitored | 1 | | |
| 9.4.3 | SLA breach triggers alerts (Data Activator, email, Teams) | 1 | | |
| 9.4.4 | Historical SLA compliance tracked and reported | 1 | | |

---

## Area 10: Monitoring & Observability (Weight: 5%)

> **Note**: MLC uses **Audit Tables** (maintained in an Audit Lakehouse) for data-quality/operational logging and a **Metadata DB** for run tracking and lineage. If an **Eventhouse/KQL** database is also used, complete section 10.3; otherwise mark 10.3 items N/A with justification.

### 10.1 Pipeline & Job Monitoring

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 10.1.1 | Pipeline run history monitored beyond Fabric's default retention | 1 | | |
| 10.1.2 | Spark application logs captured for historical analysis | 1 | | |
| 10.1.3 | Dashboard shows pipeline status, duration trends, and failure rates | 1 | | |
| 10.1.4 | Alerting on pipeline failure (Data Activator or equivalent) | 1 | | |
| 10.1.5 | Warehouse load jobs monitored (duration, failures, row counts) | 1 | | |

### 10.2 Audit Tables & Metadata DB Log Strategy

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 10.2.1 | Audit Tables schema designed for queryability (structured, not free-text) | 1 | | |
| 10.2.2 | Audit log retention configured per compliance requirements | 3 | | |
| 10.2.3 | DQ logs, row counts, null checks, and exceptions captured consistently across domains | 1 | | |
| 10.2.4 | Metadata DB captures every notebook run, data source changes, and lineage | 1 | | |
| 10.2.5 | Audit Tables and Metadata DB are queryable by operations (not just developers) | 1 | | |

### 10.3 Eventhouse / KQL Log Strategy (if used)

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 10.3.1 | Eventhouse/KQL DB used for high-volume or real-time telemetry where appropriate | 1 | | |
| 10.3.2 | KQL queries exist for common operational investigations and are version-controlled | 1 | | |
| 10.3.3 | Eventhouse retention configured per compliance requirements | 1 | | |
| 10.3.4 | Ingestion volume monitored (no silent drop or over-ingestion) | 1 | | |

### 10.4 Monitoring Dashboard

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 10.4.1 | Dashboard covers all critical pipelines, notebooks, and Warehouse loads | 1 | | |
| 10.4.2 | Refresh frequency of monitoring data is adequate (near-real-time or hourly) | 1 | | |
| 10.4.3 | Dashboard accessible to the operations team (not just developers) | 2 | | |
| 10.4.4 | Historical trend analysis enabled (not just current-state) | 1 | | |

### 10.5 Alerting & Incident Response

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 10.5.1 | Data Activator (or equivalent) triggers configured for critical events | 1 | | |
| 10.5.2 | Alert fatigue managed — thresholds tuned, not everything alerts | 1 | | |
| 10.5.3 | Escalation matrix defined (Level 1 → Level 2 → management) | 3 | | |
| 10.5.4 | Incident response runbook exists for common failure scenarios | 3 | | |
| 10.5.5 | Post-incident review (RCA / blameless postmortem) for Sev1/Sev2 with findings tracked to closure | 3 | | |

---

## Area 11: DevOps & Deployment (Weight: 7%)

### 11.1 Version Control

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 11.1.1 | Git integration enabled for Fabric workspaces | 1 | | |
| 11.1.2 | All pipelines, notebooks, semantic models, and Warehouse artifacts source-controlled | 1 | | |
| 11.1.3 | `.gitignore` / exclusion rules prevent sensitive data in the repo | 1 | | |
| 11.1.4 | Branching strategy defined (feature branches, main, release) | 1 | | |
| 11.1.5 | Commit messages are descriptive and linked to work items | 1 | | |
| 11.1.6 | Pull request reviews required before merge to main branch | 1 | | |
| 11.1.7 | Minimum reviewer count enforced via branch policies | 1 | | |
| 11.1.8 | Secret-scanning / credential-detection enabled on the source repository | 1 | | |

### 11.2 CI/CD & Deployment Pipelines

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 11.2.1 | Fabric Deployment Pipelines configured (Dev → QA → Prod) for all three layer workspaces | 1 | | |
| 11.2.2 | Deployment rules configured for environment-specific parameters (connections, paths, capacity) | 1 | | |
| 11.2.3 | No manual deployments to production — all go through the pipeline | 2 | | |
| 11.2.4 | Deployment approvals required (aligns with segregation of duties) | 2 | | |
| 11.2.5 | Rollback procedure defined and tested | 3 | | |

### 11.3 Environment Management

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 11.3.1 | Separate workspaces for Dev, QA, and Production per layer (9 total) | 1 | | |
| 11.3.2 | Production workspaces have restricted access (no developer write) | 2 | | |
| 11.3.3 | QA environment representative of production (data, scale) | 3 | | |
| 11.3.4 | Environment parity maintained — no "works on dev" surprises | 1 | | |

### 11.4 Warehouse & Artifact Deployment

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 11.4.1 | Gold Warehouse schema changes are source-controlled (SQL project / DACPAC or equivalent) | 1 | | |
| 11.4.2 | Warehouse deployments are automated and environment-parameterized (not manual T-SQL in Prod) | 1 | | |
| 11.4.3 | Schema drift between environments is detectable and reconciled | 1 | | |
| 11.4.4 | Metadata DB configuration promotion across environments is controlled and repeatable | 3 | | |
| 11.4.5 | Semantic model deployment is versioned and part of the Data Consumption pipeline | 1 | | |

### 11.5 Testing Strategy

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 11.5.1 | Unit tests exist for critical transformation logic | 1 | | |
| 11.5.2 | Integration tests validate end-to-end pipeline execution | 1 | | |
| 11.5.3 | Data validation tests run post-deployment (record counts, schema checks) | 1 | | |
| 11.5.4 | Performance tests exist for critical Spark jobs and Warehouse queries | 3 | | |
| 11.5.5 | Regression testing on schema changes | 3 | | |

---

## Area 12: Cost Management & Capacity (Weight: 6%)

### 12.1 Capacity Planning

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 12.1.1 | Fabric SKU selected based on workload analysis (not guesswork) | 3 | | |
| 12.1.2 | Peak vs off-peak utilization profiled | 1 | | |
| 12.1.3 | Capacity autoscale/burst behavior understood and deliberately configured | 2 | | |
| 12.1.4 | Growth projections modeled for next 6–12 months (data volume, workspaces, domains) | 3 | | |

### 12.2 Capacity Utilization

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 12.2.1 | Fabric Capacity Metrics App deployed and monitored | 1 | | |
| 12.2.2 | Top CU-consuming workloads identified (pipelines, notebooks, Warehouse, semantic models) | 1 | | |
| 12.2.3 | CU smoothing behavior understood (background vs interactive) | 3 | | |
| 12.2.4 | Capacity bursting/throttling incidents tracked — frequent throttling indicates undersizing | 1 | | |
| 12.2.5 | Workloads distributed to avoid peak-hour contention across domains and layers | 1 | | |
| 12.2.6 | Warehouse and semantic-model query load included in capacity analysis | 1 | | |
| 12.2.7 | CU consumption alerts configured for proactive throttling prevention | 1 | | |

### 12.3 Cost Optimization

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 12.3.1 | Storage tiers / retention tuned for Bronze/Silver history | 2 | | |
| 12.3.2 | Lifecycle / purge policies for aged raw files and audit history | 3 | | |
| 12.3.3 | Spark pools not running idle (Environment settings tuned) | 1 | | |
| 12.3.4 | Unused or orphaned Fabric items cleaned up (esp. Dev/QA) | 1 | | |
| 12.3.5 | Reserved capacity vs. pay-as-you-go evaluated | 3 | | |
| 12.3.6 | Azure SQL DB (IFS connection) sizing and cost reviewed | 2 | | |

---

## Area 13: Documentation & Knowledge Management (Weight: 4%)

### 13.1 Solution Documentation

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 13.1.1 | Architecture overview document exists (or can be generated from workspaces) | 3 | | |
| 13.1.2 | Data flow documentation covers all source-to-target mappings | 3 | | |
| 13.1.3 | Business rules for transformations documented outside of code | 3 | | |
| 13.1.4 | Known issues and tech debt registered | 3 | | |

### 13.2 Operational Runbooks

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 13.2.1 | Runbook exists for daily/weekly/monthly operations | 3 | | |
| 13.2.2 | Failure recovery procedures documented step-by-step | 3 | | |
| 13.2.3 | On-call / escalation procedures documented | 3 | | |
| 13.2.4 | Capacity scaling procedures documented | 3 | | |

### 13.3 Data Dictionary

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 13.3.1 | Gold Warehouse table definitions documented with business context | 3 | | |
| 13.3.2 | Column-level descriptions available in the semantic model | 3 | | |
| 13.3.3 | Source-to-target mapping documented per table | 3 | | |
| 13.3.4 | Acronyms and business terminology glossary available | 3 | | |

### 13.4 Knowledge Transfer

| # | Checklist Item | Cat | Score | Notes / Evidence |
|---|---------------|-----|-------|------------------|
| 13.4.1 | Solution can be maintained by someone other than the original builder | 3 | | |
| 13.4.2 | No single point of human dependency (bus factor > 1) | 3 | | |
| 13.4.3 | Onboarding materials exist for new team members | 3 | | |
| 13.4.4 | Code is self-documenting or well-commented for complex logic | 3 | | |

---

## Checklist Statistics

| Area | Categories | Items | Cat 1 (Automated) | Cat 2 (Admin Review) | Cat 3 (Client Doc) |
|------|-----------|-------|--------------------|----------------------|--------------------|
| 1. Architecture & Design | 4 | 50 | 32 | 1 | 17 |
| 2. Data Integration & Ingestion | 7 | 50 | 39 | 1 | 10 |
| 3. Data Processing & Transformation | 7 | 75 | 73 | 0 | 2 |
| 4. Data Modeling & Storage | 6 | 44 | 41 | 0 | 3 |
| 5. Data Quality Framework | 5 | 46 | 41 | 0 | 5 |
| 6. Security & Access Control | 4 | 28 | 9 | 18 | 1 |
| 7. Compliance & Regulatory | 4 | 25 | 4 | 6 | 15 |
| 8. Data Governance | 4 | 21 | 4 | 2 | 15 |
| 9. Reliability & Resilience | 4 | 19 | 11 | 3 | 5 |
| 10. Monitoring & Observability | 5 | 23 | 18 | 1 | 4 |
| 11. DevOps & Deployment | 5 | 27 | 19 | 3 | 5 |
| 12. Cost Management & Capacity | 3 | 17 | 9 | 3 | 5 |
| 13. Documentation & Knowledge Mgmt | 4 | 16 | 0 | 0 | 16 |
| **Total** | **62** | **441** | **300** | **38** | **103** |
