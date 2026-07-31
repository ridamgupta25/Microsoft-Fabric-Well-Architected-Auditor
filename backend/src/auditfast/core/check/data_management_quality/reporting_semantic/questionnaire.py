"""Data Management & Quality · Reporting / Semantic — interactive (self-assessed) checks."""
from auditfast.core.check import Option, questionnaire_check
from auditfast.core.enums import Layer, Pillar

questionnaire_check(
    id="Q-DATA-CATALOG",
    ref="Q-DATA-2",
    title="Datasets documented in a business glossary / data catalog",
    pillar=Pillar.DATA,
    layers=(Layer.STORAGE, Layer.REPORTING),
    question=(
        "Are datasets and their key measures documented in a business glossary or "
        "data catalog with descriptions and lineage that consumers can discover?"
    ),
    options=(
        Option("cataloged", "Documented in a catalog with descriptions and lineage", 3),
        Option(
            "partial",
            "Some documentation, but incomplete or scattered",
            1,
            guidance="Complete descriptions for critical datasets/measures and publish "
            "them where consumers look (Purview / OneLake catalog).",
        ),
        Option(
            "none",
            "No catalog or business glossary",
            0,
            guidance="Document datasets and key measures in a discoverable catalog with "
            "owners, descriptions, and lineage.",
        ),
    ),
)
