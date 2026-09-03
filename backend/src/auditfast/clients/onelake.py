"""OneLake Files listing through the ADLS Gen2 List Path API.

OneLake exposes Fabric item storage at ``onelake.dfs.fabric.microsoft.com`` via
the standard Azure Data Lake Storage Gen2 API. The auditor uses that only to
build a small per-Lakehouse aggregate for the Files section; it never persists
individual file paths in the knowledge base.
"""
from __future__ import annotations

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
        import requests

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
