"""The validated checklist — the single source of truth for the Phase 1 flag.

A check being *registered* means its evaluator exists and runs. A check being
*validated* means the corresponding checklist point has been reviewed against
real workspace data. This module records which checklist points have reached that
bar, so a report can flag a **Validated** check apart from one that is still
**Pending validation** for the next phase.

The flag is keyed by the checklist **ref id** — the check's ``ref`` (the "Ref"
column of any report or the Catalog page). Validating a point is therefore a
one-line edit: add the checklist item, keyed by its ref.

--------------------------------------------------------------------------------
HOW TO UPDATE (this is the only file you need to touch)
--------------------------------------------------------------------------------
Add a ``"<ref>": "<checklist item>"`` line to :data:`VALIDATED_CHECKLIST` to mark
the check(s) with that ref as **Validated**; delete the line to send them back to
**Pending validation**. The flag then updates everywhere at once:

* the check catalog (Catalog page, ``GET /api/v1/catalog/checks``),
* the audit report in the UI (Findings table column + filter),
* the downloaded **Excel** report (``Checks`` sheet + ``Scorecard`` summary),
* the **Markdown** report (Findings column + summary line).

Notes:

* The key is the **ref**, not the internal check id. Find it in the "Ref" column
  of any report or the Catalog page, or with ``auditfast checks``.
* Several checks can share one ref (e.g. a pipeline and a notebook variant of the
  same point). Adding that ref validates **all** of them — usually what you want.
* The text after the ``:`` is only a human-readable label; only the ref (the key)
  drives the flag.
* A test (``tests/test_validation.py``) asserts every ref below is a real
  registered check's ref, so a typo fails fast instead of silently flagging
  nothing.
"""
from __future__ import annotations

#: Shown for a check whose ref is in the validated checklist.
VALIDATED_LABEL = "Validated"
#: Shown for a registered check still awaiting validation in the next phase.
PENDING_LABEL = "Pending validation"

