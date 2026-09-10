#!/usr/bin/env python3
r"""Standalone, client-run Microsoft Fabric Tenant KB crawl CLI.

This file is the entire handoff: no AuditFAST installation or repository needed.
Use Python 3.10 or newer. --help needs only the standard library.
Collection dependencies (install only in an approved environment):
    python -m pip install requests PyYAML msal
No SQL driver is required by the elevated crawl.

Originally bundled from production collection and serialization sources. Readable factory
functions isolate each module's names without exec, archives, or import hooks.
Literal interiors deliberately retain their original whitespace. Tenant-admin
methods remain source-compatible; this CLI selects only its elevated category.
This file is maintained directly; no builder or separate template is required.
After changes, run --self-test and verify a real export and offline audit.
"""
from __future__ import annotations

import importlib as _collector_importlib
from threading import RLock as _collector_RLock
from types import SimpleNamespace as _collector_SimpleNamespace


def _collector_import_dependency(name):
    package = {"yaml": "PyYAML", "requests": "requests"}[name]
    try:
        return _collector_importlib.import_module(name)
    except ImportError as exc:
        raise ImportError(
            f"Cannot import the collector dependency {package}. "
            f"In an approved Python environment run: python -m pip install {package}"
        ) from exc


class _collector_LazyModule:
    def __init__(self, name):
        self._name = name
        self._module = None

    def __getattr__(self, name):
        if self._module is None:
            self._module = _collector_import_dependency(self._name)
        return getattr(self._module, name)


def _collector_namespace(values):
    return _collector_SimpleNamespace(**{
        name: value for name, value in values.items()
        if not name.startswith("_collector_")
    })


_collector_runtime = None
_collector_metadata_runtime = None
_collector_modules = {}
_collector_lock = _collector_RLock()

COLLECTOR_VERSION = '1.1.0-tenant-admin'
COLLECTOR_BUILD_SHA256 = '4ef1e6633b9ee9cb00daae875ee7bc4929c6af62d8ed855006e0c69867f4301b'
# Historical source hashes from the original bundle, including retired build inputs.
# These record provenance, not runtime dependencies or a hash of later manual edits.
COLLECTOR_SOURCE_SHA256 = {
  "backend/src/auditfast/core/enums.py": "844bb62e37aeed91c3f7627ccf82c636f4e7dba32b2dc5cdc8a1216f07c5e13d",
  "backend/src/auditfast/core/models.py": "725d9fa68d4ef4861da535d1393545ab3c764f29ce6c382aefd1e5fac7a4f8ef",
  "backend/src/auditfast/core/errors.py": "622899f632b21e815d5aa72145c1442bdcecc7fc0d2adc2e9ba509a186e93840",
  "backend/src/auditfast/clients/base.py": "40ef94ab32da36285c84d99d19f2f40f44f62d3c3ef90869ce4f80e4f68dc0fd",
  "backend/src/auditfast/clients/errors.py": "1d56a94ca3312b551b5af1457350416f440c128d8e8eda34dde0781adfd25902",
  "backend/src/auditfast/clients/tmsl.py": "bb400c2a680d8262ca5da5a1c8bdf1c661485b743c4f4bc07ddb9dc09df69b29",
  "backend/src/auditfast/clients/onelake.py": "a15847bdb12a7f78807ad513e5853831a2a99398d0d0bf9c96b5c227ce0653c8",
  "backend/src/auditfast/clients/sqlendpoint.py": "29a9100b5142e8a51dedb8c29362d95dc622a41c015d103770a35d67ae3454b0",
  "backend/src/auditfast/discovery/scanner.py": "1841535c719f5d4bae66e84d9567548eddea898fda7554bf3a37fc80735686b9",
  "backend/src/auditfast/clients/powerbi.py": "36ab0af294fd061d7f73b936c41ebfa5a698c293ac8e6434e4c3ac6a032b3d62",
  "backend/src/auditfast/clients/live.py": "eb8d9bb669d0e4a0b919a949699b21f56503b65f9a2835814218a3ebfa37d111",
  "extras/kb-crawler/tools/cli_template.py": "f3a717a160d16bd543923ca79fc2236795f96587b459f5123b04bc00c3af12e1",
  "extras/kb-crawler/tools/build_kb_crawl_cli.ps1": "87f642573a8f0d11d28fdf6da8d06f8f39cc1842e57412d78f66c7c568796133"
}
COLLECTOR_SOURCE_HASHES = COLLECTOR_SOURCE_SHA256
# --- Production source: backend\src\auditfast\core\enums.py ---
def _collector_build_core_enums(_collector_modules):
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
        #: On-premises / VNet data gateways and their members, from ``/gateways`` and
        #: ``/gateways/{id}/members``. An **elevated** read: ``Gateway.Read.All`` plus
        #: a role on each gateway, so the list returns only the gateways the caller
        #: administers. Not tenant-admin. Without it the gateway checks report N/A.
        GATEWAYS = "gateways"
        #: Per-Lakehouse OneLake data access roles, from
        #: ``…/items/{id}/dataAccessRoles``. Needs ``OneLake.Read.All`` plus a
        #: workspace role. Only role names, member counts and the granted permissions
        #: are kept — never the data itself.
        DATA_ACCESS_ROLES = "dataAccessRoles"
        #: Tenant-wide settings returned by the Power BI admin API. Available only
        #: to a Fabric tenant administrator.
        TENANT_SETTINGS = "tenantSettings"
        #: Fabric domain membership plus workspace assignments, used to verify
        #: business-area ownership boundaries.
        TENANT_DOMAINS = "tenantDomains"
        #: Tenant-admin metadata scan results, including endorsement details that
        #: ordinary workspace item APIs omit.
        ADMIN_SCANNER = "adminScanner"
        #: Tenant activity events used by access, audit coverage and deployment
        #: checks. The provider stores a bounded recent window only.
        ADMIN_ACTIVITY = "adminActivity"
        #: Normalized observations queried from the Fabric Capacity Metrics model.
        CAPACITY_METRICS = "capacityMetrics"


    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\core\models.py ---
def _collector_build_core_models(_collector_modules):
    from dataclasses import asdict, dataclass, field

    Layer = _collector_modules['core.enums'].Layer
    Resource = _collector_modules['core.enums'].Resource

    @dataclass(frozen=True, slots=True)
    class Item:
        """One Fabric item inside a workspace."""

        id: str = ""
        type: str = ""
        display_name: str = ""
        sensitivity_label: str | None = None
        last_run_utc: str | None = None
        #: ISO-8601 creation timestamp. For semantic models this comes from the Power
        #: BI datasets API (``createdDate``) — available without admin or capacity, so
        #: it is the reliable "when created" signal even when there is no refresh
        #: history. Other item types leave it ``None`` (the List Items API omits it).
        created_date: str | None = None

        @classmethod
        def from_api(cls, raw: dict) -> Item:
            # Two spellings are real: the Fabric Core items API documents
            # ``sensitivityLabel.id``, while the Power BI admin/scanner API returns
            # ``sensitivityLabel.labelId``. Accepting both means a label read through
            # either route lands in the same field.
            label = raw.get("sensitivityLabel")
            if isinstance(label, dict):
                label = label.get("id") or label.get("labelId")
            return cls(
                id=raw.get("id") or "",
                type=raw.get("type") or "",
                display_name=raw.get("displayName") or "",
                sensitivity_label=label,
                last_run_utc=raw.get("lastRunUtc") or raw.get("lastUpdatedDate"),
                created_date=raw.get("createdDate"),
            )



    @dataclass(frozen=True, slots=True)
    class RoleAssignment:
        """One principal granted a role on a workspace."""

        principal_type: str = ""
        display_name: str = ""
        role: str = ""
        principal_id: str = ""

        @classmethod
        def from_api(cls, raw: dict) -> RoleAssignment:
            principal = raw.get("principal")
            if isinstance(principal, dict):  # live Fabric shape
                return cls(
                    principal_type=principal.get("type") or "",
                    display_name=principal.get("displayName") or "",
                    role=raw.get("role") or "",
                    principal_id=principal.get("id") or "",
                )
            return cls(  # flat fixture shape
                principal_type=raw.get("principalType") or "",
                display_name=raw.get("displayName") or "",
                role=raw.get("role") or "",
                principal_id=raw.get("principalId") or "",
            )

        @property
        def is_guest(self) -> bool:
            return self.principal_type == "Guest" or "#EXT#" in self.display_name

        @property
        def is_individual(self) -> bool:
            return self.principal_type == "User"



    @dataclass(slots=True)
    class WorkspaceContext:
        """A normalized snapshot of one workspace — the contract every provider meets.

    Fields a provider did not fetch stay at their defaults, so a check that
    forgets to declare a ``requires`` sees empty data rather than stale data.
    """

        id: str
        display_name: str = ""
        layer: Layer = Layer.MIXED
        capacity_id: str | None = None
        git_connected: bool = False
        deployment_pipeline: bool = False
        role_assignments: list[RoleAssignment] = field(default_factory=list)
        items: list[Item] = field(default_factory=list)
        pipelines: dict[str, dict] = field(default_factory=dict)
        notebooks: dict[str, dict] = field(default_factory=dict)
        environments: dict[str, dict] = field(default_factory=dict)
        #: The workspace's default Spark runtime, from
        #: ``/workspaces/{id}/spark/settings``: ``{"runtime_version": "1.3",
        #: "default_environment": str}``. A notebook that binds to no named
        #: Environment inherits this, so without it the runtime check reported N/A on
        #: the commonest configuration of all. Empty when the settings could not be
        #: read - which the check must treat as unknown, never as out of date.
        spark_settings: dict = field(default_factory=dict)
        tables: dict[str, dict] = field(default_factory=dict)
        shortcuts: dict[str, list] = field(default_factory=dict)
        semantic_models: dict[str, dict] = field(default_factory=dict)
        #: Per-semantic-model refresh *schedule configuration*, keyed by model display
        #: name: ``{"enabled": bool, "notify_option": str, "notifies_on_failure": bool,
        #: "days": [str], "times": [str], "local_time_zone_id": str}``. A model absent
        #: from the map has no configured schedule (Direct Lake, push, or refresh
        #: driven entirely by a pipeline) — which is a real, readable answer, not a
        #: read failure. Read failures mark the resource unavailable instead.
        refresh_schedules: dict[str, dict] = field(default_factory=dict)
        #: Warehouse row-level-security policies, keyed by warehouse name. Read over the
        #: SQL analytics endpoint (``sys.security_policies``) because the Fabric REST
        #: API does not expose them. An empty list for a warehouse is a real finding
        #: ("defines no policy"); the warehouse being absent from the map means it could
        #: not be read, which is N/A.
        warehouse_security: dict[str, list] = field(default_factory=dict)
        #: Per-Warehouse database options that govern automatic statistics, keyed by
        #: store name: ``auto_create_stats`` / ``auto_update_stats`` /
        #: ``auto_update_stats_async``. Read from ``sys.databases``.
        #:
        #: These are the *auditable* statistics setting. Fabric maintains statistics
        #: itself, so no manual UPDATE STATISTICS is required - but a user can switch
        #: the automatic behaviour off with ALTER DATABASE, and Microsoft says OFF
        #: "can cause suboptimal query plans and degraded query performance".
        #: ``None`` means the value could not be read, which is never the same as
        #: "off".
        warehouse_options: dict[str, dict] = field(default_factory=dict)
        #: Per-Warehouse SQL audit *configuration*, keyed by warehouse display name.
        #: Each value is the normalised ``settings/sqlAudit`` payload:
        #: ``{"state": str, "enabled": bool, "action_groups": [str], "retention_days": int|None}``.
        #: A warehouse missing from the map could not be read (N/A); a warehouse
        #: present with ``enabled=False`` is a real finding. Audit *rows* are never
        #: fetched — only the configuration.
        warehouse_audit: dict[str, dict] = field(default_factory=dict)
        #: Per-item job-run timestamps (ISO-8601 UTC, newest first), keyed by item id.
        #: Read from the same ``…/jobs/instances`` page that yields
        #: :attr:`Item.last_run_utc` — no extra call — but retained in full so an
        #: *observed cadence* (the interval between consecutive runs) can be derived.
        #: Semantic models are absent: their refresh history is read one row at a time
        #: from the Power BI API, so no interval is derivable for them.
        run_history: dict[str, list[str]] = field(default_factory=dict)
        #: Tenant-level Fabric connection metadata. Credentials and secrets are
        #: never stored; TLS version and health remain unknown unless a provider
        #: supplies explicit evidence for them.
        connections: list[dict] = field(default_factory=list)
        #: Report → semantic-model bindings, one dict per report in the workspace:
        #: ``{"id": str, "name": str, "dataset_id": str, "dataset_workspace_id": str}``.
        #: Only what the reuse checks need — no report definition, no pages, no
        #: visuals. ``dataset_id`` is empty for a paginated (RDL) report that binds to
        #: no semantic model, which is a real answer, not a read failure; a failed
        #: read marks the resource unavailable instead.
        reports: list[dict] = field(default_factory=list)
        #: Per-Lakehouse OneLake Files-section summary, keyed by Lakehouse display
        #: name. Each value is a bounded aggregate only: counts, byte buckets, a small
        #: top-level folder sample, depth/date counts, and truncation flags. Individual
        #: file names/paths are deliberately never persisted in the KB.
        lakehouse_files: dict[str, dict] = field(default_factory=dict)
        #: Per-Lakehouse summary of the **Tables** section - the same bounded shape as
        #: :attr:`lakehouse_files`, but for the Delta data files. A Lakehouse's tables
        #: live under ``Tables/``, so a file-size check that read only ``Files/`` was
        #: measuring loose landing-area files and ignoring every Parquet file the
        #: point is actually about. Empty when the listing could not be read, which
        #: the checks must treat as unknown rather than as an empty Lakehouse.
        lakehouse_tables_files: dict[str, dict] = field(default_factory=dict)
        #: Per-Data-Activator (Reflex) rule summary, keyed by item display name:
        #: ``{"rules": int, "active_rules": int, "sources": int, "actions": int}``,
        #: parsed from the item's ``ReflexEntities.json`` definition. An Activator
        #: absent from the map had no readable definition (N/A); one present with
        #: ``rules=0`` is a real finding — an empty Activator with no trigger.
        activators: dict[str, dict] = field(default_factory=dict)
        #: The Git provider connection details (provider, org, repo, branch, dir) when
        #: the workspace is Git-connected — the authoritative source for item code.
        git_details: dict = field(default_factory=dict)
        #: View definitions read over the SQL analytics endpoint:
        #: ``{"schema", "name", "definition", "store"}``. The definition is capped by
        #: the reader, so this cannot grow with the size of the data.
        sql_views: list[dict] = field(default_factory=list)
        #: Stored procedures and functions, same shape as :attr:`sql_views` plus
        #: ``type``. This is the Warehouse load logic that no Fabric REST endpoint
        #: exposes - TRY/CATCH handling, incremental patterns, statistics maintenance.
        sql_routines: list[dict] = field(default_factory=list)
        #: Database-scoped principals from the SQL endpoint:
        #: ``{"name", "type", "authentication", "store"}``. A second, admin-free view
        #: of "who has access", for the workspace whose role assignments could not be
        #: read from Fabric REST.
        sql_principals: list[dict] = field(default_factory=list)
        #: On-premises / VNet data gateways the caller administers:
        #: ``{"id", "display_name", "type", "version", "number_of_member_gateways",
        #: "members"}``. An elevated read (``Gateway.Read.All`` + a role on each
        #: gateway), so an empty list can mean "none" *or* "none you administer" —
        #: which is why the checks consult :attr:`unavailable` first.
        gateways: list[dict] = field(default_factory=list)
        #: OneLake data access roles per Lakehouse, keyed by item display name:
        #: ``[{"name", "members", "permissions"}]``. Only role shape is kept, never
        #: the data the role grants access to.
        data_access_roles: dict[str, list] = field(default_factory=dict)
        #: Tenant-admin evidence is repeated on each selected workspace context so
        #: existing workspace-scoped engine dispatch and snapshot replay need no
        #: special global-context path. Providers may cache the tenant-wide calls.
        tenant_settings: list[dict] = field(default_factory=list)
        tenant_domains: list[dict] = field(default_factory=list)
        admin_scan: dict = field(default_factory=dict)
        activity_events: list[dict] = field(default_factory=list)
        capacity_metrics: dict = field(default_factory=dict)
        #: Resources the provider tried and failed to read. A check whose data lands
        #: here must report N/A rather than failing: "we could not determine this" is
        #: not the same finding as "this is not configured".
        unavailable: set[Resource] = field(default_factory=set)
        #: Per-resource read outcomes for the one-call-per-item definition/table
        #: reads (notebooks, pipelines, tables, semantic models). Maps a Resource
        #: *value* to counts (attempted/read/failed/forbidden/transient), so a
        #: *partial* crawl ("42 of 138 notebook definitions could not be read") is
        #: visible instead of silently shrinking the object set. Set by the provider.
        read_failures: dict[str, dict] = field(default_factory=dict)

        def has(self, resource: Resource) -> bool:
            """True when ``resource`` was read successfully and can be judged."""
            return resource not in self.unavailable

        @property
        def is_complete(self) -> bool:
            """True when the crawl read everything a re-crawl could still recover.

        Incomplete when a per-item definition/table read was *blocked* — forbidden
        or throttled (tracked in :attr:`read_failures`) — or a core list read,
        items or role assignments, was unavailable. Such a snapshot must not be
        cached and served as if whole: it would freeze a permission/throttle gap
        into a believable-looking low score. A lone GIT read failure is tolerated
        (cheap, rarely blocking), and so is an ``empty`` count — a definition that
        came back unusable is reported, but re-crawling will not change it.
        """
            blocking = {Resource.ITEMS, Resource.ROLE_ASSIGNMENTS}
            recoverable = any(
                stat.get("forbidden") or stat.get("transient")
                for stat in self.read_failures.values()
            )
            return not recoverable and not (self.unavailable & blocking)

        @property
        def name(self) -> str:
            """Display name, falling back to the id so reports never show a blank."""
            return self.display_name or self.id

        def item_types(self) -> set[str]:
            """Every Fabric item type present, including pipelines fetched separately."""
            types = {i.type for i in self.items if i.type}
            if self.pipelines:
                types.add("DataPipeline")
            return types

        def to_dict(self) -> dict:
            """A JSON-safe snapshot of this workspace, for the on-disk KB cache."""
            return {
                "id": self.id,
                "display_name": self.display_name,
                "layer": self.layer.value,
                "capacity_id": self.capacity_id,
                "git_connected": self.git_connected,
                "deployment_pipeline": self.deployment_pipeline,
                "role_assignments": [asdict(r) for r in self.role_assignments],
                "items": [asdict(i) for i in self.items],
                "pipelines": self.pipelines,
                "notebooks": self.notebooks,
                "environments": self.environments,
                "spark_settings": self.spark_settings,
                "tables": self.tables,
                "shortcuts": self.shortcuts,
                "semantic_models": self.semantic_models,
                "refresh_schedules": self.refresh_schedules,
                "warehouse_security": self.warehouse_security,
                "warehouse_options": self.warehouse_options,
                "warehouse_audit": self.warehouse_audit,
                "run_history": self.run_history,
                "connections": self.connections,
                "reports": self.reports,
                "lakehouse_files": self.lakehouse_files,
                "lakehouse_tables_files": self.lakehouse_tables_files,
                "activators": self.activators,
                "git_details": self.git_details,
                "sql_views": self.sql_views,
                "sql_routines": self.sql_routines,
                "sql_principals": self.sql_principals,
                "gateways": self.gateways,
                "data_access_roles": self.data_access_roles,
                "tenant_settings": self.tenant_settings,
                "tenant_domains": self.tenant_domains,
                "admin_scan": self.admin_scan,
                "activity_events": self.activity_events,
                "capacity_metrics": self.capacity_metrics,
                "unavailable": sorted(r.value for r in self.unavailable),
                "read_failures": self.read_failures,
            }

        @classmethod
        def from_dict(cls, data: dict) -> WorkspaceContext:
            """Rebuild a context from :meth:`to_dict` output (the KB cache)."""
            return cls(
                id=data["id"],
                display_name=data.get("display_name", ""),
                layer=Layer(data["layer"]) if data.get("layer") else Layer.MIXED,
                capacity_id=data.get("capacity_id"),
                git_connected=bool(data.get("git_connected", False)),
                deployment_pipeline=bool(data.get("deployment_pipeline", False)),
                role_assignments=[RoleAssignment(**r) for r in data.get("role_assignments", [])],
                items=[Item(**i) for i in data.get("items", [])],
                pipelines=dict(data.get("pipelines", {})),
                notebooks=dict(data.get("notebooks", {})),
                environments=dict(data.get("environments", {})),
                spark_settings=dict(data.get("spark_settings", {})),
                tables=dict(data.get("tables", {})),
                shortcuts=dict(data.get("shortcuts", {})),
                semantic_models=dict(data.get("semantic_models", {})),
                refresh_schedules=dict(data.get("refresh_schedules", {})),
                warehouse_security=dict(data.get("warehouse_security", {})),
                warehouse_options=dict(data.get("warehouse_options", {})),
                warehouse_audit=dict(data.get("warehouse_audit", {})),
                run_history=dict(data.get("run_history", {})),
                connections=list(data.get("connections", [])),
                reports=list(data.get("reports", [])),
                lakehouse_files=dict(data.get("lakehouse_files", {})),
                lakehouse_tables_files=dict(data.get("lakehouse_tables_files", {})),
                activators=dict(data.get("activators", {})),
                git_details=dict(data.get("git_details", {})),
                sql_views=list(data.get("sql_views", [])),
                sql_routines=list(data.get("sql_routines", [])),
                sql_principals=list(data.get("sql_principals", [])),
                gateways=list(data.get("gateways", [])),
                data_access_roles=dict(data.get("data_access_roles", {})),
                tenant_settings=list(data.get("tenant_settings", [])),
                tenant_domains=list(data.get("tenant_domains", [])),
                admin_scan=dict(data.get("admin_scan", {})),
                activity_events=list(data.get("activity_events", [])),
                capacity_metrics=dict(data.get("capacity_metrics", {})),
                unavailable={Resource(v) for v in data.get("unavailable", [])},
                read_failures=dict(data.get("read_failures", {})),
            )


    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\core\errors.py ---
def _collector_build_core_errors(_collector_modules):
    """Provider failures the pure engine can classify without importing adapters."""
    pass  # annotations are enabled once, at the top of the script


    class ProviderError(Exception):
        """Base class for anything that stops a provider returning a context."""


    class WorkspaceAccessError(ProviderError):
        """A workspace could not be read — missing, forbidden, or unreachable."""

        def __init__(self, workspace_id: str, status: int | None = None):
            self.workspace_id = workspace_id
            self.status = status
            super().__init__(self.friendly_message())

        def friendly_message(self) -> str:
            """Plain-English guidance, written for the person running the audit."""
            if self.status == 401:
                return (
                    "Sign-in/token problem (HTTP 401): the token was rejected or has "
                    "expired. Sign in again, then retry."
                )
            if self.status == 403:
                return (
                    "Access denied (HTTP 403): the signed-in user does not have access "
                    "to this workspace. Ask for at least a Viewer role, then retry."
                )
            if self.status == 404:
                return (
                    "Not found (HTTP 404): no workspace with this name/ID is visible to "
                    "you. Use “Load my Fabric workspaces” to pick the right one."
                )
            if self.status is None:
                return "Could not reach Fabric to read this workspace (network/connection error)."
            return f"Fabric returned HTTP {self.status} for this workspace, so it was not audited."

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\base.py ---
def _collector_build_clients_base(_collector_modules):
    Resource = _collector_modules['core.enums'].Resource

    ALL_RESOURCES: frozenset[Resource] = frozenset(Resource)

    #: Resources only the elevated ("admin") checks read. They need scopes and roles
    #: an ordinary reviewer does not hold (``Gateway.Read.All`` plus a role on each
    #: gateway; ``OneLake.Read.All`` plus a workspace role), so a standard audit must
    #: not request them: it would spend calls on data no standard check reads and
    #: collect 403s that look like findings in the crawl-completeness section.
    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\errors.py ---
