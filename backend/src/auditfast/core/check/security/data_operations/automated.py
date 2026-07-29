"""Security · Data Operations — identity, access, and pipeline secrets.

Who can reach the workspace (IAM), and whether any pipeline bakes in a credential.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._pipeline import PIPELINE_LAYERS
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_ROLES_UNREADABLE = "Workspace role assignments could not be read from Fabric"

#: Principal types that represent a non-human automation identity.
_AUTOMATION_PRINCIPALS = frozenset({"ServicePrincipal", "ManagedIdentity", "Application"})

#: Literal secret patterns — a *value* being present, not merely a parameter name.
SECRET_PATTERNS = [
    re.compile(r"password\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\bpwd\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"AccountKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"SharedAccessKey\s*=\s*[^;\"'\s]+", re.IGNORECASE),
    re.compile(r"\"secret\"\s*:\s*\"[^\"]{6,}\"", re.IGNORECASE),
]


@check(
    id="WS-ROLES-GROUPS", ref="6.1.2",
    title="Roles assigned to security groups, not individuals",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
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
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
)
def least_privilege(ctx: CheckContext) -> Verdict:
    """Admin is granted to no more principals than the project's target."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    admins = [a for a in ctx.workspace.role_assignments if a.role == "Admin"]
    target = int(ctx.setting("max_admins", 2))
    count = len(admins)
    score = 3 if count <= target else (1 if count <= target + 2 else 0)
    return graded(score, f"{count} Admin grant(s) (target <= {target})")


@check(
    id="WS-SPN", ref="6.1.3",
    title="Automation uses a service principal or managed identity",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
)
def automation_identity(ctx: CheckContext) -> Verdict:
    """A non-human identity holds a role, so automation is not run as a person.

    Service principals / managed identities are the supported way to run
    pipelines and deployments; personal accounts break when someone leaves and
    cannot be least-privileged the same way.
    """
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    principals = {a.principal_type for a in ctx.workspace.role_assignments}
    has_spn = bool(principals & _AUTOMATION_PRINCIPALS)
    return binary(
        has_spn,
        "A service principal / managed identity is assigned" if has_spn
        else "No service principal or managed identity among role assignments",
    )


@check(
    id="WS-GUESTS", ref="6.1", title="No unmanaged external/guest access",
    pillar=Pillar.SECURITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
)
def no_guest_access(ctx: CheckContext) -> Verdict:
    """No guest or external (#EXT#) principal holds a role on the workspace."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return not_applicable(_ROLES_UNREADABLE)
    guests = [a for a in ctx.workspace.role_assignments if a.is_guest]
    names = ", ".join(a.display_name or "?" for a in guests) or "none"
    return binary(not guests, f"External/guest principals: {names}")


@check(
    id="PL-SECRETS", ref="6.4.2", title="No hardcoded secrets in pipeline",
    pillar=Pillar.SECURITY, scope=Scope.PIPELINE, severity=Severity.CRITICAL,
    layers=PIPELINE_LAYERS, requires=[Resource.PIPELINE_DEFINITIONS], required=True,
)
def no_hardcoded_secrets(ctx: CheckContext) -> Verdict:
    """No credential literal appears anywhere in the pipeline definition.

    Evidence deliberately reports only the number of matching patterns, never the
    matched text — an audit report must not become a second copy of the secret.
    """
    blob = json.dumps(ctx.obj)
    hits = [p.pattern for p in SECRET_PATTERNS if p.search(blob)]
    return binary(
        not hits,
        "No hardcoded secret patterns detected" if not hits
        else f"Potential hardcoded secret(s) detected ({len(hits)} pattern match)",
    )
