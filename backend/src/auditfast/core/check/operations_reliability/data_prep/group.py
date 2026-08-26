"""Operations & Reliability · Data Prep — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) rather than judging
one workspace in isolation. Registers into the separate ``GROUP_REGISTRY`` via
:func:`group_check`; N/A-not-FAIL when fewer than two members can be read.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.operations_reliability.data_prep.automated import (
    notebook_validates_post_failure_integrity,
)
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext

#: A pipeline branch that runs after a failure, or a recovery/backfill pipeline.
_PL_RECOVERY = re.compile(
    r'"dependencyConditions"\s*:\s*\[[^\]]*"Failed"'
    r"|recover\w*|re-?run|backfill|replay|reprocess\w*|(?:^|\W)resume(?:\W|$)|repair",
    re.IGNORECASE,
)
#: A cross-layer integrity comparison expressed in a pipeline (Lookup/Script that
#: reconciles counts). Deliberately count/reconciliation wording only — the
#: Fabric ``Validation`` activity checks file existence, not layer integrity, so
#: it is excluded to avoid a false pass.
_PL_INTEGRITY = re.compile(
    r"reconcil\w*|row[_ ]?count|record[_ ]?count|count[_ ]?check"
    r"|validate[^\"]{0,40}count|source[_ ]?vs[_ ]?target|control[_ ]?total",
    re.IGNORECASE,
)


#: A notebook recovery/replay/backfill path (before the integrity re-check).
_NB_RECOVERY = re.compile(
    r"recover\w*|re-?run|backfill|replay|reprocess\w*|(?:^|\W)resume(?:\W|$)|repair",
    re.IGNORECASE,
)


def _pipeline_validates_post_failure_integrity(definition_json: str) -> bool:
    """True when a pipeline runs a count/reconciliation check on a failure path."""
    return bool(
        _PL_RECOVERY.search(definition_json)
        and _PL_INTEGRITY.search(definition_json)
    )


def _post_failure_paths(ws: WorkspaceContext) -> tuple[list[str], list[str]]:
    """Return ``(recovery_paths, validated_paths)`` inspected in a workspace.

    ``recovery_paths`` are the notebooks/pipelines that run on a recovery,
    replay or backfill path; ``validated_paths`` are the subset that also
    re-validate cross-layer integrity there (a source-vs-target row/key count
    comparison that fails loudly on a mismatch). Naming both lets the evidence
    say exactly what was inspected and why it did or did not pass.
    """
    recovery: list[str] = []
    validated: list[str] = []
    for name, definition in (ws.notebooks or {}).items():
        code = executable_code(definition)
        if notebook_validates_post_failure_integrity(code):
            recovery.append(name)
            validated.append(name)
        elif _NB_RECOVERY.search(code):
            recovery.append(name)
    for name, definition in (ws.pipelines or {}).items():
        blob = json.dumps(definition)
        if _PL_RECOVERY.search(blob):
            recovery.append(name)
            if _PL_INTEGRITY.search(blob):
                validated.append(name)
    return recovery, validated


@group_check(
    id="XW-POST-FAILURE-INTEGRITY", ref="9.3.4",
    title="Data integrity validated across layers after failures",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS],
    required=False,
)
def post_failure_integrity_consistent(ctx: GroupContext) -> Verdict:
    """Every environment re-checks cross-layer integrity on a recovery path.

    For each environment its notebooks and pipelines are scanned for a recovery /
    replay / backfill path that re-validates cross-layer row/key counts (a
    source-vs-target count comparison referencing the run audit's
    ``source_count`` / ``target_count``) and fails loudly on a mismatch. The
    verdict is the coverage of environments that do so, and the evidence names
    the paths inspected in each — the validated ones, or the recovery paths that
    were found but do not re-validate. N/A when fewer than two members' notebook
    or pipeline definitions could be read.
    """
    present: list[str] = []
    absent: list[str] = []
    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.NOTEBOOK_DEFINITIONS)
                or ws.has(Resource.PIPELINE_DEFINITIONS)):
            continue
        label = _xw.env_label(member)
        recovery, validated = _post_failure_paths(ws)
        if validated:
            present.append(f"{label} [{', '.join(validated[:3])}]")
        elif recovery:
            absent.append(
                f"{label} (recovery path(s) {', '.join(recovery[:3])} do not "
                "re-validate cross-layer counts)"
            )
        else:
            absent.append(f"{label} (no recovery/replay/backfill path found)")

    total = len(present) + len(absent)
    if total < 2:
        return not_applicable(
            "fewer than two environments had readable notebook or pipeline "
            "definitions to compare"
        )
    if not absent:
        return covered(
            total, total,
            f"every environment re-validates cross-layer integrity on a recovery "
            f"path: {'; '.join(present)}",
        )
    passing = f"validated in {'; '.join(present)}. " if present else ""
    return covered(
        len(present), total,
        f"cross-layer integrity is re-validated on a recovery path in "
        f"{len(present)} of {total} environment(s); {passing}not in {'; '.join(absent)}",
    )
