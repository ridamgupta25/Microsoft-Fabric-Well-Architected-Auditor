"""Build a :class:`KnowledgeGraph` from a discovered :class:`WorkspaceContext`.

This is the bridge between *discovery* (what a provider fetched) and the *Digital
Twin* (the graph checks reason over). It is deliberately total: every artifact the
provider returned becomes a node, every relationship it implies becomes an edge,
and every resource the provider *failed* to read becomes an access finding — so
the graph reflects the real, possibly-incomplete, picture rather than pretending
absence means "not configured".

It is pure and synchronous: given the same context it always yields the same
graph, which is what makes it trivially testable offline.
"""
from __future__ import annotations

from typing import Any

from ..enums import Resource
from ..models import WorkspaceContext
from .model import KnowledgeGraph, Node, make_node_id
from .types import DiscoverySource, EdgeType, NodeType, node_type_for_item

#: What an unread resource means for the audit, surfaced on the finding node.
_RESOURCE_IMPACT: dict[Resource, str] = {
    Resource.ITEMS: "Workspace inventory is incomplete; item-level checks cannot run.",
    Resource.ROLE_ASSIGNMENTS: "Access model is unknown; security-role checks cannot run.",
    Resource.GIT: "Source-control status is unknown; DevOps checks cannot run.",
    Resource.PIPELINE_DEFINITIONS: "Pipeline internals are unread; pipeline checks are limited.",
    Resource.NOTEBOOK_DEFINITIONS: "Notebook code is unread; notebook checks are limited.",
    Resource.TABLE_SCHEMAS: "Table schemas are unread; data-model checks are limited.",
}


def _preview(source: Any, limit: int = 280) -> str:
    """A short, safe text preview of a notebook cell's source."""
    if isinstance(source, list):
        source = "".join(str(part) for part in source)
    text = str(source or "")
    return text[:limit]


def _activities(definition: dict) -> list[dict]:
    """Pull the activity list out of a pipeline definition, tolerating shapes."""
    if not isinstance(definition, dict):
        return []
    props = definition.get("properties")
    if isinstance(props, dict) and isinstance(props.get("activities"), list):
        return props["activities"]
    if isinstance(definition.get("activities"), list):
        return definition["activities"]
    return []


def build_graph(
    context: WorkspaceContext,
    source: DiscoverySource = DiscoverySource.FABRIC_REST,
) -> KnowledgeGraph:
    """Assemble the Digital Twin for one workspace from its discovered context."""
    graph = KnowledgeGraph(
        context.id,
        properties={
            "layer": context.layer.value,
            "git_connected": context.git_connected,
            "deployment_pipeline": context.deployment_pipeline,
        },
    )

    workspace_id = make_node_id(NodeType.WORKSPACE, context.id)
    graph.add_node(Node(
        id=workspace_id,
        type=NodeType.WORKSPACE,
        name=context.name,
        source=source,
        properties={
            "fabric_id": context.id,
            "layer": context.layer.value,
            "git_connected": context.git_connected,
            "deployment_pipeline": context.deployment_pipeline,
        },
    ))

    _add_capacity(graph, context, workspace_id, source)
    name_to_node = _add_items(graph, context, workspace_id, source)
    _add_pipelines(graph, context, name_to_node, source)
    _add_notebooks(graph, context, name_to_node, source)
    _add_tables(graph, context, workspace_id, source)
    _add_shortcuts(graph, context, name_to_node, source)
    _add_semantic_models(graph, context, name_to_node, source)
    _add_roles(graph, context, workspace_id, source)
    _add_git(graph, context, workspace_id, source)
    _add_access_findings(graph, context, workspace_id)
    return graph


def _add_capacity(graph, context, workspace_id, source) -> None:
    if not context.capacity_id:
        return
    capacity_id = make_node_id(NodeType.CAPACITY, context.capacity_id)
    graph.add_node(Node(
        id=capacity_id, type=NodeType.CAPACITY, name=context.capacity_id,
        source=source, properties={"fabric_id": context.capacity_id},
    ))
    graph.add_edge(workspace_id, capacity_id, EdgeType.ASSIGNED_TO_CAPACITY)


