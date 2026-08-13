"""Git ingestion adapter — the authoritative fallback for item *source code*.

When a workspace is Git-connected, Fabric serialises every item into the repo:
each item is a folder holding a ``.platform`` descriptor (its display name + type)
and a content file (``notebook-content.py`` / ``.ipynb`` for notebooks). That is
the authoritative place to read notebook code when the REST ``getDefinition`` call
is unavailable (for example, a token without the item-definition scope).

``notebooks_from_git_files`` is pure and tested. The discoverer pairs it with a
file reader for the workspace's Git provider (Azure DevOps or GitHub). Node ids
align with the Fabric REST builder when a ``name -> item id`` map is supplied, so
the code merges onto the same notebook node the metadata crawl produced.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ..core.graph import (
    DiscoverySource,
    EdgeType,
    KnowledgeGraph,
    Node,
    NodeType,
    make_node_id,
)

log = logging.getLogger("auditfast.git")

#: A file reader returns ``{repo_path: decoded_text}`` for the item files.
FileReader = Callable[[], dict[str, str]]

_CELL_MARKER = re.compile(r"^# (CELL|MARKDOWN|METADATA)\b.*$", re.MULTILINE)


def _platform(text: str | None) -> dict:
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {}


def _source_text(source: Any) -> str:
    return "".join(source) if isinstance(source, list) else str(source or "")


def _ipynb_cells(text: str) -> list[tuple[str, str]]:
    try:
        notebook = json.loads(text)
    except Exception:
        return [("code", text.strip())] if text.strip() else []
    return [(c.get("cell_type", "code"), _source_text(c.get("source")))
            for c in notebook.get("cells", [])]


def _py_cells(text: str) -> list[tuple[str, str]]:
    """Split a Fabric ``notebook-content.py`` on its CELL/MARKDOWN markers."""
    marks = [(m.start(), m.group(1)) for m in _CELL_MARKER.finditer(text)]
    if not marks:
        stripped = text.strip()
        return [("code", stripped)] if stripped else []
    cells: list[tuple[str, str]] = []
    for index, (start, kind) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        body = text[start:end].split("\n", 1)[1] if "\n" in text[start:end] else ""
        if kind == "METADATA":
            continue
        if kind == "MARKDOWN":
            body = "\n".join(
                line.replace("# MAGIC ", "").replace("# MAGIC", "")
                for line in body.splitlines()
            )
            cells.append(("markdown", body.strip()))
        else:
            cells.append(("code", body.strip()))
    return [(kind, text) for kind, text in cells if text]


def _cells(filename: str, text: str) -> list[tuple[str, str]]:
    return _ipynb_cells(text) if filename.endswith(".ipynb") else _py_cells(text)


def notebooks_from_git_files(
    files: dict[str, str],
    name_to_id: dict[str, str] | None = None,
    workspace_id: str = "",
    source: DiscoverySource = DiscoverySource.GIT,
) -> KnowledgeGraph:
    """Build a graph slice of notebooks (with real code) from Git-stored files."""
    name_to_id = name_to_id or {}
    graph = KnowledgeGraph(workspace_id)

    # Group files by their item folder.
    folders: dict[str, dict] = {}
    for path, text in files.items():
        norm = path.replace("\\", "/")
        folder, _, filename = norm.rpartition("/")
        entry = folders.setdefault(folder, {})
        if filename == ".platform":
            entry["platform"] = text
        elif filename == "notebook-content.py" or filename.endswith(".ipynb"):
            entry["content"] = (filename, text)

    for folder, entry in folders.items():
        content = entry.get("content")
        if not content:
            continue
        platform = _platform(entry.get("platform")).get("metadata", {})
        item_type = platform.get("type", "Notebook")
        if item_type != "Notebook":
            continue
        display_name = platform.get("displayName") or folder.rsplit("/", 1)[-1]

        native_id = name_to_id.get(display_name, display_name)
        nb_id = make_node_id(NodeType.NOTEBOOK, native_id)
        graph.add_node(Node(
            id=nb_id, type=NodeType.NOTEBOOK, name=display_name, source=source,
            properties={"git_path": folder, "source_available": True},
        ))

        filename, text = content
        cells = _cells(filename, text)
        graph.node(nb_id).properties["cell_count"] = len(cells)
        for index, (cell_type, cell_source) in enumerate(cells):
            cell_id = f"{nb_id}/cell/{index}"
            graph.add_node(Node(
                id=cell_id, type=NodeType.NOTEBOOK_CELL,
                name=f"{display_name} · cell {index}", source=source,
                properties={
                    "cell_type": cell_type,
                    "language": "markdown" if cell_type == "markdown" else "python",
                    "source_preview": cell_source[:280],
                    "source_full": cell_source,
                },
            ))
            graph.add_edge(nb_id, cell_id, EdgeType.HAS_CELL)
    return graph


class GitDiscoverer:
    """Reads item source from the workspace's Git repo (notebook code, etc.)."""

    source = DiscoverySource.GIT

    def __init__(
        self,
        git_details: dict | None,
        file_reader: FileReader | None,
        name_to_id: dict[str, str] | None = None,
    ):
        self._git_details = git_details or {}
        self._file_reader = file_reader
        self._name_to_id = name_to_id or {}

    def available(self) -> tuple[bool, str]:
        if not self._git_details or not self._git_details.get("connected", True):
            return False, "workspace is not Git-connected"
        if self._file_reader is None:
            return False, "no Git file reader (provider token required)"
        return True, ""

    def discover(self, workspace_id: str) -> KnowledgeGraph:
        try:
            files = self._file_reader()
        except Exception as exc:  # noqa: BLE001
            log.warning("Git file read failed: %s", exc)
            files = {}
        return notebooks_from_git_files(
            files, name_to_id=self._name_to_id, workspace_id=workspace_id,
        )
