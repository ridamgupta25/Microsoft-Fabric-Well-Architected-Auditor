"""4.3.4 - a Lakehouse with no files has nothing to purge.

The reviewer's comment: *"out of 9 lakehouses only 1 lakehouse has content in the
files folder, it should be N/A for the other lakehouses, and add the lakehouse
name in the evidence."*

Both halves were real. The check counted every Lakehouse and Warehouse in the
workspace and failed the lot if no notebook held a purge routine - so 8 empty
Lakehouses were reported as accumulating stale files they did not have. And the
evidence named none of them, so a reviewer could not tell which store the gap
applied to.

The ``Files/`` listing is readable now (``lakehouse_files``), so the check no
longer has to assume every Lakehouse holds data. An unreadable listing is
reported as unassessed rather than counted either way - not readable is not the
same as empty.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_prep.automated import ws_file_purge
from auditfast.core.enums import Status
from auditfast.core.models import Item

from .fixtures.builders import workspace_ctx


def _notebook(source: str) -> dict:
    """An ipynb-style definition - the shape ``notebook_code`` actually reads.

    It walks ``cells[].source``, not the base64 ``parts`` payload the Fabric API
    returns; the crawl decodes that before storing. Building the wrong shape here
    made a purge notebook look empty, so the check reported "no purge routine"
    against a fixture that contained one.
    """
    return {"cells": [{"cell_type": "code", "source": source}]}


_PURGE_NOTEBOOK = _notebook("notebookutils.fs.rm('Files/staging', True)")
_PLAIN_NOTEBOOK = _notebook("df = spark.read.parquet('Files/in')")


def _lakehouses(*names: str) -> list[Item]:
    return [Item(id=f"id-{n}", display_name=n, type="Lakehouse") for n in names]


def _files(count: int) -> dict:
    return {"file_count": count, "data_file_count": count, "total_bytes": count * 1024}


def _ctx(items, listings, notebooks=None):
    return workspace_ctx(
        items=items,
        lakehouse_files=listings,
        notebooks=notebooks if notebooks is not None else {"nb": _PLAIN_NOTEBOOK},
    )


def _rows(verdicts: list) -> dict[str, str]:
    return {v.obj: v.evidence for v in verdicts if v.obj}


# ---------------------------------------------------------------------------
# the defect: empty lakehouses were counted as accumulating stale files
# ---------------------------------------------------------------------------

def test_only_lakehouses_holding_files_are_assessed():
    """The reviewer's case: 1 of 9 holds files, the other 8 have nothing to purge."""
    names = [f"lh_{i}" for i in range(9)]
    listings = {n: _files(0) for n in names}
    listings["lh_0"] = _files(120)
    verdicts = ws_file_purge(_ctx(_lakehouses(*names), listings))
    summary = verdicts[0]
    assert "1 lakehouse/warehouse item(s) hold Files-section data" in summary.evidence
    assert "lh_0" in summary.evidence
    assert "8 store(s) hold no Files-section data and are not assessed" in summary.evidence


def test_no_lakehouse_holds_files_is_na_not_fail():
    """Nothing to accumulate is not a housekeeping failure."""
    names = ["lh_a", "lh_b"]
    verdicts = ws_file_purge(_ctx(_lakehouses(*names), {n: _files(0) for n in names}))
    assert verdicts[0].status is Status.NA
    assert "no orphaned files to archive or purge" in verdicts[0].evidence


def test_an_unreadable_listing_is_not_treated_as_empty():
    """A listing we could not read says nothing - it must not clear the finding."""
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a", "lh_b"), {"lh_a": _files(5)}))
    summary = verdicts[0]
    assert "1 store(s) could not be listed and are not assessed" in summary.evidence
    assert "lh_b" in summary.evidence


def test_every_lakehouse_unreadable_is_na():
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a", "lh_b"), {}))
    assert verdicts[0].status is Status.NA


# ---------------------------------------------------------------------------
# the evidence names the stores
# ---------------------------------------------------------------------------

def test_each_affected_lakehouse_gets_a_named_row():
    listings = {"lh_a": _files(10), "lh_b": _files(20), "lh_c": _files(0)}
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a", "lh_b", "lh_c"), listings))
    rows = _rows(verdicts)
    assert set(rows) == {"lh_a", "lh_b"}
    assert "accumulate indefinitely" in rows["lh_a"]


def test_the_named_rows_are_unscored():
    """They say which store, they do not re-cast the summary's verdict."""
    listings = {f"lh_{i}": _files(10) for i in range(6)}
    verdicts = ws_file_purge(_ctx(_lakehouses(*listings), listings))
    summary, *rows = verdicts
    assert summary.scored is True
    assert rows and all(not row.scored for row in rows)


def test_the_store_list_in_the_evidence_is_bounded():
    listings = {f"lh_{i:02d}": _files(10) for i in range(12)}
    verdicts = ws_file_purge(_ctx(_lakehouses(*listings), listings))
    assert "(+7 more)" in verdicts[0].evidence


# ---------------------------------------------------------------------------
# a purge routine satisfies the point
# ---------------------------------------------------------------------------

def test_a_purge_routine_passes_and_still_reports_the_empty_stores():
    listings = {"lh_a": _files(10), "lh_b": _files(0)}
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a", "lh_b"), listings,
                                  notebooks={"purge_nb": _PURGE_NOTEBOOK}))
    assert verdicts[0].score == 3
    assert "purge_nb" in verdicts[0].evidence
    assert "not assessed" in verdicts[0].evidence
    assert not _rows(verdicts)


