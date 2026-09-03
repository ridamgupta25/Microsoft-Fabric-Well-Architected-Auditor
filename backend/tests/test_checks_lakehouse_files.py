from __future__ import annotations

from auditfast.core.check.performance_capacity.data_storage.automated import (
    lakehouse_file_sizes_avoid_small_files,
    lakehouse_files_are_source_date_partitioned,
)
from auditfast.core.enums import Resource, Status
from auditfast.core.models import CheckContext, WorkspaceContext


def _summary(
    *,
    file_count: int = 0,
    data_file_count: int = 0,
    excluded_file_count: int = 0,
    buckets: dict[str, int] | None = None,
    top_level_folders: list[str] | None = None,
    max_depth: int = 0,
    dated_path_count: int = 0,
    truncated: bool = False,
) -> dict:
    return {
        "file_count": file_count,
        "data_file_count": data_file_count,
        "excluded_file_count": excluded_file_count,
        "total_bytes": 0,
        "size_buckets": buckets or {
            "under_16mb": 0,
            "16_128mb": 0,
            "128mb_1gb": 0,
            "over_1gb": 0,
        },
        "top_level_folders": top_level_folders or [],
        "max_depth": max_depth,
        "dated_path_count": dated_path_count,
        "sampled": truncated,
        "truncated": truncated,
    }


def _ctx(lakehouse_files: dict[str, dict] | None = None, *, unavailable=False) -> CheckContext:
    workspace = WorkspaceContext(
        id="ws",
        lakehouse_files=lakehouse_files or {},
        unavailable={Resource.LAKEHOUSE_FILES} if unavailable else set(),
    )
    return CheckContext(workspace=workspace, settings={})


def test_file_size_check_scores_healthy_bucket_share():
    ctx = _ctx({"Lakehouse": _summary(
        file_count=6,
        data_file_count=5,
        excluded_file_count=1,
        buckets={"under_16mb": 1, "16_128mb": 0, "128mb_1gb": 4, "over_1gb": 0},
    )})

    verdict = lakehouse_file_sizes_avoid_small_files(ctx)[0]
    assert verdict.score == 2
    assert "4 of 5" in verdict.evidence
    assert "1 obvious non-data" in verdict.evidence


def test_file_size_check_fails_when_files_are_all_small():
    ctx = _ctx({"Lakehouse": _summary(
        file_count=3,
        data_file_count=3,
        buckets={"under_16mb": 3, "16_128mb": 0, "128mb_1gb": 0, "over_1gb": 0},
    )})

    assert lakehouse_file_sizes_avoid_small_files(ctx)[0].score == 0


def test_file_size_check_is_na_when_listing_unavailable_or_empty():
    assert lakehouse_file_sizes_avoid_small_files(_ctx(unavailable=True))[0].status is Status.NA

    empty = _ctx({"Lakehouse": _summary(file_count=1, excluded_file_count=1)})
    assert lakehouse_file_sizes_avoid_small_files(empty)[0].status is Status.NA


def test_hierarchy_check_passes_with_source_and_date_segments():
    ctx = _ctx({"Lakehouse": _summary(
        file_count=6,
        top_level_folders=["sap", "oracle"],
        max_depth=5,
        dated_path_count=6,
    )})

    verdict = lakehouse_files_are_source_date_partitioned(ctx)
    assert verdict.score == 3
    assert "source/domain split=yes" in verdict.evidence
    assert "date-like path segment=yes" in verdict.evidence


def test_hierarchy_check_fails_flat_undated_files():
    ctx = _ctx({"Lakehouse": _summary(
        file_count=3,
        top_level_folders=["landing"],
        max_depth=1,
        dated_path_count=0,
    )})

    assert lakehouse_files_are_source_date_partitioned(ctx).score == 0


def test_hierarchy_check_is_na_when_listing_unavailable_or_files_empty():
    assert lakehouse_files_are_source_date_partitioned(_ctx(unavailable=True)).status is Status.NA

    empty = _ctx({"Lakehouse": _summary(file_count=0)})
    assert lakehouse_files_are_source_date_partitioned(empty).status is Status.NA
