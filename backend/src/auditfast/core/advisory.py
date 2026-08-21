"""The advisory checklist — the single source of truth for the advisory flag.

Some registered checks assert **data-level correctness** (row-level
reconciliation, referential integrity, precision) or read **warehouse/table
structure** that no Fabric or SQL-endpoint API reliably exposes. Their automated
verdict is therefore a weak, low-confidence proxy — a human reviewer or a
knowledge-base / AI evaluator would judge them more accurately.

These refs are kept **out of the deterministic scorecard and the main report**
and routed to a separate **Advisory report** (identical format) for review. The
deterministic score stays reproducible and free of these low-confidence signals.

Keyed by the checklist **ref id**, exactly like :mod:`auditfast.core.validation`:
add a ``"<ref>": "<why it is advisory>"`` line to move a check into the advisory
set; delete the line to send it back to the deterministic run. A test asserts
every ref below is a real registered check's ref, so a typo fails fast.
"""
from __future__ import annotations

#: Shown for a check whose ref is in the advisory checklist.
ADVISORY_LABEL = "Advisory review"

#: ``ref id -> why the deterministic verdict is low-confidence``. THIS is the
#: list to edit. The ref (the key) is all that drives the flag.
ADVISORY_CHECKLIST: dict[str, str] = {
    "5.3.2":  "Referential integrity (FK values exist in dimensions) needs row-level data the tool does not profile.",
    "5.4.1":  "Fact-dimension referential integrity needs actual key matching, not readable structurally.",
    "5.2.5":  "Record-count reconciliation vs source needs runtime counts.",
    "5.3.6":  "Cross-source reconciliation correctness is not verifiable from configuration.",
    "5.3.9":  "Merge result validation needs post-merge runtime counts.",
    "5.4.6":  "Cross-layer (Gold vs Silver) reconciliation needs data comparison.",
    "5.4.9":  "No-duplicate-grain requires querying the data; grain is not declared readably.",
    "7.2.6":  "Source-to-target financial reconciliation is a data-level completeness/accuracy check.",
    "4.5.12": "Every fact FK having a matching dimension record is row-level integrity.",
    "5.5.2":  "Numeric/financial precision and rounding are value-level, not readable.",
    "5.5.6":  "Key uniqueness and no-nulls-in-keys need data profiling.",
    "4.5.2":  "Fact-table grain is not exposed by any Fabric or SQL-endpoint API.",
    "4.5.8":  "SCD strategy is intent/documentation, not machine-readable.",
    "4.5.9":  "SCD Type-2 column semantics need the SQL endpoint, which is often unavailable.",
    "4.5.4":  "Dimension denormalisation vs snowflake is a modeling judgment.",
    "4.5.11": "Degenerate/junk dimension modeling requires design judgment on columns.",
    "4.4.9":  "Conformed-dimension sharing across domains is a cross-store design judgment.",
    "4.2.5":  "Whether tables carry audit columns needs the SQL-endpoint column list.",
    "14.1.4": "DAX good-practice quality is a heuristic judgment over expressions.",
    "3.1.4":  "Whether markdown cells explain business logic is a semantic-intent judgment.",
}

#: The set of advisory refs, derived from the checklist above.
ADVISORY_REFS: frozenset[str] = frozenset(ADVISORY_CHECKLIST)


def is_advisory(ref: str) -> bool:
    """True when a check's ``ref`` is in the advisory checklist."""
    return ref in ADVISORY_CHECKLIST
