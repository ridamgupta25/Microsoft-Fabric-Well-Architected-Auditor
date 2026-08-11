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
        summary = empty_lakehouse_files_summary()
        continuation = ""
        seen = 0
        while True:
            params = {
                "recursive": "true",
                "resource": "filesystem",
                "directory": f"{item_id}/Files",
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
                    _add_entry(summary, item_id, entry)
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
    return summary


def _add_entry(summary: dict, item_id: str, entry: dict) -> None:
    if _is_directory(entry):
        return
    name = str(entry.get("name") or "")
    if not name:
        return
    relative = _relative_to_files(item_id, name)
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


def _relative_to_files(item_id: str, name: str) -> str:
    prefix = f"{item_id}/Files/"
    if name.startswith(prefix):
        return name[len(prefix):]
    marker = "/Files/"
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
