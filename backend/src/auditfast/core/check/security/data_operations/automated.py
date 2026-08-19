"""Security · Data Operations — identity, access, and pipeline secrets.

Who can reach the workspace (IAM), and whether any pipeline bakes in a credential.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._pipeline import PIPELINE_LAYERS
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
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
    id="WS-ROLES-GROUPS", ref="13.2.2",
    title="No individual user accounts for role assignments — security groups used",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
)
def roles_use_groups(ctx: CheckContext) -> Verdict:
    """Access is granted to Entra security groups rather than to named users.

    **Two sources, one question.** Workspace role assignments are the primary
    evidence, but Fabric requires *Member or higher* to read them - on a
    Viewer/Contributor sign-in they are simply unavailable. The SQL analytics
    endpoint answers the same question at database scope from
    ``sys.database_principals``, which needs no elevated role, so a workspace
    whose roles could not be read is still assessed rather than skipped.

    **What the fallback cannot determine.** Workspace-level roles (Admin /
    Member / Contributor / Viewer) are not visible from the database, so the
    fallback judges *who* holds access, never *what* they can do. The evidence
    names which source was used so the two are never confused.
    """
    if ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        assignments = ctx.workspace.role_assignments
        if assignments:
            individuals = [a for a in assignments if a.is_individual]
            return covered(
                len(assignments) - len(individuals), len(assignments),
                f"{len(individuals)} of {len(assignments)} role assignments are "
                f"individual users",
            )

    principals = ctx.workspace.sql_principals
    if not principals:
        return not_applicable(_ROLES_UNREADABLE)
    users = [p for p in principals if _is_individual_principal(p)]
    return covered(
        len(principals) - len(users), len(principals),
        f"Workspace role assignments could not be read, so this uses the SQL "
        f"analytics endpoint instead: {len(users)} of {len(principals)} database "
        f"principal(s) are individual users rather than groups or service "
        f"identities. Database scope only - workspace roles are not visible here",
    )


#: A database principal that names one person. Fabric surfaces an Entra group as
#: ``EXTERNAL_GROUP`` and a service principal as an application; anything else
#: carrying a user-shaped login is an individual grant.
_INDIVIDUAL_PRINCIPAL_TYPES: frozenset[str] = frozenset({
    "EXTERNAL_USER", "SQL_USER", "WINDOWS_USER",
})


def _is_individual_principal(principal: dict) -> bool:
    """True when a database principal is one person rather than a group."""
    kind = str(principal.get("type") or "").strip().upper()
    if kind in _INDIVIDUAL_PRINCIPAL_TYPES:
        return True
    # An email-shaped name is a person even where the type is ambiguous.
    return "@" in str(principal.get("name") or "")


@check(
    id="WS-LEASTPRIV", ref="13.2.4", title="Workspace roles follow least-privilege principle (Admin/Member/Contributor/Viewer used correctly)",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    id="WS-SPN", ref="1.3.5",
    title="Connections use secure, non-personal identities (SPN / Workspace Identity) rather than individual accounts",
    pillar=Pillar.ARCHITECTURE, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    id="WS-GUESTS", ref="13.2.1", title="Guest/external user access is explicitly governed",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS], required=True,
)
def no_guest_access(ctx: CheckContext) -> Verdict:
    """No guest or external (#EXT#) principal holds access to this workspace.

    **Two sources, one question.** Workspace role assignments are primary, but
    reading them needs *Member or higher*. When they are unavailable, database
    principals from the SQL analytics endpoint answer the same question without
    any elevated role: a guest carries the ``#EXT#`` marker in either place.

    **What the fallback cannot determine.** A guest granted a workspace role but
    no database access is invisible to it, so a clean result from the fallback is
    weaker evidence than a clean result from the role assignments. The evidence
    names which source was used.
    """
    if ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        assignments = ctx.workspace.role_assignments
        if assignments:
            guests = [a for a in assignments if a.is_guest]
            names = ", ".join(a.display_name or "?" for a in guests) or "none"
            return binary(not guests, f"External/guest principals: {names}")

    principals = ctx.workspace.sql_principals
    if not principals:
        return not_applicable(_ROLES_UNREADABLE)
    guests = [p for p in principals if _is_guest_principal(p)]
    names = ", ".join(str(p.get("name") or "?") for p in guests[:5]) or "none"
    return binary(
        not guests,
        f"Workspace role assignments could not be read, so this uses the SQL "
        f"analytics endpoint instead: external/guest database principal(s): {names}. "
        f"Database scope only - a guest holding a workspace role but no database "
        f"access would not appear here",
    )


def _is_guest_principal(principal: dict) -> bool:
    """True when a database principal is an external (guest) identity.

    ``#EXT#`` is the Entra marker for a guest in a B2B tenant, and survives into
    the database principal name.
    """
    name = str(principal.get("name") or "").upper()
    return "#EXT#" in name


@check(
    id="PL-SECRETS", ref="6.4.2", title="No secrets in notebook code, pipeline expressions, or Spark config",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.PIPELINE, severity=Severity.CRITICAL,
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


def _tls_version(value: object) -> tuple[int, int] | None:
    """Parse explicit TLS version values without treating encryption as TLS proof."""
    text = str(value or "").strip().upper().replace("TLS", "").replace("V", "")
    parts = text.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


@check(
    id="WS-TLS", ref="6.3.4", title="API / source connections use TLS 1.2+",
    pillar=Pillar.SECURITY_ACCESS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.OPERATIONS,), requires=[Resource.CONNECTIONS], required=True,
)
def connections_use_tls12(ctx: CheckContext) -> Verdict:
    """Every visible API or source connection explicitly requires TLS 1.2 or newer."""
    if not ctx.workspace.has(Resource.CONNECTIONS):
        return not_applicable("Fabric connection metadata could not be read")
    connections = ctx.workspace.connections
    if not connections:
        return not_applicable("No Fabric source connections were returned")
    unknown = [c for c in connections if _tls_version(c.get("minimum_tls_version")) is None]
    if unknown:
        return not_applicable(
            f"TLS minimum version is not exposed for {len(unknown)} of "
            f"{len(connections)} connection(s); encrypted does not prove TLS 1.2+"
        )
    noncompliant = [
        c for c in connections
        if _tls_version(c.get("minimum_tls_version")) < (1, 2)
    ]
    return binary(
        not noncompliant,
        f"{len(connections) - len(noncompliant)} of {len(connections)} connection(s) "
        "explicitly require TLS 1.2+"
        if not noncompliant else
        f"{len(noncompliant)} of {len(connections)} connection(s) explicitly allow TLS below 1.2",
    )
