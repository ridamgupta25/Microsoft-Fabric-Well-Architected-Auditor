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
