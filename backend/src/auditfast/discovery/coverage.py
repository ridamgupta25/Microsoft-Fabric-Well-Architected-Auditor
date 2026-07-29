"""The coverage map — which source supplies which data need.

This makes "can every checklist point get its data?" answerable. Each data need
maps to the source(s) that can supply it (best first), or is listed as
*unobtainable* with the reason (data-plane, external system, or human process).
The audit can then distinguish three very different findings:

* configured / not configured  (we read it),
* could not read               (a source was unavailable — a permission gap),
* not obtainable from any API   (needs the data plane or a human).
"""
from __future__ import annotations

from ..core.graph import DiscoverySource

_REST = DiscoverySource.FABRIC_REST
_SCAN = DiscoverySource.SCANNER_API
_GRAPH = DiscoverySource.MS_GRAPH

#: data need -> sources that can supply it, best first.
COVERAGE: dict[str, list[DiscoverySource]] = {
    "workspace_metadata": [_REST, _SCAN],
    "capacity_assignment": [_REST, _SCAN],
    "item_inventory": [_REST, _SCAN],
    "notebook_code": [_REST],
    "pipeline_definition": [_REST],
    "dataflow_definition": [_REST, _SCAN],
    "lakehouse_tables": [_REST],
    "lakehouse_shortcuts": [_REST],
    "semantic_model_measures": [_REST, _SCAN],
    "semantic_model_relationships": [_REST, _SCAN],
    "semantic_model_datasources": [_SCAN],
    "report_dataset_lineage": [_SCAN],
    "sensitivity_labels": [_SCAN, _REST],
    "endorsement": [_SCAN],
    "git_integration": [_REST],
    "deployment_pipeline": [_REST],
    "role_assignments": [_REST, _SCAN],
    "item_permissions": [_SCAN, _REST],
    "principal_identity": [_GRAPH],
    "group_membership": [_GRAPH],
    "activity_events": [_REST],  # Admin Activity Events API
}

#: data need -> why no API can satisfy it (so a check reports 'unobtainable').
UNOBTAINABLE: dict[str, str] = {
    "row_level_data_quality": "Data-plane; requires querying the data (SQL endpoint / Spark).",
    "lakehouse_column_schema": "Requires the SQL analytics endpoint (TDS), not metadata APIs.",
    "capacity_utilization_metrics": "Capacity Metrics app / metrics API, not core REST or Scanner.",
    "monetary_cost": "Azure Cost Management, not a Fabric API.",
    "legal_process_controls": "Organizational/legal process; human attestation only.",
}


def sources_for(need: str) -> list[DiscoverySource]:
    """Sources that can supply ``need`` (empty if unobtainable or unknown)."""
    return COVERAGE.get(need, [])


def is_obtainable(need: str) -> bool:
    return need in COVERAGE


def coverage_report() -> dict:
    """A serializable view of the whole coverage map."""
    return {
        "obtainable": {need: [s.value for s in sources] for need, sources in COVERAGE.items()},
        "unobtainable": dict(UNOBTAINABLE),
    }
