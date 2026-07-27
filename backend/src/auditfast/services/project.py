"""Loading a project definition.

A project spans one or more Fabric workspaces, each tagged with a layer role.
:func:`load_project` turns the YAML file into a :class:`ProjectConfig` — a typed
object, rather than the four-element tuple the previous implementation returned,
so callers stop having to remember positional ordering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.enums import Layer

DEFAULT_REMEDIATION_FILE = "config/remediation.yaml"


def _load_yaml(path: Path) -> dict:
    import yaml

    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(slots=True)
class ProjectConfig:
    """One engagement: its settings, its workspaces, and where its data lives."""

    path: Path
    name: str = "Fabric Project"
    settings: dict = field(default_factory=dict)
    targets: list[tuple[str, Layer]] = field(default_factory=list)
    remediation_file: Path | None = None
    auth: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def base_dir(self) -> Path:
        """Directory relative paths in the YAML resolve against.

        Two levels up from the file, so ``backend/config/project.yaml`` resolves
        ``config/remediation.yaml`` to ``backend/config/remediation.yaml``.
        """
        return self.path.parent.parent


def _resolve(value: str, base: Path) -> Path:
    """Resolve a config path against the project base, preferring what exists."""
    candidate = Path(value)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    for option in (candidate, base / candidate):
        if option.exists():
            return option
    return base / candidate


def load_project(project_path: str | Path) -> ProjectConfig:
    """Parse a project YAML file.

    Raises:
        FileNotFoundError: the project file does not exist. Failing here beats
            silently auditing an empty project.
    """
    path = Path(project_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Project file not found: {path}")

    raw = _load_yaml(path)
    settings = raw.get("project") or {}
    base = path.parent.parent

    targets = [
        (entry["id"], Layer.parse(entry.get("role")))
        for entry in (raw.get("workspaces") or [])
        if entry.get("id")
    ]

    return ProjectConfig(
        path=path,
        name=settings.get("name", "Fabric Project"),
        settings=settings,
        targets=targets,
        remediation_file=_resolve(raw.get("remediation", DEFAULT_REMEDIATION_FILE), base),
        auth=raw.get("auth") or {},
        raw=raw,
    )


def load_remediation(config: ProjectConfig):
    """Build the remediation book for a project, empty when the file is absent."""
    from ..core.checks.helpers import RemediationBook

    path = config.remediation_file
    if path and path.exists():
        return RemediationBook(_load_yaml(path))
    return RemediationBook()
