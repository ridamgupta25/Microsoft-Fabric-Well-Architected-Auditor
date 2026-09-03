"""The vocabulary of the domain.

Every dimension the auditor reasons about is an enum here, defined exactly once.
Before this module existed, pillars were bare strings in ``models.py`` and layer
roles were re-declared in three separate places; keeping them here means adding a
pillar or a layer is a one-line change.

All enums subclass ``str`` so they serialize straight to JSON and compare equal to
their string value — ``Pillar.SECURITY_ACCESS == "Security & Access Control"`` is
``True``. That keeps the API responses and the YAML config human-readable without a
conversion layer.
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
    """The audit pillars aligned to the source checklist."""

    ARCHITECTURE = "Architecture & Design"
    DATA_INTEGRATION = "Data Integration & Ingestion"
    DATA_PROCESSING = "Data Processing & Transformation"
    DATA_MODELING = "Data Modeling & Storage"
    DATA_QUALITY = "Data Quality Framework"
    SECURITY_ACCESS = "Security & Access Control"
    COMPLIANCE = "Compliance & Regulatory"
    DATA_GOVERNANCE = "Data Governance"
    RELIABILITY = "Reliability & Resilience"
    MONITORING = "Monitoring & Observability"
    DEVOPS = "DevOps & Deployment"
    COST_MANAGEMENT = "Cost Management & Capacity"
    DOCUMENTATION = "Documentation & Knowledge Mgmt"
    FOUNDATION = "Foundation"

    @classmethod
    def scored(cls) -> list[Pillar]:
        """The pillars that appear on the scorecard, in report order."""
        return [
            cls.ARCHITECTURE,
            cls.DATA_INTEGRATION,
            cls.DATA_PROCESSING,
            cls.DATA_MODELING,
            cls.DATA_QUALITY,
            cls.SECURITY_ACCESS,
            cls.COMPLIANCE,
            cls.DATA_GOVERNANCE,
            cls.RELIABILITY,
            cls.MONITORING,
            cls.DEVOPS,
            cls.COST_MANAGEMENT,
            cls.DOCUMENTATION,
        ]

    @classmethod
    def for_checklist_ref(cls, ref: str, fallback: Pillar) -> Pillar:
        """Return the updated checklist pillar for ``ref`` when it is mapped."""
        if ref in _UNMAPPED_CHECKLIST_REFS:
            return fallback
        if ref in _CHECKLIST_REF_PILLARS:
            return _CHECKLIST_REF_PILLARS[ref]

        parts = ref.split(".")
        section = ".".join(parts[:2]) if parts and parts[0] == "14" else parts[0]
        return _CHECKLIST_SECTION_PILLARS.get(section, fallback)


_CHECKLIST_SECTION_PILLARS: dict[str, Pillar] = {
    "1": Pillar.ARCHITECTURE,
    "2": Pillar.DATA_INTEGRATION,
    "3": Pillar.DATA_PROCESSING,
    "4": Pillar.DATA_MODELING,
    "5": Pillar.DATA_QUALITY,
    "6": Pillar.SECURITY_ACCESS,
    "7": Pillar.COMPLIANCE,
    "8": Pillar.DATA_GOVERNANCE,
    "9": Pillar.RELIABILITY,
    "10": Pillar.MONITORING,
    "11": Pillar.DEVOPS,
    "12": Pillar.COST_MANAGEMENT,
    "14.1": Pillar.ARCHITECTURE,
    "14.2": Pillar.DATA_PROCESSING,
    "14.3": Pillar.ARCHITECTURE,
    "14.4": Pillar.SECURITY_ACCESS,
    "14.5": Pillar.DATA_INTEGRATION,
}

_CHECKLIST_REF_PILLARS: dict[str, Pillar] = {
    "IMPL-01": Pillar.SECURITY_ACCESS,
    "IMPL-02": Pillar.SECURITY_ACCESS,
    "IMPL-04": Pillar.SECURITY_ACCESS,
    "IMPL-06": Pillar.SECURITY_ACCESS,
    "IMPL-15": Pillar.COST_MANAGEMENT,
    "IMPL-20": Pillar.ARCHITECTURE,
    "IMPL-23": Pillar.DATA_INTEGRATION,
    "IMPL-24": Pillar.ARCHITECTURE,
    "14.5.3": Pillar.MONITORING,
    "14.5.4": Pillar.DEVOPS,
}

# All registered refs are now mapped to the updated checklist taxonomy. Add a
# ref here to hold it on its declared pillar when the checklist omits it.
_UNMAPPED_CHECKLIST_REFS: set[str] = set()


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
    #: A cross-workspace comparison spanning a whole project group. Not dispatched
    #: per object like the others; a group check runs once per group over its
    #: members' contexts. Kept last so the per-workspace dispatch order is
    #: unchanged.
    GROUP = "group"


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
    ENVIRONMENT_DEFINITIONS = "environmentDefinitions"
    TABLE_SCHEMAS = "tableSchemas"
    #: Column names and types, read over TDS from the SQL analytics endpoint - the
    #: Fabric REST API does not expose them. Separate from TABLE_SCHEMAS so a run
    #: whose selected checks never look at a column pays no SQL round trip.
    TABLE_COLUMNS = "tableColumns"
    #: Warehouse row-level-security policies (``sys.security_policies``), likewise
    #: only readable over the SQL analytics endpoint.
    WAREHOUSE_SECURITY = "warehouseSecurity"
    #: Per-Warehouse SQL audit *configuration* — state, action groups and
    #: retention — from
    #: ``…/warehouses/{id}/settings/sqlAudit``. Plain Fabric REST: it needs the
    #: Audit permission on the Warehouse item, **not** tenant-admin. Only the
    #: configuration is read; audit *rows* (``sys.fn_get_audit_file_v2``) are
    #: runtime data and are deliberately never fetched.
    WAREHOUSE_AUDIT = "warehouseAudit"
    SHORTCUTS = "shortcuts"
    SEMANTIC_MODEL_DEFINITIONS = "semanticModelDefinitions"
    #: Per-semantic-model *refresh schedule configuration* — enabled, days/times
    #: and ``notifyOption`` — from the Power BI Datasets API
    #: (``…/datasets/{id}/refreshSchedule``). An ordinary delegated read on the
    #: Power BI token audience, **not** tenant-admin; without a Power BI token it
    #: is unreadable and its checks report N/A. Only the configuration is read —
    #: no refresh rows and no refresh history.
    SEMANTIC_MODEL_REFRESH_SCHEDULE = "semanticModelRefreshSchedule"
    CONNECTIONS = "connections"
    #: Report → semantic-model bindings, from the Power BI *Get Reports In Group*
    #: API (``/groups/{id}/reports``). Each row carries the report's ``datasetId``,
    #: which is the only readable evidence of which model a report is built on.
    #: An ordinary delegated ``Report.Read.All`` — **not** tenant-admin; without a
    #: Power BI token it is unreadable and its checks report N/A. Report
    #: definitions and pages are deliberately never fetched.
    REPORTS = "reports"
    #: Per-item run/refresh recency, read from the job-scheduler history
    #: (``…/items/{id}/jobs/instances``) — the List Items API carries no
    #: timestamp, so this is a one-call-per-runnable-item enrichment.
    ITEM_RUN_HISTORY = "itemRunHistory"
    #: Aggregated OneLake Files-section listing per Lakehouse, read through the
    #: ADLS Gen2 List Path API using a Storage-audience token. The provider stores
    #: only bounded counts/buckets, never individual file paths.
    LAKEHOUSE_FILES = "lakehouseFiles"
    #: Per-Data-Activator (Reflex) rule configuration, parsed from the item's
    #: ``ReflexEntities.json`` definition via ``getDefinition``. Only bounded
    #: counts are kept (rules / active rules / sources / actions), never the rule
    #: bodies. Needs the ``Item.ReadWrite`` scope getDefinition requires; without
    #: it the definition is unreadable and the trigger-depth check reports N/A.
    ACTIVATOR_DEFINITIONS = "activatorDefinitions"


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