#: The validated checklist: ``ref id -> checklist item``. THIS is the list to
#: edit — add a line to validate a point, delete one to un-validate it. Order is
#: the working-checklist order and has no effect; the ref (the key) is all that
#: drives the flag. Keys are unique — one line per ref.
VALIDATED_CHECKLIST: dict[str, str] = {
    "6.4.2":  "No secrets in notebook code, pipeline expressions, or Spark config",
    "3.1.3":  "No hardcoded paths, connection strings, secrets, or environment-specific values",
    "2.4.1":  "All pipeline activities have appropriate retry policies configured (copy, notebook, lookup, web, ForEach)",
    "11.2.1": "Fabric Deployment Pipelines configured (Dev -> QA -> Prod) for all three layer workspaces",
    "11.1.1": "Git integration enabled for Fabric workspaces",
    "2.4.3":  "On-failure paths defined for critical activities",
    "2.4.5":  "Pipeline failure triggers notification (Data Activator, email, Teams)",
    "2.4.2":  "Retry count and interval follow reasonable patterns (not infinite retries)",
    "12.3.4": "Unused or orphaned Fabric items cleaned up (esp. Dev/QA)",
    "3.3.1":  "Single MERGE INTO handles I/U/D atomically - not separate sequential DELETE/INSERT/UPDATE",
    "3.1.4":  "Cell-level documentation (markdown cells) explains business logic, not just code",
    "3.2.4":  "Broadcast joins used for small-large table joins where appropriate",
    "3.2.1":  "Consistent language approach (PySpark vs Spark SQL - one primary, not mixed ad-hoc)",
    "3.2.2":  "DataFrame API used over RDD API",
    "3.2.7":  "Explicit imports only (no import *)",
    "4.1.1":  "Lakehouse Tables (managed) used for structured data; Files section for raw/unstructured",
    "3.1.5":  "Functions are modular and reusable - not monolithic single-cell scripts",
    "4.2.1":  "Tables use meaningful, consistent naming conventions (agreed standard)",
    "2.1.6":  "Pipeline annotations/descriptions populated for pipelines and key activities",
    "2.1.1":  "Pipelines follow consistent naming conventions (including domain prefix/folder alignment)",
    "3.2.3":  "No unnecessary collect(), toPandas(), or count() on large datasets",
    "3.3.3":  "VACUUM scheduled to clean up old Delta files",
    "3.1.6":  "Notebooks avoid display() / show() in production execution paths",
    "3.2.5":  "UDFs avoided where native Spark functions exist",
    "2.6.2":  "Copy activities use appropriate parallelism (DIU, degree of copy parallelism)",
    "3.3.4":  "Z-ORDER / liquid clustering applied on high-cardinality filter columns",
    "IMPL-04": "Sensitivity labels applied across Fabric items",
    "IMPL-15": "Workspace is assigned to a Fabric capacity [WS-CAPACITY]",
    "IMPL-23": "Pipeline activities set an explicit timeout (not Fabric's multi-day default) [PL-TIMEOUT]",
    "IMPL-24": "Workspace name follows the organization naming convention [WS-NAME]",
    "2.4.6":  "Idempotency ensured - re-running a failed pipeline does not produce duplicates",
    "5.1.10": "DQ quarantine pattern: failed records routed to error tables with failure reason",
    "9.1.3":  "Poison message / corrupt file handling (quarantine, not crash)",
    "6.2.2":  "Column-Level Security / Object-Level Security applied for sensitive fields",
    "6.2.3":  "Dynamic Data Masking applied in the Warehouse for sensitive columns where appropriate",
    "6.3.4":  "API / source connections use TLS 1.2+",
    "14.4.1": "RLS defined on semantic models and tested per role",
    "14.4.3": "Object-Level Security applied where fields must be hidden from some audiences",
    "2.3.8":  "Out-of-order / late-arriving change records handled without data corruption",
    "2.4.4":  "Failed records captured to dead-letter / quarantine area (not silently dropped)",
    "3.6.4":  "Warehouse load procedures are idempotent and re-runnable",
    "4.3.4":  "Orphaned files cleaned up periodically (archiving/purging policy)",
    "4.5.10": "Late-arriving dimensions and facts handled (unknown/inferred member pattern)",
    "4.5.12": "Referential integrity validated (every FK in fact tables has a matching dimension record)",
    "5.2.5":  "Record count reconciliation vs. source system control counts",
    "5.3.6":  "Cross-source reconciliation: records from multiple sources reconciled correctly",
    "5.3.7":  "Orphan detection: child records without matching parent records identified and handled",
    "5.3.9":  "Merge result validation: post-merge counts reconcile with source I/U/D counts",
    "5.4.1":  "Fact-dimension referential integrity: all FKs in fact tables match dimension surrogate keys",
    "5.4.4":  "Completeness: all expected dimension members present; unknown/orphan member usage monitored",
    "5.4.6":  "Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    "5.4.9":  "No duplicate grain: fact tables contain unique records per defined grain",
    "7.2.6":  "Source-to-target reconciliation exists for financial data (completeness and accuracy)",
    "9.3.1":  "All pipelines and notebooks are idempotent (safe to re-run)",
    "3.5.5":  "No full-table scans when partition pruning is possible",
    "3.5.6":  "Long-running notebooks profiled and optimized",
    "4.1.2":  "OneLake used as the single data lake - no ungoverned shadow storage",
    "4.6.4":  "Audit Tables capture data quality logs, row counts, null checks, and exceptions",
    "5.1.2":  "DQ rules codified in code/config (not ad-hoc manual checks)",
    "1.2.3":  "Bronze Lakehouse captures raw data with audit metadata (ingestion timestamp, source system, batch ID)",
    "1.2.5":  "Silver Lakehouse applies cleansing, deduplication, conforming, and type standardization",
    "2.6.3":  "Large data movements use bulk/batch patterns, not row-by-row",
    "2.6.6":  "JSON ingestion (EAM) is efficient (streaming/partitioned parse, no oversized single-file bottlenecks)",
    "5.2.8":  "Source metadata captured: ingestion timestamp, source system, file name, batch ID",
    "5.3.4":  "Deduplication verification: no duplicate business keys after merge/upsert",
    "5.5.3":  "String / Text: Encoding validated (UTF-8); max length respected; no silent truncation",
    "5.5.7":  "Boolean / Flag: Only expected values present (not mixed formats across tables)",
    "5.2.1":  "Schema validation: incoming records match expected schema (column count, names, data types) - incl. EAM JSON",
    "5.2.4":  "Format validation: expected encoding (UTF-8), delimiters, and JSON structure for EAM",
    "5.2.6":  "Duplicate detection across batches",
    "5.3.1":  "Data type conformance: all columns cast to standard types (dates as DATE, correct numeric precision)",
    "5.3.5":  "Standardization: consistent formatting (dates, codes, reference mappings)",
    "5.5.6":  "Identifiers / Keys: uniqueness verified; format consistent; no nulls in key columns",
    "14.1.1": "Star schema followed in the semantic model (single-direction relationships, no unnecessary bidirectional filters)",
    "14.1.3": "Measures centralized (no duplicated calculation logic across reports)",
    "14.1.4": "DAX follows good practices (variables, no repeated sub-expressions, avoids expensive iterators)",
    "5.3.2":  "Range / domain validation: values fall within expected ranges and allowed value sets",
    "1.1.8":  "Single source of truth per data domain (no duplicate stores serving the same purpose)",
    "2.6.5":  "SQL-source Copy activities are tuned (source read folded, partitioned, sink batch size set)",
    "9.3.3":  "Transaction boundaries defined so a part-way failure leaves no inconsistent set of targets",
    "10.3.1": "Eventhouse / KQL DB used for high-volume or real-time telemetry where appropriate",
    "10.3.2": "KQL queries exist for common operational investigations and are version-controlled",
    "10.5.1": "Data Activator (or equivalent) triggers configured for critical events",
    "11.4.2": "Warehouse schema changes deployed through source control or a deployment pipeline",
    "11.4.5": "Semantic model deployment is versioned and orchestrated, not manual",
    "14.5.4": "Reporting content (semantic models and reports) is deployed under version control",
    "4.2.4":  "Columns use appropriate data types (dates as DATE, no over-wide text columns)",
    "4.2.5":  "Tables carry audit columns (load timestamp, source system, batch id)",
    "3.4.3":  "Spark pool size appropriate for workload (not over- or under-provisioned)",
    "9.1.1":  "Failed pipelines can be restarted from point of failure (not full re-run)",
    "10.1.4": "Alerting on pipeline failure (Data Activator or equivalent)",
    "11.5.1": "Unit tests exist for critical transformation logic",
    "2.2.1":  "Incremental load implemented where applicable (watermark, CDC, delta detection) for IFS/EAM/LIMS",
    "3.3.7":  "Delta table history / log retention configured and monitored",
    "3.1.7":  "All notebooks have meaningful, consistent names aligned to domain/layer",
    "2.1.3":  "Master/orchestrator pipeline pattern used for coordinating dependent domain pipelines",
    "3.3.2":  "OPTIMIZE (bin-compaction) scheduled appropriately (not after every micro-batch)",
    "3.5.3":  "Caching (persist/cache) used judiciously, not indiscriminately",
    "3.1.8":  "Notebook execution timeout / max runtime configured to prevent runaway Spark sessions",
    "3.2.6":  "Schema explicitly defined at read time for external sources (not inferred on CSV/JSON)",
    "3.4.2":  "Custom library versions pinned (not latest/floating)",
    "3.4.1":  "Fabric Environments used to manage Spark dependencies",
    "3.3.6":  "Table properties set appropriately (optimizeWrite, autoCompaction)",
    "3.4.4":  "Spark configuration tuned from defaults where justified (shuffle partitions, memory)",
    "3.5.8":  "Unnecessary columns eliminated in reads (explicit select, not SELECT *)",
    "3.3.5":  "V-Order enabled where Fabric recommends for read-optimized workloads",
    "3.5.4":  "Write operations use appropriate partition strategy (coalesce vs repartition; right-sized files)",
    "IMPL-20": "Workspace item inventory captured (informational - enumerates all items; never fails) [WS-INVENTORY]",
    "2.2.5":  "Initial load vs. incremental load clearly separated or parameterized",
    "2.1.2":  "Pipelines are parameterized (no hardcoded sources, targets, dates, or environment values)",
    "2.2.3":  "Adage historical load clearly separated from ongoing incremental patterns",
    "2.3.3":  "All applicable operation types (I/U/D) handled correctly in the merge strategy",
    "2.3.4":  "Insert records validated for uniqueness / business key before merge into target",
    "2.5.1":  "Metadata DB drives ingestion (source list, load type, schedule, target mapping) rather than hardcoded pipelines",
    "9.1.2":  "Transient failure handling: retries with backoff for pipelines and notebooks",
    "9.3.4":  "Data integrity validated across layers after failures",
    "5.1.7":  "DQ tool/library standardized across the solution",
    "5.2.2":  "Completeness: all expected source files/batches received (no missing partitions or source tables)",
    "5.2.3":  "Timeliness: data arrives within expected SLA window",
    "5.2.7":  "Null/empty handling: known nullable fields documented; unexpected nulls flagged",
    "5.3.3":  "Business rule validation: domain-specific rules applied (e.g., start_date <= end_date)",
    "5.3.10": "Null propagation check: no nulls introduced by failed joins or type casts",
    "5.5.1":  "Dates: Valid date ranges; consistent timezone handling; no invalid future dates where prohibited",
    "5.5.2":  "Numeric / Financial: Precision preserved; no rounding errors; currency codes valid",
    "5.5.4":  "Sensitive data: Masked/tokenized where required; format validation applied",
    "5.5.8":  "JSON (EAM): Structure validated; required elements present; type coercion verified",
    "11.1.2": "All pipelines, notebooks, semantic models, and Warehouse artifacts source-controlled",
    "11.1.4": "Branching strategy defined (feature branches, main, release)",
    "4.4.1": "Organise the Warehouse into schemas by domain (sales, finance, hr...) rather than putting every table in dbo, and give landing/staging tables their own schema (stg / staging / landing) separate from the presentation schemas consumers query. Grant on the schema rather than table by table so the boundary is also a security boundary.",
    "4.4.9": "Build each conformed dimension once and share it across domains (a shortcut, a view, or a single Warehouse schema) instead of materialising a per-domain copy — duplicate copies drift apart and break cross-domain analysis.",
    "4.5.2": "Give every fact table an explicitly defined grain and make it readable from the schema — the foreign keys and the date key that together say what one row is — then write that grain down in the model documentation, since no Fabric or SQL endpoint API exposes a table description for a reviewer (or this auditor) to read back.",
    "4.5.3": "Move descriptive text attributes off the fact table onto the dimension they describe, leaving the fact with foreign keys, measures and audit columns only.",
    "4.5.4": "Denormalise the dimension: fold the outrigger's attributes into the dimension itself rather than keying out to another dimension, unless the snowflake is a deliberate, documented exception (a very large or independently versioned outrigger).",
    "4.5.8": "Decide and record the SCD strategy for each dimension (Type 1, Type 2 or hybrid) and make it visible in the schema — Type 2 dimensions carry validity dates and a current flag; a deliberate Type 1 should be documented so overwrite-in-place is a choice rather than a default.",
    "4.5.11": "Review each fact column flagged here: a key with no dimension behind it is a degenerate dimension — keep it on the fact deliberately if the business key genuinely has no attributes, otherwise model the missing dimension; and where a fact carries several low-cardinality flag/status columns, collapse them into a junk dimension with a single key on the fact.",
    "4.6.2": "Consolidate ingestion/orchestration configuration — source lists, watermarks, control and schedule tables — into one metadata store, and point every pipeline at it, instead of keeping config tables in several stores.",
    "4.6.3": "Restrict write access to the workspace holding the Metadata DB: grant Admin/Member/Contributor only to the framework's service principal or managed identity (and a small owning group), move named individual users to Viewer, and back that with SQL-level grants on the metadata tables themselves — the workspace role is the only layer this auditor can read.",
    "4.6.5": "Append to the audit tables and never rewrite them: replace UPDATE / DELETE / TRUNCATE / MERGE and overwrite-mode writes against audit, log and DQ tables with inserts (append mode), and correct a wrong row by writing a new corrective row rather than editing history.",
    "4.6.8": "Design the audit tables for operational queries: a run/batch identifier, a run timestamp, and typed columns for counts and status, rather than a free-text or JSON payload column that has to be parsed before it can be filtered.",
    "4.4.2": "Agree one naming convention for the Warehouse -- snake_case or PascalCase, either is fine -- and apply it to every table and column, including the ones inherited from an imported spreadsheet or an external source. Rename the outliers (or expose them through views that use the house style) so consumers do not have to guess the spelling of a column.",
}

#: The set of validated refs, derived from the checklist above — what the flag
#: actually tests against.
VALIDATED_REFS: frozenset[str] = frozenset(VALIDATED_CHECKLIST)


def is_validated(ref: str) -> bool:
    """True when a check's ``ref`` is in the validated checklist."""
    return ref in VALIDATED_CHECKLIST


def validation_label(ref: str) -> str:
    """Human-readable validation state for a check's ``ref``.

    :data:`VALIDATED_LABEL` when the ref is in the validated checklist, otherwise
    :data:`PENDING_LABEL`.
    """
    return VALIDATED_LABEL if ref in VALIDATED_CHECKLIST else PENDING_LABEL
