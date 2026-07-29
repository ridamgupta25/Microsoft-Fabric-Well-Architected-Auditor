"""Local export ingestion — read item source from a folder of exported files.

The zero-privilege path to notebook code. A user with *normal* Fabric access can
export a notebook from the portal (or drop a Git checkout) into a local folder;
this adapter ingests the same ``.platform`` + ``notebook-content.py`` / ``.ipynb``
files the Git serialization uses. It needs no admin, no Git connection, and no
``Item.ReadWrite`` scope — the export happens in the user's own interactive
session, so ``getDefinition``'s 401 never applies.

It reuses the tested :func:`~auditfast.discovery.git.notebooks_from_git_files`
parser, so exported code lands as the same notebook/cell nodes and merges onto the
REST-discovered metadata when a ``name -> item id`` map is supplied.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..core.graph import DiscoverySource, KnowledgeGraph
from .git import notebooks_from_git_files

log = logging.getLogger("auditfast.local")

_ITEM_FILES = (".platform", "notebook-content.py")


def read_export_folder(root: str | Path) -> dict[str, str]:
    """Read exported item files under ``root`` into ``{relative_path: text}``."""
    root = Path(root)
    files: dict[str, str] = {}
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _ITEM_FILES or path.suffix == ".ipynb":
            try:
                files[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read %s: %s", path, exc)
    return files


class LocalExportDiscoverer:
    """Ingests item source code from a local folder of exported files."""

    source = DiscoverySource.LOCAL_EXPORT

    def __init__(self, root: str | Path | None, name_to_id: dict[str, str] | None = None):
        self._root = root
        self._name_to_id = name_to_id or {}

    def available(self) -> tuple[bool, str]:
        if not self._root or not Path(self._root).exists():
            return False, "no local export folder configured"
        return True, ""

    def discover(self, workspace_id: str) -> KnowledgeGraph:
        files = read_export_folder(self._root)
        return notebooks_from_git_files(
            files, name_to_id=self._name_to_id, workspace_id=workspace_id,
            source=DiscoverySource.LOCAL_EXPORT,
        )