def _collector_build_clients_errors(_collector_modules):
    """Backward-compatible exports for provider failure types."""
    pass  # annotations are enabled once, at the top of the script

    ProviderError = _collector_modules['core.errors'].ProviderError
    WorkspaceAccessError = _collector_modules['core.errors'].WorkspaceAccessError

    __all__ = ["ProviderError", "WorkspaceAccessError"]

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\tmsl.py ---
def _collector_build_clients_tmsl(_collector_modules):
    """Parse a Tabular Model Scripting Language (TMSL) semantic-model definition.

Fabric's ``getDefinition?format=TMSL`` returns the model as a single JSON document
(the ``model.bim`` shape). This module reduces that to the handful of facts the
audit and the Digital Twin care about — the model's tables, its measures (with
their DAX and descriptions), and its relationships — without pulling in a Tabular
Object Model dependency.

It is pure and defensive: a missing or oddly-shaped section yields empty lists
rather than raising, so a partial or future TMSL variant still parses cleanly.
"""
    pass  # annotations are enabled once, at the top of the script

    import re
    from typing import Any

    # Power BI's "Auto date/time" feature silently generates one hidden date table per
    # date/datetime column (``LocalDateTable_<guid>``) plus a single ``DateTableTemplate_<guid>``.
    # These system tables never appear in the Power BI model or report UI, so they are
    # excluded from the captured facts to match what a reviewer actually sees.
    _AUTO_DATE_TABLE = re.compile(
        r"^(?:LocalDateTable|DateTableTemplate)_"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )


    def _is_auto_date_table(name: str) -> bool:
        """True for a Power BI "Auto date/time" hidden table (not shown in the model UI)."""
        return bool(_AUTO_DATE_TABLE.match(name or ""))


    def _expression(value: Any) -> str:
        """A measure/column expression is a string or an array of source lines."""
        if isinstance(value, list):
            return "\n".join(str(part) for part in value)
        return str(value or "")


    #: Partition ``source.type`` values, mapped to the storage mode a reader cares
    #: about. ``entity`` is Direct Lake (the partition points at a Lakehouse table);
    #: ``m``/``query`` are Power Query / native SQL, whose mode comes from the
    #: partition's own ``mode`` field; ``calculated`` is a DAX-computed table.
    _SOURCE_TYPE_MODE = {
        "entity": "directLake",
        "calculated": "calculated",
        "calculationgroup": "calculationGroup",
    }

    #: A native SQL / M partition expression is kept (capped) so a check can tell a
    #: plain source read apart from an inline transformation. Never row data — the
    #: query *text* only, and only up to this many characters.
    _MAX_QUERY_EXPRESSION_CHARS = 4000


    def _table_storage(table: dict) -> dict:
        """Storage facts for one table, read from its partitions.

    Structure only — partition *definitions*, never the rows behind them. A
    model states its mode per partition, so a table can legitimately be mixed
    (a "dual" or hybrid table); every distinct mode seen is reported.
    """
        modes: set[str] = set()
        source_types: set[str] = set()
        native_queries = 0
        native_expressions: list[str] = []
        #: Where a Direct Lake partition actually points. TMSL records it as
        #: ``entityName`` (the Lakehouse/Warehouse table) plus ``expressionSource``
        #: (the model-level M expression naming the store). Both were previously
        #: dropped, so a Direct Lake model's source was unreadable and the
        #: model -> store hop of the lineage chain could not be resolved at all.
        entity_sources: list[dict] = []
        for part in table.get("partitions") or []:
            if not isinstance(part, dict):
                continue
            source = part.get("source") if isinstance(part.get("source"), dict) else {}
            source_type = str(source.get("type") or "").lower()
            if source_type:
                source_types.add(source_type)
            # A native SQL / M partition that carries its own query text is a
            # per-refresh transformation *candidate* living in the model rather than
            # upstream. The query text is kept (capped) so a check can tell a plain
            # source read apart from a genuine transform.
            if source_type in {"query", "m"}:
                expression = _expression(source.get("expression")).strip()
                if expression:
                    native_queries += 1
                    native_expressions.append(expression[:_MAX_QUERY_EXPRESSION_CHARS])
            if source_type == "entity":
                entity = {
                    "entity": str(source.get("entityName") or ""),
                    "schema": str(source.get("schemaName") or ""),
                    "expression_source": str(source.get("expressionSource") or ""),
                }
                if any(entity.values()):
                    entity_sources.append(entity)
            mode = str(part.get("mode") or "").strip()
            modes.add(mode or _SOURCE_TYPE_MODE.get(source_type, ""))
        return {
            "modes": sorted(m for m in modes if m),
            "source_types": sorted(source_types),
            "native_query_partitions": native_queries,
            "native_query_expressions": native_expressions,
            "entity_sources": entity_sources,
        }


    def _table_refresh_policy(table: dict, table_name: str) -> dict | None:
        """The table's incremental-refresh policy, or ``None`` when it has none.

    TMSL records this as ``refreshPolicy`` on the table. Only the *shape* of the
    policy is kept — the windows it declares — never any data it would load.
    """
        policy = table.get("refreshPolicy")
        if not isinstance(policy, dict):
            return None
        return {
            "table": table_name,
            "policy_type": str(policy.get("policyType") or ""),
            "rolling_window_granularity": str(policy.get("rollingWindowGranularity") or ""),
            "rolling_window_periods": policy.get("rollingWindowPeriods"),
            "incremental_granularity": str(policy.get("incrementalGranularity") or ""),
            "incremental_periods": policy.get("incrementalPeriods"),
        }


    def _table_aggregations(table: dict, table_name: str) -> list[dict]:
        """Aggregation columns on one table, declared in TMSL as ``alternateOf``.

    A column carrying ``alternateOf`` is an aggregation of a detail column in
    another table — the mechanism that lets a visual answer from a summary
    instead of scanning detail rows.
    """
        found: list[dict] = []
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            alternate = column.get("alternateOf")
            if not isinstance(alternate, dict):
                continue
            base = alternate.get("baseColumn") if isinstance(alternate.get("baseColumn"), dict) else {}
            found.append({
                "table": table_name,
                "column": column.get("name", ""),
                "summarization": str(alternate.get("summarization") or ""),
                "base_table": str(base.get("table") or ""),
                "base_column": str(base.get("column") or ""),
            })
        return found


    def _table_columns(table: dict, table_name: str) -> list[dict]:
        """Column *definitions* for one table — names and declared types only.

    Deliberately shallow: a column's ``dataType`` (its tabular type), its
    ``sourceProviderType`` (the SQL type it was imported from, when the model
    records one), whether it is hidden, and whether the model marks it a key.
    None of that is row data — no distinct-value count, no statistics, nothing
    that would require reading the model's contents.

    ``sourceProviderType`` matters because TMSL's own type system has no
    ``uniqueidentifier``: a GUID column arrives as ``string`` and is otherwise
    indistinguishable from a two-value status code.
    """
        out: list[dict] = []
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "")
            if not name:
                continue
            out.append({
                "table": table_name,
                "name": name,
                "data_type": str(column.get("dataType") or ""),
                "source_provider_type": str(column.get("sourceProviderType") or ""),
                "source_column": str(column.get("sourceColumn") or ""),
                "is_hidden": bool(column.get("isHidden", False)),
                "is_key": bool(column.get("isKey", False)),
                # The folder a report author sees this column filed under. TMSL carries
                # it per column; without it the "model organisation" half of ref 14.1.8
                # was unassessable and the check said so rather than judging it.
                "display_folder": str(column.get("displayFolder") or ""),
            })
        return out


    def parse_tmsl(document: dict) -> dict:
        """Normalize a TMSL document to the facts the audit and Digital Twin need.

    Accepts either the full ``{"model": {...}}`` envelope or a bare model object.

    Everything here is **model metadata** — table and partition *definitions*,
    measure DAX, relationships, roles, refresh policies, column *declarations*.
    No row data is read or stored; the semantic model's actual contents stay in
    Fabric. In particular no column *cardinality* is computed: a distinct-value
    count needs the rows, and rows never enter the knowledge base.
    """
        if not isinstance(document, dict):
            return {
                "tables": [], "measures": [], "relationships": [], "roles": [],
                "storage": {}, "refresh_policies": [], "aggregations": [],
                "columns": [], "expressions": [], "direct_lake_behavior": "",
            }

        model = document.get("model") if isinstance(document.get("model"), dict) else document
        tables = model.get("tables") or []

        table_names: list[str] = []
        measures: list[dict] = []
        model_columns: list[dict] = []
        storage: dict[str, dict] = {}
        refresh_policies: list[dict] = []
        aggregations: list[dict] = []
        #: Per-table ``dataCategory``. Microsoft's star-schema guidance is explicit
        #: that no property marks a table as fact or dimension - role is determined
        #: by relationships - but ``dataCategory`` is a *declared* hint when a
        #: modeller sets it ("Time" on a date table is set automatically by Power BI).
        #: Stored so the role classifier can prefer a stated intent over a guess.
        data_categories: dict[str, str] = {}
        for table in tables:
            if not isinstance(table, dict):
                continue
            table_name = table.get("name", "")
            if _is_auto_date_table(table_name):
                continue  # skip Power BI auto date/time hidden tables
            table_names.append(table_name)
            model_columns.extend(_table_columns(table, table_name))
            category = str(table.get("dataCategory") or "")
            if category:
                data_categories[table_name] = category
            # Partition modes, incremental-refresh policy and aggregation columns.
            # These three were previously initialised and returned but never filled,
            # so refs 14.2.1, 14.2.2, 14.2.4, 14.2.6 and 14.5.2 read an empty
            # structure and returned N/A on every audit - coverage on the catalog,
            # none in the report.
            storage[table_name] = _table_storage(table)
            policy = _table_refresh_policy(table, table_name)
            if policy is not None:
                refresh_policies.append(policy)
            aggregations.extend(_table_aggregations(table, table_name))
            for measure in table.get("measures") or []:
                if not isinstance(measure, dict):
                    continue
                measures.append({
                    "name": measure.get("name", ""),
                    "table": table_name,
                    "expression": _expression(measure.get("expression")),
                    "description": measure.get("description", "") or "",
                    "is_hidden": bool(measure.get("isHidden", False)),
                    "format_string": measure.get("formatString", "") or "",
                    "display_folder": str(measure.get("displayFolder") or ""),
                })

        relationships: list[dict] = []
        for rel in model.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            from_table = rel.get("fromTable", "")
            to_table = rel.get("toTable", "")
            if _is_auto_date_table(from_table) or _is_auto_date_table(to_table):
                continue  # drop relationships that point at the hidden auto date tables
            relationships.append({
                "name": rel.get("name", ""),
                "from_table": from_table,
                "from_column": rel.get("fromColumn", ""),
                "to_table": to_table,
                "to_column": rel.get("toColumn", ""),
                "cross_filter": rel.get("crossFilteringBehavior", "") or "",
                #: Declared relationship cardinality — structural metadata (never row
                #: data). TMSL omits these for a standard many-to-one relationship, so
                #: an empty string means "defaulted", not "unknown"; both ends set to
                #: ``many`` is a direct many-to-many relationship (no bridge).
                "from_cardinality": str(rel.get("fromCardinality", "") or "").strip().lower(),
                "to_cardinality": str(rel.get("toCardinality", "") or "").strip().lower(),
                "is_active": bool(rel.get("isActive", True)),
            })

        roles: list[dict] = []
        model_expressions: list[dict] = []
        for expression in model.get("expressions") or []:
            if not isinstance(expression, dict):
                continue
            text = _expression(expression.get("expression")).strip()
            if text:
                model_expressions.append({
                    "name": str(expression.get("name") or ""),
                    "expression": text[:_MAX_QUERY_EXPRESSION_CHARS],
                })
        for role in model.get("roles") or []:
            if not isinstance(role, dict):
                continue
            perms = role.get("tablePermissions") or []
            roles.append({
                "name": role.get("name", ""),
                "model_permission": role.get("modelPermission", "") or "",
                "table_permissions": [
                    {
                        "table": p.get("name", ""),
                        "filter": _expression(p.get("filterExpression")),
                        # "None" here hides the whole table — table-level OLS.
                        "metadata_permission": p.get("metadataPermission", "") or "",
                        "column_permissions": [
                            {"column": cp.get("name", ""), "permission": cp.get("metadataPermission", "")}
                            for cp in (p.get("columnPermissions") or []) if isinstance(cp, dict)
                        ],
                    }
                    for p in perms if isinstance(p, dict)
                ],
            })

        return {
            "tables": table_names,
            "measures": measures,
            "relationships": relationships,
            "roles": roles,
            #: Declared ``dataCategory`` per table ("Time", "Customers", ...), when
            #: the modeller set one. Empty for every table on most models.
            "data_categories": data_categories,
            #: Per-table partition modes / source types (structure, not rows).
            "storage": storage,
            #: Tables carrying an incremental-refresh policy.
            "refresh_policies": refresh_policies,
            #: Aggregation columns declared via ``alternateOf``.
            "aggregations": aggregations,
            #: Every column *declaration* in the model (name + declared types +
            #: hidden/key flags). Structure only — never a distinct-value count.
            "columns": model_columns,
            #: Model-level shared M expressions, ``name -> capped expression``. A
            #: Direct Lake partition names one of these in ``expressionSource``, and
            #: the expression is where the Lakehouse/Warehouse it reads is actually
            #: written down — so without it the model -> store hop is unresolvable.
            "expressions": model_expressions,
            #: Model-level Direct Lake fallback: automatic | directLakeOnly | directQueryOnly.
            "direct_lake_behavior": str(model.get("directLakeBehavior") or ""),
        }

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\onelake.py ---
def _collector_build_clients_onelake(_collector_modules):
    """OneLake Files listing through the ADLS Gen2 List Path API.

OneLake exposes Fabric item storage at ``onelake.dfs.fabric.microsoft.com`` via
the standard Azure Data Lake Storage Gen2 API. The auditor uses that only to
build a small per-Lakehouse aggregate for the Files section; it never persists
individual file paths in the knowledge base.
"""
    pass  # annotations are enabled once, at the top of the script

    import re
    from urllib.parse import quote

    _ONELAKE_BASE = "https://onelake.dfs.fabric.microsoft.com"
    _DEFAULT_MAX_ENTRIES = 5_000
    _MAX_TOP_LEVEL_FOLDERS = 25

    #: How many offending file paths one Lakehouse summary retains. A *sample*, not
    #: a list: the point of naming them is to give a reviewer somewhere to start,
    #: and 20 does that. Keeping every path would defeat the reason this summary is
    #: bounded at all - one real crawl carried 4,456 files in a single Lakehouse, so
    #: an unbounded list would grow the KB by the size of the estate and persist the
    #: customer's whole directory structure.
    _MAX_NAMED_FILES = 20

    #: Only the two extremes are worth a reviewer's time. A 16-128MB file is
    #: slightly small; a sub-1MB file in a Delta table is the small-file problem
    #: itself, and a >1GB file is the opposite failure. Sampling the extremes keeps
    #: the retained set both small and actionable.
    _TINY_FILE = 1024 * 1024

    _UNDER_16MB = 16 * 1024 * 1024
    _UNDER_128MB = 128 * 1024 * 1024
    _UNDER_1GB = 1024 * 1024 * 1024

    _DATE_SEGMENT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _YEAR = re.compile(r"^\d{4}$")
    _MONTH = re.compile(r"^\d{1,2}$")
    _DAY = re.compile(r"^\d{1,2}$")
    _HIVE_YEAR = re.compile(r"^year=\d{4}$", re.IGNORECASE)
    _HIVE_MONTH = re.compile(r"^month=\d{1,2}$", re.IGNORECASE)
    _HIVE_DAY = re.compile(r"^day=\d{1,2}$", re.IGNORECASE)

    _METADATA_JSON = {
        "_metadata.json", "metadata.json", "manifest.json", "_manifest.json",
        "commits.json", "_commits.json",
    }


    class OneLakeClient:
        """Read OneLake Files listings and reduce them to bounded summaries."""

        def __init__(self, token: str, *, timeout: int = 60,
                     max_entries: int = _DEFAULT_MAX_ENTRIES):
            requests = _collector_import_dependency('requests')

            self._session = requests.Session()
            self._session.headers.update({"Authorization": f"Bearer {token}"})
            self._timeout = timeout
            self._max_entries = max(1, int(max_entries))

        def lakehouse_files_summary(self, workspace_id: str, item_id: str) -> tuple[dict, str]:
            """Return ``(summary, failure)`` for ``<item_id>/Files``.

        ``failure`` is ``""`` for a readable listing, ``"forbidden"`` for
        401/403, and ``"transient"`` for throttling, 5xx or transport failures.
        A 404 means the Files directory is absent/empty and returns an empty
        summary rather than a read failure.
        """
            return self._listing_summary(workspace_id, item_id, "Files")

        def lakehouse_tables_summary(self, workspace_id: str, item_id: str) -> tuple[dict, str]:
            """Return ``(summary, failure)`` for ``<item_id>/Tables``.

        **Why this exists.** A Lakehouse's Delta tables live under ``Tables/``,
        not ``Files/`` - the Parquet files a "small file problem" check is about
        are all there. Summarising only ``Files/`` measured whatever loose files
        happened to sit in the landing area: on a real estate that reported
        "0 of 3 data files in band" for a Bronze Lakehouse whose actual Delta
        data was never looked at.

        Same shape and same bounded aggregate as the Files summary, so nothing
        downstream has to special-case it, and the same enumeration cap applies.
        """
            return self._listing_summary(workspace_id, item_id, "Tables")

        def _listing_summary(self, workspace_id: str, item_id: str,
                             section: str) -> tuple[dict, str]:
            """Aggregate one Lakehouse section (``Files`` or ``Tables``) into a summary."""
            summary = empty_lakehouse_files_summary()
            continuation = ""
            seen = 0
            while True:
                params = {
                    "recursive": "true",
                    "resource": "filesystem",
                    "directory": f"{item_id}/{section}",
                }
                if continuation:
                    params["continuation"] = continuation
                url = f"{_ONELAKE_BASE}/{quote(workspace_id, safe='')}"
                try:
                    response = self._session.get(url, params=params, timeout=self._timeout)
                except Exception:
                    return {}, "transient"

                if response.status_code in (401, 403):
                    return {}, "forbidden"
                if response.status_code == 404:
                    return _finalize_summary(summary), ""
                if response.status_code == 429 or response.status_code >= 500:
                    return {}, "transient"
                if response.status_code != 200:
                    return {}, "transient"
                try:
                    body = response.json()
                except ValueError:
                    return {}, "transient"
                paths = body.get("paths")
                if not isinstance(paths, list):
                    return {}, "transient"

                remaining = self._max_entries - seen
                if len(paths) > remaining:
                    summary["truncated"] = True
                    paths = paths[:remaining]
                for entry in paths:
                    if isinstance(entry, dict):
                        _add_entry(summary, item_id, entry, section)
                seen += len(paths)
                if seen >= self._max_entries:
                    summary["truncated"] = True
                    break

                continuation = (
                    response.headers.get("x-ms-continuation")
                    or body.get("continuation")
                    or ""
                )
                if not continuation:
                    break

            return _finalize_summary(summary), ""

        def lakehouse_table_partitions(
            self, workspace_id: str, item_id: str
        ) -> tuple[dict[str, list[str]], str]:
            """Return ``({table: [partition columns]}, failure)`` for ``<item_id>/Tables``.

        A Delta table's partitioning is visible in OneLake as Hive-style
        directories (``event_date=2026-08-01``) under the table root, so the
        declared strategy is readable without opening ``_delta_log``. A table
        present with an empty list is *known* to be unpartitioned; a table absent
        from the map was never listed, which is not the same finding.
        """
            entries: list[list[str]] = []
            continuation = ""
            seen = 0
            while True:
                params = {
                    "recursive": "true",
                    "resource": "filesystem",
                    "directory": f"{item_id}/Tables",
                }
                if continuation:
                    params["continuation"] = continuation
                url = f"{_ONELAKE_BASE}/{quote(workspace_id, safe='')}"
                try:
                    response = self._session.get(url, params=params, timeout=self._timeout)
                except Exception:
                    return {}, "transient"

                if response.status_code in (401, 403):
                    return {}, "forbidden"
                if response.status_code == 404:
                    return {}, ""
                if response.status_code == 429 or response.status_code >= 500:
                    return {}, "transient"
                if response.status_code != 200:
                    return {}, "transient"
                try:
                    body = response.json()
                except ValueError:
                    return {}, "transient"
                paths = body.get("paths")
                if not isinstance(paths, list):
                    return {}, "transient"

                for entry in paths[: self._max_entries - seen]:
                    if not isinstance(entry, dict):
                        continue
                    segments = str(entry.get("name") or "").split("/")
                    if "Tables" in segments:
                        entries.append(segments[segments.index("Tables") + 1:])
                seen += len(paths)
                if seen >= self._max_entries:
                    break

                continuation = (
                    response.headers.get("x-ms-continuation")
                    or body.get("continuation")
                    or ""
                )
                if not continuation:
                    break

            return _partitions_from_paths(entries), ""


    def _partitions_from_paths(entries: list[list[str]]) -> dict[str, list[str]]:
        """Reduce ``Tables``-relative path segments to ``{table: [partition columns]}``.

    ``_delta_log`` is the anchor: it sits directly under every Delta table root,
    so it identifies the table without having to guess whether a leading segment
    is a schema or the table itself.
    """
        roots: dict[str, str] = {}
        for segments in entries:
            if "_delta_log" not in segments:
                continue
            index = segments.index("_delta_log")
            if index >= 1:
                roots["/".join(segments[:index])] = segments[index - 1]
        if not roots:
            return {}

        found: dict[str, set[str]] = {root: set() for root in roots}
        for segments in entries:
            joined = "/".join(segments)
            for root in roots:
                if not joined.startswith(f"{root}/"):
                    continue
                for segment in segments[len(root.split("/")):]:
                    if "=" in segment and not segment.startswith("_"):
                        found[root].add(segment.split("=", 1)[0])
                break
        return {roots[root]: sorted(columns) for root, columns in found.items()}


    def empty_lakehouse_files_summary() -> dict:
        """Create the compact KB shape used for one Lakehouse's Files section."""
        return {
            "file_count": 0,
            "data_file_count": 0,
            "excluded_file_count": 0,
            "total_bytes": 0,
            "size_buckets": {
                "under_16mb": 0,
                "16_128mb": 0,
                "128mb_1gb": 0,
                "over_1gb": 0,
            },
            #: A bounded sample of the worst offenders, so a finding can say *which*
            #: files to look at instead of only how many. Two lists, because they are
            #: opposite problems with opposite fixes: compact the tiny ones, split
            #: the huge ones. Paths are relative to the section and truncated to the
            #: last two segments - enough to locate the table or folder, without
            #: persisting the customer's full directory structure.
            "smallest_files": [],
            "largest_files": [],
            "top_level_folders": [],
            "_top_level": set(),
            "max_depth": 0,
            "dated_path_count": 0,
            "sampled": False,
            "truncated": False,
        }


    def _finalize_summary(summary: dict) -> dict:
        summary["top_level_folders"] = sorted(summary["_top_level"])[:_MAX_TOP_LEVEL_FOLDERS]
        summary.pop("_top_level", None)
        summary["sampled"] = bool(summary["truncated"])
        # Sort and trim once, here, rather than on every file added.
        summary["smallest_files"].sort(key=lambda entry: entry["bytes"])
        del summary["smallest_files"][_MAX_NAMED_FILES:]
        summary["largest_files"].sort(key=lambda entry: entry["bytes"], reverse=True)
        del summary["largest_files"][_MAX_NAMED_FILES:]
        return summary


    def _add_entry(summary: dict, item_id: str, entry: dict, section: str = "Files") -> None:
        if _is_directory(entry):
            return
        name = str(entry.get("name") or "")
        if not name:
            return
        relative = _relative_to_section(item_id, name, section)
        if not relative:
            return
        parts = [p for p in relative.split("/") if p]
        if not parts:
            return

        size = _content_length(entry)
        summary["file_count"] += 1
        summary["max_depth"] = max(int(summary.get("max_depth") or 0), len(parts))

        if not _is_data_file(parts, size):
            summary["excluded_file_count"] += 1
            return

        # Folder layout and date partitioning describe where *data* lives, so they
        # are recorded only for data files. Counting the Delta log would report
        # ``_delta_log`` as a top-level data folder in every Lakehouse.
        if len(parts) > 1:
            summary["_top_level"].add(parts[0])
        if _has_date_segment(parts):
            summary["dated_path_count"] += 1

        summary["data_file_count"] += 1
        summary["total_bytes"] += size
        bucket = _size_bucket(size)
        summary["size_buckets"][bucket] += 1
        _sample_offender(summary, parts, size)


    def _short_path(parts: list[str]) -> str:
        """The last two path segments - enough to locate a file, not a directory map."""
        return "/".join(parts[-2:]) if len(parts) > 1 else (parts[-1] if parts else "")


    def _sample_offender(summary: dict, parts: list[str], size: int) -> None:
        """Keep a bounded sample of the smallest and largest data files.

    Insertion is O(1) per file with a single comparison against the current worst
    kept, so a 4,000-file Lakehouse costs no more than counting them. The lists
    are sorted and trimmed once, in :func:`_finalize_summary`, rather than on
    every file.
    """
        if size < _UNDER_16MB:
            smallest = summary["smallest_files"]
            # Only sort/trim when the buffer grows past twice the cap, so the common
            # path stays an append.
            smallest.append({"path": _short_path(parts), "bytes": size})
            if len(smallest) > _MAX_NAMED_FILES * 2:
                smallest.sort(key=lambda entry: entry["bytes"])
                del smallest[_MAX_NAMED_FILES:]
        elif size > _UNDER_1GB:
            largest = summary["largest_files"]
            largest.append({"path": _short_path(parts), "bytes": size})
            if len(largest) > _MAX_NAMED_FILES * 2:
                largest.sort(key=lambda entry: entry["bytes"], reverse=True)
                del largest[_MAX_NAMED_FILES:]


    def _is_directory(entry: dict) -> bool:
        value = entry.get("isDirectory")
        if isinstance(value, bool):
            return value
        return str(value).lower() == "true"


    def _content_length(entry: dict) -> int:
        try:
            return max(0, int(entry.get("contentLength") or 0))
        except (TypeError, ValueError):
            return 0


    def _relative_to_section(item_id: str, name: str, section: str = "Files") -> str:
        """The path below ``<item>/<section>/``, for either Files or Tables."""
        prefix = f"{item_id}/{section}/"
        if name.startswith(prefix):
            return name[len(prefix):]
        marker = f"/{section}/"
        if marker in name:
            return name.split(marker, 1)[1]
        return name


    def _is_data_file(parts: list[str], size: int) -> bool:
        if size <= 0:
            return False
        lowered = [p.lower() for p in parts]
        filename = lowered[-1]
        if "_delta_log" in lowered or filename.endswith(".crc"):
            return False
        return not (
            filename in _METADATA_JSON
            or filename.endswith("_metadata.json")
            or filename.endswith(".metadata.json")
        )


    def _size_bucket(size: int) -> str:
        if size < _UNDER_16MB:
            return "under_16mb"
        if size < _UNDER_128MB:
            return "16_128mb"
        if size <= _UNDER_1GB:
            return "128mb_1gb"
        return "over_1gb"


    def _has_date_segment(parts: list[str]) -> bool:
        lowered = [p.lower() for p in parts[:-1]]
        if any(_DATE_SEGMENT.match(p) for p in lowered):
            return True
        for i in range(len(lowered) - 2):
            if _YEAR.match(lowered[i]) and _MONTH.match(lowered[i + 1]) and _DAY.match(lowered[i + 2]):
                return True
            if (_HIVE_YEAR.match(lowered[i]) and _HIVE_MONTH.match(lowered[i + 1])
                    and _HIVE_DAY.match(lowered[i + 2])):
                return True
        return False

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\sqlendpoint.py ---
def _collector_build_clients_sqlendpoint(_collector_modules):
    """Read-only SQL analytics endpoint client — column schemas and security policies.

Some data a Well-Architected audit needs is **not in the Fabric REST API at all**.
Column names and types, and Warehouse row-level-security policies, live only behind
the SQL analytics endpoint that Fabric provisions for every Lakehouse and Warehouse.
This module is the transport for that.

Three things make it safe to run against a client tenant:

1. **Nothing is ever asked of the user.** The endpoint address is *discovered* over
   plain Fabric REST (``properties.sqlEndpointProperties.connectionString`` on a
   lakehouse, ``properties.connectionString`` on a warehouse) with the token the
   crawl already holds. No connection string, no PAT, no app registration.
2. **Every failure degrades to ``None``.** A blocked port 1433, a missing ODBC
   driver, an unconsented token audience, a throttled tenant — all of them return
   ``None`` with a recorded reason so the checks report **N/A**, never FAIL. The
   audit behaves exactly as it did before this module existed.
3. **Strictly read-only.** Only ``SELECT`` against ``INFORMATION_SCHEMA`` and
   ``sys.*`` catalog views. Nothing here writes, and the endpoint is read-only by
   design anyway.

Connection requirements that are easy to get wrong (see
``fabric-skills/common/SQLDW-CONSUMPTION-CORE.md``):

* Token audience is ``https://database.windows.net`` — a *different* audience from
  both the Fabric and Power BI tokens.
* ``Database`` must be the item's **display name**, not the server FQDN.
* ``Encrypt=Yes`` is required; **MARS must be off** (it is unsupported and fails in
  a confusing way).
"""
    pass  # annotations are enabled once, at the top of the script

    import contextlib
    import logging
    import struct
    import time
    from typing import Any

    log = logging.getLogger("auditfast.sqlendpoint")

    #: ODBC attribute that carries an Entra access token (``SQL_COPT_SS_ACCESS_TOKEN``).
    _SQL_COPT_SS_ACCESS_TOKEN = 1256

    #: Per-connection and per-query ceilings. A slow endpoint must not stall a crawl,
    #: but the login timeout has to absorb Azure SQL's gateway redirect and Entra
    #: token validation, which are slower than a plain SQL Server handshake.
    _CONNECT_TIMEOUT_SECONDS = 30
    _QUERY_TIMEOUT_SECONDS = 30

    #: Microsoft's guidance: beyond roughly this many warehouses + SQL endpoints in one
    #: workspace the Entra token can exceed its size limit. We read what we can and
    #: record the rest as unread rather than failing the whole crawl.
    MAX_ENDPOINTS_PER_WORKSPACE = 40

    #: How many times to re-attempt one endpoint read before giving up, and how long
    #: to wait between attempts.
    #:
    #: Without this a single transient failure lost a whole store's schema for the
    #: entire audit: two crawls of the same 105-endpoint workspace a day apart read
    #: 502 and then 307 tables, and the second lost Warehouse RLS completely. The
    #: verdicts moved with it (4.2.5 went PARTIAL -> FAIL) purely because less data
    #: was read, which reads as "the estate got worse" when nothing changed.
    #:
    #: Only *transient* failures are retried. A permission denial or a blocked port
    #: will fail identically on a second attempt, so retrying it just doubles the
    #: time a large workspace takes to crawl.
    _MAX_ATTEMPTS = 3
    _RETRY_BACKOFF_SECONDS = 2.0

    #: Failure reasons worth a second attempt: throttling, a dropped connection, and
    #: timeouts. Matched against :func:`_classify`'s output, so the vocabulary stays
    #: in one place.
    _RETRYABLE_REASONS = (
        "did not finish in time",
        "not reachable",
        "connection",
        "timeout",
        "timed out",
        "throttl",
        "too many requests",
        "transport",
    )


    def _is_auth_failure(reason: str) -> bool:
        """True when the reason looks like a rejected/expired token.

    An Entra access token lives about an hour. A large workspace takes longer
    than that to crawl, which is exactly why the Fabric REST client carries a
    refresher. pyodbc reports a dead token as ``Login failed for user
    '<token-identified principal>'``, which :func:`_classify` renders as an
    access problem - indistinguishable from a genuine permission denial, and
    deliberately *not* retryable, since retrying with the same dead token
    changes nothing. Re-minting the token is the only thing that helps.
    """
        return "no access to this database" in (reason or "").lower()


    def _is_retryable(reason: str) -> bool:
        """True when a failure reason describes a condition that may clear on retry.

    A permission problem ("no access") and the Entra token-size limit are
    deliberately excluded: they are deterministic for a given token, so a second
    attempt costs time and changes nothing.
    """
        text = (reason or "").lower()
        if "no access" in text or "system limit" in text or "too many warehouses" in text:
            return False
        return any(marker in text for marker in _RETRYABLE_REASONS)


    class SqlEndpoint:
        """One queryable SQL database: where it is, what it is called, and its kind.

    ``kind`` matters beyond bookkeeping: a Lakehouse SQL endpoint maps every Delta
    ``string`` column to ``varchar(8000)`` whatever the author intended, so a
    declared width is only a *design choice* on a Warehouse. Checks that judge
    column widths need to know which they are looking at.
    """

        __slots__ = ("kind", "name", "server", "status")

        def __init__(self, kind: str, name: str, server: str, status: str = "Success"):
            self.kind = kind          # "Lakehouse" | "Warehouse"
            self.name = name          # database name == item display name
            self.server = server      # FQDN, no port
            self.status = status

        @property
        def queryable(self) -> bool:
            """True once Fabric has finished provisioning the endpoint."""
            return (self.status or "").strip().lower() == "success"

        @property
        def host(self) -> str:
            """Bare FQDN, with any ``tcp:`` prefix or ``,port`` suffix removed.

        Fabric returns a plain host today, but a connection string is allowed to
        carry either. Appending ``,1433`` to a value that already has a port
        yields ``host,1433,1433``, which the driver cannot parse and which
        surfaces only as a connection timeout - indistinguishable from a blocked
        port. Normalising here makes that impossible.
        """
            text = (self.server or "").strip()
            if text.lower().startswith("tcp:"):
                text = text[4:]
            return text.split(",")[0].strip()

        def __repr__(self) -> str:  # pragma: no cover - debugging aid
            return f"SqlEndpoint({self.kind} {self.name!r} @ {self.server[:40]}…)"


    def discover_endpoints(get_json, workspace_id: str) -> list[SqlEndpoint]:
        """Every SQL endpoint in a workspace, via the Fabric REST API.

    ``get_json`` is a ``(path) -> dict`` callable owned by the caller, so this
    module never does its own HTTP or auth. Type-specific list endpoints are used
    deliberately: the generic ``/items`` list does not carry connection strings.

    A discovery failure yields an empty list, not an exception — no endpoints is
    simply "no column data", which the checks already handle as N/A.
    """
        endpoints: list[SqlEndpoint] = []

        try:
            for item in (get_json(f"/workspaces/{workspace_id}/lakehouses") or {}).get("value", []):
                props = (item.get("properties") or {}).get("sqlEndpointProperties") or {}
                server = (props.get("connectionString") or "").strip()
                if server:
                    endpoints.append(SqlEndpoint(
                        "Lakehouse", item.get("displayName") or "", server,
                        props.get("provisioningStatus") or "",
                    ))
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort
            log.warning("sql endpoint discovery (lakehouses) failed for %s: %s - "
                        "those lakehouses will have no column data", workspace_id, exc)

        try:
            for item in (get_json(f"/workspaces/{workspace_id}/warehouses") or {}).get("value", []):
                props = item.get("properties") or {}
                server = (props.get("connectionString") or "").strip()
                if server:
                    endpoints.append(SqlEndpoint(
                        "Warehouse", item.get("displayName") or "", server, "Success",
                    ))
        except Exception as exc:  # noqa: BLE001
            log.warning("sql endpoint discovery (warehouses) failed for %s: %s - "
                        "Warehouse security policies will not be read", workspace_id, exc)

        return [e for e in endpoints if e.queryable and e.name]


    def _token_struct(access_token: str) -> bytes:
        """Pack an Entra token the way the ODBC driver expects it."""
        raw = access_token.encode("utf-16-le")
        return struct.pack(f"<I{len(raw)}s", len(raw), raw)


    class SqlEndpointReader:
        """Runs read-only catalog queries against SQL endpoints.

    Constructed with the SQL-audience token. Every public method returns ``None``
    on any failure and records why in :attr:`failures`, so a caller can report an
    accurate reason instead of an empty result that looks like "nothing found".
    """

        def __init__(self, sql_token: str | None, token_provider=None):
            self._token = sql_token
            #: Re-mints the SQL-audience token when the current one is rejected.
            #: Optional: without it the reader behaves exactly as before.
            self._token_provider = token_provider
            #: ``endpoint name -> reason it could not be read``.
            self.failures: dict[str, str] = {}
            self._driver: str | None = None
            self._unavailable: str | None = None
            if not sql_token:
                self._unavailable = (
                    "no SQL-audience token (the sign-in did not yield a "
                    "https://database.windows.net token)"
                )

        # -- availability ---------------------------------------------------------

        @property
        def available(self) -> bool:
            """True when a token and an ODBC driver are both present."""
            return self._unavailable is None and self._resolve_driver() is not None

        @property
        def unavailable_reason(self) -> str:
            return self._unavailable or "unknown"

        def _resolve_driver(self) -> str | None:
            """Newest installed SQL Server ODBC driver, or None (recording why)."""
            if self._driver is not None:
                return self._driver
            try:
                import pyodbc
            except ImportError:
                self._unavailable = "pyodbc is not installed on the server"
                return None
            drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
            if not drivers:
                self._unavailable = "no 'ODBC Driver for SQL Server' is installed"
                return None
            self._driver = next((d for d in drivers if "18" in d), drivers[-1])
            return self._driver

        # -- queries --------------------------------------------------------------

        def columns(self, endpoint: SqlEndpoint) -> dict[str, list[dict[str, Any]]] | None:
            """``table key -> [{name, type, ...}]`` for one endpoint, or None.

        Warehouse keys retain their SQL schema as ``<schema>.<table>``. A
        Lakehouse keeps its bare table name so SQL columns merge with the table
        inventory already read from the Fabric REST API.

        ``type`` is rendered the way the table checks expect: ``varchar(8000)``,
        ``decimal(18,2)``, ``int``. ``source_kind`` travels with every column so a
        check can tell a Lakehouse's forced ``varchar(8000)`` from a width a
        Warehouse author actually chose.

        ``is_masked`` comes from ``sys.columns`` and records whether Dynamic Data
        Masking is applied. It is read with a ``LEFT JOIN`` so a Lakehouse
        endpoint - where the catalog view may be absent or empty - still returns
        every column, just with ``is_masked`` false.

        ``schema`` carries ``INFORMATION_SCHEMA.TABLE_SCHEMA``. It is recorded on
        the column rather than folded into the table key: the key is the join
        point between this reader and the REST item listing, and re-shaping it to
        ``schema.table`` would invalidate every snapshot already in the knowledge
        base. Checks that need the schema read it off any column.
        """
            rows = self._query(endpoint, """
            SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
                   c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE,
                   c.IS_NULLABLE, c.ORDINAL_POSITION,
                   COALESCE(sc.is_masked, 0) AS is_masked,
                   c.TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.COLUMNS AS c
            LEFT JOIN sys.columns AS sc
                   ON sc.object_id = OBJECT_ID(
                          QUOTENAME(c.TABLE_SCHEMA) + '.' + QUOTENAME(c.TABLE_NAME))
                  AND sc.name = c.COLUMN_NAME
            ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
        """)
            if rows is None:
                # ``sys.columns`` is not guaranteed on every endpoint kind. Fall back
                # to the plain projection rather than losing every column schema:
                # masking is one check, columns feed a dozen.
                rows = self._query(endpoint, """
                SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                       IS_NULLABLE, ORDINAL_POSITION, 0 AS is_masked, TABLE_SCHEMA
                FROM INFORMATION_SCHEMA.COLUMNS
                ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
            """)
            if rows is None:
                return None
            tables: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                schema = str(row[0] or "")
                table_name = str(row[1] or "")
                if not table_name:
                    continue
                table = (
                    f"{schema}.{table_name}"
                    if endpoint.kind == "Warehouse" and schema
                    else table_name
                )
                column: dict[str, Any] = {
                    "name": str(row[2] or ""),
                    "type": _render_type(row[3], row[4], row[5], row[6]),
                    "nullable": str(row[7] or "").upper() == "YES",
                    "source_kind": endpoint.kind,
                }
                # Only recorded when true, so a snapshot does not grow by a false
                # flag on every one of tens of thousands of columns.
                if len(row) > 9 and row[9]:
                    column["is_masked"] = True
                if len(row) > 9 and row[9]:
                    column["schema"] = str(row[9])
                tables.setdefault(table, []).append(column)
            return tables

        def security_policies(self, endpoint: SqlEndpoint) -> list[dict[str, Any]] | None:
            """Row-level-security policies on a Warehouse, or None if unreadable.

        An empty list is a real answer - "this warehouse defines no RLS policy" -
        and is different from ``None``, which means we could not look.
        """
            rows = self._query(endpoint, """
            SELECT p.name, p.is_enabled, OBJECT_NAME(d.target_object_id)
            FROM sys.security_policies AS p
            LEFT JOIN sys.security_predicates AS d
                   ON d.object_id = p.object_id
        """)
            if rows is None:
                return None
            return [
                {"policy": str(r[0] or ""), "enabled": bool(r[1]), "table": str(r[2] or "")}
                for r in rows
            ]

        def metadata(self, endpoint: SqlEndpoint) -> dict[str, list[tuple]]:
            """Every remaining catalog read for one endpoint, in a **single** connection.

        **Why one batch.** A connection costs far more than a query here: each
        pays a login handshake plus the Azure SQL gateway redirect, which is why
        :meth:`_attempt` opens a fresh connection per call and why crawl time
        tracks connection *count*, not row count. Issuing these reads separately
        would multiply the expensive part by the number of statements. Sending
        one batch and walking the result sets with ``nextset()`` pays the
        handshake **once** and adds only query time - milliseconds each, since
        every statement reads a catalog view rather than user data.

        Returns ``{name: rows}`` for whatever the endpoint answered; a name is
        absent when its statement returned nothing usable. Never raises: a
        connection failure yields ``{}`` and is already recorded by
        :meth:`_attempt`, because this is *additional* metadata - losing it must
        not cost the column schemas that the same crawl depends on.
        """
            statements = _METADATA_STATEMENTS
            if endpoint.kind == "Warehouse":
                # Foreign keys and key constraints are documented for Fabric
                # Warehouse only; a Lakehouse SQL endpoint does not expose them.
                statements = statements + _WAREHOUSE_METADATA_STATEMENTS
            batch = ";\n".join(sql.strip() for _name, sql in statements)
            rows_per_set = self._query_batch(endpoint, batch, len(statements))
            if rows_per_set is None:
                return {}
            return {
                name: rows
                for (name, _sql), rows in zip(statements, rows_per_set, strict=False)
                if rows
            }

        def _query_batch(self, endpoint: SqlEndpoint, sql: str,
                         expected: int) -> list[list[tuple]] | None:
            """Run a multi-statement batch, returning one row list per result set.

        Uses the same retry and token-refresh path as :meth:`_query`. A statement
        the endpoint rejects aborts the batch, so this returns whatever arrived
        before the failure rather than nothing - a Lakehouse endpoint missing one
        view should still yield the reads that preceded it.
        """
            return self._query(endpoint, sql, multi=True, expected=expected)

        def _query(self, endpoint: SqlEndpoint, sql: str, *, multi: bool = False,
                   expected: int = 1):
            """Run one read-only query, retrying a transient failure. None on failure.

        With ``multi``, ``sql`` is a batch of statements and the result is a list
        of row lists - one per result set - rather than a single row list.

        A transient failure - a throttle, a dropped connection, a slow gateway -
        previously lost a whole store's schema for the entire audit. Each attempt
        opens a fresh connection, because a half-open one is often what failed.

        An *expired* token is handled separately: it is not transient (retrying
        with the same dead token fails identically), so it gets one re-mint and
        one extra attempt, outside the retry budget. Without this a crawl longer
        than the token's ~1h life reads the first N endpoints and loses every one
        after, which looks exactly like throttling but is not.
        """
            last_reason = ""
            refreshed = False
            # Only pass the batch kwargs when a batch was asked for. A plain read
            # then calls ``_attempt`` with its original signature, so a test double
            # (or any other caller) written against that signature keeps working -
            # threading a new kwarg through unconditionally broke eight of them.
            extra = {"multi": True, "expected": expected} if multi else {}
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                rows = self._attempt(endpoint, sql, **extra)
                if rows is not None:
                    if attempt > 1:
                        # The endpoint recovered, so it is no longer a failure.
                        self.failures.pop(endpoint.name, None)
                        log.info("sql endpoint read for %s succeeded on attempt %d",
                                 endpoint.name, attempt)
                    return rows
                last_reason = self.failures.get(endpoint.name, "")

                if not refreshed and self._token_provider and _is_auth_failure(last_reason):
                    refreshed = True
                    if self._refresh_token():
                        rows = self._attempt(endpoint, sql)
                        if rows is not None:
                            self.failures.pop(endpoint.name, None)
                            return rows
                        last_reason = self.failures.get(endpoint.name, "")

                if attempt == _MAX_ATTEMPTS or not _is_retryable(last_reason):
                    break
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                log.info("retrying sql endpoint read for %s (attempt %d of %d) - %s",
                         endpoint.name, attempt + 1, _MAX_ATTEMPTS, last_reason)
            return None

        def _refresh_token(self) -> bool:
            """Re-mint the SQL token. True when a *different* token was obtained."""
            try:
                new_token = self._token_provider()
            except Exception as exc:  # noqa: BLE001 - a failed refresh must not crash
                log.warning("sql token refresh failed: %s", exc)
                return False
            if not new_token or new_token == self._token:
                log.warning("sql token refresh returned no new token")
                return False
            self._token = new_token
            self._unavailable = None
            log.info("sql token refreshed, resuming endpoint reads")
            return True

        def _attempt(self, endpoint: SqlEndpoint, sql: str, *, multi: bool = False,
                     expected: int = 1):
            """One connection + query. Any failure returns None and records why.

        With ``multi``, every result set in the batch is walked via ``nextset()``
        and returned as a list of row lists. A statement that fails part-way
        through aborts the batch, so what arrived before the failure is returned
        rather than discarded - the reads are independent of one another.
        """
            if not self.available:
                self.failures[endpoint.name] = self.unavailable_reason
                return None
            try:
                import pyodbc
            except ImportError:  # pragma: no cover - guarded by .available
                return None

            conn_str = (
                f"Driver={{{self._driver}}};Server={endpoint.host},1433;"
                f"Database={endpoint.name};Encrypt=Yes;TrustServerCertificate=No;"
                f"MultipleActiveResultSets=False;"
                f"Connection Timeout={_CONNECT_TIMEOUT_SECONDS};"
            )
            conn = None
            try:
                # No ``timeout=`` kwarg: that sets SQL_ATTR_CONNECTION_TIMEOUT, which
                # bounds the *whole* connection including the login handshake and the
                # gateway redirect Azure SQL performs. Fabric's redirect routinely
                # exceeds a short value, and the abort surfaces as a plain timeout -
                # indistinguishable from a blocked port. ``Connection Timeout`` in the
                # string (SQL_ATTR_LOGIN_TIMEOUT) is the correct knob and is set above.
                conn = pyodbc.connect(
                    conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _token_struct(self._token)},
                )
                # ``timeout`` is a *Connection* attribute in pyodbc, not a Cursor one.
                # Setting it on the cursor raises AttributeError, and because that
                # message contains the word "timeout" it used to be misreported as a
                # blocked port - a connection that had in fact already succeeded.
                conn.timeout = _QUERY_TIMEOUT_SECONDS
                cursor = conn.cursor()
                cursor.execute(sql)
                if not multi:
                    return [tuple(r) for r in cursor.fetchall()]
                return _collect_result_sets(cursor, expected)
            except Exception as exc:  # noqa: BLE001 - every failure must degrade to N/A
                self.failures[endpoint.name] = _classify(exc)
                log.info("sql endpoint read failed for %s (%s): %s",
                         endpoint.name, endpoint.kind, exc)
                return None
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        conn.close()


    def _collect_result_sets(cursor, expected: int) -> list[list[tuple]]:
        """Every result set from a batch, as one row list each.

    Walks ``nextset()`` until the cursor is exhausted. A statement that produced
    no result set contributes an empty list, so the caller's zip against the
    statement table stays aligned. Stops at ``expected`` sets so a stray extra
    set cannot shift the mapping.
    """
        collected: list[list[tuple]] = []
        while len(collected) < expected:
            try:
                collected.append([tuple(r) for r in cursor.fetchall()])
            except Exception:  # noqa: BLE001 - a statement with no rows to fetch
                collected.append([])
            try:
                if not cursor.nextset():
                    break
            except Exception:  # noqa: BLE001 - no further sets
                break
        while len(collected) < expected:
            collected.append([])
        return collected


    #: Catalog reads issued against **every** endpoint kind, as ``(name, sql)``.
    #:
    #: All are metadata reads - catalog views, never user data - so their cost is
    #: dominated by the connection they share rather than by the queries themselves.
    #: Fetching them together means a future check finds its data already in the
    #: knowledge base instead of needing another crawl change.
    #:
    #: Availability was verified against Microsoft's Fabric T-SQL surface-area
    #: documentation. Deliberately **not** attempted, because Microsoft documents
    #: them as unavailable on Fabric Warehouse / SQL analytics endpoint:
    #: ``INFORMATION_SCHEMA.TABLE_CONSTRAINTS``, ``KEY_COLUMN_USAGE``,
    #: ``REFERENTIAL_CONSTRAINTS``, ``sys.indexes``, ``sys.index_columns``,
    #: ``sys.views``, ``sys.dm_db_partition_stats``, ``OBJECT_DEFINITION()``.
    _METADATA_STATEMENTS: tuple[tuple[str, str], ...] = (
        # Object inventory: identifies views, procedures and constraints, and carries
        # create/modify dates that INFORMATION_SCHEMA does not expose.
        ("objects", """
        SELECT o.object_id, s.name, o.name, o.type,
               CONVERT(varchar(33), o.create_date, 126),
               CONVERT(varchar(33), o.modify_date, 126)
        FROM sys.objects AS o
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF', 'PK', 'F', 'UQ')
    """),
        # Approximate row counts from partition metadata - never by scanning rows.
        # ``index_id IN (0, 1)`` is the heap/clustered row set; other index ids would
        # count the same rows again.
        ("row_counts", """
        SELECT p.object_id, SUM(p.rows)
        FROM sys.partitions AS p
        WHERE p.index_id IN (0, 1)
        GROUP BY p.object_id
    """),
        # View definitions, capped so one enormous view cannot bloat the snapshot.
        ("views", """
        SELECT TABLE_SCHEMA, TABLE_NAME, LEFT(VIEW_DEFINITION, 4000)
        FROM INFORMATION_SCHEMA.VIEWS
    """),
        # Stored procedures and functions - the load logic the Warehouse checks want
        # to inspect for TRY/CATCH, incremental patterns and statistics maintenance.
        ("routines", """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE,
               LEFT(ROUTINE_DEFINITION, 8000)
        FROM INFORMATION_SCHEMA.ROUTINES
    """),
        # Statistics objects, for the statistics-maintenance checks.
        #
        # ``no_recompute`` and ``STATS_DATE`` are what make this an audit rather than
        # an assumption. Fabric creates and refreshes statistics automatically
        # (learn.microsoft.com/fabric/data-warehouse/statistics), so the absence of a
        # manual UPDATE STATISTICS is not a finding - but ``no_recompute = 1``
        # switches that automatic refresh *off* for a statistics object, which is the
        # one way an estate genuinely ends up with stale statistics. Without reading
        # it a check can only assert that the platform is doing its job; with it, the
        # check can verify that nothing has been disabled.
        #
        # ``STATS_DATE`` returns the UTC time of the last refresh (NULL if never), so
        # the same read also shows whether the automatic maintenance is actually
        # happening on this estate.
        ("stats", """
        SELECT object_id, name, auto_created, user_created, no_recompute,
               STATS_DATE(object_id, stats_id) AS last_updated
        FROM sys.stats
    """),
        # The database-level automatic-statistics switches. These are the genuinely
        # auditable setting: a user CAN turn them off
        # (learn.microsoft.com/sql/t-sql/statements/alter-database-transact-sql-set-options?view=fabric
        # lists AUTO_CREATE_STATISTICS and AUTO_UPDATE_STATISTICS in the Fabric
        # Warehouse syntax), and Microsoft says OFF "can cause suboptimal query plans
        # and degraded query performance".
        #
        # This is the read that lets the statistics checks *verify* rather than
        # assume. NORECOMPUTE looked like the equivalent signal but Fabric rejects
        # the option outright - "INCREMENTAL, MAXDOP, SAMPLE x ROWS options, and
        # filter clause are not supported statistics options" - so no_recompute is
        # always 0 and proves nothing. sys.dm_db_stats_properties, which would give
        # modification_counter for real staleness, is not available on Warehouse.
        ("database_options", """
        SELECT name, is_auto_create_stats_on, is_auto_update_stats_on,
               is_auto_update_stats_async_on
        FROM sys.databases
    """),
        # Database-scoped principals and role membership. Workspace role assignments
        # come from Fabric REST and frequently need a permission the sign-in lacks;
        # this is the database-level view of the same question, with no admin needed.
        ("principals", """
        SELECT principal_id, name, type_desc, authentication_type_desc
        FROM sys.database_principals
        WHERE type <> 'R'
    """),
        ("role_members", """
        SELECT rm.role_principal_id, rm.member_principal_id
        FROM sys.database_role_members AS rm
    """),
    )

    #: Reads documented for Fabric **Warehouse** only - a Lakehouse SQL analytics
    #: endpoint does not expose foreign-key metadata, so asking there would fail
    #: every time. Splitting the tables keeps the batch honest rather than relying on
    #: an error path.
    _WAREHOUSE_METADATA_STATEMENTS: tuple[tuple[str, str], ...] = (
        # Declared (NOT ENFORCED) foreign keys. This is the structural evidence for
        # "which table is a fact and which is a dimension": a referenced table is a
        # dimension, a referencing one a fact. It replaces guessing from a table name.
        ("foreign_keys", """
        SELECT fk.object_id, fk.parent_object_id, fk.referenced_object_id, fk.name
        FROM sys.foreign_keys AS fk
    """),
        ("foreign_key_columns", """
        SELECT fkc.constraint_object_id, fkc.parent_object_id, fkc.parent_column_id,
               fkc.referenced_object_id, fkc.referenced_column_id
        FROM sys.foreign_key_columns AS fkc
    """),
        # Declared primary/unique key constraints.
        ("key_constraints", """
        SELECT kc.object_id, kc.parent_object_id, kc.name, kc.type
        FROM sys.key_constraints AS kc
    """),
        # IDENTITY columns - Microsoft's documented way to generate a surrogate key
        # in Fabric Warehouse: "IDENTITY columns enable automatic generation of these
        # surrogate keys when inserting new rows into a table"
        # (learn.microsoft.com/fabric/data-warehouse/identity). Reading this turns
        # "the column is named _sk, so it is probably generated" into a declared
        # fact. Warehouse only - a Lakehouse Delta table has no IDENTITY concept and
        # returns nothing, which is why this sits in the Warehouse-only batch.
        ("identity_columns", """
        SELECT ic.object_id, c.name
        FROM sys.identity_columns AS ic
        INNER JOIN sys.columns AS c
            ON ic.object_id = c.object_id AND ic.column_id = c.column_id
    """),
    )


    def _render_type(data_type: Any, max_len: Any, precision: Any, scale: Any) -> str:
        """``varchar(8000)`` / ``decimal(18,2)`` / ``int`` from INFORMATION_SCHEMA parts."""
        base = str(data_type or "").strip().lower()
        if not base:
            return ""
        if max_len is not None and base in {"varchar", "nvarchar", "char", "nchar", "binary",
                                            "varbinary"}:
            width = "max" if int(max_len) < 0 else str(int(max_len))
            return f"{base}({width})"
        if precision is not None and base in {"decimal", "numeric"}:
            return f"{base}({int(precision)},{int(scale or 0)})"
        return base



    def _classify(exc: Exception) -> str:
        """A short, actionable reason a SQL endpoint read failed.

    Driver errors are matched on their message; anything that is *not* a driver
    error is reported as an internal fault rather than being pattern-matched.
    Keyword matching on an arbitrary exception is how an ``AttributeError`` whose
    message merely contained the word "timeout" was once reported as a blocked
    port - a wrong answer that sent a diagnosis in entirely the wrong direction.
    """
        if not _is_driver_error(exc):
            return (f"internal error in the SQL reader - {type(exc).__name__}: "
                    f"{str(exc)[:160]}")
        text = str(exc).lower()
        if "system limit" in text:
            return ("the workspace has too many warehouses/SQL endpoints for one Entra "
                    f"token (Microsoft's limit is about {MAX_ENDPOINTS_PER_WORKSPACE})")
        if "login failed" in text or "cannot open" in text:
            return "the signed-in user has no access to this database"
        if "login timeout" in text or "tcp provider" in text or "network-related" in text \
                or "10060" in text:
            return "the endpoint is not reachable - port 1433 may be blocked"
        if "query timeout" in text or "timeout" in text or "timed out" in text:
            return "the endpoint accepted the connection but the query did not finish in time"
        if "driver" in text:
            return "the ODBC driver could not be loaded"
        return f"{type(exc).__name__}: {str(exc)[:160]}"


    def _is_driver_error(exc: Exception) -> bool:
        """True when the exception came from pyodbc rather than from this module."""
        try:
            import pyodbc
        except ImportError:  # pragma: no cover - only reachable without pyodbc
            return False
        return isinstance(exc, pyodbc.Error)

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\discovery\scanner.py ---
def _collector_build_discovery_scanner(_collector_modules):
    import logging

    import time

    from typing import Any

    log = logging.getLogger("auditfast.scanner")


    _BASE = "https://api.powerbi.com/v1.0/myorg/admin"

    _SCAN_PARAMS = (
        "?lineage=true&datasourceDetails=true"
        "&datasetSchema=true&datasetExpressions=true&getArtifactUsers=true"
    )



    class ScannerApiClient:
        """Drives the async Scanner API scan for a set of workspaces."""

        def __init__(self, admin_token: str, timeout: int = 100,
                     poll_interval: float = 1.0, max_polls: int = 60):
            requests = _collector_import_dependency('requests')

            self._session = requests.Session()
            self._session.headers.update({"Authorization": f"Bearer {admin_token}"})
            self._timeout = timeout
            self._poll_interval = poll_interval
            self._max_polls = max_polls

        def _json(self, response) -> Any:
            try:
                return response.json()
            except ValueError:
                return None

        def scan(self, workspace_ids: list[str]) -> dict:
            """Run getInfo -> poll scanStatus -> return scanResult for the workspaces."""
            started = self._session.post(
                f"{_BASE}/workspaces/getInfo{_SCAN_PARAMS}",
                json={"workspaces": workspace_ids}, timeout=self._timeout,
            )
            if started.status_code not in (200, 202):
                log.warning("Scanner getInfo -> HTTP %s", started.status_code)
                return {}
            scan_id = (self._json(started) or {}).get("id")
            if not scan_id:
                return {}
            for _ in range(self._max_polls):
                time.sleep(self._poll_interval)
                status = self._session.get(
                    f"{_BASE}/workspaces/scanStatus/{scan_id}", timeout=self._timeout)
                state = str((self._json(status) or {}).get("status", "")).lower()
                if state == "succeeded":
                    result = self._session.get(
                        f"{_BASE}/workspaces/scanResult/{scan_id}", timeout=self._timeout)
                    return self._json(result) or {}
                if state in ("failed", "error"):
                    log.warning("Scanner scan %s failed", scan_id)
                    return {}
            log.warning("Scanner scan %s timed out", scan_id)
            return {}


    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\powerbi.py ---