def _add_items(graph, context, workspace_id, source) -> dict[tuple[str, str], str]:
    """Add a node per item; return a ``(fabric_type, name) -> node id`` index."""
    index: dict[tuple[str, str], str] = {}
    for item in context.items:
        node_type = node_type_for_item(item.type)
        node_id = make_node_id(node_type, item.id or item.display_name)
        graph.add_node(Node(
            id=node_id, type=node_type, name=item.display_name or item.id,
            source=source,
            properties={
                "fabric_id": item.id,
                "fabric_type": item.type,
                "last_run_utc": item.last_run_utc,
                "sensitivity_label": item.sensitivity_label,
            },
        ))
        graph.add_edge(workspace_id, node_id, EdgeType.CONTAINS)
        if item.display_name:
            index[(item.type, item.display_name)] = node_id
    return index


def _item_node_id(graph, context, name_to_node, fabric_type, name, node_type) -> str:
    """Find the item node for a fetched definition, creating one if the list missed it."""
    existing = name_to_node.get((fabric_type, name))
    if existing:
        return existing
    node_id = make_node_id(node_type, name)
    graph.add_node(Node(id=node_id, type=node_type, name=name,
                        source=DiscoverySource.DERIVED, properties={"fabric_type": fabric_type}))
    graph.add_edge(make_node_id(NodeType.WORKSPACE, context.id), node_id, EdgeType.CONTAINS)
    return node_id


def _add_pipelines(graph, context, name_to_node, source) -> None:
    for name, definition in context.pipelines.items():
        pipeline_id = _item_node_id(
            graph, context, name_to_node, "DataPipeline", name, NodeType.DATA_PIPELINE
        )
        activities = _activities(definition)
        graph.node(pipeline_id).properties["activity_count"] = len(activities)
        for index, activity in enumerate(activities):
            act_name = activity.get("name") or f"activity_{index}"
            act_type = activity.get("type") or "Unknown"
            act_id = f"{pipeline_id}/activity/{index}"
            graph.add_node(Node(
                id=act_id, type=NodeType.PIPELINE_ACTIVITY, name=act_name, source=source,
                properties={"activity_type": act_type},
            ))
            graph.add_edge(pipeline_id, act_id, EdgeType.HAS_ACTIVITY)


def _add_notebooks(graph, context, name_to_node, source) -> None:
    for name, definition in context.notebooks.items():
        notebook_id = _item_node_id(
            graph, context, name_to_node, "Notebook", name, NodeType.NOTEBOOK
        )
        cells = (definition or {}).get("cells") or []
        languages: list[str] = []
        graph.node(notebook_id).properties["cell_count"] = len(cells)
        for index, cell in enumerate(cells):
            cell_type = cell.get("cell_type") or "code"
            language = (
                cell.get("metadata", {}).get("language")
                or (definition.get("metadata", {})
                    .get("language_info", {}).get("name") if isinstance(definition, dict) else None)
                or ("markdown" if cell_type == "markdown" else "python")
            )
            languages.append(language)
            cell_id = f"{notebook_id}/cell/{index}"
            graph.add_node(Node(
                id=cell_id, type=NodeType.NOTEBOOK_CELL, name=f"{name} · cell {index}",
                source=source,
                properties={
                    "cell_type": cell_type,
                    "language": language,
                    "source_preview": _preview(cell.get("source")),
                },
            ))
            graph.add_edge(notebook_id, cell_id, EdgeType.HAS_CELL)
        graph.node(notebook_id).properties["languages"] = sorted(set(languages))


def _add_tables(graph, context, workspace_id, source) -> None:
    if not context.tables:
        return
    # Attach tables to the sole lakehouse when there is exactly one, else to the
    # workspace — the flat ``tables`` map does not carry a parent id.
    lakehouses = graph.nodes_of_type(NodeType.LAKEHOUSE)
    parent_id = lakehouses[0].id if len(lakehouses) == 1 else workspace_id
    for table_name, meta in context.tables.items():
        meta = meta or {}
        table_id = make_node_id(NodeType.TABLE, f"{context.id}/{table_name}")
        columns = meta.get("columns") or []
        graph.add_node(Node(
            id=table_id, type=NodeType.TABLE, name=table_name, source=source,
            properties={
                "storage_type": meta.get("type", ""),
                "format": meta.get("format", ""),
                "column_count": len(columns),
            },
        ))
        graph.add_edge(parent_id, table_id, EdgeType.HAS_TABLE)
        for column in columns:
            col_name = column.get("name") if isinstance(column, dict) else str(column)
            if not col_name:
                continue
            col_id = f"{table_id}/col/{col_name}"
            graph.add_node(Node(
                id=col_id, type=NodeType.COLUMN, name=col_name, source=source,
                properties={"data_type": column.get("type", "")
                            if isinstance(column, dict) else ""},
            ))
            graph.add_edge(table_id, col_id, EdgeType.HAS_COLUMN)


