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
    _RECOVERY_CONTEXTS,
    notebook_validates_post_failure_integrity,
)
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext

#: A pipeline branch that runs after a failure, or a recovery/backfill pipeline.
#: Each word is bounded, so ``repair``/``recover``/``rerun`` inside a longer
#: identifier is not a recovery path.
_PL_RECOVERY = re.compile(
    r'"dependencyConditions"\s*:\s*\[[^\]]*"Failed"'
    r"|(?:^|\W|_)recover\w*|(?:^|\W|_)re-?run(?:\W|_|$)"
    r"|(?:^|\W|_)backfill(?:\W|_|$)|(?:^|\W|_)replay(?:\W|_|$)"
    r"|(?:^|\W|_)reprocess\w*|(?:^|\W|_)resume(?:\W|_|$)"
    r"|(?:^|\W|_)repair(?:\W|_|$)",
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


def _notebook_recovery_contexts(code: str) -> list[str]:
    """The recovery wordings a notebook's executable code uses, if any.

    Delegates to ``_RECOVERY_CONTEXTS`` — the same table the per-workspace
    ``NB-POST-FAILURE-INTEGRITY`` uses on this ref — so the two cannot disagree
    about what counts as a recovery path. A second, looser copy lived here and
    matched ``recover``/``repair``/``rerun`` **anywhere inside a longer word**,
    which is how capacity-metrics notebooks came to be reported as recovery
    paths that fail to re-validate.
    """
    return [label for label, pattern in _RECOVERY_CONTEXTS if pattern.search(code)]


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
        elif _notebook_recovery_contexts(code):
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
    """Every environment that *has* a recovery path re-checks integrity on it.

    For each environment its notebooks and pipelines are scanned for a recovery /
    replay / backfill path that re-validates cross-layer row/key counts (a
    source-vs-target comparison that fails loudly on a mismatch). The evidence
    names the paths inspected in each — the validated ones, or the recovery paths
    found that do not re-validate.

    An environment with **no recovery path at all** is excluded, not failed.
    "Validate integrity after a failure" presupposes something that runs after a
    failure; a workspace that has no such path has nothing to validate on, and
    reporting it as a gap sends its owner to fix code that does not exist. That
    is a separate finding — *whether* a recovery path should exist — and not this
    one. N/A when fewer than two environments hold a recovery path.
    """
    present: list[str] = []
    absent: list[str] = []
    skipped: list[str] = []
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
            skipped.append(f"{label} (no recovery, replay or backfill path exists)")

    excluded = (f"; {len(skipped)} environment(s) excluded with nothing to validate "
                f"on: {'; '.join(skipped)}") if skipped else ""

    total = len(present) + len(absent)
    if total < 2:
        # Only one environment has a recovery path, so there is no cross-environment
        # comparison to make -- but that environment's own posture is a real
        # finding and must not vanish into the N/A. Naming it keeps a lone
        # unvalidated recovery path visible.
        lone = ""
        if present:
            lone = f"; {'; '.join(present)} does re-validate on its recovery path"
        elif absent:
            lone = f"; {'; '.join(absent)}"
        return not_applicable(
            "fewer than two environments in this group have a recovery, replay or "
            f"backfill path whose integrity re-check could be compared{lone}{excluded}"
        )
    if not absent:
        return covered(
            total, total,
            f"every environment with a recovery path re-validates cross-layer "
            f"integrity on it: {'; '.join(present)}{excluded}",
        )
    passing = f"validated in {'; '.join(present)}. " if present else ""
    return covered(
        len(present), total,
        f"cross-layer integrity is re-validated on a recovery path in "
        f"{len(present)} of {total} environment(s) that have one; {passing}not in "
        f"{'; '.join(absent)}{excluded}",
    )
