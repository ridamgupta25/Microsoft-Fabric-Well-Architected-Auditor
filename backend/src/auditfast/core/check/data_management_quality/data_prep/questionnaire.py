"""Data Management & Quality · Data Prep — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-DATA-QUALITY-RULES",
    ref="Q-DATA-1",
    title="Automated data-quality validations run during ingestion",
    pillar=Pillar.DATA,
    layers=(Layer.PREP, Layer.STORAGE),
    question=(
        "Are automated data-quality checks (nulls, ranges, uniqueness, referential "
        "integrity, row-count reconciliation) run as part of ingestion/transformation?"
    ),
    options=(
        Option("comprehensive", "Comprehensive checks that fail or quarantine bad data", 3),
        Option(
            "some",
            "Some checks, but coverage is partial or failures are not acted on",
            1,
            guidance="Expand coverage to the critical fields and make failures block or "
            "quarantine data rather than passing it downstream.",
        ),
        Option(
            "none",
            "No automated data-quality validation",
            0,
            guidance="Add data-quality validations to ingestion (e.g., constraints, "
            "expectation checks, reconciliation) with clear pass/fail handling.",
        ),
    ),
)