def _add_shortcuts(graph, context, name_to_node, source) -> None:
    for lakehouse_name, shortcuts in context.shortcuts.items():
        parent_id = name_to_node.get(("Lakehouse", lakehouse_name)) or make_node_id(
            NodeType.WORKSPACE, context.id
        )
        for index, shortcut in enumerate(shortcuts):
            sc_name = shortcut.get("name") or f"shortcut_{index}"
            sc_id = f"{parent_id}/shortcut/{sc_name}"
            graph.add_node(Node(
                id=sc_id, type=NodeType.SHORTCUT, name=sc_name, source=source,
                properties={
                    "path": shortcut.get("path", ""),
                    "target_type": shortcut.get("target_type", ""),
                },
            ))
            graph.add_edge(parent_id, sc_id, EdgeType.HAS_SHORTCUT)


def _add_semantic_models(graph, context, name_to_node, source) -> None:
    for model_name, model in context.semantic_models.items():
        model_id = _item_node_id(
            graph, context, name_to_node, "SemanticModel", model_name, NodeType.SEMANTIC_MODEL
        )
        node = graph.node(model_id)
        node.properties["measure_count"] = len(model.get("measures") or [])
        node.properties["relationship_count"] = len(model.get("relationships") or [])
        node.properties["table_count"] = len(model.get("tables") or [])
        for measure in model.get("measures") or []:
            m_name = measure.get("name") or "measure"
            m_id = f"{model_id}/measure/{measure.get('table', '')}.{m_name}"
            graph.add_node(Node(
                id=m_id, type=NodeType.MEASURE, name=m_name, source=source,
                properties={
                    "table": measure.get("table", ""),
                    "expression": measure.get("expression", ""),
                    "description": measure.get("description", ""),
                    "has_description": bool(measure.get("description")),
                },
            ))
            graph.add_edge(model_id, m_id, EdgeType.HAS_MEASURE)
        for index, rel in enumerate(model.get("relationships") or []):
            r_id = f"{model_id}/rel/{index}"
            label = f"{rel.get('from_table', '')} -> {rel.get('to_table', '')}"
            graph.add_node(Node(
                id=r_id, type=NodeType.RELATIONSHIP, name=label, source=source,
                properties={
                    "from_table": rel.get("from_table", ""),
                    "from_column": rel.get("from_column", ""),
                    "to_table": rel.get("to_table", ""),
                    "to_column": rel.get("to_column", ""),
                    "is_active": rel.get("is_active", True),
                },
            ))
            graph.add_edge(model_id, r_id, EdgeType.HAS_RELATIONSHIP)


def _add_roles(graph, context, workspace_id, source) -> None:
    for role in context.role_assignments:
        label = role.display_name or role.principal_type or "unknown"
        principal_id = make_node_id(NodeType.PRINCIPAL, f"{role.principal_type}:{label}")
        graph.add_node(Node(
            id=principal_id, type=NodeType.PRINCIPAL, name=label, source=source,
            properties={
                "principal_type": role.principal_type,
                "aad_id": role.principal_id,
                "is_guest": role.is_guest,
                "is_individual": role.is_individual,
            },
        ))
        graph.add_edge(
            workspace_id, principal_id, EdgeType.GRANTED_TO,
            role=role.role, principal_type=role.principal_type,
        )


def _add_git(graph, context, workspace_id, source) -> None:
    if not context.git_connected:
        return
    git_id = make_node_id(NodeType.GIT_CONNECTION, context.id)
    graph.add_node(Node(id=git_id, type=NodeType.GIT_CONNECTION, name="Git connection",
                        source=source, properties={"connected": True}))
    graph.add_edge(workspace_id, git_id, EdgeType.GIT_CONNECTED)


def _add_access_findings(graph, context, workspace_id) -> None:
    """Turn every unread resource into a first-class 'could not read' finding."""
    for resource in sorted(context.unavailable, key=lambda r: r.value):
        finding_id = make_node_id(NodeType.ACCESS_FINDING, f"{context.id}/{resource.value}")
        graph.add_node(Node(
            id=finding_id, type=NodeType.ACCESS_FINDING, name=f"Unreadable: {resource.value}",
            source=DiscoverySource.DERIVED,
            properties={
                "resource": resource.value,
                "status": "Access Denied or Unavailable",
                "audit_impact": _RESOURCE_IMPACT.get(resource, "Some checks cannot run."),
            },
        ))
        graph.add_edge(workspace_id, finding_id, EdgeType.HAS_FINDING)
