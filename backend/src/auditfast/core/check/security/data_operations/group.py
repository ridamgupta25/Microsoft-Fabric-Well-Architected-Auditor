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


def _repo_phrase(ws: WorkspaceContext) -> str:
    """A human ``GitHub repo `org/repo` (branch `x`)`` phrase from the Git details."""
    details = ws.git_details or {}
    provider = str(details.get("provider") or "").strip() or "the"
    org = str(details.get("organization") or "").strip()
    repo = str(details.get("repository") or "").strip()
    branch = str(details.get("branch") or "").strip()
    slug = "/".join(part for part in (org, repo) if part)
    phrase = f"{provider} repo `{slug}`" if slug else f"the connected {provider} repository"
    if branch:
        phrase += f" (branch `{branch}`)"
    return phrase


def _provider_name(ws: WorkspaceContext) -> str:
    """The Git provider name for prose (e.g. ``GitHub``), or a neutral fallback."""
    return str((ws.git_details or {}).get("provider") or "").strip() or "the provider"


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
    reported as *unverified* — it never implies coverage. But when **no**
    environment's Git connection could be read there is nothing to be covered, so
    the verdict is N/A: a blocked read is a permission gap, not a security
    finding.
    """
    total = len(ctx.members)
    if total < 2:
        return not_applicable(
            "fewer than two environments in this group could be compared"
        )

    bullets: list[str] = []
    enabled: list[str] = []
    disabled: list[str] = []
    unverified: list[str] = []
    not_connected: list[str] = []
    unreadable: list[str] = []

    for member in ctx.members:
        ws = member.workspace
        tier = _xw.env_tier(member)
        label = _xw.bold_member(member)
        if not ws.has(Resource.GIT):
            unreadable.append(tier)
            bullets.append(
                f"- {label} — the Git connection could not be read (it needs a "
                "higher workspace role), so its secret-scanning status is unknown."
            )
            continue
        if not ws.git_connected:
            not_connected.append(tier)
            bullets.append(
                f"- {label} is not connected to source control, so there is no "
                "repository to scan."
            )
            continue
        repo = _repo_phrase(ws)
        provider = _provider_name(ws)
        state = (ws.git_details.get("secret_scanning") or {}).get("enabled")
        if state is True:
            enabled.append(tier)
            bullets.append(f"- {label} has {provider} secret scanning enabled on {repo}.")
        elif state is False:
            disabled.append(tier)
            bullets.append(
                f"- {label} is connected to {repo}, but {provider} secret scanning "
                "is turned off, so committed credentials are not detected or blocked."
            )
        else:  # connected, but the provider security status could not be verified
            unverified.append(tier)
            bullets.append(
                f"- {label} is connected to {repo}, but whether {provider} secret "
                "scanning / push protection is turned on is not readable — Fabric's "
                "Git API doesn't expose it, and the audit has no repo-security token "
                "to check."
            )

    detail = "\n".join(bullets)

    # Nothing at all could be read: every environment's Git connection was
    # blocked, so there is no evidence either way. Scoring that would turn a
    # permission gap into a Security finding — "we could not determine this" is
    # not the same as "this is not configured".
    if len(unreadable) == total:
        return not_applicable(
            f"the Git connection could not be read in any of the {total} "
            f"environments, so secret-scanning status is unknown across the "
            f"group — this is a permission gap, not a finding:\n{detail}"
        )

    # PASS only when secret scanning is confirmed on in every environment.
    if enabled and not (disabled or unverified or not_connected or unreadable):
        return covered(
            total, total,
            f"Secret scanning is confirmed enabled in all {total} environments:\n{detail}",
        )
    if enabled:
        return covered(
            len(enabled), total,
            f"Secret scanning is confirmed enabled in {len(enabled)} of {total} "
            f"environments; it could not be confirmed in the rest:\n{detail}",
        )
    # Nothing confirmed. A connected-yet-unverifiable or unreadable repo is not a
    # definite failure, so that is PARTIAL; a repo that cannot be scanned because
    # scanning is off or it is not connected at all is a real gap (FAIL).
    if unverified or unreadable:
        return graded(
            1,
            f"Secret scanning could not be confirmed in any of the {total} "
            f"environments:\n{detail}",
        )
    return covered(
        0, total,
        f"No environment has secret scanning enabled ({total} environments):\n{detail}",
    )
