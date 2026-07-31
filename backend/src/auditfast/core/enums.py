"""The vocabulary of the domain.

Every dimension the auditor reasons about is an enum here, defined exactly once.
Before this module existed, pillars were bare strings in ``models.py`` and layer
roles were re-declared in three separate places; keeping them here means adding a
pillar or a layer is a one-line change.

All enums subclass ``str`` so they serialize straight to JSON and compare equal to
their string value — ``Pillar.SECURITY == "Security"`` is ``True``. That keeps the
API responses and the YAML config human-readable without a conversion layer.
"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """A ``str``-backed enum that *renders* as its value.

    Plain ``class X(str, Enum)`` compares and joins like a string but formats as
    ``"Pillar.SECURITY"``, so a stray ``f"{pillar}"`` silently corrupts a report.
    Overriding ``__str__``/``__format__`` closes that trap: the member is
    interchangeable with its value everywhere, including f-strings.

    (``enum.StrEnum`` does this natively but only from Python 3.11; this project
    supports 3.10.)
    """

    __str__ = str.__str__

    def __format__(self, format_spec: str) -> str:
        return str.__format__(self, format_spec)


class Pillar(StrEnum):
    """The audit pillars — quality attributes aligned to team ownership.

    Six scored pillars, each with a clear owner (Security team, Governance/
    Compliance, DevOps/SRE, Performance engineers, FinOps, Data engineers).

    ``FOUNDATION`` is cross-cutting and internal: it holds informational results
    (inventory, access errors) that describe the estate rather than judging it,
    and is never scored — so it is deliberately excluded from :meth:`scored`.
    """

    SECURITY = "Security"
    GOVERNANCE = "Governance & Compliance"
    OPERATIONS = "Operations & Reliability"
    PERFORMANCE = "Performance & Capacity"
    COST = "Cost & Resource Optimization"
    DATA = "Data Management & Quality"
    FOUNDATION = "Foundation"

    @classmethod
    def scored(cls) -> list[Pillar]:
        """The pillars that appear on the scorecard, in report order."""
        return [cls.SECURITY, cls.GOVERNANCE, cls.OPERATIONS,
                cls.PERFORMANCE, cls.COST, cls.DATA]


class Layer(StrEnum):
    """The role a workspace plays in the project — its "inner pillar".

    A project's layers usually live in separate Fabric workspaces. ``ANY`` is a
    sentinel used only by check definitions to mean "applies to every layer"; a
    workspace is never tagged ``ANY``.
    """

    PREP = "Data Prep"
    STORAGE = "Data Storage"
    LOGS = "Data Logs"
    OPERATIONS = "Data Operations"
    REPORTING = "Reporting / Semantic"
    MIXED = "Mixed"
    ANY = "*"

    @classmethod
    def assignable(cls) -> list[Layer]:
        """Layers a user can actually tag a workspace with (excludes ``ANY``)."""
        return [m for m in cls if m is not cls.ANY]

    @classmethod
    def parse(cls, value: str | Layer | None) -> Layer:
        """Coerce a config/API string into a Layer, tolerating unknown values.

        Unrecognized or blank roles become ``MIXED`` rather than raising, so a
        typo in a project YAML degrades to "audit everything" instead of killing
        the run.
        """
        if isinstance(value, cls):
            return value
        if not value:
            return cls.MIXED
        text = str(value).strip()
        for member in cls:
            if member.value.lower() == text.lower():
                return member
        return cls.MIXED


class Automation(StrEnum):
    """How a check's verdict is (or could be) reached.

    Honesty about verifiability: not every industry-standard point can be read
    from Fabric. This separates what the tool checks today from what it *could*
    check with more integration, and what only a human can attest.

    - ``AUTOMATED``: verified now from data the provider fetches.
    - ``ROADMAP``: technically automatable, but needs a Fabric API the provider
      does not yet call (e.g. notebook definitions, Delta table metadata,
      capacity metrics). Listed as a manual attestation until then.
    - ``INTERACTIVE``: not machine-verifiable, but *self-assessed* — the reviewer
      picks one of a fixed set of options during the audit and that choice is
      scored, just like the Azure Well-Architected Review questionnaire. The
      engine never runs it (the human supplies the verdict); its answer is merged
      into the audit afterwards.
    - ``MANUAL``: never machine-verifiable and not offered as a scored question —
      a legal agreement, an organisational process, a documentation/judgement
      call, or row-level data profiling that is out of scope for a configuration
      auditor.
    """

    AUTOMATED = "automated"
    ROADMAP = "roadmap"
    INTERACTIVE = "interactive"
    MANUAL = "manual"


class Scope(StrEnum):
    """What kind of object a check inspects.

    The engine dispatches purely on this: it asks the workspace context for the
    objects of a given scope, then runs the checks registered for it. Adding a
    new artifact type means adding a member here plus a provider that yields it —
    no engine change.
    """

    WORKSPACE = "workspace"
    PIPELINE = "pipeline"
    NOTEBOOK = "notebook"
    LAKEHOUSE = "lakehouse"
    SEMANTIC_MODEL = "semantic_model"
    REPORT = "report"
    EVENTHOUSE = "eventhouse"


class Resource(StrEnum):
    """A unit of data a check needs the provider to fetch.

    Checks declare their needs via ``requires=``; the engine unions the
    requirements of the *selected* checks and hands that set to the provider, so
    a run that scores no pipeline checks never pays for the (expensive,
    one-call-per-pipeline) pipeline definitions.
    """

    WORKSPACE = "workspace"
    ITEMS = "items"
    ROLE_ASSIGNMENTS = "roleAssignments"
    GIT = "git"
    PIPELINE_DEFINITIONS = "pipelineDefinitions"
    NOTEBOOK_DEFINITIONS = "notebookDefinitions"
    TABLE_SCHEMAS = "tableSchemas"
    SHORTCUTS = "shortcuts"
    SEMANTIC_MODEL_DEFINITIONS = "semanticModelDefinitions"


class Status(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NA = "N/A"
    INFO = "INFO"


class Severity(StrEnum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Informational"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}

#: Fabric item types that make up each layer. Used by the layer-content and
#: layer-separation checks, and by the providers to bucket items by scope.
LAYER_ITEM_TYPES: dict[Layer, frozenset[str]] = {
    Layer.PREP: frozenset({"DataPipeline", "Notebook", "Dataflow"}),
    Layer.STORAGE: frozenset({"Lakehouse", "Warehouse", "SQLDatabase"}),
    Layer.LOGS: frozenset({"Eventhouse", "KQLDatabase", "KQLDashboard"}),
    Layer.OPERATIONS: frozenset({"DataPipeline", "Reflex"}),
    Layer.REPORTING: frozenset({"SemanticModel", "Report", "Dashboard", "PaginatedReport"}),
}

#: Fabric item type -> the Scope its checks are registered under. Drives the
#: engine's generic object dispatch.
ITEM_TYPE_SCOPE: dict[str, Scope] = {
    "DataPipeline": Scope.PIPELINE,
    "Notebook": Scope.NOTEBOOK,
    "Lakehouse": Scope.LAKEHOUSE,
    "Warehouse": Scope.LAKEHOUSE,
    "SemanticModel": Scope.SEMANTIC_MODEL,
    "Report": Scope.REPORT,
    "PaginatedReport": Scope.REPORT,
    "Eventhouse": Scope.EVENTHOUSE,
}
