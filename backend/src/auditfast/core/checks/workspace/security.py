"""Workspace checks — Security.

Who can reach the workspace, and how the data inside it is classified.
"""
from __future__ import annotations

from ...enums import Pillar, Resource, Scope, Severity
from ...models import CheckContext
from ..helpers import Verdict, binary, covered, graded, not_applicable
from ..registry import check

_ROLES_UNREADABLE = "Workspace role assignments could not be read from Fabric"


@check(
    id="WS-ROLES-GROUPS", ref="6.1.2",
    title="Roles assigned to security groups, not individuals",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def roles_use_groups(ctx: CheckContext) -> Verdict:
    """Workspace roles are granted to Entra security groups rather than named users."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    assignments = ctx.workspace.role_assignments
    individuals = [a for a in assignments if a.is_individual]
    return covered(
        len(assignments) - len(individuals), len(assignments),
        f"{len(individuals)} of {len(assignments)} role assignments are individual users",
    )


@check(
    id="WS-LEASTPRIV", ref="6.1.8", title="Least-privilege admin grants",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def least_privilege(ctx: CheckContext) -> Verdict:
    """Admin is granted to no more principals than the project's target."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    admins = [a for a in ctx.workspace.role_assignments if a.role == "Admin"]
    target = int(ctx.setting("max_admins", 2))
    count = len(admins)
    # Graded rather than binary: a little over target is a finding, well over is a gap.
    score = 3 if count <= target else (1 if count <= target + 2 else 0)
    return graded(score, f"{count} Admin grant(s) (target <= {target})")


@check(
    id="WS-GUESTS", ref="6.1", title="No unmanaged external/guest access",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def no_guest_access(ctx: CheckContext) -> Verdict:
    """No guest or external (#EXT#) principal holds a role on the workspace."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    guests = [a for a in ctx.workspace.role_assignments if a.is_guest]
    names = ", ".join(a.display_name or "?" for a in guests) or "none"
    return binary(not guests, f"External/guest principals: {names}")


@check(
    id="WS-LABELS", ref="6.2.4", title="Sensitivity labels applied to items",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ITEMS],
)
def sensitivity_labels(ctx: CheckContext) -> Verdict:
    """Every item carries a sensitivity label, especially those holding PII."""
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    items = ctx.workspace.items
    labeled = [i for i in items if i.sensitivity_label]
    return covered(
        len(labeled), len(items),
        f"{len(labeled)} of {len(items)} items carry a sensitivity label",
    )
