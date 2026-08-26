"""Security - Data Operations — cross-workspace (group) check.

Compares the members of a project group (Dev -> UAT -> Prod) for source-control
security that should hold in every environment. Registers into the separate
``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than two
members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict, covered, graded, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext, WorkspaceContext


def _git_repo_summary(ws: WorkspaceContext) -> str:
    """A human ``provider org/repo@branch`` string from the workspace Git details."""
    details = ws.git_details or {}
    provider = str(details.get("provider") or "").strip() or "Git"
    org = str(details.get("organization") or "").strip()
    repo = str(details.get("repository") or "").strip()
    branch = str(details.get("branch") or "").strip()
    location = "/".join(part for part in (org, repo) if part) or "repository name unavailable"
    if branch:
        location = f"{location}@{branch}"
    return f"{provider} {location}"


@group_check(
    id="XW-SECRET-SCAN", ref="11.1.8",
    title="Secret-scanning / credential-detection enabled on the source repository",
    pillar=Pillar.SECURITY_ACCESS, severity=Severity.HIGH, requires=[Resource.GIT],
    required=False,
)
def secret_scanning_consistent(ctx: GroupContext) -> Verdict:
    """Secret scanning / credential detection is enabled on every repo.

    Being *connected* to Git is only the precondition; the practice is that the
    provider's secret scanning (GitHub Advanced Security secret scanning /
    push-protection, or Azure DevOps push protection) is **enabled** on the
    connected repository, so committed credentials are detected and blocked.
    That status is read from ``git_details.secret_scanning`` (populated by the
    provider when a repo-security token is available).

    Every environment is kept in the denominator: an environment whose Git
    connection could not be read is reported as *unknown* and counted, not
    silently dropped, so "1 of 3 (UAT unreadable)" never masquerades as "1 of 2".
    A connected repo whose provider security status cannot be verified is
    reported as *unverified* — it never implies coverage.
    """
    total = len(ctx.members)
    if total < 2:
        return not_applicable(
            "fewer than two environments in this group could be compared"
        )

    enabled: list[str] = []
    disabled: list[str] = []
    unverified: list[str] = []
    not_connected: list[str] = []
    unreadable: list[str] = []

    for member in ctx.members:
        ws = member.workspace
        label = _xw.env_label(member)
        if not ws.has(Resource.GIT):
            unreadable.append(label)
            continue
        if not ws.git_connected:
            not_connected.append(label)
            continue
        # Name the repository so the reviewer knows exactly where to act.
        repo_label = f"{label} ({_git_repo_summary(ws)})"
        scan = ws.git_details.get("secret_scanning") or {}
        state = scan.get("enabled")
        if state is True:
            enabled.append(repo_label)
        elif state is False:
            disabled.append(repo_label)
        else:  # connected, but the provider security status was not verified
            unverified.append(repo_label)

    parts: list[str] = []
    if enabled:
        parts.append(f"enabled in {', '.join(enabled)}")
    if disabled:
        parts.append(f"disabled in {', '.join(disabled)}")
    if unverified:
        parts.append(f"connected but secret-scanning status not verified in {', '.join(unverified)}")
    if not_connected:
        parts.append(f"not connected to source control in {', '.join(not_connected)}")
    if unreadable:
        parts.append(f"Git connection unreadable (unknown) in {', '.join(unreadable)}")
    detail = "; ".join(parts)

    # PASS only when secret scanning is confirmed on in every environment.
    if enabled and not (disabled or unverified or not_connected or unreadable):
        return covered(total, total,
                       f"secret scanning confirmed enabled in all {total} environment(s): {detail}")
    if enabled:
        return covered(
            len(enabled), total,
            f"secret scanning confirmed in {len(enabled)} of {total} environment(s); {detail}",
        )
    # Nothing confirmed. A real gap exists (a disconnected repo cannot be scanned),
    # but a connected-yet-unverifiable repo is not a definite failure — so this is
    # PARTIAL, with the accurate per-environment breakdown, never a false pass.
    if unverified or (enabled == disabled == not_connected == []):
        return graded(
            1,
            f"secret scanning could not be confirmed on any of {total} "
            f"environment(s): {detail}. Enable the provider's secret scanning on "
            "each connected repository and grant the audit a repo-security token "
            "so it can be verified.",
        )
    return covered(
        0, total,
        f"no environment has secret scanning enabled ({total} environment(s)): {detail}",
    )
