"""Shared helpers for the derived-lineage checks.

Underscore-prefixed so the package auto-loader skips it: it registers no checks.

**Why a derived graph rather than a lineage API.** Fabric's lineage view is built
in — every workspace has one and any workspace-role user can open it — so "is
lineage enabled" is not a question anyone can fail. What *can* go wrong is the
wiring: Fabric infers the graph from how items reference each other, so an item
that reads or writes through a hard-coded ``abfss://`` / ``https://`` path
instead of an attached lakehouse or a referenced Fabric item is invisible in the
lineage view even though the data flow is real. That is the defect these helpers
detect, from the definitions the crawl already holds. No Microsoft Purview
dependency: Purview is a separate product, and these points name the Fabric
lineage view.
"""
from __future__ import annotations

import json
import re

from ._notebook import executable_code

#: A storage path written out in full. A reference like this carries no Fabric
#: item identity, so nothing in the lineage graph can hang off it.
HARDCODED_PATH = re.compile(
    r"(?:abfss|wasbs?|s3a?|gs)://|"
    r"https://[\w.-]*(?:onelake\.|dfs\.core\.windows\.net|blob\.core\.windows\.net)",
    re.IGNORECASE,
)

#: A reference Fabric can resolve to an item: the attached-lakehouse mount path,
#: a Spark catalog call, or a SQL statement naming a table rather than a URL.
#:
#: A bare ``FROM <name>`` is deliberately **not** matched: in a notebook that is
#: far more often ``from pyspark.sql import ...`` than a table read, and a
#: detector that treats every Python import as lineage wiring would pass every
#: notebook ever written. Catalog access is matched through the API that performs
#: it (``spark.sql`` / ``spark.table`` / ``%%sql``) instead.
ATTACHED_REF = re.compile(
    r"/lakehouse/default/(?:Tables|Files)/|"
    r"\bspark\.(?:table|sql)\s*\(|"
    r"\bspark\.read\.table\s*\(|"
    r"\bsaveAsTable\s*\(|"
    r"%%sql\b|"
    r"\b(?:INSERT\s+INTO|MERGE\s+INTO|COPY\s+INTO|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+"
    r"(?!['\"`]?(?:abfss|wasbs?|https?|s3a?|gs|delta)\b)"
    r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*){0,2}\b",
    re.IGNORECASE,
)

#: A pipeline activity that names a Fabric item — the id fields Fabric uses to
#: draw an edge, or a linked-service type that resolves to a workspace item.
PIPELINE_ITEM_REF = re.compile(
    r'"(?:workspaceId|artifactId|notebookId|lakehouseId|warehouseId|itemId|'
    r'dataflowId|pipelineId|datasetId)"\s*:|'
    r'"type"\s*:\s*"(?:Lakehouse|LakehouseTable|DataWarehouse|DataWarehouseTable|'
    r'Warehouse|WarehouseTable|Fabric\w*|TridentNotebook|InvokePipeline|'
    r'ExecutePipeline|SparkJobDefinition)"',
    re.IGNORECASE,
)

#: An abfss path into OneLake: ``abfss://<workspace>@<host>/<item>/...``. The two
#: captured segments are a workspace and an item, each of which Fabric accepts as
#: either a display name or a GUID.
ONELAKE_PATH = re.compile(
    r"abfss://([^@\s\"'/]+)@[^/\s\"']*onelake\.[^/\s\"']*/([^/\s\"']+)",
    re.IGNORECASE,
)

#: A path segment that is a bare GUID names nothing a human (or a lineage view)
#: can identify without a second lookup.
GUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def pipeline_texts(workspace) -> dict[str, str]:
    """``pipeline name -> serialized definition``. References live in JSON properties."""
    return {name: json.dumps(defn) for name, defn in (workspace.pipelines or {}).items()}


def notebook_texts(workspace) -> dict[str, str]:
    """``notebook name -> executable code``.

    :func:`executable_code` strips comments, so a commented-out path cannot make a
    notebook look either wired or broken.
    """
    return {name: executable_code(defn) for name, defn in (workspace.notebooks or {}).items()}


def attached_lakehouses(definition: dict) -> list[str]:
    """The lakehouses attached to a notebook, per its ``trident`` metadata.

    An attached lakehouse is the strongest possible wiring signal: Fabric draws
    the lineage edge from the attachment itself, with no code inspection at all.
    """
    trident = ((definition or {}).get("metadata") or {}).get("trident") or {}
    lakehouse = trident.get("lakehouse") or {}
    known = lakehouse.get("known_lakehouses") or []
    names: list[str] = []
    for entry in known:
        if isinstance(entry, dict):
            value = entry.get("id") or entry.get("name") or ""
        else:
            value = str(entry)
        if value:
            names.append(str(value))
    default = lakehouse.get("default_lakehouse_name") or lakehouse.get("default_lakehouse")
    if default and str(default) not in names:
        names.append(str(default))
    return names

