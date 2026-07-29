"""The vocabulary of the Workspace Knowledge Graph (the Digital Twin).

Every artifact discovered in a Fabric workspace becomes a :class:`NodeType`, and
every relationship becomes an :class:`EdgeType`. The types are deliberately
exhaustive: the guiding rule of discovery is *nothing is missed*, so an item type
the platform does not yet recognise still lands as :attr:`NodeType.ITEM` (a
generic node that preserves its raw Fabric type) rather than being dropped.

``DiscoverySource`` records *how* a node was learned, so the same graph can be
enriched from several sources (Fabric REST, Power BI REST, the Scanner API,
Microsoft Graph, and — as optional enrichment — Fabric AI Skills or an MCP
server) without losing provenance.
"""
from __future__ import annotations

from ..enums import StrEnum


class DiscoverySource(StrEnum):
    """Where a node or edge was learned from — kept for provenance and auditing."""

    FABRIC_REST = "fabric_rest"
    POWERBI_REST = "powerbi_rest"
    SCANNER_API = "scanner_api"
    GIT = "git"
    #: Item files exported from the portal into a local folder — the zero-privilege
    #: path to item source code (no admin, no Git, no Item.ReadWrite scope).
    LOCAL_EXPORT = "local_export"
    FABRIC_AI_SKILLS = "fabric_ai_skills"
    FABRIC_MCP = "fabric_mcp"
    MS_GRAPH = "ms_graph"
    #: Phase 2 output — knowledge derived by AI/LLM reasoning, kept separate from
    #: the authoritative raw metadata above.
    DERIVED_AI = "derived_ai"
    FIXTURE = "fixture"
    DERIVED = "derived"


class NodeType(StrEnum):
    """Every kind of node the Digital Twin can hold.

    Top-level Fabric items, their internal sub-artifacts (tables, columns,
    measures, cells, activities), the governance surface (principals, git,
    findings), and a generic :attr:`ITEM` fallback so unknown item types are
    never silently discarded.
    """

    # -- containers ----------------------------------------------------------
    WORKSPACE = "Workspace"
    CAPACITY = "Capacity"
    DOMAIN = "Domain"
    FOLDER = "Folder"

    # -- storage items -------------------------------------------------------
    LAKEHOUSE = "Lakehouse"
    WAREHOUSE = "Warehouse"
    SQL_ENDPOINT = "SQLEndpoint"
    SQL_DATABASE = "SQLDatabase"
    MIRRORED_DATABASE = "MirroredDatabase"

    # -- compute / integration items ----------------------------------------
    NOTEBOOK = "Notebook"
    DATA_PIPELINE = "DataPipeline"
    DATAFLOW = "Dataflow"
    SPARK_JOB_DEFINITION = "SparkJobDefinition"
    ENVIRONMENT = "Environment"

    # -- real-time items -----------------------------------------------------
    EVENTHOUSE = "Eventhouse"
    KQL_DATABASE = "KQLDatabase"
    KQL_DASHBOARD = "KQLDashboard"
    KQL_QUERYSET = "KQLQueryset"
    EVENTSTREAM = "Eventstream"
    REFLEX = "Reflex"

    # -- reporting / semantic items -----------------------------------------
    SEMANTIC_MODEL = "SemanticModel"
    REPORT = "Report"
    PAGINATED_REPORT = "PaginatedReport"
    DASHBOARD = "Dashboard"

    # -- ml / api items ------------------------------------------------------
    ML_MODEL = "MLModel"
    ML_EXPERIMENT = "MLExperiment"
    GRAPHQL_API = "GraphQLApi"

    # -- generic fallback: an item type the platform does not model yet ------
    ITEM = "Item"

    # -- internal sub-artifacts ---------------------------------------------
    SCHEMA = "Schema"
    TABLE = "Table"
    COLUMN = "Column"
    SHORTCUT = "Shortcut"
    MEASURE = "Measure"
    RELATIONSHIP = "Relationship"
    NOTEBOOK_CELL = "NotebookCell"
    PIPELINE_ACTIVITY = "PipelineActivity"

    # -- governance / operations --------------------------------------------
    PRINCIPAL = "Principal"
    GIT_CONNECTION = "GitConnection"
    DEPLOYMENT_PIPELINE = "DeploymentPipeline"
    CONNECTION = "Connection"

    # -- Phase 2: AI-derived knowledge (summaries, findings, recommendations) -
    DERIVED_INSIGHT = "DerivedInsight"

    # -- an artifact that could not be read (permission / transport) ---------
    ACCESS_FINDING = "AccessFinding"


#: Fabric item ``type`` string -> the node type it maps to. Anything absent here
#: becomes :attr:`NodeType.ITEM`, preserving the raw type in ``properties`` so no
#: artifact is ever lost.
ITEM_TYPE_TO_NODE: dict[str, NodeType] = {
    "Lakehouse": NodeType.LAKEHOUSE,
    "Warehouse": NodeType.WAREHOUSE,
    "MirroredWarehouse": NodeType.WAREHOUSE,
    "SQLEndpoint": NodeType.SQL_ENDPOINT,
    "SQLDatabase": NodeType.SQL_DATABASE,
    "MirroredDatabase": NodeType.MIRRORED_DATABASE,
    "Notebook": NodeType.NOTEBOOK,
    "DataPipeline": NodeType.DATA_PIPELINE,
    "Dataflow": NodeType.DATAFLOW,
    "SparkJobDefinition": NodeType.SPARK_JOB_DEFINITION,
    "Environment": NodeType.ENVIRONMENT,
    "Eventhouse": NodeType.EVENTHOUSE,
    "KQLDatabase": NodeType.KQL_DATABASE,
    "KQLDashboard": NodeType.KQL_DASHBOARD,
    "KQLQueryset": NodeType.KQL_QUERYSET,
    "Eventstream": NodeType.EVENTSTREAM,
    "Reflex": NodeType.REFLEX,
    "SemanticModel": NodeType.SEMANTIC_MODEL,
    "Report": NodeType.REPORT,
    "PaginatedReport": NodeType.PAGINATED_REPORT,
    "Dashboard": NodeType.DASHBOARD,
    "MLModel": NodeType.ML_MODEL,
    "MLExperiment": NodeType.ML_EXPERIMENT,
    "GraphQLApi": NodeType.GRAPHQL_API,
}


def node_type_for_item(item_type: str) -> NodeType:
    """Map a Fabric item ``type`` to a :class:`NodeType`, never dropping unknowns."""
    return ITEM_TYPE_TO_NODE.get(item_type, NodeType.ITEM)


class EdgeType(StrEnum):
    """Every kind of relationship the Digital Twin can record."""

    CONTAINS = "contains"                       # workspace/folder -> item
    ASSIGNED_TO_CAPACITY = "assigned_to_capacity"
    IN_DOMAIN = "in_domain"
    IN_FOLDER = "in_folder"
    GRANTED_TO = "granted_to"                   # workspace -> principal (a role)
    DEPENDS_ON = "depends_on"                   # generic dependency
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    USES_ENVIRONMENT = "uses_environment"
    REFERENCES = "references"
    HAS_SCHEMA = "has_schema"
    HAS_TABLE = "has_table"
    HAS_COLUMN = "has_column"
    HAS_SHORTCUT = "has_shortcut"
    HAS_MEASURE = "has_measure"
    HAS_RELATIONSHIP = "has_relationship"
    HAS_CELL = "has_cell"
    HAS_ACTIVITY = "has_activity"
    GIT_CONNECTED = "git_connected"
    DEPLOYS = "deploys"
    HAS_FINDING = "has_finding"
    HAS_INSIGHT = "has_insight"
