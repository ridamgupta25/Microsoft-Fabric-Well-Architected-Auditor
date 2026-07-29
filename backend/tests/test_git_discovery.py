"""Tests for Git ingestion (Phase 1 authoritative source for notebook code)."""
from __future__ import annotations

from auditfast.core.graph import EdgeType, NodeType, build_graph, make_node_id
from auditfast.core.models import Item, WorkspaceContext
from auditfast.discovery.git import GitDiscoverer, notebooks_from_git_files

_PLATFORM = '{"metadata": {"type": "Notebook", "displayName": "NB_Gold_Build"}}'
_PY = """# Fabric notebook source

# METADATA ********************

# META {}

# CELL ********************

df = spark.read.load("x")
password = "hunter2"

# MARKDOWN ********************

# MAGIC # Heading
# MAGIC some notes
"""

_FILES = {
    "workspace/NB_Gold_Build.Notebook/.platform": _PLATFORM,
    "workspace/NB_Gold_Build.Notebook/notebook-content.py": _PY,
}


def test_notebooks_from_git_files_extracts_cells():
    graph = notebooks_from_git_files(_FILES, workspace_id="ws")
    notebook = next(n for n in graph.nodes_of_type(NodeType.NOTEBOOK)
                    if n.name == "NB_Gold_Build")
    assert notebook.properties["source_available"] is True

    cells = graph.neighbors(notebook.id, EdgeType.HAS_CELL)
    kinds = {c.properties["cell_type"] for c in cells}
    assert "code" in kinds and "markdown" in kinds

    code_cell = next(c for c in cells if c.properties["cell_type"] == "code")
    assert "password" in code_cell.properties["source_full"]


def test_git_notebook_ids_align_with_rest_when_id_map_supplied():
    rest = build_graph(WorkspaceContext(
        id="ws", items=[Item(id="nb-guid", type="Notebook", display_name="NB_Gold_Build")]))
    git = notebooks_from_git_files(_FILES, name_to_id={"NB_Gold_Build": "nb-guid"},
                                   workspace_id="ws")
    rest.merge(git)

    notebook = rest.node(make_node_id(NodeType.NOTEBOOK, "nb-guid"))
    assert notebook is not None
    # Git-sourced code merged onto the same notebook node the REST crawl made.
    assert rest.neighbors(notebook.id, EdgeType.HAS_CELL)


def test_git_discoverer_skips_when_not_connected():
    discoverer = GitDiscoverer(git_details=None, file_reader=lambda: {})
    ok, reason = discoverer.available()
    assert ok is False
    assert "git" in reason.lower()


def test_git_discoverer_runs_with_a_reader():
    discoverer = GitDiscoverer(git_details={"connected": True}, file_reader=lambda: _FILES)
    ok, _reason = discoverer.available()
    assert ok is True
    graph = discoverer.discover("ws")
    assert graph.nodes_of_type(NodeType.NOTEBOOK)


def test_local_export_discoverer_ingests_a_folder(tmp_path):
    from auditfast.core.graph import DiscoverySource
    from auditfast.discovery.local_files import LocalExportDiscoverer, read_export_folder

    item_dir = tmp_path / "NB_Gold_Build.Notebook"
    item_dir.mkdir()
    (item_dir / ".platform").write_text(_PLATFORM, encoding="utf-8")
    (item_dir / "notebook-content.py").write_text(_PY, encoding="utf-8")

    assert read_export_folder(tmp_path)  # the folder reader picks the files up

    discoverer = LocalExportDiscoverer(tmp_path)
    ok, _reason = discoverer.available()
    assert ok is True
    graph = discoverer.discover("ws")
    notebook = next(n for n in graph.nodes_of_type(NodeType.NOTEBOOK)
                    if n.name == "NB_Gold_Build")
    assert notebook.source is DiscoverySource.LOCAL_EXPORT
    assert graph.nodes_of_type(NodeType.NOTEBOOK_CELL)


def test_local_export_discoverer_skips_when_no_folder():
    from auditfast.discovery.local_files import LocalExportDiscoverer

    ok, reason = LocalExportDiscoverer(None).available()
    assert ok is False
