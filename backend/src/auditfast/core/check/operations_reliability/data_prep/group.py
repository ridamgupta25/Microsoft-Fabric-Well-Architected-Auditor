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
from auditfast.core.check.helpers import Verdict
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


def _pipeline_validates_post_failure_integrity(definition_json: str) -> bool:
    """True when a pipeline runs a count/reconciliation check on a failure path."""
    return bool(
        _PL_RECOVERY.search(definition_json)
        and _PL_INTEGRITY.search(definition_json)
    )


def _validates_post_failure_integrity(ws: WorkspaceContext) -> bool:
    """True when any notebook or pipeline validates post-failure layer integrity."""
    if any(
        notebook_validates_post_failure_integrity(executable_code(defn))
        for defn in (ws.notebooks or {}).values()
    ):
        return True
    return any(
        _pipeline_validates_post_failure_integrity(json.dumps(defn))
        for defn in (ws.pipelines or {}).values()
    )


@group_check(
    id="XW-POST-FAILURE-INTEGRITY", ref="9.3.4",
    title="Data integrity validated across layers after failures",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM,
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS],
    required=False,
)
def post_failure_integrity_consistent(ctx: GroupContext) -> Verdict:
    """Every environment re-checks cross-layer integrity on a recovery path.

    The per-workspace ``NB-POST-FAILURE-INTEGRITY`` (9.3.4) judges one notebook;
    this compares across the group over both notebooks and pipelines, so a
    recovery-time integrity check present in Prod but missing from Dev/UAT is
    surfaced as drift. N/A when fewer than two members' notebook or pipeline
    definitions could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: (
            ws.has(Resource.NOTEBOOK_DEFINITIONS)
            or ws.has(Resource.PIPELINE_DEFINITIONS)
        ),
        implements=_validates_post_failure_integrity,
        practice="validates cross-layer integrity on a recovery/replay path",
        data_name="notebook or pipeline definitions",
    )
