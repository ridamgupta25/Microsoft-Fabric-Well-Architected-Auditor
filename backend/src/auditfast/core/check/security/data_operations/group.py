"""Security - Data Operations — cross-workspace (group) check.

Compares the members of a project group (Dev -> UAT -> Prod) for source-control
security that should hold in every environment. Registers into the separate
``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than two
members can be read.
"""
from __future__ import annotations

from auditfast.core.check import _xw
from auditfast.core.check.helpers import Verdict
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext


@group_check(
    id="XW-SECRET-SCAN", ref="11.1.8",
    title="Secret-scanning / credential-detection enabled on the source repository",
    pillar=Pillar.SECURITY, severity=Severity.HIGH, requires=[Resource.GIT],
    required=False,
)
def secret_scanning_consistent(ctx: GroupContext) -> Verdict:
    """Every environment is Git-connected so its repo can be secret-scanned.

    Secret-scanning status inside the external Git provider (GitHub/ADO) is not
    readable from Fabric without provider tokens, so what is deterministically
    checked is the precondition: each environment's workspace is connected to a
    Git repository. An environment not under source control cannot be scanned at
    all. N/A when fewer than two members' Git connection could be read.
    """
    return _xw.consistency(
        ctx,
        readable=lambda ws: ws.has(Resource.GIT),
        implements=lambda ws: bool(ws.git_connected),
        practice="is connected to source control",
        data_name="Git connection state",
    )
