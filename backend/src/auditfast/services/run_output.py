"""One directory per audit run, so nothing is ever overwritten.

Every run used to write ``audit-report.xlsx``, ``jobs/`` and the rest straight
into ``output/``. Auditing workspace B then destroyed workspace A's report, and
re-auditing A destroyed the run you were comparing against - silently, because
the filenames were identical either way.

A run now owns ``output/<workspace-or-project>_<timestamp>/``. The timestamp is
what makes it safe: two runs of the same workspace are two directories, so a
comparison is a diff rather than a memory.

Finding a run again is the other half. The API does not need to search - a job
records the directory it wrote - but the CLI and a human do, so
:func:`latest_run_dir` resolves "the run I just did" without anyone copying a
timestamp around.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

#: Long enough to stay recognisable, short enough to survive a path limit when
#: joined to a timestamp and a deep output root.
_MAX_LABEL = 48

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: ``_20260826_143012``. Sorts lexicographically in time order, which is what
#: lets `latest_run_dir` pick by name rather than by mtime - mtime changes when
#: a later step writes into an earlier run's folder.
_STAMP = "%Y%m%d_%H%M%S"

_STAMPED = re.compile(r"^(?P<label>.+)_(?P<stamp>\d{8}_\d{6})$")


def slugify(text: str, limit: int = _MAX_LABEL) -> str:
    """A filesystem-safe fragment of ``text``, or ``'run'`` if nothing survives."""
    cleaned = _UNSAFE.sub("-", (text or "").strip()).strip("-._")
    return (cleaned[:limit].rstrip("-._") or "run")


def run_label(project_name: str, workspaces=None) -> str:
    """What to call this run's directory.

    A single-workspace run is named for the workspace, because that is how
    someone looking for it thinks of it. Anything else falls back to the project
    name - naming a six-workspace run after whichever came first would be
    actively misleading.
    """
    selections = list(workspaces or [])
    if len(selections) == 1:
        only = selections[0]
        if isinstance(only, str):
            return slugify(only)
        if isinstance(only, dict):
            return slugify(str(only.get("name") or only.get("id") or project_name))
    return slugify(project_name)


def new_run_dir(base: str | Path, label: str, *, now: datetime | None = None) -> Path:
    """Create and return ``base/<label>_<timestamp>/``.

    Collisions are possible only within the same second; a numeric suffix keeps
    even that from overwriting, because losing a run to a coincidence would be
    exactly the bug this module exists to prevent.
    """
    base = Path(base)
    stamp = (now or datetime.now(timezone.utc)).strftime(_STAMP)
    directory = base / f"{slugify(label)}_{stamp}"
    suffix = 1
    while directory.exists():
        suffix += 1
        directory = base / f"{slugify(label)}_{stamp}-{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_dirs(base: str | Path, label: str | None = None) -> list[Path]:
    """Every run directory under ``base``, newest first.

    ``label`` narrows to one workspace or project. Directories that do not carry
    a timestamp are ignored rather than guessed at - an output folder may hold
    other things, and treating them as runs would surface nonsense.
    """
    base = Path(base)
    if not base.is_dir():
        return []
    wanted = slugify(label) if label else None
    found: list[Path] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        match = _STAMPED.match(entry.name)
        if not match:
            continue
        if wanted and match.group("label") != wanted:
            continue
        found.append(entry)
    return sorted(found, key=lambda p: p.name, reverse=True)


def latest_run_dir(base: str | Path, label: str | None = None) -> Path | None:
    """The most recent run directory, or ``None`` when there is none."""
    found = run_dirs(base, label)
    return found[0] if found else None