def test_no_store_is_na():
    verdicts = ws_file_purge(_ctx([], {}))
    assert verdicts[0].status is Status.NA


def test_no_notebooks_is_na():
    verdicts = ws_file_purge(workspace_ctx(
        items=_lakehouses("lh_a"), lakehouse_files={"lh_a": _files(10)}, notebooks={}))
    assert verdicts[0].status is Status.NA


# ---------------------------------------------------------------------------
# detection: a real purge vs incidental cleanup vs prose
# ---------------------------------------------------------------------------

def test_prose_or_a_scoring_rubric_does_not_count_as_a_purge():
    """A rubric mentioning 'retention policy' is prose, not a purge routine."""
    rubric = _notebook(
        "rubric = '''\n# Scoring rubric\n"
        "- retention policy present: 3\n- no retention policy: 0\n"
        "- archive old files periodically: partial\n'''\nprint(rubric)"
    )
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a"), {"lh_a": _files(10)},
                                  notebooks={"Setup_Section1_TenantAdmin": rubric}))
    assert verdicts[0].score == 0
    assert "no notebook implements a file archive or purge routine" in verdicts[0].evidence


def test_incidental_cleanup_is_not_a_purge_routine():
    """os.remove of a just-written file + rmtree of a temp dir is not a retention policy."""
    incidental = _notebook(
        "df.write.parquet(lakehouse_path)\n"
        "os.remove(lakehouse_path)\n"
        "shutil.rmtree(temp_dir)"
    )
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a"), {"lh_a": _files(10)},
                                  notebooks={"nb_eam_sftp_read_json_copy": incidental}))
    assert verdicts[0].score == 0
    assert "no notebook implements a file archive or purge routine" in verdicts[0].evidence


def test_a_files_section_delete_counts_as_a_purge():
    """A notebookutils.fs.rm on the Files section is a deliberate purge."""
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a"), {"lh_a": _files(10)},
                                  notebooks={"purge": _PURGE_NOTEBOOK}))
    assert verdicts[0].score == 3


def test_a_local_delete_past_a_cutoff_counts_as_a_purge():
    """os.remove filtered by an age cutoff IS a retention routine."""
    retention = _notebook(
        "cutoff = datetime.now() - timedelta(days=30)\n"
        "for f in list_files('Files/landing'):\n"
        "    if f.modification_time < cutoff:\n"
        "        os.remove(f.path)"
    )
    verdicts = ws_file_purge(_ctx(_lakehouses("lh_a"), {"lh_a": _files(10)},
                                  notebooks={"purge_old_files": retention}))
    assert verdicts[0].score == 3


def test_the_evidence_names_stores_with_their_file_counts():
    """The reviewer's ask: name which lakehouse holds Files content, with counts."""
    listings = {"Bronze_MLC_Lakehouse_TEST": _files(1030),
                "mlc_lz_lh": _files(59), "Silver_MLC_Lakehouse_TEST": _files(0)}
    verdicts = ws_file_purge(_ctx(
        _lakehouses("Bronze_MLC_Lakehouse_TEST", "mlc_lz_lh", "Silver_MLC_Lakehouse_TEST"),
        listings))
    summary = verdicts[0]
    assert summary.score == 0
    assert "Bronze_MLC_Lakehouse_TEST holds 1,030 Files" in summary.evidence
    assert "mlc_lz_lh holds 59 Files" in summary.evidence
    # The empty one is named as not-assessed, not flagged.
    assert "Silver_MLC_Lakehouse_TEST" in summary.evidence
    assert set(_rows(verdicts)) == {"Bronze_MLC_Lakehouse_TEST", "mlc_lz_lh"}


# ---------------------------------------------------------------------------
# refinements: system staging stores and system files are not user data
# ---------------------------------------------------------------------------

def test_fabric_managed_dataflow_staging_stores_are_excluded():
    """Dataflow staging lakehouses hold system cache, not user-orphaned data."""
    listings = {"Bronze": _files(100),
                "DataflowsStagingLakehouse": _files(500),
                "StagingLakehouseForDataflows_20260311211836": _files(300)}
    verdicts = ws_file_purge(_ctx(
        _lakehouses("Bronze", "DataflowsStagingLakehouse",
                    "StagingLakehouseForDataflows_20260311211836"),
        listings))
    summary = verdicts[0]
    assert "Bronze holds 100 Files" in summary.evidence
    assert "Fabric-managed Dataflow staging store(s) are excluded" in summary.evidence
    # Only the genuine user lakehouse is flagged.
    assert set(_rows(verdicts)) == {"Bronze"}


def test_counts_use_data_file_count_not_total_file_count():
    """System cache files inflate file_count; only data_file_count is user data."""
    listings = {"Bronze": {"file_count": 40, "data_file_count": 38, "total_bytes": 4096}}
    verdicts = ws_file_purge(_ctx(_lakehouses("Bronze"), listings))
    assert "Bronze holds 38 Files" in verdicts[0].evidence
    assert "holds 40 Files" not in verdicts[0].evidence


def test_a_lakehouse_with_only_system_cache_is_not_assessed():
    """data_file_count 0 (only FileCache/models$) -> nothing to purge -> N/A."""
    listings = {"Bronze": {"file_count": 12, "data_file_count": 0, "total_bytes": 1024}}
    verdicts = ws_file_purge(_ctx(_lakehouses("Bronze"), listings))
    assert verdicts[0].status is Status.NA
    assert "no orphaned files" in verdicts[0].evidence