def _collector_build_clients_powerbi(_collector_modules):
    """Read-only Power BI REST client — the transport behind the FabricIQ tools.

This is the data-plane counterpart to :class:`~auditfast.clients.live.LiveFabricProvider`.
Where that provider reads Fabric *control-plane* metadata, this client reads
Power BI *artifacts* (workspaces, semantic models, reports) and runs read-only
DAX through the ``executeQueries`` endpoint.

Two things are easy to get wrong and are handled deliberately here:

1. **Token audience.** These endpoints require a bearer token for
   ``https://analysis.windows.net/powerbi/api`` — *not* the Fabric API audience
   the auditor's own token carries. The caller supplies it; see
   :mod:`auditfast.services.fabriciq_service`.
2. **Read-only despite a POST.** ``executeQueries`` is an HTTP POST, but it only
   ever runs DAX ``EVALUATE`` statements; the Power BI service rejects anything
   that would mutate a model. Nothing in this client writes.
"""
    pass  # annotations are enabled once, at the top of the script

    import logging
    import time
    from datetime import datetime, timedelta, timezone
    from typing import Any
    from urllib.parse import quote, urlsplit

    ProviderError = _collector_modules['clients.errors'].ProviderError

    log = logging.getLogger("auditfast.powerbi")


    def _retry_after_seconds(retry_after: str | None, attempt: int, cap: float = 30.0) -> float:
        """Seconds to wait before a retry, honoring the server's ``Retry-After``.

    Falls back to exponential backoff when the header is absent, and never waits
    longer than ``cap`` so a hostile value cannot stall the crawl.
    """
        try:
            if retry_after is not None:
                return min(float(retry_after), cap)
        except (TypeError, ValueError):
            pass
        return min(2.0 ** attempt, cap)


    class PowerBIError(ProviderError):
        """A Power BI REST call failed in a way the caller must surface, not swallow.

    Carries the HTTP ``status`` and any Power BI error ``code`` so a tool can
    turn it into a structured, actionable message instead of a bare stack trace.
    """

        def __init__(self, message: str, status: int | None = None, code: str | None = None):
            self.status = status
            self.code = code
            super().__init__(message)


    class PowerBIClient:
        """Reads Power BI artifacts and runs read-only DAX with a delegated token."""

        BASE = "https://api.powerbi.com/v1.0/myorg"

        def __init__(self, token: str, timeout: int = 60):
            requests = _collector_import_dependency('requests')

            self._session = requests.Session()
            self._session.headers.update(
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            self._timeout = timeout
            self._groups_cache: list[dict] | None = None

        # -- transport -------------------------------------------------------------
        def _get(self, path: str) -> tuple[int | None, Any]:
            """GET a path, returning ``(status, body)``. ``None`` status = transport failure."""
            status, body, _retry_after = self._get_with_meta(path)
            return status, body

        def _get_with_meta(self, path: str) -> tuple[int | None, Any, str | None]:
            """GET a path, returning ``(status, body, retry_after)`` for throttle-aware retries."""
            try:
                response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
            except Exception as exc:  # network/DNS/TLS — treat as unknown, never as empty
                log.warning("GET %s transport error: %s", path, exc)
                return None, None, None
            retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
            try:
                return response.status_code, response.json(), retry_after
            except ValueError:
                return response.status_code, None, retry_after

        def _post(self, path: str, payload: dict) -> tuple[int | None, Any]:
            """POST JSON to a path, returning ``(status, body)``."""
            try:
                response = self._session.post(
                    f"{self.BASE}{path}", json=payload, timeout=self._timeout
                )
            except Exception as exc:
                log.warning("POST %s transport error: %s", path, exc)
                return None, None
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, None

        def _values(self, path: str) -> list[dict]:
            """GET a Power BI collection endpoint and return its ``value`` array."""
            return self._values_known(path)[0]

        def _values_known(self, path: str) -> tuple[list[dict], bool]:
            """GET a collection endpoint, returning ``(rows, readable)``.

        ``readable`` is what tells an *empty workspace* apart from a *forbidden
        or failed* listing. :meth:`_values` throws that distinction away, which is
        fine for the FabricIQ tools but not for an auditor: a check must report
        N/A when the listing could not be read and a real finding when the
        workspace genuinely holds none.
        """
            status, body = self._get(path)
            if status != 200 or not isinstance(body, dict):
                return [], False
            return list(body.get("value") or []), True

        # -- workspaces / items ----------------------------------------------------
        def list_groups(self) -> list[dict]:
            """List every workspace (group) the token can see, cached per client."""
            if self._groups_cache is None:
                self._groups_cache = self._values("/groups?$top=5000")
            return self._groups_cache

        def list_datasets(self, group_id: str) -> list[dict]:
            """List the semantic models (datasets) in one workspace."""
            return self._values(f"/groups/{group_id}/datasets")

        def list_reports(self, group_id: str) -> list[dict]:
            """List the reports in one workspace."""
            return self._values(f"/groups/{group_id}/reports")

        def list_reports_known(self, group_id: str) -> tuple[list[dict], bool]:
            """List the reports in one workspace, plus whether the listing was readable.

        Same call as :meth:`list_reports`; the second element distinguishes "this
        workspace has no report" from "the report list could not be read", which
        is what lets the report-reuse checks report N/A instead of a false
        finding. Each row carries ``datasetId`` — the report's semantic-model
        binding — except for paginated (RDL) reports, which bind to none.

        The personal **"My workspace"** is not a group, so ``/groups/{id}/reports``
        fails for it; when — and only when — ``group_id`` is not a group the token
        can see, its reports are read from the ``myorg`` root ``/reports`` instead.
        A *named* workspace whose group listing fails (permission/transient) is
        left unreadable, never silently swapped for the personal one.
        """
            rows, readable = self._values_known(f"/groups/{group_id}/reports")
            if readable:
                return rows, readable
            if group_id not in {g.get("id") for g in self.list_groups()}:
                return self._values_known("/reports")
            return rows, readable

        def get_report(self, report_id: str, group_id: str | None = None) -> dict | None:
            """Fetch one report's properties, or ``None`` if it is not accessible."""
            path = (
                f"/groups/{group_id}/reports/{report_id}"
                if group_id
                else f"/reports/{report_id}"
            )
            status, body = self._get(path)
            return body if status == 200 and isinstance(body, dict) else None

        def get_report_pages(self, report_id: str, group_id: str | None = None) -> list[dict]:
            """List a report's pages (name/displayName/order); [] if unavailable."""
            path = (
                f"/groups/{group_id}/reports/{report_id}/pages"
                if group_id
                else f"/reports/{report_id}/pages"
            )
            return self._values(path)

        def get_dataset(self, dataset_id: str, group_id: str | None = None) -> dict | None:
            """Fetch one semantic model's properties, or ``None`` if inaccessible."""
            path = (
                f"/groups/{group_id}/datasets/{dataset_id}"
                if group_id
                else f"/datasets/{dataset_id}"
            )
            status, body = self._get(path)
            return body if status == 200 and isinstance(body, dict) else None

        def dataset_last_refresh(
            self, dataset_id: str, group_id: str | None = None
        ) -> tuple[str | None, str]:
            """Return a semantic model's last refresh time and a failure classifier.

        Reads the Power BI refresh history (returned latest-first) and yields
        ``(timestamp, failure)`` where ``timestamp`` is the top entry's
        ``endTime`` (or ``startTime`` while a refresh is running). ``failure``
        follows the crawl convention: ``""`` read (``timestamp`` may still be
        ``None`` for a model that has genuinely never been refreshed);
        ``"forbidden"`` a 401/403 (wrong-audience or unlicensed token);
        ``"transient"`` a 404-on-every-form / throttling / 5xx / transport error.

        The workspace-scoped form is tried first; a personal ("My workspace")
        model 404s there, so the no-group form is used as a fallback. Keeping
        "could not read" distinct from "never refreshed" is what lets the caller
        report N/A instead of silently dropping the model.
        """
            paths = []
            if group_id:
                paths.append(f"/groups/{group_id}/datasets/{dataset_id}/refreshes?$top=1")
            paths.append(f"/datasets/{dataset_id}/refreshes?$top=1")
            failure = "transient"
            for path in paths:
                status, body = self._get(path)
                if status == 200 and isinstance(body, dict):
                    rows = body.get("value") or []
                    if rows and isinstance(rows[0], dict):
                        return rows[0].get("endTime") or rows[0].get("startTime"), ""
                    return None, ""  # 200-empty: never refreshed — a real, readable answer
                if status in (401, 403):
                    failure = "forbidden"  # token rejected; the other form will fare no better
            return None, failure

        def dataset_created_dates(self, group_id: str | None = None) -> dict[str, str]:
            """Map ``dataset id -> createdDate`` for a workspace's semantic models.

        ``GET /groups/{id}/datasets`` returns every model's ISO-8601
        ``createdDate`` in a single call — no admin or capacity needed, and it is
        present even for models that have never been refreshed. A personal
        ("My workspace") id 401s on the group form, so the no-group ``/datasets``
        form is used as a fallback. Returns ``{}`` when nothing could be read.
        """
            rows = self._values(f"/groups/{group_id}/datasets") if group_id else []
            if not rows:
                rows = self._values("/datasets")
            return {
                row["id"]: row["createdDate"]
                for row in rows
                if isinstance(row, dict) and row.get("id") and row.get("createdDate")
            }

        def dataset_refresh_schedule(
            self, dataset_id: str, group_id: str | None = None
        ) -> tuple[dict | None, str]:
            """Return a semantic model's *refresh schedule configuration* and a failure classifier.

        ``GET /groups/{gid}/datasets/{did}/refreshSchedule`` is an ordinary
        delegated read (``Dataset.Read.All``) — **no tenant-admin scope**, and the
        same shape and audience as :meth:`dataset_last_refresh`, which this client
        already calls. It returns the configured days/times, whether the schedule
        is enabled, and ``notifyOption``: ``MailOnFailure`` (the model's
        contacts/owner are mailed when a refresh fails) or ``NoNotification``.

        Returns ``(schedule, failure)`` following the crawl convention: ``""``
        read; ``"forbidden"`` a 401/403 (wrong-audience or unlicensed token);
        ``"transient"`` throttling / 5xx / transport error. A **404 is not a
        failure** — it is how the API says "this model has no refresh schedule"
        (a Direct Lake or push model), so it yields ``(None, "")`` and the caller
        records it as *no schedule* rather than as unreadable.

        A **transport failure is transient, not forbidden**, and the *last*
        attempt decides. A DNS blip or a reset connection mid-crawl looks nothing
        like a permission denial, but recording it as ``forbidden`` tells the
        knowledge base "this will never work with this token" and stops the next
        crawl from retrying. On a real crawl 7 DNS failures and 1 connection
        reset were filed as 410 forbidden reads for exactly this reason.

        Only the schedule *configuration* is read. No refresh rows, no history.
        """
            paths = []
            if group_id:
                paths.append(f"/groups/{group_id}/datasets/{dataset_id}/refreshSchedule")
            paths.append(f"/datasets/{dataset_id}/refreshSchedule")
            failure = "transient"
            reason = "no attempt completed"
            for path in paths:
                for attempt in range(3):
                    status, body, retry_after = self._get_with_meta(path)
                    if status == 200 and isinstance(body, dict):
                        return _refresh_schedule(body), ""
                    if status in (400, 404):
                        return None, ""  # no schedule configured for this model - a real answer
                    if status is None:
                        # Transport failure: DNS, reset, timeout. Retryable, and it says
                        # nothing about whether the token would have been accepted.
                        failure, reason = "transient", "transport error (DNS/reset/timeout)"
                    elif status in (401, 403):
                        failure, reason = "forbidden", f"HTTP {status}"
                        break
                    elif status == 429 or (status is not None and status >= 500):
                        failure, reason = "transient", f"HTTP {status}"
                    else:
                        failure, reason = "transient", f"HTTP {status}"
                        break
                    if attempt < 2:
                        time.sleep(_retry_after_seconds(retry_after, attempt))
            log.info("dataset %s refresh schedule unread (%s): %s",
                     dataset_id, failure, reason)
            return None, failure

        def execute_queries(
            self, dataset_id: str, dax_queries: list[str], group_id: str | None = None
        ) -> dict:
            """Run 1..N read-only DAX ``EVALUATE`` queries against a semantic model.

        Returns the raw Power BI ``executeQueries`` body
        (``{"results": [{"tables": [{"rows": [...]}]}]}``). Raises
        :class:`PowerBIError` on any non-200, with the service's own error code
        and message attached.
        """
            payload = {
                "queries": [{"query": q} for q in dax_queries],
                "serializerSettings": {"includeNulls": True},
            }
            path = (
                f"/groups/{group_id}/datasets/{dataset_id}/executeQueries"
                if group_id
                else f"/datasets/{dataset_id}/executeQueries"
            )
            status, body = self._post(path, payload)
            if status == 200 and isinstance(body, dict):
                return body
            raise PowerBIError(*_error_detail(status, body))

        # -- elevated audit reads -------------------------------------------------
        def tenant_settings(self) -> tuple[list[dict], bool]:
            """Return tenant settings, preserving unreadable versus genuinely empty."""
            status, body = self._get("/admin/tenantsettings")
            if status != 200 or not isinstance(body, dict):
                return [], False
            rows = body.get("tenantSettings", body.get("value", [])) or []
            return [row for row in rows if isinstance(row, dict)], True

        def activity_events(self, days_back: int = 14) -> tuple[list[dict], bool]:
            """Read a bounded activity-log window one UTC day at a time."""
            rows: list[dict] = []
            today = datetime.now(timezone.utc).date()
            for offset in range(max(1, min(int(days_back), 28))):
                day = today - timedelta(days=offset + 1)
                start = f"{day.isoformat()}T00:00:00.000Z"
                end = f"{day.isoformat()}T23:59:59.999Z"
                path = ("/admin/activityevents?startDateTime=" + quote(f"'{start}'")
                        + "&endDateTime=" + quote(f"'{end}'"))
                seen: set[str] = set()
                while path and path not in seen:
                    seen.add(path)
                    status, body = self._get(path)
                    if status != 200 or not isinstance(body, dict):
                        return rows, False
                    rows.extend(row for row in body.get("activityEventEntities", [])
                                if isinstance(row, dict))
                    continuation = body.get("continuationUri")
                    if not continuation:
                        break
                    parsed = urlsplit(str(continuation))
                    base_path = urlsplit(self.BASE).path
                    relative_path = parsed.path.removeprefix(base_path)
                    path = relative_path + (f"?{parsed.query}" if parsed.query else "")
            return rows, True

        def admin_scan(self, workspace_id: str) -> tuple[dict, bool]:
            """Run the existing read-only Scanner API client for one workspace."""
            ScannerApiClient = _collector_modules['discovery.scanner'].ScannerApiClient

            token = self._session.headers.get("Authorization", "").removeprefix("Bearer ")
            payload = ScannerApiClient(token, timeout=self._timeout).scan([workspace_id])
            return payload, bool(payload)

        def capacity_metrics(self, preferred_workspace: str = "",
                             model_name_contains: str = "Capacity Metrics",
                             peak_start_hour: int = 8,
                             peak_end_hour: int = 18) -> tuple[dict, bool]:
            """Find and query the Capacity Metrics model, adapting to its schema.

        ``preferred_workspace`` is the reviewer's answer to "which workspace holds
        the app?" — a name or an id. It is searched first, because the app can be
        installed anywhere and a tenant with many workspaces would otherwise be
        walked in list order until it happens to turn up.

        ``model_name_contains`` is how the app is *recognised*, matched as a
        case-insensitive substring of the semantic model's name. Unlike the
        workspace hint this is a **filter, not an ordering**: the stock app,
        FUAM, and a renamed install all carry different names, and a value that
        matches nothing reports "app not deployed" for an estate that monitors
        its capacity perfectly well. It is the notebook's ``MODEL_NAME_CONTAINS``.

        ``peak_start_hour``/``peak_end_hour`` bound the client's working day on a
        24-hour clock. The times in the metrics model are **UTC**, so a client
        that is not on UTC needs these shifted or the busy/quiet split for 12.1.2
        measures the wrong half of the day.
        """
            match = None
            group_id = None
            wanted = preferred_workspace.strip().lower()
            needle = model_name_contains.strip().lower() or "capacity metrics"
            groups = list(self.list_groups())
            if wanted:
                # Named workspace first; the rest still follow, so a wrong name costs
                # nothing beyond ordering.
                groups.sort(
                    key=lambda g: str(g.get("name") or "").strip().lower() != wanted
                    and str(g.get("id") or "").strip().lower() != wanted
                )
            for group in groups:
                candidate_group = str(group.get("id") or "")
                for dataset in self.list_datasets(candidate_group):
                    if needle in str(dataset.get("name") or "").lower():
                        match, group_id = dataset, candidate_group
                        break
                if match:
                    break
            if not match:
                return {"model_found": False, "model_name_searched": needle}, True

            dataset_id = str(match.get("id") or "")
            try:
                schema_body = self.execute_queries(dataset_id, ["EVALUATE INFO.VIEW.COLUMNS()"], group_id)
            except PowerBIError:
                return {}, False
            columns: dict[str, list[str]] = {}
            for row in _query_rows(schema_body):
                table = str(_row_value(row, "table") or "")
                column = str(_row_value(row, "name", "column") or "")
                if table and column:
                    columns.setdefault(table, []).append(column)

            fact = _capacity_fact(columns)
            overload = _overload_fact(columns)
            result: dict[str, Any] = {
                "model_found": True,
                "model_id": dataset_id,
                "model_workspace_id": group_id,
                "peak_split": None,
                "top_consumers": [],
                "throttling_readable": overload is not None,
                "throttling_events": 0,
                "query_load": [],
            }
            if fact:
                table, cu, when, operation, item, workspace = fact
                if when:
                    dax = f"EVALUATE SUMMARIZECOLUMNS({_dax_col(table, when)}, \"TotalCU\", SUM({_dax_col(table, cu)}))"
                    try:
                        timed = _query_rows(self.execute_queries(dataset_id, [dax], group_id))
                    except PowerBIError:
                        timed = []
                    peak = off_peak = 0.0
                    for row in timed:
                        hour = _hour(_row_value(row, when, "When"))
                        value = float(_row_value(row, "TotalCU") or 0)
                        if hour is not None and _in_working_day(hour, peak_start_hour, peak_end_hour):
                            peak += value
                        else:
                            off_peak += value
                    if timed:
                        result["peak_split"] = {
                            "peak": peak, "off_peak": off_peak,
                            "window": f"{peak_start_hour:02d}:00-{peak_end_hour:02d}:00 UTC",
                        }
                keys = [name for name in (workspace, item, operation) if name]
                if keys:
                    grouping = ", ".join(_dax_col(table, name) for name in dict.fromkeys(keys))
                    dax = f"EVALUATE TOPN(20, SUMMARIZECOLUMNS({grouping}, \"TotalCU\", SUM({_dax_col(table, cu)})), [TotalCU], DESC)"
                    try:
                        result["top_consumers"] = _query_rows(
                            self.execute_queries(dataset_id, [dax], group_id)
                        )
                    except PowerBIError:
                        # Top consumers are a nice-to-have: 12.2.2 reports N/A on an
                        # empty list rather than failing the whole metrics read.
                        log.debug("capacity metrics: top-consumer query refused")
                if operation:
                    dax = f"EVALUATE SUMMARIZECOLUMNS({_dax_col(table, operation)}, \"TotalCU\", SUM({_dax_col(table, cu)}))"
                    try:
                        operation_rows = _query_rows(self.execute_queries(dataset_id, [dax], group_id))
                    except PowerBIError:
                        operation_rows = []
                    words = ("sql", "warehouse", "dataset", "semantic", "model", "query", "refresh")
                    result["query_load"] = [
                        row for row in operation_rows
                        if any(word in str(_row_value(row, operation)).lower() for word in words)
                    ]
            if overload:
                table, column, when = overload
                key = _dax_col(table, when or column)
                dax = f"EVALUATE FILTER(SUMMARIZECOLUMNS({key}, \"Overload\", SUM({_dax_col(table, column)})), [Overload] > 0)"
                try:
                    result["throttling_events"] = len(_query_rows(
                        self.execute_queries(dataset_id, [dax], group_id)
                    ))
                except PowerBIError:
                    result["throttling_readable"] = False
            return result, True

        # -- resolution helpers ----------------------------------------------------
        def find_report_group(self, report_id: str) -> tuple[str | None, dict | None]:
            """Locate which workspace a report lives in by scanning accessible groups."""
            target = report_id.lower()
            for group in self.list_groups():
                gid = group.get("id")
                for report in self.list_reports(gid):
                    if str(report.get("id")).lower() == target:
                        return gid, report
            return None, None

        def find_dataset_group(self, dataset_id: str) -> tuple[str | None, dict | None]:
            """Locate which workspace a semantic model lives in by scanning groups."""
            target = dataset_id.lower()
            for group in self.list_groups():
                gid = group.get("id")
                for dataset in self.list_datasets(gid):
                    if str(dataset.get("id")).lower() == target:
                        return gid, dataset
            return None, None


    def _refresh_schedule(body: dict) -> dict:
        """Normalise a ``refreshSchedule`` payload into the shape checks read.

    The Power BI API returns the schedule either bare or wrapped in
    ``properties``/``value``, and spells the notification setting
    ``notifyOption``. Normalising here keeps every variation out of the check
    bodies, which must stay pure. ``notify_option`` is preserved verbatim so a
    future Fabric spelling is reported rather than silently mapped to "off".
    """
        raw = body.get("properties") if isinstance(body.get("properties"), dict) else body
        days = [str(d) for d in (raw.get("days") or []) if str(d).strip()]
        times = [str(t) for t in (raw.get("times") or []) if str(t).strip()]
        notify = str(raw.get("notifyOption") or raw.get("notifyOptions") or "").strip()
        return {
            "enabled": bool(raw.get("enabled", False)),
            "notify_option": notify,
            # A refresh failure reaches a human. Anything other than an explicit
            # "no notification" counts, so a new Fabric spelling is credited rather
            # than read as silence; a blank value means the API did not say.
            "notifies_on_failure": bool(notify) and notify.lower() != "nonotification",
            "days": days,
            "times": times,
            "local_time_zone_id": str(raw.get("localTimeZoneId") or ""),
        }


    def _query_rows(body: dict) -> list[dict]:
        results = body.get("results") or []
        tables = results[0].get("tables", []) if results else []
        return [row for row in (tables[0].get("rows", []) if tables else []) if isinstance(row, dict)]


    def _row_value(row: dict, *hints: str):
        for hint in hints:
            target = hint.lower()
            for key, value in row.items():
                leaf = str(key).rsplit("[", 1)[-1].rstrip("]").lower()
                if target == leaf or target in leaf:
                    return value
        return None


    def _pick(columns: list[str], *words: str) -> str | None:
        return next((column for word in words for column in columns
                     if word in column.lower()), None)


    def _capacity_fact(schema: dict[str, list[str]]):
        for table, columns in schema.items():
            cu = _pick(columns, "cu (s)", "cu(s)", "cu ", "capacity unit")
            if cu:
                return (table, cu, _pick(columns, "hour", "date", "day", "time"),
                        _pick(columns, "operation"), _pick(columns, "item"),
                        _pick(columns, "workspace"))
        return None


    def _overload_fact(schema: dict[str, list[str]]):
        for table, columns in schema.items():
            signal = _pick(columns, "throttl", "overload", "burndown", "rejection", "delay")
            if signal:
                return table, signal, _pick(columns, "hour", "date", "day", "time")
        return None


    def _dax_col(table: str, column: str) -> str:
        return "'" + table.replace("'", "''") + "'[" + column.replace("]", "]]" ) + "]"


    def _hour(value) -> int | None:
        if isinstance(value, (int, float)) and 0 <= int(value) <= 23:
            return int(value)
        try:
            return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).hour
        except ValueError:
            return None


    def _in_working_day(hour: int, start: int, end: int) -> bool:
        """Is ``hour`` inside the ``[start, end)`` working day, in UTC?

    The window may wrap past midnight. Shifting a local working day into UTC is
    exactly how that happens — a 09:00-18:00 day in UTC+10 is 23:00-08:00 UTC —
    so a naive ``start <= hour < end`` would call the client's whole working day
    "off-peak" and invert the 12.1.2 verdict.
    """
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end


    def _error_detail(status: int | None, body: Any) -> tuple[str, int | None, str | None]:
        """Turn a failed Power BI response into ``(message, status, code)``."""
        code: str | None = None
        message = f"Power BI request failed (HTTP {status})."
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message") or message
                details = error.get("pbi.error") or {}
                if isinstance(details, dict) and details.get("details"):
                    extra = "; ".join(
                        str(d.get("detail", {}).get("value", ""))
                        for d in details["details"]
                        if isinstance(d, dict)
                    ).strip("; ")
                    if extra:
                        message = f"{message} — {extra}"
        if status == 401:
            message = (
                "Power BI rejected the token (HTTP 401). It must be issued for the "
                "audience https://analysis.windows.net/powerbi/api, not the Fabric API. "
                + message
            )
        return message, status, code

    return _collector_namespace(locals())

