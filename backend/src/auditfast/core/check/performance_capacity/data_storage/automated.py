"""Performance & Capacity · Data Storage — OneLake Files layout checks."""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_DEFAULT_FILE_SIZE_FLOOR_BYTES = 128 * 1024 * 1024
_DEFAULT_FILE_SIZE_CEILING_BYTES = 1024 * 1024 * 1024

_SIZE_BUCKET_RANGES = {
    "under_16mb": (0, 16 * 1024 * 1024),
    "16_128mb": (16 * 1024 * 1024, 128 * 1024 * 1024),
    "128mb_1gb": (128 * 1024 * 1024, 1024 * 1024 * 1024),
    "over_1gb": (1024 * 1024 * 1024, None),
}


def _summaries(ctx: CheckContext) -> dict[str, dict]:
    return ctx.workspace.lakehouse_files or {}


def _int_setting(ctx: CheckContext, key: str, default: int) -> int:
    try:
        value = int(ctx.setting(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _healthy_bucket_count(summary: dict, floor: int, ceiling: int) -> int:
    buckets = summary.get("size_buckets") or {}
    total = 0
    for name, count in buckets.items():
        lower, upper = _SIZE_BUCKET_RANGES.get(name, (None, None))
        if lower is None:
            continue
        if lower >= floor and upper is not None and upper <= ceiling:
            total += int(count or 0)
    return total


def _suffix(summary: dict) -> str:
    flags = []
    if summary.get("truncated"):
        flags.append("listing truncated at the per-lakehouse cap")
    if summary.get("sampled"):
        flags.append("summary is sampled")
    return f" ({', '.join(flags)})" if flags else ""


@check(
    id="WS-FILE-SIZE-BANDS", ref="4.3.3",
    title="File sizes avoid the small-file problem (target 128MB-1GB per file)",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.STORAGE], requires=[Resource.LAKEHOUSE_FILES], required=True,
)
def lakehouse_file_sizes_avoid_small_files(ctx: CheckContext) -> Verdict:
    """Lakehouse data files are mostly in the healthy 128MB-1GB size band."""
    if not ctx.workspace.has(Resource.LAKEHOUSE_FILES):
        return not_applicable("OneLake Files listings could not be read for Lakehouses")
    summaries = _summaries(ctx)
    if not summaries:
        return not_applicable("No Lakehouse Files summaries were read for this workspace")

    floor = _int_setting(ctx, "lakehouse_file_size_floor_bytes", _DEFAULT_FILE_SIZE_FLOOR_BYTES)
    ceiling = _int_setting(ctx, "lakehouse_file_size_ceiling_bytes", _DEFAULT_FILE_SIZE_CEILING_BYTES)
    if ceiling < floor:
        floor, ceiling = _DEFAULT_FILE_SIZE_FLOOR_BYTES, _DEFAULT_FILE_SIZE_CEILING_BYTES

    total_data = healthy = excluded = total_files = 0
    details: list[str] = []
    for name, summary in sorted(summaries.items()):
        data = int(summary.get("data_file_count") or 0)
        files = int(summary.get("file_count") or 0)
        ok = _healthy_bucket_count(summary, floor, ceiling)
        total_data += data
        total_files += files
        healthy += ok
        excluded += int(summary.get("excluded_file_count") or 0)
        details.append(f"{name}: {ok}/{data} data file(s) in band{_suffix(summary)}")

    if total_data == 0:
        return not_applicable(
            f"No Lakehouse holds assessable data files in the Files section "
            f"({total_files} file(s) listed; {excluded} Delta log/CRC/marker/metadata file(s) excluded)"
        )
    return covered(
        healthy, total_data,
        f"{healthy} of {total_data} assessable Lakehouse data file(s) are in the "
        f"{floor // (1024 * 1024)}MB-{ceiling // (1024 * 1024)}MB target band; "
        f"{excluded} obvious non-data file(s) (_delta_log, .crc, metadata JSON, zero-byte "
        f"markers) were excluded. {'; '.join(details)}",
    )


@check(
    id="WS-FILES-SOURCE-DATE-HIERARCHY", ref="4.3.2",
    title="Raw files in Files section organized by source/date hierarchy",
    pillar=Pillar.DATA_PROCESSING, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=[Layer.STORAGE], requires=[Resource.LAKEHOUSE_FILES], required=True,
)
def lakehouse_files_are_source_date_partitioned(ctx: CheckContext) -> Verdict:
    """Files paths show source and date hierarchy using structural date patterns.

    This intentionally matches only structural evidence: more than one top-level
    folder (a source/domain split) and a date-like path segment (yyyy/mm/dd,
    yyyy-mm-dd, or Hive-style year=/month=/day=). It does not infer a client's
    private naming convention.
    """
    if not ctx.workspace.has(Resource.LAKEHOUSE_FILES):
        return not_applicable("OneLake Files listings could not be read for Lakehouses")
    summaries = _summaries(ctx)
    if not summaries:
        return not_applicable("No Lakehouse Files summaries were read for this workspace")

    files = dated = 0
    top_level: set[str] = set()
    details: list[str] = []
    truncated = 0
    for name, summary in sorted(summaries.items()):
        file_count = int(summary.get("file_count") or 0)
        date_count = int(summary.get("dated_path_count") or 0)
        folders = [str(f) for f in summary.get("top_level_folders") or [] if str(f)]
        files += file_count
        dated += date_count
        top_level.update(folders)
        truncated += 1 if summary.get("truncated") else 0
        details.append(f"{name}: {len(folders)} top-level folder(s), {date_count}/{file_count} dated path(s)")

    if files == 0:
        return not_applicable("The Lakehouse Files area is empty")

    source_split = len(top_level) > 1
    date_hierarchy = dated > 0
    passed = int(source_split) + int(date_hierarchy)
    caveat = (
        f"; {truncated} Lakehouse listing(s) were truncated at the enumeration cap"
        if truncated else ""
    )
    return covered(
        passed, 2,
        f"{passed} of 2 structural hierarchy signals are present: "
        f"source/domain split={'yes' if source_split else 'no'} "
        f"({len(top_level)} top-level folder(s)); date-like path segment="
        f"{'yes' if date_hierarchy else 'no'} ({dated} of {files} file path(s)). "
        f"Patterns are structural only, not client-specific names{caveat}. "
        f"{'; '.join(details)}",
    )