# --- Production source: backend\src\auditfast\clients\live.py ---
def _collector_build_clients_live(_collector_modules):
    """Read-only Fabric REST provider.

Every call is a GET, except the read-only ``getDefinition`` POST. Nothing here
ever writes.

Two behaviours matter and are easy to get wrong:

1. **The workspace itself is read first, and its HTTP status is checked.** A
   403 must raise, not yield an empty context — otherwise an inaccessible
   workspace scores zeros and looks like a badly configured one.
2. **A failed sub-resource call is recorded as *unknown*, not as *absent*.**
   ``git/connection`` returning a network error does not mean "Git is not
   connected"; the affected checks report N/A instead of failing.
"""
    pass  # annotations are enabled once, at the top of the script

    import base64
    import contextlib
    import json
    import logging
    import os
    import re
    import threading
    import time
    from collections import Counter
    from collections.abc import Iterable, Sequence
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from dataclasses import replace
    from typing import Any

    yaml = _collector_LazyModule('yaml')

    Layer = _collector_modules['core.enums'].Layer
    Resource = _collector_modules['core.enums'].Resource
    Item = _collector_modules['core.models'].Item
    RoleAssignment = _collector_modules['core.models'].RoleAssignment
    WorkspaceContext = _collector_modules['core.models'].WorkspaceContext
    ALL_RESOURCES = _collector_modules['clients.base'].ALL_RESOURCES
    WorkspaceAccessError = _collector_modules['clients.errors'].WorkspaceAccessError
    parse_tmsl = _collector_modules['clients.tmsl'].parse_tmsl

    log = logging.getLogger("auditfast.live")

    #: How many job-run timestamps are retained per item for the cadence signal.
    #: Enough to establish an interval; small enough that a chatty item cannot bloat
    #: the knowledge-base snapshot.
    _MAX_RETAINED_RUNS = 25

    #: Why a semantic model's refresh schedule could not be read. Recorded on the
    #: snapshot so the gap explains itself without anyone reading a crawl log - and
    #: so a network blip is never mistaken for a permission denial.
    _SCHEDULE_FORBIDDEN_REASON = (
        "Power BI rejected the token (HTTP 401/403) - the sign-in lacks "
        "Dataset.Read.All, or the tenant setting for it is off"
    )
    _SCHEDULE_TRANSIENT_REASON = (
        "Power BI was unreachable or throttled (DNS failure, reset connection, "
        "timeout, 429 or 5xx) - retryable, and not a permission problem"
    )

    #: Schemas the platform owns. Their views and routines ship with every SQL
    #: endpoint, so counting them would report an abstraction layer nobody built.
    _PLATFORM_SCHEMAS: frozenset[str] = frozenset({
        "sys", "queryinsights", "information_schema", "guest",
        "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
    })

    #: Database principals every SQL endpoint ships with. Recording them would make
    #: an empty estate look populated, so the access checks judge only real grants.
    #: Lower-cased: SQL identifiers are case-insensitive, and the filter compares
    #: against a lower-cased name.
    _SYSTEM_PRINCIPALS: frozenset[str] = frozenset({
        "public", "dbo", "guest", "sys", "information_schema",
        "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
        "db_backupoperator", "db_datareader", "db_datawriter",
        "db_denydatareader", "db_denydatawriter",
    })


    def _bounded_int_env(name: str, default: int, low: int, high: int) -> int:
        """Read an int from the environment, clamped to ``[low, high]``."""
        try:
            return max(low, min(int(os.environ.get(name, default)), high))
        except (TypeError, ValueError):
            return default


    def _retry_after_seconds(response, attempt: int, cap: float = 30.0) -> float:
        """Seconds to wait before a retry, honoring the server's ``Retry-After``.

    Falls back to exponential backoff when the header is absent, and never waits
    longer than ``cap`` so a hostile value cannot stall the crawl.
    """
        header = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
        try:
            if header is not None:
                return min(float(header), cap)
        except (TypeError, ValueError):
            pass
        return min(2.0 * (attempt + 1), cap)


    def _failure_detail(item: Item, failure: str, reason: str = "") -> dict[str, str]:
        detail = {
            "id": item.id,
            "name": item.display_name or item.id,
            "failure": failure,
        }
        if reason:
            detail["reason"] = reason
        return detail


    #: Per-workspace fan-out: how many item definitions are fetched at once while
    #: crawling a single workspace. Each getDefinition is a slow long-running
    #: operation, so fetching them concurrently is the crawl's biggest speed-up.
    _ITEM_FETCH_WORKERS = _bounded_int_env("AUDITFAST_MAX_PARALLEL_ITEM_FETCHES", 4, 1, 16)

    #: Process-wide ceiling on concurrent item-definition calls, shared across every
    #: workspace and audit, so parallel crawls together never exceed what the Fabric
    #: APIs tolerate (beyond which throttling would erase the gain).
    _DEFINITION_GATE = threading.BoundedSemaphore(
        _bounded_int_env("AUDITFAST_MAX_INFLIGHT_ITEM_FETCHES", 32, 1, 32)
    )

    #: Resources whose reads walk the workspace's item list, so asking for any of
    #: them must also fetch ``/workspaces/{id}/items``.
    #:
    #: Getting this wrong is silent: a resource missing from here still *runs*, finds
    #: no items to iterate, and reports N/A on every workspace — which reads as "not
    #: applicable here" rather than "the crawl never looked". ``DATA_ACCESS_ROLES``
    #: is read per Lakehouse and was exactly that bug on an elevated-only run.
    _ITEM_DERIVED_RESOURCES: frozenset[Resource] = frozenset({
        Resource.ITEMS,
        Resource.PIPELINE_DEFINITIONS,
        Resource.NOTEBOOK_DEFINITIONS,
        Resource.ENVIRONMENT_DEFINITIONS,
        Resource.TABLE_SCHEMAS,
        Resource.SHORTCUTS,
        Resource.SEMANTIC_MODEL_DEFINITIONS,
        Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
        Resource.ITEM_RUN_HISTORY,
        Resource.WAREHOUSE_AUDIT,
        Resource.LAKEHOUSE_FILES,
        Resource.ACTIVATOR_DEFINITIONS,
        Resource.DATA_ACCESS_ROLES,
        # 14.3.6 excludes the semantic models Fabric creates for a Lakehouse or
        # Warehouse, which it recognises by their sharing that item's name.
        Resource.ADMIN_SCANNER,
    })


    class LiveFabricProvider:
        """Reads a live Fabric tenant with a delegated, read-only OAuth2 token."""

        BASE = "https://api.fabric.microsoft.com/v1"

        def __init__(self, token: str, timeout: int = 60, token_refresher=None,
                     powerbi_token: str | None = None, sql_token: str | None = None,
                     storage_token: str | None = None, sql_token_refresher=None):
            requests = _collector_import_dependency('requests')

            self._session = requests.Session()
            self._session.headers.update({"Authorization": f"Bearer {token}"})
            # One connection pool wide enough that parallel workspace crawls share it
            # without serialising on a too-small default (10).
            adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
            #: Guards the shared session's auth header and the lazily-built sub-clients
            #: so several workspaces can be crawled concurrently on one provider.
            self._lock = threading.Lock()
            self._timeout = timeout
            self._token_refresher = token_refresher
            #: A Power BI-audience token (``https://analysis.windows.net/powerbi/api``)
            #: — a *different* audience from the Fabric token above. It is optional and
            #: used only to read semantic-model refresh recency, which lives on the
            #: Power BI Datasets API, not the Fabric surface. Absent ⇒ that one signal
            #: is left unknown, never guessed.
            self._powerbi_token = powerbi_token
            self._powerbi_client = None
            #: A SQL-analytics-endpoint token (``https://database.windows.net``) — a
            #: third audience. Column schemas and Warehouse RLS policies exist nowhere
            #: in the Fabric REST API, only over TDS on port 1433. Absent, or the port
            #: blocked ⇒ those reads are skipped and the affected checks report N/A,
            #: exactly as they did before the endpoint was wired in.
            self._sql_token = sql_token
            #: Re-mints the SQL token when it is rejected mid-crawl. The Fabric token
            #: already has ``token_refresher`` for exactly this reason: a large
            #: workspace takes longer to crawl than an Entra token lives. Without the
            #: same treatment the SQL reads silently stop partway through.
            self._sql_token_refresher = sql_token_refresher
            self._sql_reader = None
            #: A Storage-audience token (``https://storage.azure.com``), used only for
            #: OneLake ADLS Gen2 Files listings. Without it, file-layout checks report
            #: N/A rather than treating unreadable Files sections as empty.
            self._storage_token = storage_token
            self._onelake_client = None
            #: Tenant-wide admin calls would otherwise repeat once per selected
            #: workspace. Cache their normalized read outcome for this provider.
            self._admin_cache: dict[str, tuple[Any, bool]] = {}
            #: Which workspace holds the Fabric Capacity Metrics app, as supplied by
            #: the reviewer. The app can be installed anywhere and no API reports
            #: where, so this is a hint, not a filter: it is searched first and the
            #: rest of the tenant still follows, so a wrong name costs only ordering.
            self.capacity_metrics_workspace: str = ""
            #: How the Capacity Metrics semantic model is recognised, by name. Unlike
            #: the workspace hint this is a filter: the stock app, FUAM and a renamed
            #: install carry different names, and a value matching nothing reports
            #: "not deployed" for an estate that monitors its capacity properly.
            self.capacity_metrics_model: str = "Capacity Metrics"
            #: The client's working day, on a 24-hour clock. Metrics-model times are
            #: UTC, so a client elsewhere needs these shifted or 12.1.2's busy/quiet
            #: split measures the wrong half of the day.
            self.capacity_peak_hours: tuple[int, int] = (8, 18)

        # -- transport -------------------------------------------------------------
        def _get(self, path: str) -> tuple[int | None, Any]:
            """GET a path, returning ``(status, body)``.

        A status of ``None`` means the request never completed — a transport
        failure, which callers must treat as *unknown* rather than *empty*.
        """
            try:
                response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
            except Exception:
                return None, None
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, None

        def _get_url(self, url: str) -> tuple[int | None, Any]:
            """GET an absolute URL (used to follow a pagination continuation link)."""
            try:
                response = self._session.get(url, timeout=self._timeout)
            except Exception:
                return None, None
            try:
                return response.status_code, response.json()
            except ValueError:
                return response.status_code, None

        def _values(self, path: str) -> tuple[list, bool]:
            """GET a collection endpoint, following continuation to the last page.

        Returns ``(rows, known)``. ``known`` is False only when the *first* call
        failed outright, so the caller can tell "no rows" from "could not ask".
        Fabric list endpoints page with ``continuationUri``; every page is
        gathered so a large workspace is never silently truncated.
        """
            status, body = self._get(path)
            if status != 200 or not isinstance(body, dict):
                return [], False
            rows = list(body.get("value") or [])
            next_uri = body.get("continuationUri")
            pages = 0
            while next_uri and pages < 1000:
                pages += 1
                status, body = self._get_url(next_uri)
                if status != 200 or not isinstance(body, dict):
                    break
                rows.extend(body.get("value") or [])
                next_uri = body.get("continuationUri")
            return rows, True

        @staticmethod
        def _json(response) -> Any:
            """Parse a response body as JSON, or ``None`` if it is not JSON."""
            try:
                return response.json()
            except ValueError:
                return None

        def _try_refresh_token(self) -> bool:
            """Attempt a silent token refresh. Returns True if a new token was set."""
            if not self._token_refresher:
                return False
            log.info("access token expired, attempting silent refresh")
            new_token = self._token_refresher()
            if new_token:
                with self._lock:
                    self._session.headers.update({"Authorization": f"Bearer {new_token}"})
                log.info("token refreshed, resuming crawl")
                return True
            log.warning("token refresh failed, could not acquire new token")
            return False

        def _fetch_items_parallel(self, items: list, worker):
            """Run ``worker(item)`` for every item concurrently, results in input order.

        Bounded by the process-wide definition gate so several workspaces (and
        several audits) crawling at once never exceed the Fabric APIs' tolerance.
        ``worker`` handles its own errors and returns a value; it must not raise.
        """
            results: list = [None] * len(items)
            if not items:
                return results
            max_workers = min(_ITEM_FETCH_WORKERS, len(items))

            def _run(index: int, obj):
                with _DEFINITION_GATE:
                    return index, worker(obj)

            with ThreadPoolExecutor(max_workers=max_workers,
                                    thread_name_prefix="item-fetch") as pool:
                futures = [pool.submit(_run, i, obj) for i, obj in enumerate(items)]
                for future in as_completed(futures):
                    index, value = future.result()
                    results[index] = value
            return results

        #: getDefinition statuses worth retrying — transient throttling / 5xx.
        _RETRYABLE = frozenset({429, 500, 502, 503, 504})

        #: Item types that expose a run/refresh history: pipelines, notebooks,
        #: dataflows and Spark jobs via the Fabric job scheduler, and semantic models
        #: via the Power BI refresh history (a different API). Types that never run
        #: (reports, dashboards, lakehouses, warehouses, …) are skipped rather than
        #: probed for nothing.
        _JOB_ITEM_TYPES = frozenset({
            "Notebook", "DataPipeline", "SparkJobDefinition", "SemanticModel",
            "Dataflow",
        })

        def _definition_parts(
            self, workspace_id: str, item_id: str, fmt: str | None = None
        ) -> tuple[list[dict], str]:
            """Read an item's definition parts via the read-only getDefinition LRO.

        Returns ``(parts, failure)`` where ``failure`` is:

        * ``""`` — the read completed (``parts`` may still be empty for a
          genuinely empty item);
        * ``"forbidden"`` — a 403 permission denial (Fabric gates
          getDefinition behind the Item.ReadWrite scope). It will not recover
          without a different token, so the caller records a known gap;
        * ``"transient"`` — a throttling/5xx/timeout/transport error that
          survived retries. It *may* recover, so a partial crawl carrying one
          must be re-fetched rather than cached.

        A 401 (token expired) is distinguished from 403 (permission denied):
        on 401 the provider attempts a silent token refresh and retries the call.

        getDefinition is a long-running operation: 200 with the body inline for
        small items, or 202 with a ``Location`` to poll until it completes.
        """
            url = f"{self.BASE}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
            if fmt:
                url += f"?format={fmt}"
            for attempt in range(3):
                try:
                    response = self._session.post(url, timeout=self._timeout)
                except Exception as exc:
                    if attempt < 2:
                        time.sleep(_retry_after_seconds(None, attempt))
                        continue
                    log.warning("item %s getDefinition transport error: %s", item_id, exc)
                    return [], "transient"
                status = response.status_code
                if status == 200:
                    body = self._json(response)
                elif status == 202:
                    body = self._await_operation(response)
                    if body is None:
                        return [], "transient"  # the LRO failed or timed out
                elif status == 401:
                    # Token expired — attempt silent refresh and retry this item
                    if self._try_refresh_token():
                        continue
                    log.warning("item %s getDefinition -> HTTP 401 (token expired, refresh failed)", item_id)
                    return [], "forbidden"
                elif status == 403:
                    log.warning("item %s getDefinition -> HTTP 403 (permission denied)", item_id)
                    return [], "forbidden"
                elif status in self._RETRYABLE and attempt < 2:
                    time.sleep(_retry_after_seconds(response, attempt))
                    continue
                else:
                    log.warning("item %s getDefinition -> HTTP %s", item_id, status)
                    return [], "transient"
                parts = ((body or {}).get("definition") or {}).get("parts") or []
                if not parts:
                    log.warning("item %s getDefinition returned no definition parts", item_id)
                return parts, ""
            return [], "transient"

        def _await_operation(self, response) -> Any:
            """Poll a 202 long-running operation to completion, returning its result body."""
            location = response.headers.get("Location")
            if not location:
                log.warning("getDefinition 202 without a Location header")
                return None
            delay = 1.0
            with contextlib.suppress(ValueError):
                delay = min(float(response.headers.get("Retry-After") or 1.0), 10.0)
            deadline = time.monotonic() + self._timeout
            while time.monotonic() < deadline:
                time.sleep(min(delay, 5.0))
                try:
                    op = self._session.get(location, timeout=self._timeout)
                except Exception:
                    return None
                state = str((self._json(op) or {}).get("status", "")).lower()
                if state == "succeeded":
                    try:
                        result = self._session.get(
                            location.rstrip("/") + "/result", timeout=self._timeout
                        )
                    except Exception:
                        return None
                    return self._json(result)
                if state == "failed":
                    log.warning("getDefinition operation failed: %s", location)
                    return None
            log.warning("getDefinition operation timed out: %s", location)
            return None

        def _pipeline_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Read a pipeline's content (getDefinition) as parsed JSON.

        Returns ``(definition, failure)`` — see :meth:`_definition_parts`.
        """
            parts, failure = self._definition_parts(workspace_id, item_id)
            for part in parts:
                if part.get("path", "").endswith(("pipeline-content.json", "pipelineContent.json")):
                    try:
                        payload = base64.b64decode(part["payload"]).decode("utf-8")
                        return json.loads(payload), ""
                    except Exception:
                        return None, ""
            return None, failure

        def _notebook_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Read a notebook's content (getDefinition) as an .ipynb dict.

        Returns ``(definition, failure)`` — see :meth:`_definition_parts`.
        """
            parts, failure = self._definition_parts(workspace_id, item_id, fmt="ipynb")
            for part in parts:
                path = part.get("path", "")
                if not path.endswith((".ipynb", "notebook-content.py")):
                    continue
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                except Exception:
                    return None, ""
                if path.endswith(".ipynb"):
                    try:
                        return json.loads(payload), ""
                    except Exception:
                        return None, ""
                # A .py export: wrap the raw source as a single code cell.
                return {"cells": [{"cell_type": "code", "source": payload}]}, ""
            return None, failure

        def _spark_settings(self, workspace_id: str) -> dict:
            """The workspace's default Spark runtime, from the Spark settings API.

        ``GET /workspaces/{id}/spark/settings`` returns
        ``environment.runtimeVersion`` - the runtime a notebook inherits when it
        binds to no named Environment, which is the commonest setup. Documented
        as an ordinary delegated read (Viewer is enough), not tenant-admin.

        Only the runtime and the default-environment name are kept: pool sizes
        and session timeouts are settings no check reads, and the KB must not
        grow with data nobody uses. A failure yields ``{}`` so the runtime check
        falls back to its other evidence rather than raising.
        """
            status, body = self._get(f"/workspaces/{workspace_id}/spark/settings")
            if status != 200 or not isinstance(body, dict):
                log.info("workspace spark settings unavailable for %s (status %s) - "
                         "notebooks with no bound Environment will have no runtime",
                         workspace_id, status)
                return {}
            environment = body.get("environment") or {}
            settings = {
                "runtime_version": str(environment.get("runtimeVersion") or ""),
                "default_environment": str(environment.get("name") or ""),
            }
            return settings if settings["runtime_version"] else {}

        def _environment_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Read an Environment's Sparkcompute settings from its definition."""
            parts, failure = self._definition_parts(workspace_id, item_id)
            result: dict[str, Any] = {}
            for part in parts:
                if not str(part.get("path") or "").lower().endswith("sparkcompute.yml"):
                    continue
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    document = yaml.safe_load(payload) or {}
                except (KeyError, ValueError, yaml.YAMLError):
                    return None, ""
                if isinstance(document, dict):
                    result.update(document)
            return (result or None), failure

        @staticmethod
        def _environment_binding(definition: dict) -> str:
            """Find an Environment binding across known notebook metadata shapes."""
            found: list[str] = []

            def visit(value: Any) -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        if (
                            str(key).lower() in {"environmentartifactid", "environmentid", "environment_id"}
                            and isinstance(child, str)
                            and child.strip()
                        ):
                            found.append(child.strip())
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(definition or {})
            return found[0] if found else ""

        @staticmethod
        def _first_identifier(row: dict, *names: str) -> str:
            """Return the first non-empty identifier across Fabric response variants."""
            for name in names:
                value = row.get(name)
                if value is not None and str(value).strip():
                    return str(value)
            return ""

        def _notebook_monitoring(self, workspace_id: str, item_id: str) -> dict:
            """Read latest Spark session metrics for a notebook, or return no evidence.

        Monitoring is best-effort enrichment of ``NOTEBOOK_DEFINITIONS``. A
        missing session, unsupported endpoint, or permission denial remains an
        empty dict so checks report N/A rather than turning an observability gap
        into a configuration failure.
        """
            base = f"/workspaces/{workspace_id}/notebooks/{item_id}/livySessions"
            status, body = self._get(base)
            if status != 200 or not isinstance(body, dict):
                return {}
            sessions = body.get("value") or body.get("data") or []
            if not isinstance(sessions, list) or not sessions:
                return {}
            session = sessions[0] if isinstance(sessions[0], dict) else {}
            livy_id = self._first_identifier(session, "livyId", "id", "sessionId")
            if not livy_id:
                return {}

            detail_status, detail = self._get(f"{base}/{livy_id}")
            if detail_status != 200 or not isinstance(detail, dict):
                detail = session
            app_id = self._first_identifier(
                detail, "appId", "applicationId", "sparkApplicationId"
            ) or self._first_identifier(session, "appId", "applicationId", "sparkApplicationId")

            monitoring: dict[str, Any] = {
                "livy_id": livy_id,
                "app_id": app_id,
                "session": detail,
            }
            if not app_id:
                return monitoring

            app_base = f"{base}/{livy_id}/applications/{app_id}"
            for key, suffix in (
                ("advice", "advice"),
                ("resource_usage", "resourceUsage"),
                ("stages", "stages"),
            ):
                metric_status, metric = self._get(f"{app_base}/{suffix}")
                if metric_status == 200 and metric is not None:
                    monitoring[key] = metric
            return monitoring

        def _read_sql_endpoints(self, ctx: WorkspaceContext, workspace_id: str,
                                wanted: set) -> None:
            """Enrich the context with column schemas and Warehouse RLS over TDS.

        Discovery is plain Fabric REST, so nothing is ever asked of the user - the
        connection string comes from the lakehouse/warehouse item itself. The TDS
        reads are strictly best-effort: no token, no ODBC driver, a blocked port
        1433 or a throttled endpoint all leave the data absent and mark the
        resource unavailable, so the affected checks report **N/A with a reason**
        rather than failing a workspace for something we could not look at.
        """
            MAX_ENDPOINTS_PER_WORKSPACE = _collector_modules['clients.sqlendpoint'].MAX_ENDPOINTS_PER_WORKSPACE
            SqlEndpointReader = _collector_modules['clients.sqlendpoint'].SqlEndpointReader
            discover_endpoints = _collector_modules['clients.sqlendpoint'].discover_endpoints

            want_columns = Resource.TABLE_COLUMNS in wanted
            want_security = Resource.WAREHOUSE_SECURITY in wanted

            def get_json(path: str):
                status, body = self._get(path)
                return body if status == 200 and isinstance(body, dict) else {}

            endpoints = discover_endpoints(get_json, workspace_id)
            if not endpoints:
                # A workspace with no Lakehouse/Warehouse never had a SQL endpoint to
                # read, so this is not a limitation to report — mark the resources N/A
                # silently, exactly as before the SQL endpoint was wired in.
                has_store = any(
                    (item.type or "") in ("Lakehouse", "Warehouse") for item in ctx.items
                )
                if not has_store:
                    for resource in (Resource.TABLE_COLUMNS, Resource.WAREHOUSE_SECURITY):
                        if resource in wanted:
                            ctx.unavailable.add(resource)
                    log.info("fetch %s: no Lakehouse/Warehouse — SQL endpoint reads skipped",
                             workspace_id)
                    return
                reason = ("no provisioned SQL analytics endpoint was discovered in this "
                          "workspace (a newly created Lakehouse/Warehouse provisions one "
                          "asynchronously, and a paused capacity serves none)")
                self._record_environment_gap(ctx, wanted, reason, len(endpoints))
                log.info("fetch %s: no provisioned SQL endpoints discovered", workspace_id)
                return

            reader = SqlEndpointReader(self._sql_token,
                                       token_provider=self._sql_token_refresher)
            if not reader.available:
                # The reason lives on the reader, and used to reach the log only - so
                # the single most common cause of "no Lakehouse columns" (a server with
                # no ODBC driver) produced a snapshot that could not explain itself.
                # Persisting it means the report says *why*, with no log to scrape.
                self._record_environment_gap(
                    ctx, wanted, reader.unavailable_reason, len(endpoints))
                log.warning("fetch %s: SQL endpoint unavailable - %s",
                            workspace_id, reader.unavailable_reason)
                return

            # Beyond Microsoft's per-workspace guidance the Entra token can exceed its
            # size limit. Read what we can rather than losing the workspace entirely.
            if len(endpoints) > MAX_ENDPOINTS_PER_WORKSPACE:
                log.warning("fetch %s: %d SQL endpoints exceeds the ~%d guidance; "
                            "reads may fail on the Entra token size limit",
                            workspace_id, len(endpoints), MAX_ENDPOINTS_PER_WORKSPACE)

            col_attempted = col_read = 0
            sec_attempted = sec_read = 0
            collisions = 0
            for endpoint in endpoints:
                if want_columns:
                    col_attempted += 1
                    tables = reader.columns(endpoint)
                    if tables is not None:
                        col_read += 1
                        for table_name, cols in tables.items():
                            existing = ctx.tables.get(table_name)
                            if existing is not None and existing.get("store"):
                                # Two stores hold a table of the same name. The flat
                                # key cannot represent both, so the second is filed
                                # under "<store>.<table>" and the first keeps the bare
                                # name that the REST listing established.
                                collisions += 1
                                ctx.tables[f"{endpoint.name}.{table_name}"] = {
                                    "type": "Managed", "format": "", "columns": cols,
                                    "store": endpoint.name, "store_kind": endpoint.kind,
                                }
                                continue
                            if existing is not None:
                                existing["columns"] = cols
                                existing["store"] = endpoint.name
                                existing["store_kind"] = endpoint.kind
                            else:
                                # A table the REST listing did not return (a Warehouse
                                # table, say) is still worth recording.
                                ctx.tables[table_name] = {
                                    "type": "Managed", "format": "",
                                    "columns": cols,
                                    "store": endpoint.name,
                                    "store_kind": endpoint.kind,
                                }
                        # One extra connection per endpoint, carrying every remaining
                        # catalog read as a single batch. Attempted only where the
                        # column read already succeeded: if that failed, this one has
                        # no better prospect and would just double the wasted time.
                        self._store_endpoint_metadata(ctx, reader, endpoint)
                if want_security and endpoint.kind == "Warehouse":
                    sec_attempted += 1
                    policies = reader.security_policies(endpoint)
                    if policies is not None:
                        sec_read += 1
                        ctx.warehouse_security[endpoint.name] = policies

            # None readable means "we could not look", which must not read as "none
            # configured". Some readable is a partial gap, recorded but still usable.
            # The per-endpoint reasons ride along so the snapshot explains itself.
            reason_counts: dict[str, int] = {}
            for reason in reader.failures.values():
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

            if want_columns:
                self._record_failures(ctx, Resource.TABLE_COLUMNS, col_attempted,
                                      col_read, 0, col_attempted - col_read,
                                      reasons=reason_counts)
            if collisions:
                log.info("fetch %s: %d table name(s) exist in more than one store; "
                         "the duplicates are keyed '<store>.<table>'",
                         workspace_id, collisions)
            if want_security and sec_attempted:
                self._record_failures(ctx, Resource.WAREHOUSE_SECURITY, sec_attempted,
                                      sec_read, 0, sec_attempted - sec_read,
                                      reasons=reason_counts)
            elif want_security:
                # No Warehouse in the workspace: nothing to read, nothing to report.
                ctx.unavailable.add(Resource.WAREHOUSE_SECURITY)

            # The per-endpoint reasons are the only way to tell a blocked port from a
            # permission gap, so surface the distinct ones rather than burying them.
            for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
                log.warning("fetch %s: %d SQL endpoint read(s) failed - %s",
                            workspace_id, count, reason)

            log.info("fetch %s: SQL endpoints - columns %d/%d, warehouse security %d/%d%s",
                     workspace_id, col_read, col_attempted, sec_read, sec_attempted,
                     f" ({len(reader.failures)} endpoint(s) failed)" if reader.failures else "")

        def _lakehouse_tables(self, workspace_id: str, item_id: str) -> tuple[list[dict], str]:
            """List a lakehouse's tables via REST (name/type/format; no columns).

        Returns ``(rows, failure)`` — see :meth:`_definition_parts` for the
        ``failure`` values. The Fabric *List Tables* endpoint returns rows under
        ``data`` (not the usual ``value`` collection key), so it is read directly.
        """
            status, body = self._get(
                f"/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
            )
            if status == 401:
                if self._try_refresh_token():
                    status, body = self._get(
                        f"/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
                    )
                else:
                    log.warning("lakehouse %s list-tables -> HTTP 401 (token expired, refresh failed)", item_id)
                    return [], "forbidden"
            if status == 403:
                log.warning("lakehouse %s list-tables -> HTTP 403 (permission denied)", item_id)
                return [], "forbidden"
            if status is None or status == 429 or (isinstance(status, int) and status >= 500):
                log.warning("lakehouse %s list-tables -> HTTP %s (transient)", item_id, status)
                return [], "transient"
            if status != 200 or not isinstance(body, dict):
                return [], ""  # e.g. 404 — no SQL endpoint / no tables; not a read failure
            return body.get("data") or [], ""

        @staticmethod
        def _connection_metadata(row: dict) -> dict:
            """Keep connection metadata useful to checks without persisting secrets."""
            details = row.get("connectionDetails") or {}
            credentials = row.get("credentialDetails") or {}
            recency = row.get("connectionRecency") or {}
            return {
                "id": row.get("id", ""),
                "display_name": row.get("displayName", ""),
                "gateway_id": row.get("gatewayId"),
                "connectivity_type": row.get("connectivityType", ""),
                "connection_type": details.get("type", ""),
                "endpoint": details.get("path", ""),
                "credential_type": credentials.get("credentialType", ""),
                "single_sign_on_type": credentials.get("singleSignOnType", ""),
                "connection_encryption": credentials.get("connectionEncryption", ""),
                "skip_test_connection": credentials.get("skipTestConnection"),
                "created_date_time": recency.get("createdDateTime"),
                "last_bound_date_time": recency.get("lastBoundDateTime"),
                "last_credential_used_date_time": recency.get("lastCredentialUsedDateTime"),
                "minimum_tls_version": None,
                "status": "unknown",
            }

        def _gateways(self) -> tuple[list[dict], bool]:
            """List the gateways this caller administers, with their members.

        An elevated read: ``Gateway.Read.All`` plus a role on each gateway, so
        the list holds only the gateways the caller can see. ``known`` is False
        when the list call itself failed, because "we could not ask" and "there
        are none" must never look the same in a report.
        """
            rows, known = self._values("/gateways")
            if not known:
                return [], False
            gateways = []
            for row in rows:
                gateway_id = row.get("id")
                if not gateway_id:
                    continue
                members, _ = self._values(f"/gateways/{gateway_id}/members")
                gateways.append({
                    "id": gateway_id,
                    "display_name": row.get("displayName", ""),
                    "type": row.get("type", ""),
                    "version": row.get("version", ""),
                    "number_of_member_gateways": row.get("numberOfMemberGateways"),
                    "load_balancing_setting": row.get("loadBalancingSetting", ""),
                    # Only shape is kept: how many machines back this gateway and
                    # whether each is enabled. Never a credential or a host name.
                    "members": [
                        {
                            "display_name": m.get("displayName", ""),
                            "enabled": m.get("enabled"),
                            "version": m.get("version", ""),
                        }
                        for m in members if isinstance(m, dict)
                    ],
                })
            return gateways, True

        def _data_access_roles(self, workspace_id: str, item_id: str) -> tuple[list[dict], bool]:
            """List a Lakehouse's OneLake data access roles (name, members, permissions).

        Returns ``(roles, known)``. The status split matters more here than
        elsewhere: Fabric answers **404** when the Lakehouse has no data access
        roles configured at all, and that is precisely the finding 6.2.6 looks
        for — "workspace access alone decides who reads the data". Treating it as
        a read failure would mark the resource unavailable and report N/A on the
        one estate the check exists to catch.

        Only a permission denial or a transient fault is genuinely "could not
        ask".
        """
            status, body = self._get(
                f"/workspaces/{workspace_id}/items/{item_id}/dataAccessRoles"
            )
            if status in (400, 404):
                # No data access roles defined on this Lakehouse. A real answer.
                return [], True
            if status != 200 or not isinstance(body, dict):
                log.warning("lakehouse %s dataAccessRoles -> HTTP %s", item_id, status)
                return [], False
            roles = []
            for row in body.get("value") or []:
                members = row.get("members") or {}
                entra = members.get("microsoftEntraMembers") or []
                item_access = members.get("fabricItemMembers") or []
                name = str(row.get("name") or "")
                roles.append({
                    "name": name,
                    # A "Default*" role is the one Fabric creates itself — it is not
                    # evidence anyone scoped access, so the check counts only the
                    # roles a human defined.
                    "built_in": name.startswith("Default"),
                    "members": len(entra) + len(item_access),
                    # Individuals named directly in a role are the same stale-access
                    # problem as individuals holding a workspace role: nobody removes
                    # them when the person moves on.
                    "individuals": sum(
                        1 for member in entra
                        if isinstance(member, dict) and member.get("objectType") == "User"
                    ),
                    "permissions": len(row.get("decisionRules") or []),
                })
            return roles, True

        def _item_shortcuts(self, workspace_id: str, item_id: str) -> tuple[list[dict], bool]:
            """List an item's OneLake shortcuts (name/path/target type), all pages.

        Returns ``(shortcuts, known)``; ``known`` is False when the list call
        itself failed, so "could not ask" is never recorded as "has none".
        """
            rows, known = self._values(
                f"/workspaces/{workspace_id}/items/{item_id}/shortcuts"
            )
            shortcuts = []
            for row in rows:
                target = row.get("target") or {}
                shortcuts.append({
                    "name": row.get("name", ""),
                    "path": row.get("path", ""),
                    "target_type": target.get("type", ""),
                })
            return shortcuts, known

        def _semantic_model_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Fetch a semantic model's TMSL definition and reduce it to model facts.

        Returns ``(model, failure)`` — see :meth:`_definition_parts`.
        """
            parts, failure = self._definition_parts(workspace_id, item_id, fmt="TMSL")
            for part in parts:
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    document = json.loads(payload)
                except Exception:
                    continue
                if isinstance(document, dict) and ("model" in document or "tables" in document):
                    return parse_tmsl(document), ""
            return None, failure

        @staticmethod
        def _git_connection(body: Any) -> dict:
            """Normalize a ``git/connection`` body into the provider/repo/branch facts.

        Fabric answers 200 with ``gitConnectionState: "NotConnected"`` for a
        workspace that has no repository, so the *state* — not the HTTP status —
        decides whether the workspace is really Git-connected.
        """
            if not isinstance(body, dict):
                return {}
            provider = body.get("gitProviderDetails") or {}
            sync = body.get("gitSyncDetails") or {}
            state = body.get("gitConnectionState") or ""
            connected = state != "NotConnected" if state else bool(provider)
            return {
                "connected": connected,
                "state": state,
                "provider": provider.get("gitProviderType", ""),
                # Azure DevOps reports organizationName; GitHub reports ownerName.
                "organization": provider.get("organizationName") or provider.get("ownerName", ""),
                "project": provider.get("projectName", ""),
                "repository": provider.get("repositoryName", ""),
                "branch": provider.get("branchName", ""),
                "directory": provider.get("directoryName", ""),
                "head": sync.get("head"),
                "last_sync_time": sync.get("lastSyncTime"),
                "secret_scanning": LiveFabricProvider._secret_scanning_status(
                    provider.get("gitProviderType", ""), connected
                ),
            }

        @staticmethod
        def _secret_scanning_status(provider_type: str, connected: bool) -> dict:
            """Record what is known about the repo's secret-scanning posture.

        Secret scanning / push protection is a *Git-provider* control (GitHub
        Advanced Security, Azure DevOps push protection) that the Fabric
        ``git/connection`` API does not expose, and reading it needs a
        repo-security token the auditor is not granted. So the honest, read-only
        answer is ``enabled: None`` (unknown, not verified) with the reason, which
        lets a check report the environment as *unverified* rather than assume a
        pass or a fail. The shape stays stable so a future provider integration
        can fill ``enabled`` in without a schema change.
        """
            if not connected:
                return {
                    "provider": provider_type,
                    "verified": False,
                    "enabled": None,
                    "push_protection": None,
                    "reason": "workspace is not connected to source control",
                }
            return {
                "provider": provider_type,
                "verified": False,
                "enabled": None,
                "push_protection": None,
                "reason": (
                    "secret scanning / push protection is a Git-provider security "
                    "control not exposed by the Fabric git/connection API and needs a "
                    "repo-security token to read; not verifiable read-only"
                ),
            }

        @staticmethod
        def _unique_key(store: dict, name: str, item_id: str) -> str:
            """A key that will not overwrite an item already stored under ``name``.

        Fabric permits two items of the same type to share a display name, so
        keying purely by name silently loses all but the last one.
        """
            if name not in store:
                return name
            return f"{name} ({item_id[:8]})"

        def _reflex_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Read a Data Activator (Reflex) definition and reduce it to rule counts.

        Returns ``(summary, failure)`` — see :meth:`_definition_parts`. The
        ``ReflexEntities.json`` part is a flat list of typed entities; only the
        bounded counts a check needs are kept, never the rule bodies.
        """
            parts, failure = self._definition_parts(workspace_id, item_id)
            for part in parts:
                if not str(part.get("path") or "").endswith("ReflexEntities.json"):
                    continue
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    entities = json.loads(payload)
                except Exception:
                    return None, ""
                if isinstance(entities, list):
                    return self._reflex_summary(entities), ""
                return None, ""
            return None, failure

        @staticmethod
        def _reflex_summary(entities: list) -> dict:
            """Count rules, active rules, sources and actions in a ReflexEntities list."""
            rules = active = sources = actions = 0
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                etype = str(entity.get("type") or "")
                payload = entity.get("payload") if isinstance(entity.get("payload"), dict) else {}
                if etype == "timeSeriesView-v1":
                    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
                    if str(definition.get("type") or "") == "Rule":
                        rules += 1
                        settings = definition.get("settings") if isinstance(definition.get("settings"), dict) else {}
                        if settings.get("shouldRun"):
                            active += 1
                elif etype.endswith("Source-v1"):
                    sources += 1
                elif etype == "fabricItemAction-v1":
                    actions += 1
            return {"rules": rules, "active_rules": active, "sources": sources, "actions": actions}

        @staticmethod
        def _annotate_partitions(ctx: WorkspaceContext, onelake, workspace_id: str, item) -> None:
            """Record each Delta table's partition columns, and that we managed to look.

        ``partitions_listed`` is what lets a check treat "no partition columns" as
        a finding rather than as missing data.
        """
            partitions, failure = onelake.lakehouse_table_partitions(workspace_id, item.id)
            if failure:
                return
            store = item.display_name or item.id
            for table_name, partition_columns in partitions.items():
                entry = ctx.tables.get(table_name) or ctx.tables.get(f"{store}.{table_name}")
                if entry is None:
                    continue
                entry["partitionColumns"] = partition_columns
                entry["partitions_listed"] = True

        @staticmethod
        def _record_failures(ctx: WorkspaceContext, resource: Resource,
                             attempted: int, read: int, forbidden: int, transient: int,
                             empty: int = 0, reasons: dict[str, int] | None = None,
                             artifacts: list[dict[str, str]] | None = None) -> None:
            """Record a one-per-item read outcome on the context.

        When some items of a type could not be read, store the counts so the gap
        is visible ("N of M could not be read"). When *none* could be read, also
        mark the resource unavailable so its checks report N/A with the reason.

        ``empty`` counts items whose definition call returned but carried nothing
        usable. Those are a real coverage gap, but re-crawling will not fix them,
        so they are reported without making the snapshot un-cacheable.

        ``reasons`` is a ``reason -> count`` histogram. Counts alone say *how
        many* reads failed but never *why*, and the why used to exist only in the
        crawl log - which is discarded once the process exits, leaving a snapshot
        that cannot explain itself. Persisting it makes a blocked port
        distinguishable from a throttled tenant after the fact.
        """
            if attempted and (forbidden or transient or empty):
                stat = {
                    "attempted": attempted,
                    "read": read,
                    "failed": forbidden + transient + empty,
                    "forbidden": forbidden,
                    "transient": transient,
                    "empty": empty,
                }
                if reasons:
                    stat["reasons"] = dict(
                        sorted(reasons.items(), key=lambda kv: -kv[1])
                    )
                if artifacts:
                    stat["artifacts"] = [dict(artifact) for artifact in artifacts]
                ctx.read_failures[resource.value] = stat
                if read == 0:
                    ctx.unavailable.add(resource)

        def _store_endpoint_metadata(self, ctx: WorkspaceContext, reader,
                                     endpoint) -> None:
            """Fold one endpoint's catalog batch into the workspace context.

        **Bounded by construction.** Nothing here scales with the *data* in the
        estate: row counts are integers read from partition metadata, foreign
        keys are a small edge list, and view/procedure bodies are already capped
        by the query. Per the knowledge-base rule, no row content is ever stored.

        Everything is keyed by the same table name the column reader uses, so a
        check reads ``table["row_count"]`` or ``table["references"]`` without
        knowing the endpoint existed.
        """
            try:
                sets = reader.metadata(endpoint)
            except Exception as exc:  # noqa: BLE001 - extra metadata is best-effort
                log.info("sql metadata batch failed for %s: %s", endpoint.name, exc)
                return
            if not sets:
                return

            # object_id -> (schema, name, type) for everything the endpoint declared.
            objects: dict[int, tuple[str, str, str]] = {}
            for row in sets.get("objects", ()):
                try:
                    objects[int(row[0])] = (str(row[1] or ""), str(row[2] or ""),
                                            str(row[3] or "").strip())
                except (TypeError, ValueError):
                    continue

            def table_entry(object_id) -> dict | None:
                """The context table an object id belongs to, if it is one we hold.

            **The key must be built the way the column reader built it**, not
            guessed at. ``SqlEndpointReader.columns`` files a Warehouse table as
            ``<schema>.<table>`` and a Lakehouse table under its bare name, so
            looking up the bare name alone matched nothing on a Warehouse: the
            IDENTITY, foreign-key, key-constraint and row-count reads all
            returned their rows and attached none of them, silently, on every
            Warehouse ever crawled.

            The store-prefixed spelling is tried last because the same reader
            falls back to ``<store>.<table>`` when two stores hold a table of the
            same name.
            """
                info = objects.get(int(object_id)) if object_id is not None else None
                if not info:
                    return None
                schema, name = info[0], info[1]
                qualified = f"{schema}.{name}" if schema else name
                primary = qualified if endpoint.kind == "Warehouse" else name
                for key in (primary, name, qualified,
                            f"{endpoint.name}.{primary}", f"{endpoint.name}.{name}"):
                    entry = ctx.tables.get(key)
                    if entry is not None:
                        return entry
                return None

            for row in sets.get("row_counts", ()):
                entry = table_entry(row[0])
                if entry is not None and row[1] is not None:
                    # Approximate by definition - partition metadata, not a COUNT(*).
                    entry["row_count"] = int(row[1])

            # Declared foreign keys, stored as a name-level edge list on the
            # referencing table. This is what lets a check tell a fact from a
            # dimension structurally instead of reading the table's name.
            for row in sets.get("foreign_keys", ()):
                parent = table_entry(row[1])
                referenced = objects.get(int(row[2])) if row[2] is not None else None
                if parent is None or not referenced:
                    continue
                parent.setdefault("references", [])
                if referenced[1] not in parent["references"]:
                    parent["references"].append(referenced[1])

            for row in sets.get("key_constraints", ()):
                entry = table_entry(row[1])
                if entry is not None:
                    entry["has_declared_key"] = True

            # IDENTITY columns: the table *declares* which column the engine
            # generates. A check reading this no longer has to infer "generated"
            # from a column name. Stored per table as a list of column names, since
            # a check needs to know which column, not merely that one exists.
            for row in sets.get("identity_columns", ()):
                entry = table_entry(row[0])
                if entry is None or not row[1]:
                    continue
                identity = entry.setdefault("identity_columns", [])
                if row[1] not in identity:
                    identity.append(str(row[1]))

            # Database-level automatic-statistics switches. Off is a real
            # misconfiguration a user can reach and we can read - unlike NORECOMPUTE,
            # which Fabric refuses to set at all.
            #
            # Keyed by the database name the row carries, not by the endpoint: one
            # SQL endpoint exposes several databases (its own plus `master` and the
            # dataflow staging stores), and an earlier `WHERE name = DB_NAME()`
            # filter matched none of them, so this read silently returned nothing
            # while `sys.stats` on the same connection worked. `master` is skipped -
            # it is a system database nobody configures.
            for row in sets.get("database_options", ()):
                database = str(row[0] or "").strip()
                if not database or database.lower() == "master":
                    continue
                ctx.warehouse_options[database] = {
                    "auto_create_stats": bool(row[1]) if row[1] is not None else None,
                    "auto_update_stats": bool(row[2]) if row[2] is not None else None,
                    "auto_update_stats_async": bool(row[3]) if len(row) > 3 and row[3] is not None else None,
                }

            for row in sets.get("stats", ()):
                entry = table_entry(row[0])
                if entry is None:
                    continue
                entry["statistics"] = int(entry.get("statistics", 0)) + 1
                # `no_recompute = 1` means someone switched automatic refresh off for
                # this statistic - the only way stale statistics survive on Fabric,
                # and the one thing worth reporting now the engine maintains the rest.
                if len(row) > 4 and row[4]:
                    entry["statistics_norecompute"] = int(
                        entry.get("statistics_norecompute", 0)) + 1
                # Newest refresh across the table's statistics, so a check can see
                # whether automatic maintenance is actually running here.
                if len(row) > 5 and row[5]:
                    stamp = str(row[5])
                    if stamp > str(entry.get("statistics_last_updated") or ""):
                        entry["statistics_last_updated"] = stamp

            # Views and routines are workspace-level, not per table: they describe the
            # load logic, which several Warehouse checks need and none can read today.
            # Platform schemas are excluded - every SQL endpoint ships hundreds of
            # ``sys``/``queryinsights`` view definitions that nobody wrote. Storing
            # them bloats the snapshot (1,171 arrived on one real crawl, all platform)
            # and would let a check report an abstraction layer nobody built.
            views = [
                {"schema": str(r[0] or ""), "name": str(r[1] or ""),
                 "definition": str(r[2] or ""), "store": endpoint.name}
                for r in sets.get("views", ())
                if str(r[0] or "").lower() not in _PLATFORM_SCHEMAS
            ]
            routines = [
                {"schema": str(r[0] or ""), "name": str(r[1] or ""),
                 "type": str(r[2] or ""), "definition": str(r[3] or ""),
                 "store": endpoint.name}
                for r in sets.get("routines", ())
                if str(r[0] or "").lower() not in _PLATFORM_SCHEMAS
            ]
            if views:
                ctx.sql_views.extend(views)
            if routines:
                ctx.sql_routines.extend(routines)

            # Database-scoped principals: the same "who has access" question as
            # workspace role assignments, from a source that needs no admin.
            # Compared case-insensitively - SQL identifiers are not case-sensitive,
            # so a ``DBO`` or ``Public`` would otherwise slip past the filter and
            # make an empty estate look populated.
            principals = [
                {"name": str(r[1] or ""), "type": str(r[2] or ""),
                 "authentication": str(r[3] or ""), "store": endpoint.name}
                for r in sets.get("principals", ())
                if str(r[1] or "").strip().lower() not in _SYSTEM_PRINCIPALS
            ]
            if principals:
                ctx.sql_principals.extend(principals)

        @staticmethod
        def _record_environment_gap(ctx: WorkspaceContext, wanted: set[Resource],
                                    reason: str, endpoints: int) -> None:
            """Persist a whole-resource SQL gap, with its reason, onto the context.

        Distinct from :meth:`_record_failures`, which reports per-item outcomes.
        Here nothing was attempted at all - the reader could not start - so the
        count is the number of endpoints that *would* have been read. Recording it
        the same way means the report's crawl-completeness section explains the
        gap ("no ODBC driver installed") instead of silently showing no columns.

        This matters most where the operator cannot see the server console: a
        hosted deployment, or a colleague running the tool on another machine.
        """
            for resource in (Resource.TABLE_COLUMNS, Resource.WAREHOUSE_SECURITY):
                if resource not in wanted:
                    continue
                ctx.unavailable.add(resource)
                ctx.read_failures[resource.value] = {
                    "attempted": endpoints,
                    "read": 0,
                    "failed": endpoints,
                    "forbidden": 0,
                    "transient": 0,
                    "empty": 0,
                    "reasons": {reason: endpoints or 1},
                }

        def _run_stamps(self, workspace_id: str, item_id: str) -> tuple[list[str], str]:
            """Return one item's job-run timestamps (newest first) and a failure classifier.

        Reads a single page of the item's job-instance history. Every run on that
        page contributes its ``endTimeUtc`` (falling back to ``startTimeUtc`` for a
        run still in flight), so the caller gets both the latest run *and* the
        intervals between runs from one call — no extra request is made for the
        cadence signal. ``failure`` follows the :meth:`_definition_parts`
        convention: ``""`` read, ``"forbidden"`` a permission/expired-token
        denial, ``"transient"`` a throttling/5xx/network error. A 400/404 (the
        item type keeps no job history) is *not* a failure — it simply yields no
        timestamps.

        ISO-8601 UTC (``…Z``) stamps sort lexicographically, so a plain reverse
        sort is chronological without any date parsing.
        """
            path = f"/workspaces/{workspace_id}/items/{item_id}/jobs/instances"
            status, body = self._get(path)
            if status == 401 and self._try_refresh_token():
                status, body = self._get(path)
            if status in (401, 403):
                log.warning("item %s jobs/instances -> HTTP %s (permission denied)", item_id, status)
                return [], "forbidden"
            if status is None or status == 429 or (isinstance(status, int) and status >= 500):
                log.warning("item %s jobs/instances -> HTTP %s (transient)", item_id, status)
                return [], "transient"
            if status != 200 or not isinstance(body, dict):
                return [], ""  # 400/404 — no job history for this item type
            stamps = [
                row.get("endTimeUtc") or row.get("startTimeUtc")
                for row in (body.get("value") or [])
                if isinstance(row, dict) and (row.get("endTimeUtc") or row.get("startTimeUtc"))
            ]
            return sorted((str(s) for s in stamps), reverse=True), ""

        def _latest_run(self, workspace_id: str, item_id: str) -> tuple[str | None, str]:
            """Return one item's most recent job-run time and a failure classifier.

        A thin wrapper over :meth:`_run_stamps` kept for callers that only need
        the recency signal.
        """
            stamps, failure = self._run_stamps(workspace_id, item_id)
            return (stamps[0] if stamps else None), failure

        def _warehouse_audit(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
            """Read one Warehouse's SQL audit *configuration*.

        ``GET …/warehouses/{id}/settings/sqlAudit`` returns the audit state, the
        configured action groups, and the retention. It needs the Audit
        permission on the Warehouse item — **not** tenant-admin — so it is an
        ordinary delegated read. Only the configuration is returned; audit rows
        (``sys.fn_get_audit_file_v2``) are runtime data and are never fetched.

        Returns ``(settings, failure)`` with the same ``failure`` classifiers as
        :meth:`_definition_parts`. A 400/404 (the Warehouse does not support the
        setting) yields ``(None, "")`` — readable, simply not offered — which the
        caller records as unread rather than as "auditing is off".
        """
            path = f"/workspaces/{workspace_id}/warehouses/{item_id}/settings/sqlAudit"
            status, body = self._get(path)
            if status == 401 and self._try_refresh_token():
                status, body = self._get(path)
            if status in (401, 403):
                log.warning("warehouse %s sqlAudit -> HTTP %s (permission denied)", item_id, status)
                return None, "forbidden"
            if status is None or status == 429 or (isinstance(status, int) and status >= 500):
                log.warning("warehouse %s sqlAudit -> HTTP %s (transient)", item_id, status)
                return None, "transient"
            if status != 200 or not isinstance(body, dict):
                return None, ""
            return self._sql_audit_settings(body), ""

        @staticmethod
        def _sql_audit_settings(body: dict) -> dict:
            """Normalise a ``settings/sqlAudit`` payload into the shape checks read.

        Fabric has spelled the payload more than one way (``state`` vs
        ``auditState``, ``auditActionsAndGroups`` vs ``auditActionGroups``), and
        the state arrives as ``Enabled``/``Disabled`` in mixed case. Normalising
        here keeps every variation out of the check bodies, which must stay pure.
        """
            raw = body.get("properties") if isinstance(body.get("properties"), dict) else body
            state = ""
            for key in ("state", "auditState", "sqlAuditState", "status"):
                value = raw.get(key)
                if value is not None and str(value).strip():
                    state = str(value).strip()
                    break
            groups: list[str] = []
            for key in ("auditActionsAndGroups", "auditActionGroups", "actionsAndGroups", "actionGroups"):
                value = raw.get(key)
                if isinstance(value, list):
                    groups = [str(g).strip() for g in value if str(g).strip()]
                    break
            retention = None
            for key in ("retentionDays", "retentionInDays", "auditRetentionDays"):
                value = raw.get(key)
                if isinstance(value, (int, float)):
                    retention = int(value)
                    break
            return {
                "state": state,
                "enabled": state.lower() in {"enabled", "enable", "on", "true"},
                "action_groups": groups,
                "retention_days": retention,
            }

        def _powerbi(self):
            """Return a lazily-built Power BI client, or ``None`` without a PBI token.

        Semantic-model refresh history is served only by the Power BI Datasets
        API, whose audience differs from the Fabric crawl token — so it needs a
        separately-minted token. A test may pre-set ``_powerbi_client``.
        """
            if self._powerbi_client is not None:
                return self._powerbi_client
            if not self._powerbi_token:
                return None
            PowerBIClient = _collector_modules['clients.powerbi'].PowerBIClient
            with self._lock:
                if self._powerbi_client is None:
                    self._powerbi_client = PowerBIClient(self._powerbi_token, timeout=self._timeout)
            return self._powerbi_client

        def _cached_admin(self, key: str, loader) -> tuple[Any, bool]:
            with self._lock:
                cached = self._admin_cache.get(key)
            if cached is not None:
                return cached
            value = loader()
            with self._lock:
                self._admin_cache.setdefault(key, value)
                return self._admin_cache[key]

        def _tenant_domains(self) -> tuple[list[dict], bool]:
            domains, known = self._values("/admin/domains")
            if not known:
                return [], False
            normalized: list[dict] = []
            for domain in domains:
                domain_id = str(domain.get("id") or "")
                rows, readable = self._values(f"/admin/domains/{domain_id}/workspaces")
                if not readable:
                    return normalized, False
                normalized.append({
                    "id": domain_id,
                    "name": domain.get("displayName") or domain.get("name") or domain_id,
                    "workspace_ids": [str(row.get("id") or row.get("workspaceId") or "")
                                      for row in rows if row.get("id") or row.get("workspaceId")],
                })
            return normalized, True

        #: Names that mean "Fabric or an app made this", not "someone built this to
        #: be trusted". The notebook leaves them out of 14.3.6's population.
        _ENDORSEMENT_EXCLUDE_KEYWORDS = ("usage metrics", "report usage", "template app")

        @staticmethod
        def _endorsement_scan(payload: dict, workspace_id: str,
                              items: Sequence[Item] = ()) -> dict:
            """Collect the semantic models and reports 14.3.6 judges for endorsement.

        Two exclusions, both from the notebook, and both there to stop the score
        being dragged down by content nobody is expected to endorse:

        * **Default semantic models.** Every Lakehouse and Warehouse gets one
          created automatically, carrying the same name as the item. Nobody
          endorses those, so a workspace with six Lakehouses would otherwise
          score 0 however well its real models are badged.
        * **Usage-metrics and template-app content**, matched by name.

        What is excluded is reported alongside the population, so a reviewer can
        see the judged set rather than trusting the filter.
        """
            auto = {
                (item.display_name or "").strip().lower()
                for item in items
                if item.type in ("Lakehouse", "Warehouse") and item.display_name
            }
            judged: list[dict] = []
            excluded: list[dict] = []
            for workspace in payload.get("workspaces", []) or []:
                if str(workspace.get("id") or "") != workspace_id:
                    continue
                for kind in ("datasets", "reports"):
                    for item in workspace.get(kind, []) or []:
                        detail = item.get("endorsementDetails") or {}
                        name = item.get("name") or item.get("displayName") or ""
                        kind_label = "SemanticModel" if kind == "datasets" else "Report"
                        row = {
                            "id": item.get("id"),
                            "name": name,
                            "type": kind_label,
                            "endorsement": detail.get("endorsement") or "None",
                        }
                        low = name.strip().lower()
                        why = []
                        if kind_label == "SemanticModel" and low in auto:
                            why.append("created automatically for a Lakehouse or Warehouse")
                        word = next(
                            (k for k in LiveFabricProvider._ENDORSEMENT_EXCLUDE_KEYWORDS
                             if k in low), None)
                        if word:
                            why.append(f"name contains '{word}'")
                        if why:
                            excluded.append({**row, "excluded_because": "; ".join(why)})
                        else:
                            judged.append(row)
            return {"items": judged, "excluded": excluded}

        def _onelake(self):
            """Return a OneLake ADLS Gen2 client, or None without a Storage token."""
            if self._onelake_client is not None:
                return self._onelake_client
            if not self._storage_token:
                return None
            OneLakeClient = _collector_modules['clients.onelake'].OneLakeClient
            with self._lock:
                if self._onelake_client is None:
                    self._onelake_client = OneLakeClient(self._storage_token, timeout=self._timeout)
            return self._onelake_client

        def _semantic_model_last_refresh(self, workspace_id: str, item_id: str) -> tuple[str | None, str]:
            """Return a semantic model's last-refresh time and a failure classifier.

        Reads the Power BI refresh history (latest first) for the model. Without
        a Power BI token the recency is *unknown* (``"forbidden"``) rather than
        "never refreshed", so it is excluded from staleness instead of counted
        as stale. A ``None`` timestamp with no failure means the model has no
        refresh history yet.
        """
            pbi = self._powerbi()
            if pbi is None:
                return None, "forbidden"
            try:
                return pbi.dataset_last_refresh(item_id, group_id=workspace_id)
            except Exception as exc:
                log.warning("semantic model %s refresh history error: %s", item_id, exc)
                return None, "transient"

        def _semantic_model_refresh_schedule(
            self, workspace_id: str, item_id: str
        ) -> tuple[dict | None, str]:
            """Read one semantic model's refresh *schedule configuration*.

        Served only by the Power BI Datasets API, whose audience differs from the
        Fabric crawl token — so without a Power BI token the schedule is
        *unknown* (``"forbidden"``), never "not configured". That distinction is
        what lets the alerting check report N/A instead of failing a workspace
        whose token simply lacked the scope.

        Returns ``(schedule, failure)``. ``(None, "")`` means the model genuinely
        has no refresh schedule (Direct Lake / push / pipeline-driven refresh).
        """
            pbi = self._powerbi()
            if pbi is None:
                return None, "forbidden"
            try:
                return pbi.dataset_refresh_schedule(item_id, group_id=workspace_id)
            except Exception as exc:
                log.warning("semantic model %s refresh schedule error: %s", item_id, exc)
                return None, "transient"

        def _workspace_reports(self, workspace_id: str) -> tuple[list[dict], bool]:
            """Read the workspace's report → semantic-model bindings.

        Served by the Power BI *Get Reports In Group* API, whose audience differs
        from the Fabric crawl token, so without a Power BI token the bindings are
        *unknown* (``readable=False``) rather than "no report exists" — the
        distinction that lets the reuse checks report N/A instead of inventing a
        finding. Needs the ordinary delegated ``Report.Read.All`` scope, not
        tenant-admin.

        Only the four fields the checks read are kept: report definitions, pages
        and visuals are deliberately never fetched. ``datasetId`` is absent for a
        paginated (RDL) report, which is a real answer — those reports are
        excluded by the checks, never failed.
        """
            pbi = self._powerbi()
            if pbi is None:
                return [], False
            try:
                rows, known = pbi.list_reports_known(group_id=workspace_id)
            except Exception as exc:
                log.warning("fetch %s: report list error: %s", workspace_id, exc)
                return [], False
            reports = [
                {
                    "id": row.get("id") or "",
                    "name": row.get("name") or "",
                    "dataset_id": row.get("datasetId") or "",
                    "dataset_workspace_id": row.get("datasetWorkspaceId") or "",
                }
                for row in rows
                if isinstance(row, dict) and row.get("id")
            ]
            return reports, known

        def _report_bindings_from_definitions(self, ctx: WorkspaceContext,
                                              workspace_id: str) -> list[dict]:
            """Report → semantic-model bindings from the *Fabric* item definitions.

        The Power BI *Get Reports In Group* API needs a Power BI-audience token,
        which a Fabric-only sign-in does not have - so the reuse checks reported
        N/A on a workspace whose bindings were perfectly readable another way.
        A Report item's ``definition.pbir`` carries ``datasetReference`` as
        either ``byPath`` (``"../Sales.SemanticModel"``, same workspace) or
        ``byConnection`` (``"semanticmodelid=<guid>"``), and ``getDefinition``
        serves it on the Fabric token the crawl already holds.

        Used only as a fallback: the Power BI listing is preferred because it
        also carries the *owning workspace* of the model, which is what tells a
        cross-workspace (central-hub) binding apart from a local one.
        """
            bindings: list[dict] = []
            for item in ctx.items:
                if item.type not in ("Report", "PaginatedReport"):
                    continue
                parts, _failure = self._definition_parts(workspace_id, item.id)
                dataset_id = ""
                for part in parts:
                    if not str(part.get("path") or "").lower().endswith("definition.pbir"):
                        continue
                    try:
                        payload = base64.b64decode(part["payload"]).decode("utf-8")
                        document = json.loads(payload)
                    except (KeyError, ValueError, UnicodeDecodeError):
                        continue
                    reference = (document.get("datasetReference") or {})
                    by_connection = reference.get("byConnection") or {}
                    connection = str(by_connection.get("connectionString") or "")
                    match = re.search(r"semanticmodelid\s*=\s*([0-9a-fA-F-]{36})", connection)
                    if match:
                        dataset_id = match.group(1)
                        break
                    by_path = reference.get("byPath") or {}
                    path = str(by_path.get("path") or "")
                    if path:
                        # A relative path names the model by *name*, not id; resolve it
                        # against the item list so the checks still see a stable key.
                        wanted = path.rsplit("/", 1)[-1].removesuffix(".SemanticModel")
                        for candidate in ctx.items:
                            if (candidate.type == "SemanticModel"
                                    and candidate.display_name == wanted):
                                dataset_id = candidate.id
                                break
                        break
                if dataset_id:
                    bindings.append({
                        "id": item.id,
                        "name": item.display_name or item.id,
                        "dataset_id": dataset_id,
                        # getDefinition names the model, never its owning workspace.
                        # Left blank rather than guessed: the reuse check treats an
                        # unknown owner as local, which is the conservative reading.
                        "dataset_workspace_id": "",
                    })
            return bindings

        def _enrich_run_history(self, ctx: WorkspaceContext, workspace_id: str) -> None:
            """Fill ``Item.last_run_utc`` from each runnable item's run/refresh history.

        One call per runnable item: semantic models are read from the Power BI
        refresh history (a different API audience); pipelines, notebooks,
        dataflows and Spark jobs from the Fabric job scheduler. Other types are
        skipped. ``Item`` is frozen, so a dated item is rebuilt with
        :func:`dataclasses.replace`.

        Recency is an optional, LOW-severity signal: when every runnable item's
        history is unreadable the resource is marked *unavailable* (so the
        staleness check reports N/A), but it is deliberately **not** added to
        ``read_failures`` — a run-history gap must never force a re-crawl or block
        the workspace from being cached.
        """
            attempted = read = failed = 0
            enriched: list[Item] = []
            semantic_models = sum(1 for i in ctx.items if i.type == "SemanticModel")
            if semantic_models and not self._powerbi_token:
                log.warning(
                    "fetch %s: %d semantic model(s) present but no Power BI token — refresh "
                    "recency will be N/A (sign-in did not yield a Power BI-audience token)",
                    workspace_id, semantic_models,
                )
            for item in ctx.items:
                if item.type not in self._JOB_ITEM_TYPES or not item.id:
                    enriched.append(item)
                    continue
                attempted += 1
                if item.type == "SemanticModel":
                    stamp, failure = self._semantic_model_last_refresh(workspace_id, item.id)
                else:
                    stamps, failure = self._run_stamps(workspace_id, item.id)
                    stamp = stamps[0] if stamps else None
                    if not failure and len(stamps) > 1:
                        # Retained so an *observed cadence* can be derived. Capped:
                        # the interval only needs a handful of runs, and the snapshot
                        # should not grow with a chatty item's history.
                        ctx.run_history[item.id] = stamps[:_MAX_RETAINED_RUNS]
                if failure:
                    failed += 1
                    enriched.append(item)
                else:
                    read += 1
                    enriched.append(replace(item, last_run_utc=stamp) if stamp else item)
            ctx.items = enriched
            if attempted and read == 0 and failed:
                ctx.unavailable.add(Resource.ITEM_RUN_HISTORY)
            log.info("fetch %s: run history read for %d of %d runnable item(s)",
                     workspace_id, read, attempted)

        def _enrich_created_dates(self, ctx: WorkspaceContext, workspace_id: str) -> None:
            """Set ``Item.created_date`` on semantic models from the Power BI datasets API.

        ``GET /groups/{ws}/datasets`` exposes each model's ``createdDate`` in one
        call (no admin or capacity), so this is the reliable "when was it created"
        signal even for models with no refresh history. Needs a Power BI token;
        without one it is a silent no-op. Only semantic models carry a dataset
        created date, so other item types are left untouched.
        """
            pbi = self._powerbi()
            if pbi is None:
                return
            try:
                created = pbi.dataset_created_dates(group_id=workspace_id)
            except Exception as exc:
                log.warning("fetch %s: dataset created-dates read error: %s", workspace_id, exc)
                return
            if not created:
                return
            ctx.items = [
                replace(item, created_date=created[item.id])
                if item.type == "SemanticModel" and not item.created_date and item.id in created
                else item
                for item in ctx.items
            ]
            log.info("fetch %s: created date set for %d of %d semantic model(s)", workspace_id,
                     sum(1 for i in ctx.items if i.type == "SemanticModel" and i.created_date),
                     sum(1 for i in ctx.items if i.type == "SemanticModel"))

        # -- the provider contract -------------------------------------------------
        def fetch(
            self,
            workspace_id: str,
            layer: Layer = Layer.MIXED,
            resources: Iterable[Resource] = ALL_RESOURCES,
        ) -> WorkspaceContext:
            wanted = set(resources)

            # The workspace itself is always read: it establishes both identity and
            # access, and its status is how we detect an unreadable workspace. A 429
            # here would otherwise drop the whole workspace, so it is retried with the
            # server's Retry-After before giving up.
            status, workspace = self._get(f"/workspaces/{workspace_id}")
            if status == 401 and self._try_refresh_token():
                status, workspace = self._get(f"/workspaces/{workspace_id}")
            for attempt in range(3):
                if status != 429:
                    break
                time.sleep(_retry_after_seconds(None, attempt))
                status, workspace = self._get(f"/workspaces/{workspace_id}")
            if status != 200 or not isinstance(workspace, dict):
                raise WorkspaceAccessError(workspace_id, status)

            ctx = WorkspaceContext(
                id=workspace_id,
                display_name=workspace.get("displayName", workspace_id),
                layer=layer,
                capacity_id=workspace.get("capacityId"),
                deployment_pipeline=bool(workspace.get("assignedToDeploymentPipeline")),
            )

            if Resource.TENANT_SETTINGS in wanted:
                pbi = self._powerbi()
                if pbi is None:
                    ctx.unavailable.add(Resource.TENANT_SETTINGS)
                else:
                    ctx.tenant_settings, readable = self._cached_admin(
                        "tenant_settings", pbi.tenant_settings
                    )
                    if not readable:
                        ctx.unavailable.add(Resource.TENANT_SETTINGS)

            if Resource.TENANT_DOMAINS in wanted:
                ctx.tenant_domains, readable = self._cached_admin(
                    "tenant_domains", self._tenant_domains
                )
                if not readable:
                    ctx.unavailable.add(Resource.TENANT_DOMAINS)

            if Resource.ADMIN_ACTIVITY in wanted:
                pbi = self._powerbi()
                if pbi is None:
                    ctx.unavailable.add(Resource.ADMIN_ACTIVITY)
                else:
                    ctx.activity_events, readable = self._cached_admin(
                        "activity_events", pbi.activity_events
                    )
                    if not readable:
                        ctx.unavailable.add(Resource.ADMIN_ACTIVITY)

            # The endorsement scan is *filtered* by the workspace's item list (default
            # semantic models share their Lakehouse's name), so the payload is held
            # here and turned into ctx.admin_scan once the item list has been read.
            scanner_payload: dict | None = None
            if Resource.ADMIN_SCANNER in wanted:
                pbi = self._powerbi()
                if pbi is None:
                    ctx.unavailable.add(Resource.ADMIN_SCANNER)
                else:
                    payload, readable = pbi.admin_scan(workspace_id)
                    scanner_payload = payload
                    if not readable:
                        ctx.unavailable.add(Resource.ADMIN_SCANNER)

            if Resource.CAPACITY_METRICS in wanted:
                pbi = self._powerbi()
                if pbi is None:
                    ctx.unavailable.add(Resource.CAPACITY_METRICS)
                else:
                    # The reviewer's answers: where the app lives (an ordering hint),
                    # how its model is named (a filter), and the working day the
                    # busy/quiet split is measured against.
                    hint = self.capacity_metrics_workspace
                    model = self.capacity_metrics_model
                    peak_start, peak_end = self.capacity_peak_hours
                    # Every input is part of the identity of the answer, so a run
                    # that changes one is not served the previous run's result.
                    key = (f"capacity_metrics:{hint.strip().lower()}:"
                           f"{model.strip().lower()}:{peak_start}:{peak_end}")
                    ctx.capacity_metrics, readable = self._cached_admin(
                        key,
                        lambda: pbi.capacity_metrics(hint, model, peak_start, peak_end),
                    )
                    if not readable:
                        ctx.unavailable.add(Resource.CAPACITY_METRICS)

            if Resource.CONNECTIONS in wanted:
                rows, known = self._values("/connections")
                ctx.connections = [
                    self._connection_metadata(row)
                    for row in rows
                    if isinstance(row, dict) and row.get("id")
                ]
                if not known:
                    ctx.unavailable.add(Resource.CONNECTIONS)
                log.info("fetch %s: %d connection records read", workspace_id, len(ctx.connections))

            if Resource.GATEWAYS in wanted:
                ctx.gateways, known = self._gateways()
                if not known:
                    ctx.unavailable.add(Resource.GATEWAYS)
                log.info("fetch %s: %d gateway(s) read", workspace_id, len(ctx.gateways))

            # Report → semantic-model bindings (Power BI Get Reports In Group). One
            # list call, no per-report fetch: only id/name/datasetId are retained.
            # A Fabric-native fallback runs after the item list below, for a sign-in
            # that yielded no Power BI token.
            if Resource.REPORTS in wanted:
                ctx.reports, readable = self._workspace_reports(workspace_id)
                if not readable:
                    ctx.unavailable.add(Resource.REPORTS)
                log.info("fetch %s: %d report binding(s) read (readable=%s)",
                         workspace_id, len(ctx.reports), readable)

            # Pipeline, notebook, and table reads all walk the item list, so fetch it
            # whenever any item-derived resource was asked for.
            if wanted & _ITEM_DERIVED_RESOURCES:
                rows, known = self._values(f"/workspaces/{workspace_id}/items")
                ctx.items = [Item.from_api(row) for row in rows]
                if not known:
                    ctx.unavailable.add(Resource.ITEMS)
                log.info("fetch %s: %d items by type %s", workspace_id,
                         len(ctx.items), dict(Counter(i.type for i in ctx.items)))

                # Fabric-native fallback for report bindings. definition.pbir carries
                # the semantic-model reference on the Fabric token the crawl already
                # holds, so a sign-in with no Power BI token no longer forces the
                # model-reuse checks to N/A. Runs here because it needs the item list.
                if Resource.REPORTS in wanted and not ctx.reports:
                    bindings = self._report_bindings_from_definitions(ctx, workspace_id)
                    if bindings:
                        ctx.reports = bindings
                        ctx.unavailable.discard(Resource.REPORTS)
                        log.info("fetch %s: %d report binding(s) recovered from Fabric "
                                 "item definitions (no Power BI token needed)",
                                 workspace_id, len(bindings))

            # Needs the item list above: the models Fabric auto-creates for a
            # Lakehouse or Warehouse are recognised by name, and counting them would
            # drag 14.3.6 down for content nobody is expected to endorse.
            if scanner_payload is not None:
                ctx.admin_scan = self._endorsement_scan(
                    scanner_payload, workspace_id, ctx.items)

            # Per-item run/refresh recency (last_run_utc) plus the per-workspace
            # semantic-model created date. Fetched whenever a recency-needing resource
            # is selected — the KB crawl requests it for every workspace.
            if Resource.ITEM_RUN_HISTORY in wanted and ctx.items:
                self._enrich_run_history(ctx, workspace_id)
                self._enrich_created_dates(ctx, workspace_id)

            if Resource.ROLE_ASSIGNMENTS in wanted:
                rows, known = self._values(f"/workspaces/{workspace_id}/roleAssignments")
                ctx.role_assignments = [RoleAssignment.from_api(row) for row in rows]
                if not known:
                    ctx.unavailable.add(Resource.ROLE_ASSIGNMENTS)

            if Resource.GIT in wanted:
                git_status, git_body = self._get(f"/workspaces/{workspace_id}/git/connection")
                if git_status == 200:
                    ctx.git_details = self._git_connection(git_body)
                    ctx.git_connected = bool(ctx.git_details.get("connected"))
                elif git_status in (400, 404):
                    ctx.git_connected = False  # genuinely not connected
                else:
                    # 401/403/500/transport failure: we could not determine it.
                    ctx.unavailable.add(Resource.GIT)

            if Resource.ENVIRONMENT_DEFINITIONS in wanted:
                environments = [i for i in ctx.items if i.type == "Environment"]
                fetched = self._fetch_items_parallel(
                    environments, lambda it: self._environment_definition(workspace_id, it.id))
                attempted = read = forbidden = transient = 0
                failed_artifacts = []
                for item, (definition, failure) in zip(environments, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    else:
                        read += 1
                        if definition:
                            record = dict(definition)
                            record["id"] = item.id
                            record["display_name"] = item.display_name
                            ctx.environments[item.id] = record
                            ctx.environments[item.display_name] = record
                self._record_failures(ctx, Resource.ENVIRONMENT_DEFINITIONS,
                                      attempted, read, forbidden, transient,
                                      artifacts=failed_artifacts)

            # The workspace's *default* Spark runtime, for notebooks that bind to no
            # named Environment. Deliberately outside the ENVIRONMENT_DEFINITIONS
            # block above: a workspace with no Environment items still has a default
            # runtime, and that is exactly the case this exists to answer. Keyed on
            # the notebook resource because it is the notebook checks that read it.
            # One call per workspace, not per item.
            if Resource.NOTEBOOK_DEFINITIONS in wanted or Resource.ENVIRONMENT_DEFINITIONS in wanted:
                ctx.spark_settings = self._spark_settings(workspace_id)

            # The expensive one — one call per pipeline. Only paid for when a
            # selected check actually reads a pipeline definition.
            if Resource.PIPELINE_DEFINITIONS in wanted:
                pipelines = [i for i in ctx.items if i.type == "DataPipeline"]
                fetched = self._fetch_items_parallel(
                    pipelines, lambda it: self._pipeline_definition(workspace_id, it.id))
                attempted = read = forbidden = transient = empty = 0
                failed_artifacts = []
                for item, (definition, failure) in zip(pipelines, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    elif definition:
                        read += 1
                        key = self._unique_key(ctx.pipelines, item.display_name or item.id, item.id)
                        ctx.pipelines[key] = definition
                    else:
                        empty += 1
                        failed_artifacts.append(_failure_detail(item, "empty"))
                self._record_failures(ctx, Resource.PIPELINE_DEFINITIONS,
                                      attempted, read, forbidden, transient, empty,
                                      artifacts=failed_artifacts)

            # Notebook definitions: same one-call-per-item getDefinition pattern.
            if Resource.NOTEBOOK_DEFINITIONS in wanted:
                found = [i for i in ctx.items if i.type == "Notebook"]

                def _notebook_bundle(it):
                    definition, failure = self._notebook_definition(workspace_id, it.id)
                    monitoring = self._notebook_monitoring(workspace_id, it.id) if definition else {}
                    return definition, failure, monitoring

                fetched = self._fetch_items_parallel(found, _notebook_bundle)
                attempted = read = forbidden = transient = empty = 0
                failed_artifacts = []
                for item, (definition, failure, monitoring) in zip(found, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    elif definition:
                        read += 1
                        binding = self._environment_binding(definition)
                        environment = ctx.environments.get(binding)
                        if environment:
                            definition["_auditfast_environment"] = {
                                "id": environment.get("id", binding),
                                "name": environment.get("display_name", binding),
                                "runtime_version": environment.get("runtime_version"),
                            }
                        if monitoring:
                            definition["_auditfast_monitoring"] = monitoring
                        ctx.notebooks[item.display_name or item.id] = definition
                self._record_failures(ctx, Resource.NOTEBOOK_DEFINITIONS,
                                      attempted, read, forbidden, transient, empty,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: %d notebooks found, %d definitions read",
                         workspace_id, len(found), len(ctx.notebooks))

            # Lakehouse table listing (name/type/format). Column schemas need the SQL
            # analytics endpoint and are left empty here; column-level checks report
            # N/A rather than failing when they are absent.
            if Resource.TABLE_SCHEMAS in wanted:
                lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
                fetched = self._fetch_items_parallel(
                    lakehouses, lambda it: self._lakehouse_tables(workspace_id, it.id))
                attempted = read = forbidden = transient = 0
                failed_artifacts = []
                # ``item`` is intentionally unused: a Lakehouse table is stored under
                # its own name, not the Lakehouse's. Two Lakehouses holding a table
                # of the same name therefore collide here - the second wins - which
                # the SQL column reader repairs by re-filing collisions under
                # ``<store>.<table>``. Keeping the unpacking symmetrical with every
                # other parallel-fetch loop is worth more than renaming this one.
                for _item, (tables, failure) in zip(lakehouses, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(_item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(_item, failure))
                    else:
                        read += 1
                        for tbl in tables:
                            name = tbl.get("name")
                            if name:
                                ctx.tables[name] = {
                                    "type": tbl.get("type", ""),
                                    "format": tbl.get("format", ""),
                                    "columns": [],
                                }
                self._record_failures(ctx, Resource.TABLE_SCHEMAS,
                                      attempted, read, forbidden, transient,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: %d lakehouses, %d tables read",
                         workspace_id, len(lakehouses), len(ctx.tables))

            # Column schemas + Warehouse RLS, over the SQL analytics endpoint. Not in
            # the Fabric REST API at all - only reachable over TDS (port 1433). Every
            # failure here leaves the data absent, which the checks already report as
            # N/A, so a blocked port degrades to the pre-SQL behaviour rather than
            # failing the crawl.
            if Resource.TABLE_COLUMNS in wanted or Resource.WAREHOUSE_SECURITY in wanted:
                self._read_sql_endpoints(ctx, workspace_id, wanted)

            # OneLake Files listing per Lakehouse. The ADLS Gen2 API can return very
            # large listings, so the OneLake client aggregates during fetch and caps
            # enumeration. The KB stores only bounded counts/buckets, not file paths.
            if Resource.LAKEHOUSE_FILES in wanted:
                lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
                attempted = read = forbidden = transient = 0
                failed_artifacts = []
                onelake = self._onelake()
                if lakehouses and onelake is None:
                    ctx.unavailable.add(Resource.LAKEHOUSE_FILES)
                    forbidden = len(lakehouses)
                    attempted = len(lakehouses)
                    log.warning(
                        "fetch %s: %d lakehouse Files listing(s) need a Storage-audience "
                        "token; lakehouse file checks will be N/A",
                        workspace_id, len(lakehouses),
                    )
                elif onelake is not None:
                    fetched = self._fetch_items_parallel(
                        lakehouses,
                        lambda it: onelake.lakehouse_files_summary(workspace_id, it.id))
                    for item, (summary, failure) in zip(lakehouses, fetched, strict=True):
                        attempted += 1
                        if failure == "forbidden":
                            forbidden += 1
                            failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                        elif failure == "transient":
                            transient += 1
                            failed_artifacts.append(_failure_detail(item, failure))
                        else:
                            read += 1
                            key = self._unique_key(
                                ctx.lakehouse_files, item.display_name or item.id, item.id
                            )
                            ctx.lakehouse_files[key] = summary
                            # Delta table data lives under Tables/, not Files/. Summarising
                            # only Files/ measured whatever loose files sat in the landing
                            # area and reported "0 of 3 data files in band" for a Lakehouse
                            # whose actual Delta data was never looked at.
                            tables_summary, tables_failure = onelake.lakehouse_tables_summary(
                                workspace_id, item.id)
                            if not tables_failure:
                                ctx.lakehouse_tables_files[key] = tables_summary
                            self._annotate_partitions(ctx, onelake, workspace_id, item)
                self._record_failures(ctx, Resource.LAKEHOUSE_FILES,
                                      attempted, read, forbidden, transient,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: lakehouse Files summaries read for %d of %d lakehouse(s)",
                         workspace_id, read, len(lakehouses))

            # Warehouse SQL audit *configuration* — plain Fabric REST, one call per
            # Warehouse, gated on the Audit permission of the item (not tenant-admin).
            # No audit rows are ever read: this is a configuration audit, not a log
            # pull, so runtime data never enters the knowledge base.
            if Resource.WAREHOUSE_AUDIT in wanted:
                warehouses = [i for i in ctx.items if i.type == "Warehouse"]
                fetched = self._fetch_items_parallel(
                    warehouses, lambda it: self._warehouse_audit(workspace_id, it.id))
                attempted = read = forbidden = transient = empty = 0
                failed_artifacts = []
                for item, (settings, failure) in zip(warehouses, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    elif settings is None:
                        empty += 1
                        failed_artifacts.append(_failure_detail(item, "empty"))
                    else:
                        read += 1
                        key = self._unique_key(
                            ctx.warehouse_audit, item.display_name or item.id, item.id
                        )
                        ctx.warehouse_audit[key] = settings
                self._record_failures(ctx, Resource.WAREHOUSE_AUDIT,
                                      attempted, read, forbidden, transient, empty,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: sql audit settings read for %d of %d warehouse(s)",
                         workspace_id, read, attempted)

            # Data Activator (Reflex) rule definitions — one getDefinition per Reflex
            # item, decoded to bounded rule counts. Same Item.ReadWrite scope as any
            # getDefinition; when it is denied the definitions are unreadable and the
            # trigger-depth check reports N/A rather than failing a present Activator.
            if Resource.ACTIVATOR_DEFINITIONS in wanted:
                reflexes = [i for i in ctx.items if i.type in ("Reflex", "Activator")]
                fetched = self._fetch_items_parallel(
                    reflexes, lambda it: self._reflex_definition(workspace_id, it.id))
                attempted = read = forbidden = transient = empty = 0
                failed_artifacts = []
                for item, (summary, failure) in zip(reflexes, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    elif summary is not None:
                        read += 1
                        key = self._unique_key(
                            ctx.activators, item.display_name or item.id, item.id
                        )
                        ctx.activators[key] = summary
                    else:
                        empty += 1
                        failed_artifacts.append(_failure_detail(item, "empty"))
                self._record_failures(ctx, Resource.ACTIVATOR_DEFINITIONS,
                                      attempted, read, forbidden, transient, empty,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: activator definitions read for %d of %d reflex item(s)",
                         workspace_id, read, attempted)

            # OneLake shortcuts per lakehouse (governance/lineage: external references).
            if Resource.SHORTCUTS in wanted:
                shortcut_lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
                fetched = self._fetch_items_parallel(
                    shortcut_lakehouses,
                    lambda it: self._item_shortcuts(workspace_id, it.id))
                total = 0
                attempted = failed = 0
                for item, (shortcuts, known) in zip(shortcut_lakehouses, fetched, strict=True):
                    attempted += 1
                    if not known:
                        failed += 1
                        continue
                    if shortcuts:
                        ctx.shortcuts[item.display_name or item.id] = shortcuts
                        total += len(shortcuts)
                # Every listing failed: "could not ask" must not read as "has none".
                if attempted and failed == attempted:
                    ctx.unavailable.add(Resource.SHORTCUTS)
                log.info("fetch %s: %d shortcuts read (%d of %d listings failed)",
                         workspace_id, total, failed, attempted)

            if Resource.DATA_ACCESS_ROLES in wanted:
                role_lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
                fetched = self._fetch_items_parallel(
                    role_lakehouses,
                    lambda it: self._data_access_roles(workspace_id, it.id))
                attempted = failed = 0
                for item, (roles, known) in zip(role_lakehouses, fetched, strict=True):
                    attempted += 1
                    if not known:
                        failed += 1
                        continue
                    # Recorded even when empty: a Lakehouse with *no* data access
                    # role is the finding 6.2.6 is looking for, and dropping it would
                    # make that indistinguishable from an unreadable one.
                    ctx.data_access_roles[item.display_name or item.id] = roles
                if attempted and failed == attempted:
                    ctx.unavailable.add(Resource.DATA_ACCESS_ROLES)
                log.info("fetch %s: data access roles read for %d of %d lakehouse(s)",
                         workspace_id, attempted - failed, attempted)

            # Semantic-model measures + relationships, parsed from the TMSL definition.
            if Resource.SEMANTIC_MODEL_DEFINITIONS in wanted:
                models = [i for i in ctx.items if i.type == "SemanticModel"]
                fetched = self._fetch_items_parallel(
                    models, lambda it: self._semantic_model_definition(workspace_id, it.id))
                attempted = read = forbidden = transient = empty = 0
                failed_artifacts = []
                for item, (model, failure) in zip(models, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    elif model:
                        read += 1
                        key = self._unique_key(
                            ctx.semantic_models, item.display_name or item.id, item.id
                        )
                        ctx.semantic_models[key] = model
                    else:
                        empty += 1
                        failed_artifacts.append(_failure_detail(item, "empty"))
                self._record_failures(ctx, Resource.SEMANTIC_MODEL_DEFINITIONS,
                                      attempted, read, forbidden, transient, empty,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: %d semantic models parsed", workspace_id, len(ctx.semantic_models))

            # Semantic-model refresh *schedule configuration* — one Power BI Datasets
            # API GET per model, delegated scope only (no tenant-admin). Carries
            # ``notifyOption``, which is how "a refresh failure alerts the owning
            # team" is actually configured. No refresh rows are read.
            if Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE in wanted:
                schedule_models = [i for i in ctx.items if i.type == "SemanticModel"]
                fetched = self._fetch_items_parallel(
                    schedule_models,
                    lambda it: self._semantic_model_refresh_schedule(workspace_id, it.id))
                attempted = read = forbidden = transient = 0
                schedule_reasons: dict[str, int] = {}
                failed_artifacts = []
                for item, (schedule, failure) in zip(schedule_models, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        reason = _SCHEDULE_FORBIDDEN_REASON
                    elif failure == "transient":
                        transient += 1
                        reason = _SCHEDULE_TRANSIENT_REASON
                    else:
                        # A model with no schedule read cleanly: absence from the map
                        # is the finding, so it must not count as a failure.
                        read += 1
                        if schedule is not None:
                            key = self._unique_key(
                                ctx.refresh_schedules, item.display_name or item.id, item.id
                            )
                            ctx.refresh_schedules[key] = schedule
                        continue
                    failed_artifacts.append(_failure_detail(item, failure, reason))
                    schedule_reasons[reason] = schedule_reasons.get(reason, 0) + 1
                self._record_failures(ctx, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
                                      attempted, read, forbidden, transient,
                                      reasons=schedule_reasons,
                                      artifacts=failed_artifacts)
                log.info("fetch %s: refresh schedule read for %d of %d semantic model(s) "
                         "(%d forbidden, %d transient)",
                         workspace_id, read, attempted, forbidden, transient)

            return ctx

        def list_workspaces(self) -> list[dict]:
            rows, _known = self._values("/workspaces")
            return [
                {
                    "id": row.get("id"),
                    "name": row.get("displayName", row.get("id")),
                    "layer": "",
                    "items": None,
                    "pipelines": None,
                }
                for row in rows
            ]

        # -- diagnostics -----------------------------------------------------------
        def probe(self, max_workspaces: int = 3) -> dict:
            """Report what this token can actually read, per sub-resource.

        Used by the Diagnose button when a live run returns less than expected —
        it surfaces partial permissions (for example: items readable, role
        assignments forbidden) that would otherwise look like clean passes.
        """
            result: dict[str, Any] = {"list_status": None, "count": 0, "samples": [], "error": None}
            status, body = self._get("/workspaces")
            result["list_status"] = status
            if status != 200 or not isinstance(body, dict):
                result["error"] = f"Listing workspaces returned HTTP {status}."
                return result

            workspaces = body.get("value") or []
            result["count"] = len(workspaces)
            member_of = 0
            for workspace in workspaces[:max_workspaces]:
                workspace_id = workspace.get("id")
                items_status, items_body = self._get(f"/workspaces/{workspace_id}/items")
                roles_status, _ = self._get(f"/workspaces/{workspace_id}/roleAssignments")
                if roles_status == 200:
                    member_of += 1
                items = (items_body or {}).get("value", []) if items_status == 200 else []
                result["samples"].append({
                    "name": workspace.get("displayName", workspace_id),
                    "items_status": items_status,
                    "items": len(items),
                    "pipelines": sum(1 for i in items if i.get("type") == "DataPipeline"),
                    "roles_status": roles_status,
                })

            result["admin"] = self._probe_admin(member_of, len(result["samples"]))
            return result

        def _probe_admin(self, member_of: int, sampled: int) -> dict:
            """Can this token read what the elevated-access checks need?

        Three independent capabilities, probed with one cheap call each, because
        they fail independently: a workspace Admin with no gateway role reads
        role assignments but not gateways.

        ``roleAssignments`` returning 200 is the practical test for "Member or
        higher" — the role itself is not exposed anywhere the caller can read, so
        the read *is* the permission check. It is sampled over the same few
        workspaces the caller already probed, so this adds no extra requests.
        """
            connections_status, _ = self._get("/connections")
            gateways_status, _ = self._get("/gateways")
            return {
                "connections_status": connections_status,
                "gateways_status": gateways_status,
                "member_workspaces": member_of,
                "sampled_workspaces": sampled,
                "role_assignments_readable": member_of > 0,
                "connections_readable": connections_status == 200,
                "gateways_readable": gateways_status == 200,
            }

    return _collector_namespace(locals())

def load_collector(*, metadata_only=False):
    """Return DTOs/errors only, or the full collectors, without live dependency imports."""
    global _collector_runtime, _collector_metadata_runtime, _collector_modules
    with _collector_lock:
        if _collector_metadata_runtime is None:
            _collector_modules = {}
            _collector_modules['core.enums'] = _collector_build_core_enums(_collector_modules)
            _collector_modules['core.models'] = _collector_build_core_models(_collector_modules)
            _collector_modules['core.errors'] = _collector_build_core_errors(_collector_modules)
            _collector_metadata_runtime = _collector_SimpleNamespace(
                Layer=_collector_modules['core.enums'].Layer,
                Resource=_collector_modules['core.enums'].Resource,
                Item=_collector_modules['core.models'].Item,
                RoleAssignment=_collector_modules['core.models'].RoleAssignment,
                WorkspaceContext=_collector_modules['core.models'].WorkspaceContext,
                ProviderError=_collector_modules['core.errors'].ProviderError,
                WorkspaceAccessError=_collector_modules['core.errors'].WorkspaceAccessError,
            )
            globals().update(vars(_collector_metadata_runtime))
        if metadata_only:
            return _collector_metadata_runtime
        if _collector_runtime is None:
            _collector_modules['clients.base'] = _collector_build_clients_base(_collector_modules)
            _collector_modules['clients.errors'] = _collector_build_clients_errors(_collector_modules)
            _collector_modules['clients.tmsl'] = _collector_build_clients_tmsl(_collector_modules)
            _collector_modules['clients.onelake'] = _collector_build_clients_onelake(_collector_modules)
            _collector_modules['clients.sqlendpoint'] = _collector_build_clients_sqlendpoint(_collector_modules)
            _collector_modules['discovery.scanner'] = _collector_build_discovery_scanner(_collector_modules)
            _collector_modules['clients.powerbi'] = _collector_build_clients_powerbi(_collector_modules)
            _collector_modules['clients.live'] = _collector_build_clients_live(_collector_modules)
            _collector_runtime = _collector_SimpleNamespace(
                Layer=_collector_modules['core.enums'].Layer,
                Resource=_collector_modules['core.enums'].Resource,
                Item=_collector_modules['core.models'].Item,
                RoleAssignment=_collector_modules['core.models'].RoleAssignment,
                WorkspaceContext=_collector_modules['core.models'].WorkspaceContext,
                ProviderError=_collector_modules['core.errors'].ProviderError,
                WorkspaceAccessError=_collector_modules['core.errors'].WorkspaceAccessError,
                LiveFabricProvider=_collector_modules['clients.live'].LiveFabricProvider,
                PowerBIClient=_collector_modules['clients.powerbi'].PowerBIClient,
                PowerBIError=_collector_modules['clients.powerbi'].PowerBIError,
                OneLakeClient=_collector_modules['clients.onelake'].OneLakeClient,
                SqlEndpoint=_collector_modules['clients.sqlendpoint'].SqlEndpoint,
                SqlEndpointReader=_collector_modules['clients.sqlendpoint'].SqlEndpointReader,
                parse_tmsl=_collector_modules['clients.tmsl'].parse_tmsl,
            )
            globals().update(vars(_collector_runtime))
        return _collector_runtime


def load_snapshot_model():
    """Return only the production enums, snapshot dataclasses, and provider errors."""
    return load_collector(metadata_only=True)


def __getattr__(name):
    if name in _collector_metadata_exports:
        return getattr(load_collector(metadata_only=True), name)
    if name in _collector_exports:
        return getattr(load_collector(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_collector_exports = frozenset(('Layer', 'Resource', 'Item', 'RoleAssignment', 'WorkspaceContext', 'ProviderError', 'WorkspaceAccessError', 'LiveFabricProvider', 'PowerBIClient', 'PowerBIError', 'OneLakeClient', 'SqlEndpoint', 'SqlEndpointReader', 'parse_tmsl',))
_collector_metadata_exports = frozenset(('Layer', 'Resource', 'Item', 'RoleAssignment', 'WorkspaceContext', 'ProviderError', 'WorkspaceAccessError',))
# --- Standalone command-line interface ---
"""Standalone command-line interface."""
import argparse
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4


_FABRIC_AUDIENCE = "https://api.fabric.microsoft.com"
_POWERBI_AUDIENCE = "https://analysis.windows.net/powerbi/api"
_SQL_AUDIENCE = "https://database.windows.net"
_STORAGE_AUDIENCE = "https://storage.azure.com"
_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
_LAYERS = (
    "Mixed", "Data Prep", "Data Storage", "Data Logs",
    "Data Operations", "Reporting / Semantic",
)
_WORKSPACE_RESOURCE_NAMES = (
    "workspace", "items", "roleAssignments", "git", "pipelineDefinitions",
    "notebookDefinitions", "environmentDefinitions", "tableSchemas", "tableColumns",
    "warehouseSecurity", "warehouseAudit", "shortcuts", "semanticModelDefinitions",
    "semanticModelRefreshSchedule", "connections", "reports", "itemRunHistory",
    "lakehouseFiles", "activatorDefinitions",
)
_ELEVATED_CATEGORY = "Tenant"
_ELEVATED_PROFILES = {
    "Workspace Admin": {
        "name": "workspace-admin",
        "output": "fabric-admin-kb",
        "resources": ("workspace", "items", "roleAssignments", "connections", "gateways", "dataAccessRoles"),
        "access": (
            "Needs Member/Admin on selected workspaces, plus access to each connection/gateway "
            "and the corresponding Connection.Read.All, Gateway.Read.All and OneLake.Read.All scopes."
        ),
    },
    "Capacity": {
        "name": "capacity-admin",
        "output": "fabric-capacity-kb",
        "resources": ("workspace", "capacityMetrics"),
        "access": (
            "Needs access to the Capacity Metrics app's semantic model and permission to execute "
            "read-only DAX queries. Capacity admin alone does not grant model access."
        ),
    },
    "Tenant": {
        "name": "tenant-admin",
        "output": "fabric-tenant-kb",
        "resources": ("workspace", "items", "tenantSettings", "tenantDomains", "adminScanner", "adminActivity"),
        "access": (
            "Needs Fabric tenant administrator access and consent for the tenant settings, "
            "domains, activity and scanner APIs, plus read access to selected workspace metadata."
        ),
    },
}


class CollectorError(Exception):
    """A safe, actionable collector failure; never carries token payloads."""


def _uuid_argument(value):
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("Expected a tenant/workspace/client UUID.") from exc


def _positive_timeout(value):
    try:
        timeout = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Timeout must be an integer.") from exc
    if not 1 <= timeout <= 3600:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 3600 seconds.")
    return timeout


def build_parser():
    profile = _ELEVATED_PROFILES[_ELEVATED_CATEGORY]
    script = Path(__file__).name
    parser = argparse.ArgumentParser(
        description=(
            f"Collect {_ELEVATED_CATEGORY} evidence into archive-compatible Microsoft Fabric KB JSON. "
            "No AuditFAST installation is needed. No checks or AI are executed."
        ),
        epilog=(
            "Install approved dependencies: python -m pip install requests PyYAML msal\n"
            "This elevated profile does not read SQL metadata, notebook or pipeline definitions.\n"
            + profile["access"] + "\n\n"
            "Examples:\n"
            f"  python {script} --self-test\n"
            f"  python {script} --auth browser\n"
            f"  python {script} --auth browser --list-workspaces\n"
            f"  python {script} --auth browser --tenant-id TENANT_UUID --workspace-id WORKSPACE_UUID\n\n"
            "Exports may include tenant-wide settings/events, identities and metrics outside "
            "selected workspaces. Review before sharing. Replay through source='kb', "
            f"check_set='admin', admin_categories=['{_ELEVATED_CATEGORY}']; the standard audit "
            "and the live-only Admin Checks page do not select this offline category."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=COLLECTOR_VERSION)
    parser.add_argument(
        "--self-test", action="store_true",
        help="Run built-in synthetic collection/export tests without sign-in or network calls.",
    )
    parser.add_argument(
        "--tenant-id", type=_uuid_argument,
        help=(
            "Optional Microsoft Entra tenant UUID. When omitted, use organizational sign-in "
            "or Azure CLI's active tenant. Specify it for a particular customer/guest tenant."
        ),
    )
    parser.add_argument(
        "--workspace-id", type=_uuid_argument, action="append", default=[],
        help=(
            "Workspace UUID to collect; repeat for multiple workspaces. "
            "When omitted, sign in and choose from a numbered list. No implicit tenant-wide crawl."
        ),
    )
    parser.add_argument(
        "--list-workspaces", action="store_true",
        help="Sign in, list visible workspaces and exit without collecting or prompting for selection.",
    )
    parser.add_argument(
        "--auth", choices=("device-code", "browser", "azure-cli"), default="device-code",
        help="Client-owned sign-in method (default: device-code). Tenant policy must permit it.",
    )
    parser.add_argument(
        "--client-id", type=_uuid_argument,
        help="Optional approved public-client app UUID for MSAL sign-in; no client secret is used.",
    )
    parser.add_argument("--layer", choices=_LAYERS, default="Mixed", help="Workspace layer (default: Mixed).")
    parser.add_argument(
        "--output-dir", type=Path, default=Path(profile["output"]),
        help=f"Parent for a new timestamped output directory (default: {profile['output']}).",
    )
    parser.add_argument(
        "--timeout", type=_positive_timeout, default=180,
        help="Per-request/definition-poll timeout in seconds, not a whole-crawl deadline (default: 180).",
    )
    if _ELEVATED_CATEGORY == "Capacity":
        parser.add_argument(
            "--capacity-metrics-workspace",
            help="Metrics app workspace name/UUID hint, not a filter. Prompts when omitted.",
        )
        parser.add_argument(
            "--capacity-metrics-model", type=_nonempty_text,
            help="Model-name substring filter. Prompts when omitted (default: Capacity Metrics).",
        )
        parser.add_argument(
            "--capacity-peak-start-hour", type=_utc_hour,
            help="Peak window start hour in UTC, 0-23. Prompts when omitted (default: 8).",
        )
        parser.add_argument(
            "--capacity-peak-end-hour", type=_utc_hour,
            help="Peak window end hour in UTC, 0-23. Prompts when omitted (default: 18).",
        )
    return parser


def _dependency_check(auth, *, self_test=False):
    required = [("requests", "requests"), ("yaml", "PyYAML")]
    if not self_test and auth != "azure-cli":
        required.append(("msal", "msal"))
    missing = [package for module, package in required if importlib.util.find_spec(module) is None]
    if missing:
        raise CollectorError(
            "Missing Python dependencies. Install them in an approved environment with:\n"
            "  python -m pip install " + " ".join(missing)
        )
    if not self_test and auth == "azure-cli" and not shutil.which("az"):
        raise CollectorError(
            "Azure CLI is not available. Use an approved Azure CLI installation, "
            "or select --auth browser / --auth device-code."
        )


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_auth_code(result):
    if not isinstance(result, dict):
        return "authentication_failed"
    code = result.get("error", "authentication_failed")
    return code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) else "authentication_failed"


class ClientTokens:
    """Client-local delegated tokens; never serialized, printed or persisted by this script."""

    def __init__(self, tenant_id, method, client_id=None):
        self.tenant_id = tenant_id
        self.method = method
        self.client_id = client_id or _PUBLIC_CLIENT_ID
        self._app = None
        self._account = None
        self._tokens = {}
        self._known_secrets = set()
        self._lock = threading.RLock()
        self.audience_status = {}

    def _bind_tenant(self, value):
        if not isinstance(value, str):
            raise CollectorError(
                "Authentication did not return a tenant ID. Retry sign-in with --tenant-id TENANT_UUID."
            )
        try:
            tenant_id = _uuid_argument(value)
        except argparse.ArgumentTypeError as exc:
            raise CollectorError(
                "Authentication returned an invalid tenant ID. Retry sign-in with --tenant-id TENANT_UUID."
            ) from exc
        if self.tenant_id is not None and tenant_id != self.tenant_id:
            raise CollectorError("Authentication returned a token for a different tenant; collection stopped.")
        self.tenant_id = tenant_id

    def _remember(self, audience, token, expires_in):
        if not isinstance(token, str) or not token:
            raise CollectorError("Authentication returned no usable access token.")
        try:
            lifetime = max(0, int(expires_in))
        except (ValueError, TypeError):
            lifetime = 0
        self._tokens[audience] = (token, time.time() + lifetime)
        self._known_secrets.add(token)
        self.audience_status[audience] = "available"
        return token

    def sign_in(self):
        if self.method == "azure-cli":
            return self.acquire(_FABRIC_AUDIENCE, required=True, force_refresh=True)
        import msal

        import requests

        authority_tenant = self.tenant_id or "organizations"
        try:
            self._app = msal.PublicClientApplication(
                self.client_id,
                authority=f"https://login.microsoftonline.com/{authority_tenant}",
            )
            scopes = [_FABRIC_AUDIENCE + "/.default"]
            if self.method == "browser":
                result = self._app.acquire_token_interactive(scopes=scopes)
            else:
                flow = self._app.initiate_device_flow(scopes=scopes)
                if not flow.get("user_code"):
                    raise CollectorError(
                        f"Device sign-in could not start ({_safe_auth_code(flow)}). "
                        "Check tenant policy and the approved client app."
                    )
                uri = flow.get("verification_uri")
                if not isinstance(uri, str) or not uri.startswith("https://"):
                    raise CollectorError("Microsoft sign-in returned an invalid verification URL.")
                print(f"Open {uri} and enter the one-time device code: {flow['user_code']}")
                print("Complete sign-in with the client's account. Do not send this code to anyone.")
                result = self._app.acquire_token_by_device_flow(flow)
        except (requests.exceptions.RequestException, ValueError) as exc:
            raise CollectorError(
                "Microsoft sign-in could not complete. Check connectivity, tenant policy "
                "and the approved public-client configuration."
            ) from exc
        if not isinstance(result, dict) or not result.get("access_token"):
            raise CollectorError(
                f"Sign-in failed ({_safe_auth_code(result or {})}). "
                "Confirm the tenant, consent, identity permissions and allowed sign-in flow."
            )
        claims = result.get("id_token_claims")
        if not isinstance(claims, dict):
            raise CollectorError("Microsoft sign-in did not return account/tenant details; retry sign-in.")
        self._bind_tenant(claims.get("tid"))
        accounts = self._app.get_accounts()
        account_id = claims.get("oid")
        # MSAL's cache realm follows the token endpoint, so it can be
        # "organizations" rather than the tenant GUID in the signed-in claims.
        matches = [
            account for account in accounts
            if account_id and account.get("local_account_id") == account_id
            and account.get("realm") in (self.tenant_id, authority_tenant)
        ]
        if len(matches) != 1:
            raise CollectorError(
                "The signed-in account could not be matched uniquely in the authentication cache. "
                "Retry sign-in."
            )
        self._account = matches[0]
        if authority_tenant != self.tenant_id:
            # acquire_token_silent ignores its authority argument. Bind the app
            # itself to the resolved tenant, retaining the in-memory refresh tokens.
            try:
                self._app = msal.PublicClientApplication(
                    self.client_id,
                    authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                    token_cache=self._app.token_cache,
                )
            except (requests.exceptions.RequestException, ValueError) as exc:
                raise CollectorError(
                    "Sign-in succeeded, but the tenant-specific token client could not initialize. "
                    "Check connectivity and retry sign-in."
                ) from exc
        return self._remember(_FABRIC_AUDIENCE, result["access_token"], result.get("expires_in", 0))

    def _az_token(self, audience):
        executable = shutil.which("az")
        if not executable:
            raise CollectorError("Azure CLI is not available.")
        command = [executable, "account", "get-access-token"]
        if self.tenant_id is not None:
            command.extend(["--tenant", self.tenant_id])
        command.extend([
            "--resource", audience, "--output", "json", "--only-show-errors",
        ])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
        if completed.returncode:
            return None
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(result, dict) or not result.get("accessToken"):
            return None
        self._bind_tenant(result.get("tenant"))
        expires_on = result.get("expires_on")
        try:
            lifetime = max(0, int(expires_on) - int(time.time())) if expires_on else 0
        except (TypeError, ValueError):
            lifetime = 0
        return self._remember(audience, result["accessToken"], lifetime)

    def acquire(self, audience, *, required=False, force_refresh=False):
        import requests

        with self._lock:
            current = self._tokens.get(audience)
            if current and not force_refresh and current[1] > time.time() + 300:
                return current[0]
            try:
                if self.method == "azure-cli":
                    token = self._az_token(audience)
                else:
                    result = None
                    if self._app is not None and self._account is not None:
                        result = self._app.acquire_token_silent(
                            [audience.rstrip("/") + "/.default"],
                            account=self._account,
                            force_refresh=force_refresh,
                        )
                    if result and result.get("access_token") and "id_token_claims" in result:
                        claims = result["id_token_claims"]
                        self._bind_tenant(claims.get("tid") if isinstance(claims, dict) else None)
                    token = (
                        self._remember(audience, result["access_token"], result.get("expires_in", 0))
                        if result and result.get("access_token") else None
                    )
            except (requests.exceptions.RequestException, subprocess.TimeoutExpired) as exc:
                self.audience_status[audience] = "unavailable"
                if required:
                    raise CollectorError("Fabric token acquisition failed because authentication was unreachable.") from exc
                logging.warning("Token acquisition for %s was unavailable; dependent evidence may be missing.", audience)
                return None
            if token:
                return token
            self.audience_status[audience] = "unavailable"
            if required:
                raise CollectorError(
                    "A Fabric access token could not be acquired. For Azure CLI, first run "
                    "'az login' (add --tenant TENANT_UUID for a specific tenant). Otherwise check "
                    "consent, account permissions and tenant sign-in policy."
                )
            logging.warning("No token for %s; dependent evidence may be unavailable.", audience)
            return None

    def known_secrets(self):
        with self._lock:
            return tuple(self._known_secrets)

    def clear(self):
        with self._lock:
            self._tokens.clear()
            self._known_secrets.clear()
            self._app = None
            self._account = None


class _TokenRedactionFilter(logging.Filter):
    def __init__(self, tokens):
        super().__init__()
        self.tokens = tokens

    def filter(self, record):
        message = record.getMessage()
        for value in self.tokens.known_secrets():
            message = message.replace(value, "[REDACTED]")
        message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", message)
        record.msg, record.args = message, ()
        # Provider warnings are useful; arbitrary exception tracebacks may contain requests.
        record.exc_info = None
        record.exc_text = None
        return True


def _configure_logging(tokens):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handler.addFilter(_TokenRedactionFilter(tokens))
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    return handler


def workspace_resources(runtime):
    known = {resource.value: resource for resource in runtime.Resource}
    missing = set(_WORKSPACE_RESOURCE_NAMES) - set(known)
    if missing:
        raise CollectorError("Embedded collection resource profile is incompatible with its snapshot schema.")
    return frozenset(known[name] for name in _WORKSPACE_RESOURCE_NAMES)


def _write_json(path, value, *, forbidden_values=()):
    try:
        payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str)
    except (ValueError, TypeError) as exc:
        raise CollectorError("Collected content could not be serialized as valid KB JSON.") from exc
    if any(secret and secret in payload for secret in forbidden_values):
        raise CollectorError(
            "Refusing to export collector authentication material. No unsafe snapshot was written."
        )
    encoded = payload.encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(encoded).hexdigest()


def _truncated_fields(snapshot):
    """Read provider-owned OneLake listing flags, never settings inside item definitions."""
    return [
        f"{field}.{name}.truncated"
        for field in ("lakehouse_files", "lakehouse_tables_files")
        for name, listing in snapshot.get(field, {}).items()
        if listing.get("truncated") is True
    ]


def _has_read_failures(context, requested):
    keys = {resource.value for resource in requested}
    for resource, result in context.read_failures.items():
        if resource not in keys:
            continue
        if any(result.get(key, 0) for key in ("failed", "forbidden", "transient", "empty")):
            return True
        if result.get("reasons") and not result.get("read"):
            return True
    return False


def _new_run_directory(parent, prefix="fabric-kb"):
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = parent / f"{prefix}_{stamp}_{uuid4().hex[:8]}"
    directory.mkdir(mode=0o700)
    return directory


def collect_workspaces(
    runtime, provider_factory, workspace_ids, layer, output_dir, *, tokens=None,
    resources=None, category=None, crawl_settings=None,
):
    """Run the production collector and write raw archive-compatible workspace JSON."""
    requested = workspace_resources(runtime) if resources is None else frozenset(resources)
    excluded = frozenset(runtime.Resource) - requested
    profile = _ELEVATED_PROFILES[category] if category else None
    run_dir = _new_run_directory(Path(output_dir), profile["output"] if profile else "fabric-kb")
    summary = {
        "format": "fabric-workspace-kb-collection-summary",
        "summary_version": 1,
        "collector_version": COLLECTOR_VERSION,
        "embedded_source_sha256": COLLECTOR_SOURCE_SHA256,
        "captured_from": _utc_now(),
        "captured_until": None,
        "profile": profile["name"] if profile else "workspace-non-admin",
        "requested_resources": sorted(resource.value for resource in requested),
        "excluded_resources": sorted(resource.value for resource in excluded),
        "workspaces": [],
        "status": "running",
        "scope_note": (
            "Workspace JSON files use the production WorkspaceContext schema. "
            "Tenant/admin/capacity-only collection is excluded. Empty, unreadable and "
            "partial resources retain the production availability/failure semantics."
        ),
        "data_warning": (
            "Definitions can contain proprietary code, embedded secrets and notebook outputs. "
            "No automatic redaction of evidence is performed; review before sharing."
        ),
    }
    if profile:
        summary.update({
            "check_set": "admin",
            "admin_categories": [category],
            "admin_settings": dict(crawl_settings or {}),
            "scope_note": (
                f"Only {category} resources plus required workspace identity/inventory are collected. "
                "Tenant-wide and account-visible metadata may extend beyond selected workspaces. "
                "Unrequested resources are unavailable, not evidence of an empty configuration."
            ),
            "data_warning": (
                "Elevated evidence can include user identities, tenant activity, connection "
                "metadata and capacity workload details. Review before approved transfer."
            ),
        })
    summary_path = run_dir / "collection-summary.json"
    secrets = tokens.known_secrets() if tokens else ()
    _write_json(summary_path, summary, forbidden_values=secrets)
    failures = 0
    partial = 0
    for index, workspace_id in enumerate(workspace_ids, start=1):
        print(f"[{index}/{len(workspace_ids)}] Collecting workspace {workspace_id} ...")
        started = _utc_now()
        try:
            provider = provider_factory()
            context = provider.fetch(workspace_id, layer, requested)
        except (runtime.WorkspaceAccessError, runtime.PowerBIError) as exc:
            failures += 1
            summary["workspaces"].append({
                "workspace_id": workspace_id,
                "status": "failed",
                "http_status": exc.status,
                "reason": "Workspace/elevated metadata could not be read; verify access, identity and connectivity.",
                "capture_started_at": started,
                "capture_finished_at": _utc_now(),
            })
            print(f"  Failed: workspace/elevated read problem (HTTP {exc.status or 'unavailable'}).")
        else:
            if context.id != workspace_id:
                raise CollectorError("Collector returned an unexpected workspace identity; export stopped.")
            context.unavailable.update(excluded)
            payload = context.to_dict()
            # Use the same normalizer in both directions before handing off evidence.
            if runtime.WorkspaceContext.from_dict(payload).to_dict() != payload:
                raise CollectorError("Workspace snapshot failed its serialization round-trip validation.")
            unavailable = sorted(resource.value for resource in context.unavailable & requested)
            truncated = _truncated_fields(payload)
            incomplete = bool(unavailable or truncated or _has_read_failures(context, requested))
            partial += int(incomplete)
            filename = f"{workspace_id}.json"
            digest = _write_json(
                run_dir / filename, payload,
                forbidden_values=tokens.known_secrets() if tokens else (),
            )
            summary["workspaces"].append({
                "workspace_id": workspace_id,
                "workspace_name": context.name,
                "layer": context.layer.value,
                "status": "partial" if incomplete else "collected",
                "file": filename,
                "sha256": digest,
                "capture_started_at": started,
                "capture_finished_at": _utc_now(),
                "items": len(context.items),
                "notebooks_read": len(context.notebooks),
                "pipelines_read": len(context.pipelines),
                "tables_read": len(context.tables),
                "semantic_models_read": len(context.semantic_models),
                "unavailable_requested_resources": unavailable,
                "read_failures": context.read_failures,
                "truncation_flags": truncated,
                "cache_completeness": context.is_complete,
            })
            if profile:
                summary["workspaces"][-1]["elevated_evidence"] = {
                    "role_assignments": len(context.role_assignments),
                    "connections": len(context.connections),
                    "gateways": len(context.gateways),
                    "lakehouses_with_data_access_roles_read": len(context.data_access_roles),
                    "tenant_settings": len(context.tenant_settings),
                    "tenant_domains": len(context.tenant_domains),
                    "activity_events": len(context.activity_events),
                    "endorsement_items": len(context.admin_scan.get("items", [])),
                    "capacity_model_found": context.capacity_metrics.get("model_found"),
                    "capacity_model_id": context.capacity_metrics.get("model_id"),
                    "capacity_model_workspace_id": context.capacity_metrics.get("model_workspace_id"),
                }
            print(f"  {'Partial' if incomplete else 'Collected'}: {filename}")
            if unavailable:
                print("  Unavailable evidence: " + ", ".join(unavailable))
            if truncated:
                print("  Truncated evidence: " + ", ".join(truncated))
        summary["captured_until"] = _utc_now()
        _write_json(
            summary_path, summary,
            forbidden_values=tokens.known_secrets() if tokens else (),
        )
    summary["status"] = "failed" if failures == len(workspace_ids) else "partial" if failures or partial else "collected"
    summary["captured_until"] = _utc_now()
    if tokens:
        summary["token_audience_status"] = dict(tokens.audience_status)
    _write_json(
        summary_path, summary,
        forbidden_values=tokens.known_secrets() if tokens else (),
    )
    print(f"KB output: {run_dir.resolve()}")
    print(f"Collection status: {summary['status']}. No audit checks or AI calls were executed.")
    if category:
        print(f"Replay these JSON files with source='kb', check_set='admin', admin_categories=['{category}'].")
        print("Use the offline API steps in the README; the current Admin Checks page is live-only.")
    else:
        print("Upload the workspace UUID JSON file(s) to the tool's saved-KB audit; do not upload the summary as a workspace.")
    print("Partial output is retained: review its missing evidence before assessing coverage.")
    return (1 if summary["status"] == "failed" else 2 if summary["status"] == "partial" else 0), summary


def _create_provider(runtime, tokens, timeout):
    fabric = tokens.acquire(_FABRIC_AUDIENCE, required=True)
    powerbi = tokens.acquire(_POWERBI_AUDIENCE)
    sql = tokens.acquire(_SQL_AUDIENCE)
    storage = tokens.acquire(_STORAGE_AUDIENCE)
    return runtime.LiveFabricProvider(
        fabric, timeout=timeout,
        token_refresher=lambda: tokens.acquire(_FABRIC_AUDIENCE, force_refresh=True),
        powerbi_token=powerbi,
        sql_token=sql,
        storage_token=storage,
        sql_token_refresher=lambda: tokens.acquire(_SQL_AUDIENCE, force_refresh=True),
    )


def discover_workspaces(provider):
    """Use production pagination, but do not hide failed discovery as an empty list."""
    records, readable = provider._values("/workspaces")
    if not readable:
        raise CollectorError(
            "Workspace listing could not be read. Check Fabric access, consent and connectivity. "
            "If you know the workspace UUID, you can retry with --workspace-id."
        )
    workspaces = {}
    for record in records:
        if not isinstance(record, dict):
            raise CollectorError("Fabric returned an invalid workspace-list entry.")
        try:
            workspace_id = str(UUID(record.get("id", "")))
        except (ValueError, AttributeError, TypeError) as exc:
            raise CollectorError("Fabric returned a workspace without a valid UUID.") from exc
        name = record.get("displayName") or record.get("name") or workspace_id
        if not isinstance(name, str):
            raise CollectorError("Fabric returned an invalid workspace display name.")
        existing = workspaces.get(workspace_id)
        if existing is not None and existing["name"] != name:
            raise CollectorError(
                "The workspace list changed during discovery. Retry before choosing workspaces."
            )
        workspaces[workspace_id] = {"id": workspace_id, "name": name}
    return sorted(workspaces.values(), key=lambda row: (row["name"].casefold(), row["id"]))


def _display_workspace_name(value):
    return "".join(character for character in " ".join(value.split()) if character.isprintable())


def print_workspace_list(workspaces):
    print(f"\nWorkspaces visible to this account: {len(workspaces)}")
    print("Visibility does not guarantee permission to read every definition or metadata resource.")
    for index, workspace in enumerate(workspaces, start=1):
        print(f"  {index:>3}. {_display_workspace_name(workspace['name'])}  [{workspace['id']}]")


def parse_workspace_selection(value, count):
    """Return one-based choices; blank input never silently means all."""
    value = value.strip().lower()
    if value in {"q", "quit", "cancel"}:
        return []
    if value == "all":
        return list(range(1, count + 1))
    if not value:
        raise ValueError("Enter workspace numbers, a range, 'all', or 'q' to cancel.")
    selected = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if match is None:
            raise ValueError("Use comma-separated numbers or ranges, for example 1,3-5.")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if not 1 <= first <= last <= count:
            raise ValueError(f"Selections must be between 1 and {count}, with ascending ranges.")
        selected.update(range(first, last + 1))
    return sorted(selected)


def choose_workspaces(workspaces, input_fn=None):
    if input_fn is None:
        input_fn = input
    if not workspaces:
        raise CollectorError("No Fabric workspaces are visible to this account in the selected tenant.")
    print_workspace_list(workspaces)
    while True:
        try:
            response = input_fn("\nChoose numbers (for example 1,3-5), 'all', or 'q' to cancel: ")
        except EOFError as exc:
            raise CollectorError(
                "Interactive selection is unavailable. Supply --workspace-id for a non-interactive run."
            ) from exc
        try:
            choices = parse_workspace_selection(response, len(workspaces))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")
            continue
        if not choices:
            print("Cancelled. No KB files were created.")
            return []
        selected = [workspaces[index - 1] for index in choices]
        print("\nSelected workspaces:")
        for workspace in selected:
            print(f"  {_display_workspace_name(workspace['name'])}  [{workspace['id']}]")
        try:
            confirmed = input_fn(f"Collect these {len(selected)} workspace(s)? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise CollectorError("Collection was not confirmed; no KB files were created.") from exc
        if confirmed in {"y", "yes"}:
            return [workspace["id"] for workspace in selected]
        print("Cancelled. No KB files were created.")
        return []


def run_self_test(runtime):
    """Synthetic provider, serialization and export checks; no real credentials or network."""
    import base64
    from unittest.mock import patch

    workspace_id = "00000000-0000-0000-0000-000000000001"
    missing_id = "00000000-0000-0000-0000-000000000002"
    notebook_id = "00000000-0000-0000-0000-000000000010"
    pipeline_id = "00000000-0000-0000-0000-000000000011"
    calls = []
    unknown_calls = []
    denied = False
    notebook = {
        "cells": [
            {"cell_type": "markdown", "source": ["Synthetic collection example"]},
            {"cell_type": "code", "source": ["value = 1"], "outputs": [], "execution_count": None},
        ],
        "nbformat": 4, "nbformat_minor": 5, "metadata": {},
    }
    pipeline = {"properties": {"activities": [{
        "name": "Copy example",
        "type": "Copy",
        "typeProperties": {"translator": {"typeConversionSettings": {"allowDataTruncation": True}}},
    }]}}

    class Response:
        def __init__(self, body, status=200):
            self.status_code = status
            self.headers = {}
            self._body = body

        def json(self):
            return self._body

    class Session:
        def __init__(self):
            self.headers = {}

        def mount(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(("GET", url))
            suffix = url.removeprefix("https://api.fabric.microsoft.com/v1")
            if suffix == "/workspaces":
                return Response({"value": [
                    {"id": workspace_id, "displayName": "Synthetic workspace"},
                ], "continuationUri": "https://api.fabric.microsoft.com/v1/workspaces?continuationToken=example"})
            if suffix == "/workspaces?continuationToken=example":
                return Response({"value": [
                    {"id": missing_id, "displayName": "Unavailable example"},
                ]})
            if suffix == f"/workspaces/{missing_id}":
                return Response({}, 403)
            if suffix == f"/workspaces/{workspace_id}":
                return Response({"id": workspace_id, "displayName": "Synthetic workspace", "capacityId": "test-capacity"})
            if suffix == f"/workspaces/{workspace_id}/items":
                return Response({"value": [
                    {"id": notebook_id, "type": "Notebook", "displayName": "Example notebook"},
                    {"id": pipeline_id, "type": "DataPipeline", "displayName": "Example pipeline"},
                ]})
            if suffix == f"/workspaces/{workspace_id}/roleAssignments":
                return Response({"value": [{"principal": {"id": "group-1", "type": "Group", "displayName": "Example group"}, "role": "Contributor"}]})
            if suffix == f"/workspaces/{workspace_id}/git/connection":
                return Response({}, 404)
            if suffix == f"/workspaces/{workspace_id}/spark/settings":
                return Response({"environment": {"runtimeVersion": "1.3"}})
            if suffix == f"/workspaces/{workspace_id}/notebooks/{notebook_id}/livySessions":
                return Response({"value": []})
            unknown_calls.append(("GET", suffix))
            raise AssertionError(f"Unexpected synthetic GET: {suffix}")

        def post(self, url, **_kwargs):
            calls.append(("POST", url))
            if notebook_id in url and "/getDefinition" in url:
                if denied:
                    return Response({}, 403)
                document, path = notebook, "notebook-content.ipynb"
            elif pipeline_id in url and "/getDefinition" in url:
                document, path = pipeline, "pipeline-content.json"
            else:
                unknown_calls.append(("POST", url))
                raise AssertionError("Unexpected synthetic POST; collection must not mutate items.")
            payload = base64.b64encode(json.dumps(document).encode("utf-8")).decode("ascii")
            return Response({"definition": {"parts": [{"path": path, "payloadType": "InlineBase64", "payload": payload}]}})

    resources = {
        runtime.Resource.WORKSPACE, runtime.Resource.ITEMS, runtime.Resource.ROLE_ASSIGNMENTS,
        runtime.Resource.GIT, runtime.Resource.NOTEBOOK_DEFINITIONS,
        runtime.Resource.PIPELINE_DEFINITIONS,
    }
    forbidden = {
        "tenantSettings", "tenantDomains", "adminScanner", "adminActivity",
        "capacityMetrics", "gateways", "dataAccessRoles",
    }
    assert not forbidden & {resource.value for resource in workspace_resources(runtime)}
    with patch("requests.Session", Session), patch("socket.socket.connect", side_effect=AssertionError("Network access in self-test")):
        provider = runtime.LiveFabricProvider("synthetic-token", timeout=1)
        discovered = discover_workspaces(provider)
        assert [row["id"] for row in discovered] == [workspace_id, missing_id]
        assert parse_workspace_selection("1,2", 2) == [1, 2]
        assert parse_workspace_selection("1-2", 2) == [1, 2]
        assert parse_workspace_selection("all", 2) == [1, 2]
        assert parse_workspace_selection("q", 2) == []
        answers = iter(("1,2", "yes"))
        assert choose_workspaces(discovered, input_fn=lambda _prompt: next(answers)) == [workspace_id, missing_id]
        assert choose_workspaces(discovered, input_fn=lambda _prompt: "q") == []
        ctx = provider.fetch(workspace_id, runtime.Layer.MIXED, resources)
        assert ctx.id == workspace_id and len(ctx.items) == 2
        assert ctx.notebooks["Example notebook"] == notebook
        assert ctx.pipelines["Example pipeline"] == pipeline
        assert ctx.role_assignments[0].principal_type == "Group"
        assert ctx.git_connected is False
        raw = ctx.to_dict()
        assert runtime.WorkspaceContext.from_dict(json.loads(json.dumps(raw))).to_dict() == raw
        assert "synthetic-token" not in json.dumps(raw)
        assert _truncated_fields(raw) == []
        for field in ("lakehouse_files", "lakehouse_tables_files"):
            assert _truncated_fields({field: {"Example": {"truncated": True}}}) == [
                f"{field}.Example.truncated",
            ]
        denied = True
        partial = provider.fetch(workspace_id, runtime.Layer.MIXED, resources)
        assert not partial.notebooks
        assert partial.read_failures[runtime.Resource.NOTEBOOK_DEFINITIONS.value]["forbidden"] == 1

        class ExportProvider:
            def fetch(self, wid, _layer, requested):
                assert requested == workspace_resources(runtime)
                if wid == missing_id:
                    raise runtime.WorkspaceAccessError(wid, 403)
                context = runtime.WorkspaceContext.from_dict(raw)
                context.unavailable.update(requested - resources)
                return context

        with tempfile.TemporaryDirectory(prefix="fabric-kb-self-test-") as directory:
            code, summary = collect_workspaces(
                runtime, ExportProvider, [workspace_id, missing_id],
                runtime.Layer.MIXED, Path(directory),
            )
            assert code == 2 and summary["status"] == "partial"
            output = next(Path(directory).glob("fabric-kb_*"))
            loaded = json.loads((output / f"{workspace_id}.json").read_text(encoding="utf-8"))
            reconstructed = runtime.WorkspaceContext.from_dict(loaded)
            assert reconstructed.id == workspace_id
            assert forbidden <= set(loaded["unavailable"])
            assert loaded["notebooks"] == raw["notebooks"]
            assert not (output / f"{missing_id}.json").exists()
            assert hashlib.sha256((output / f"{workspace_id}.json").read_bytes()).hexdigest() == summary["workspaces"][0]["sha256"]
            try:
                _write_json(output / "unsafe.json", {"token": "synthetic-secret"}, forbidden_values=("synthetic-secret",))
            except CollectorError:
                pass
            else:
                raise AssertionError("Authentication material was not blocked from export.")
            assert not (output / "unsafe.json").exists()
    assert calls and all(method == "GET" or "/getDefinition" in url for method, url in calls)
    assert not unknown_calls, f"Unexpected collector calls: {unknown_calls}"
    print("SELF-TEST PASSED: workspace discovery/selection, production parsing, exact JSON round-trip, partial reads,")
    print("non-admin resource profile, crawl truncation flags, atomic export, checksums and credential exclusion.")
    print("No sign-in, network requests, tenant writes or audit/check execution occurred.")
    return 0


def _nonempty_text(value):
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("Enter a non-empty model-name filter.")
    return text


def _utc_hour(value):
    try:
        hour = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Enter a whole UTC hour between 0 and 23.") from exc
    if not 0 <= hour <= 23:
        raise argparse.ArgumentTypeError("Enter a whole UTC hour between 0 and 23.")
    return hour


def elevated_resources(runtime):
    names = _ELEVATED_PROFILES[_ELEVATED_CATEGORY]["resources"]
    known = {resource.value: resource for resource in runtime.Resource}
    if set(names) - set(known):
        raise CollectorError("The elevated resource profile is incompatible with the snapshot schema.")
    return frozenset(known[name] for name in names)


def _capacity_settings(args, input_fn=None):
    if _ELEVATED_CATEGORY != "Capacity":
        return {}
    if input_fn is None:
        input_fn = input

    def prompt(label, default, parse):
        while True:
            try:
                response = input_fn(f"{label} [{default}]: ").strip()
            except EOFError as exc:
                raise CollectorError(
                    "Capacity inputs require a terminal. Supply --capacity-metrics-workspace, "
                    "--capacity-metrics-model, --capacity-peak-start-hour and --capacity-peak-end-hour."
                ) from exc
            try:
                return parse(response if response else str(default))
            except argparse.ArgumentTypeError as exc:
                print(f"Invalid input: {exc}")

    print("Capacity settings: the workspace is a search hint; the model name is a substring filter.")
    print("The first matching accessible model is used for every selected workspace; hours are UTC.")
    workspace = args.capacity_metrics_workspace
    if workspace is None:
        workspace = prompt("Metrics app workspace name/UUID (blank searches all accessible)", "", str)
    model = args.capacity_metrics_model
    if model is None:
        model = prompt("Metrics model name contains", "Capacity Metrics", _nonempty_text)
    start = args.capacity_peak_start_hour
    if start is None:
        start = prompt("Peak start hour UTC", 8, _utc_hour)
    end = args.capacity_peak_end_hour
    if end is None:
        end = prompt("Peak end hour UTC", 18, _utc_hour)
    if start == end:
        raise CollectorError("Peak start and end must differ. Rerun with distinct UTC hours.")
    print(f"Metrics search: workspace={workspace.strip() or '(all accessible)'}, model contains={model}")
    print(f"Peak window: {start:02d}:00-{end:02d}:00 UTC (may cross midnight).")
    return {
        "capacity_metrics_workspace": workspace.strip(),
        "capacity_metrics_model": model,
        "capacity_peak_start_hour": start,
        "capacity_peak_end_hour": end,
    }


def _elevated_powerbi_client(runtime, token, timeout):
    import requests

    class EvidencePowerBIClient(runtime.PowerBIClient):
        # The production discovery helpers discard failed-listing status. A
        # denied listing must not become "metrics app not deployed" in this KB.
        def _values(self, path):
            status, body = self._get(path)
            if status != 200 or not isinstance(body, dict) or not isinstance(body.get("value"), list):
                raise runtime.PowerBIError("Power BI metadata listing could not be read.", status)
            return body["value"]

        def capacity_metrics(self, *args, **kwargs):
            try:
                return super().capacity_metrics(*args, **kwargs)
            except runtime.PowerBIError as exc:
                logging.warning("Capacity metadata discovery failed (HTTP %s); evidence is unavailable.", exc.status)
                return {}, False

        def admin_scan(self, workspace_id):
            try:
                return super().admin_scan(workspace_id)
            except requests.exceptions.RequestException:
                logging.warning("Tenant scanner was unreachable; scanner evidence is unavailable.")
                return {}, False

    return EvidencePowerBIClient(token, timeout=timeout)


def _create_elevated_provider(runtime, tokens, timeout, crawl_settings):
    fabric = tokens.acquire(_FABRIC_AUDIENCE, required=True)
    powerbi = tokens.acquire(_POWERBI_AUDIENCE) if _ELEVATED_CATEGORY in {"Capacity", "Tenant"} else None
    provider = runtime.LiveFabricProvider(
        fabric, timeout=timeout,
        token_refresher=lambda: tokens.acquire(_FABRIC_AUDIENCE, force_refresh=True),
        powerbi_token=powerbi,
    )
    if powerbi:
        provider._powerbi_client = _elevated_powerbi_client(runtime, powerbi, timeout)
    if _ELEVATED_CATEGORY == "Capacity":
        provider.capacity_metrics_workspace = crawl_settings["capacity_metrics_workspace"]
        provider.capacity_metrics_model = crawl_settings["capacity_metrics_model"]
        provider.capacity_peak_hours = (
            crawl_settings["capacity_peak_start_hour"], crawl_settings["capacity_peak_end_hour"],
        )
    return provider


def run_elevated_self_test(runtime):
    """Exercise real bundled providers with synthetic HTTP and no network."""
    from unittest.mock import patch

    workspace_id = "00000000-0000-0000-0000-000000000001"
    second_id = "00000000-0000-0000-0000-000000000002"
    missing_id = "00000000-0000-0000-0000-000000000003"
    lakehouse_id = "00000000-0000-0000-0000-000000000004"
    metrics_workspace = "00000000-0000-0000-0000-000000000005"
    model_id = "00000000-0000-0000-0000-000000000006"
    calls = []
    unexpected = []
    denied = False
    datasets_denied = False
    no_model = False
    scan_unreachable = False
    requested = elevated_resources(runtime)
    forbidden_standard = {
        runtime.Resource.NOTEBOOK_DEFINITIONS, runtime.Resource.PIPELINE_DEFINITIONS,
        runtime.Resource.TABLE_COLUMNS, runtime.Resource.LAKEHOUSE_FILES,
    }
    assert not requested & forbidden_standard
    assert runtime.Resource.WORKSPACE in requested

    class Tokens:
        def __init__(self):
            self.audiences = []
            self.audience_status = {}

        def acquire(self, audience, **_kwargs):
            self.audiences.append(audience)
            self.audience_status[audience] = "available"
            return "synthetic-elevated-token"

        def known_secrets(self):
            return ("synthetic-elevated-token",)

    class Response:
        def __init__(self, body, status=200):
            self.status_code = status
            self.headers = {}
            self._body = body

        def json(self):
            return self._body

    def query_response(rows):
        return Response({"results": [{"tables": [{"rows": rows}]}]})

    class Session:
        def __init__(self):
            self.headers = {}

        def mount(self, *_args):
            pass

        def get(self, url, **_kwargs):
            calls.append(("GET", url))
            fabric = _FABRIC_AUDIENCE + "/v1"
            pbi = "https://api.powerbi.com/v1.0/myorg"
            if url == fabric + "/workspaces":
                return Response({"value": [
                    {"id": workspace_id, "displayName": "First"},
                    {"id": second_id, "displayName": "Second"},
                ]})
            for wid in (workspace_id, second_id, missing_id):
                if url == f"{fabric}/workspaces/{wid}":
                    return Response({"displayName": "Synthetic", "capacityId": "synthetic-capacity"},
                                    403 if wid == missing_id else 200)
                if url == f"{fabric}/workspaces/{wid}/items":
                    assert _ELEVATED_CATEGORY != "Capacity"
                    return Response({"value": [
                        {"id": lakehouse_id, "displayName": "Store", "type": "Lakehouse"},
                        {"id": "notebook", "displayName": "Do not fetch", "type": "Notebook"},
                        {"id": "pipeline", "displayName": "Do not fetch", "type": "DataPipeline"},
                    ]})
                if url == f"{fabric}/workspaces/{wid}/roleAssignments":
                    assert _ELEVATED_CATEGORY == "Workspace Admin"
                    return Response({"value": [{
                        "principal": {"id": "group", "type": "Group", "displayName": "Operations"},
                        "role": "Member",
                    }]}, 403 if denied else 200)
                if url == f"{fabric}/workspaces/{wid}/items/{lakehouse_id}/dataAccessRoles":
                    assert _ELEVATED_CATEGORY == "Workspace Admin"
                    return Response({"value": [{
                        "name": "Readers", "members": {"microsoftEntraMembers": [{"objectType": "Group"}]},
                        "decisionRules": [{"effect": "Permit"}],
                    }]}, 403 if denied else 200)
            if _ELEVATED_CATEGORY == "Workspace Admin":
                responses = {
                    fabric + "/connections": {"value": [{
                        "id": "connection", "displayName": "Managed",
                        "credentialDetails": {"credentialType": "WorkspaceIdentity"},
                    }]},
                    fabric + "/gateways": {"value": [{"id": "gateway", "displayName": "Gateway"}]},
                    fabric + "/gateways/gateway/members": {"value": [{"enabled": True}]},
                }
                if url in responses:
                    return Response(responses[url], 403 if denied else 200)
            if _ELEVATED_CATEGORY == "Tenant":
                responses = {
                    pbi + "/admin/tenantsettings": {"tenantSettings": [{"settingName": "Export", "enabled": False}]},
                    fabric + "/admin/domains": {"value": [{"id": "domain", "displayName": "Business"}]},
                    fabric + "/admin/domains/domain/workspaces": {"value": [{"id": workspace_id}, {"id": second_id}]},
                }
                if url in responses:
                    return Response(responses[url], 403 if denied else 200)
                if url.startswith(pbi + "/admin/activityevents?"):
                    return Response({"activityEventEntities": [{
                        "Activity": "UpdateWorkspace", "WorkSpaceId": workspace_id,
                    }]}, 403 if denied else 200)
                if url.startswith(pbi + "/admin/workspaces/scanStatus/"):
                    return Response({"status": "Succeeded"})
                if url.startswith(pbi + "/admin/workspaces/scanResult/"):
                    wid = url.rsplit("/", 1)[-1]
                    return Response({"workspaces": [{"id": wid, "datasets": [
                        {"id": "auto", "name": "Store"},
                        {"id": model_id, "name": "Curated", "endorsementDetails": {"endorsement": "Certified"}},
                    ]}]})
            if _ELEVATED_CATEGORY == "Capacity":
                if url == pbi + "/groups?$top=5000":
                    return Response({"value": [
                        {"id": "unrelated", "name": "Other"},
                        {"id": metrics_workspace, "name": "Metrics home"},
                    ]}, 403 if denied else 200)
                if url == f"{pbi}/groups/{metrics_workspace}/datasets":
                    return Response(
                        {"value": [] if no_model else [{"id": model_id, "name": "Custom metrics"}]},
                        403 if datasets_denied else 200,
                    )
                if url == f"{pbi}/groups/unrelated/datasets":
                    return Response({"value": []})
            unexpected.append(("GET", url))
            raise AssertionError(f"Unexpected elevated GET: {url}")

        def post(self, url, **kwargs):
            calls.append(("POST", url))
            pbi = "https://api.powerbi.com/v1.0/myorg"
            if _ELEVATED_CATEGORY == "Tenant" and url.startswith(pbi + "/admin/workspaces/getInfo?"):
                if scan_unreachable:
                    import requests
                    raise requests.exceptions.ConnectionError("Synthetic scanner outage")
                ids = kwargs["json"]["workspaces"]
                assert len(ids) == 1 and ids[0] in {workspace_id, second_id}
                return Response({"id": ids[0]}, 403 if denied else 202)
            if _ELEVATED_CATEGORY == "Capacity" and url == f"{pbi}/groups/{metrics_workspace}/datasets/{model_id}/executeQueries":
                query = kwargs["json"]["queries"][0]["query"]
                assert query.startswith("EVALUATE ")
                if "INFO.VIEW.COLUMNS()" in query:
                    return query_response([
                        {"[Table]": "Usage", "[Name]": name}
                        for name in ("CU (s)", "Hour", "Workspace", "Item", "Operation", "Overload")
                    ])
                if "TOPN(" in query:
                    return query_response([{"[Item]": "Notebook", "[TotalCU]": 10}])
                if '"Overload"' in query:
                    return query_response([])
                if "'Usage'[Hour]" in query:
                    return query_response([{"[Hour]": 23, "[TotalCU]": 7}, {"[Hour]": 12, "[TotalCU]": 3}])
                if "'Usage'[Operation]" in query:
                    return query_response([{"[Operation]": "SQL query", "[TotalCU]": 10}])
            unexpected.append(("POST", url))
            raise AssertionError("Unexpected elevated POST; no mutation or standard definition crawl is allowed.")

    arguments = ["--auth", "browser"]
    settings = {}
    if _ELEVATED_CATEGORY == "Capacity":
        arguments.extend([
            "--capacity-metrics-workspace", "Metrics home", "--capacity-metrics-model", "Custom metrics",
            "--capacity-peak-start-hour", "22", "--capacity-peak-end-hour", "6",
        ])
        settings = _capacity_settings(build_parser().parse_args(arguments))
        answers = iter(("", "", "99", "8", "18"))
        defaults = _capacity_settings(
            build_parser().parse_args([]), input_fn=lambda _: next(answers),
        )
        assert defaults["capacity_metrics_model"] == "Capacity Metrics"
        assert defaults["capacity_peak_start_hour"] == 8
        for bad in ("-1", "24", "8.5", "text"):
            try:
                _utc_hour(bad)
            except argparse.ArgumentTypeError:
                pass
            else:
                raise AssertionError("Invalid peak hour was accepted.")
    assert build_parser().parse_args(arguments).tenant_id is None
    with (
        patch("requests.Session", Session),
        patch("socket.socket.connect", side_effect=AssertionError("No network in elevated self-test")),
        patch("time.sleep"),
        tempfile.TemporaryDirectory(prefix="elevated-kb-self-test-") as directory,
    ):
        tokens = Tokens()
        provider = _create_elevated_provider(runtime, tokens, 1, settings)
        assert _SQL_AUDIENCE not in tokens.audiences and _STORAGE_AUDIENCE not in tokens.audiences
        assert (_POWERBI_AUDIENCE in tokens.audiences) == (_ELEVATED_CATEGORY != "Workspace Admin")
        assert [row["id"] for row in discover_workspaces(provider)] == [workspace_id, second_id]
        answers = iter(("1,2", "y"))
        assert choose_workspaces(
            discover_workspaces(provider), input_fn=lambda _: next(answers),
        ) == [workspace_id, second_id]
        code, summary = collect_workspaces(
            runtime, lambda: provider, [workspace_id, second_id], runtime.Layer.MIXED, directory,
            tokens=tokens, resources=requested, category=_ELEVATED_CATEGORY, crawl_settings=settings,
        )
        assert code == 0 and summary["status"] == "collected"
        assert summary["admin_categories"] == [_ELEVATED_CATEGORY] and summary["check_set"] == "admin"
        assert summary["admin_settings"] == settings
        assert set(summary["requested_resources"]) == {r.value for r in requested}
        exported = next(Path(directory).rglob(f"{workspace_id}.json"))
        raw = json.loads(exported.read_text(encoding="utf-8"))
        assert runtime.WorkspaceContext.from_dict(raw).to_dict() == raw
        assert set(raw["unavailable"]) == {r.value for r in set(runtime.Resource) - requested}
        assert not raw["notebooks"] and not raw["pipelines"]
        assert hashlib.sha256(exported.read_bytes()).hexdigest() == summary["workspaces"][0]["sha256"]
        assert "synthetic-elevated-token" not in exported.read_text(encoding="utf-8")
        if _ELEVATED_CATEGORY == "Workspace Admin":
            assert raw["role_assignments"][0]["principal_type"] == "Group"
            assert raw["data_access_roles"]["Store"][0]["members"] == 1
            assert len(raw["connections"]) == len(raw["gateways"]) == 1
        elif _ELEVATED_CATEGORY == "Tenant":
            assert len(raw["activity_events"]) == 14
            assert len(raw["admin_scan"]["items"]) == len(raw["admin_scan"]["excluded"]) == 1
            assert len(raw["tenant_settings"]) == len(raw["tenant_domains"]) == 1
            assert sum("/admin/tenantsettings" in url for _, url in calls) == 1
            assert sum("/admin/activityevents?" in url for _, url in calls) == 14
            scan_unreachable = True
            scanner_gap = _create_elevated_provider(runtime, tokens, 1, settings).fetch(
                workspace_id, runtime.Layer.MIXED, requested,
            )
            assert runtime.Resource.ADMIN_SCANNER in scanner_gap.unavailable
            scan_unreachable = False
        else:
            metrics = raw["capacity_metrics"]
            assert metrics["model_workspace_id"] == metrics_workspace and metrics["model_id"] == model_id
            assert metrics["peak_split"] == {"peak": 7.0, "off_peak": 3.0, "window": "22:00-06:00 UTC"}
            assert metrics["top_consumers"] and metrics["query_load"]
            assert sum(url.endswith("/executeQueries") for _, url in calls) == 5
            datasets_denied = True
            model_gap = _create_elevated_provider(runtime, tokens, 1, settings).fetch(
                workspace_id, runtime.Layer.MIXED, requested,
            )
            assert runtime.Resource.CAPACITY_METRICS in model_gap.unavailable
            datasets_denied = False
            no_model = True
            model_absent = _create_elevated_provider(runtime, tokens, 1, settings).fetch(
                workspace_id, runtime.Layer.MIXED, requested,
            )
            assert runtime.Resource.CAPACITY_METRICS not in model_absent.unavailable
            assert model_absent.capacity_metrics["model_found"] is False
            no_model = False

        denied = True
        denied_provider = _create_elevated_provider(runtime, tokens, 1, settings)
        code, partial = collect_workspaces(
            runtime, lambda: denied_provider, [workspace_id, missing_id], runtime.Layer.MIXED, directory,
            resources=requested, category=_ELEVATED_CATEGORY, crawl_settings=settings,
        )
        assert code == 2 and partial["status"] == "partial"
        expected_gaps = requested - {runtime.Resource.WORKSPACE, runtime.Resource.ITEMS}
        assert set(partial["workspaces"][0]["unavailable_requested_resources"]) == {r.value for r in expected_gaps}
        assert partial["workspaces"][1]["status"] == "failed"
        assert not list(Path(directory).rglob(f"{missing_id}.json"))
        code, failure = collect_workspaces(
            runtime, lambda: denied_provider, [missing_id], runtime.Layer.MIXED, directory,
            resources=requested, category=_ELEVATED_CATEGORY,
        )
        assert code == 1 and failure["status"] == "failed"
    assert not unexpected, unexpected
    assert all("/getDefinition" not in url for _, url in calls)
    if _ELEVATED_CATEGORY == "Workspace Admin":
        assert all(method == "GET" for method, _ in calls)
    print(f"SELF-TEST PASSED: {_ELEVATED_CATEGORY} resource scope, production parsing, input validation,")
    print("snapshot round-trip, separate exports, checksums, caching and unavailable-evidence handling.")
    print("No sign-in, network requests, tenant writes or audit/check execution occurred.")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.self_test:
        if args.list_workspaces and args.workspace_id:
            parser.error("Use --list-workspaces alone, or --workspace-id to collect explicit workspaces.")
        if len(set(args.workspace_id)) != len(args.workspace_id):
            parser.error("Each --workspace-id must be unique.")
        if args.auth == "azure-cli" and args.client_id:
            parser.error("--client-id is for browser/device-code sign-in, not Azure CLI.")
        if _ELEVATED_CATEGORY == "Capacity":
            if args.capacity_peak_start_hour is not None and args.capacity_peak_start_hour == args.capacity_peak_end_hour:
                parser.error("Capacity peak start and end hours must differ.")
    tokens = None
    handler = None
    try:
        _dependency_check(args.auth, self_test=args.self_test)
        runtime = load_collector()
        if args.self_test:
            return run_elevated_self_test(runtime)
        tokens = ClientTokens(args.tenant_id, args.auth, args.client_id)
        handler = _configure_logging(tokens)
        print(f"Fabric {_ELEVATED_CATEGORY} KB crawl CLI. No AuditFAST installation is required.")
        print(_ELEVATED_PROFILES[_ELEVATED_CATEGORY]["access"])
        print("Only this category is collected; no notebook, pipeline, SQL or OneLake Files crawl is performed.")
        print("WARNING: exports can contain tenant-wide metadata/events, identities and capacity workload details.")
        print("Review and approve the resulting files before transferring them.")
        tokens.sign_in()
        print(f"Signed-in tenant: {tokens.tenant_id}")
        if args.tenant_id is None:
            print("For a different customer/guest tenant, rerun with --tenant-id TENANT_UUID.")
        workspace_ids = args.workspace_id
        if not workspace_ids:
            discovery_provider = runtime.LiveFabricProvider(
                tokens.acquire(_FABRIC_AUDIENCE, required=True),
                timeout=args.timeout,
                token_refresher=lambda: tokens.acquire(_FABRIC_AUDIENCE, force_refresh=True),
            )
            available = discover_workspaces(discovery_provider)
            if args.list_workspaces:
                print_workspace_list(available)
                print("Listing complete. No KB files were created.")
                return 0
            workspace_ids = choose_workspaces(available)
            if not workspace_ids:
                return 0
        settings = _capacity_settings(args)
        provider = _create_elevated_provider(runtime, tokens, args.timeout, settings)
        code, _summary = collect_workspaces(
            runtime, lambda: provider,
            workspace_ids, runtime.Layer(args.layer), args.output_dir, tokens=tokens,
            resources=elevated_resources(runtime), category=_ELEVATED_CATEGORY, crawl_settings=settings,
        )
        return code
    except CollectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(
            f"ERROR: collection stopped because of {type(exc).__name__}. "
            "Check output permissions, connectivity and the approved authentication installation. "
            "Completed snapshots, if any, remain in the output directory.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Collection cancelled. Completed snapshots may be present; do not treat the run as complete.", file=sys.stderr)
        return 130
    finally:
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
        if tokens is not None:
            tokens.clear()


if __name__ == "__main__":
    sys.exit(main())
