"""Workspace-administration checks — ``AdminCategory.WORKSPACE``.

Ported from the reviewed ``Setup3-Admin`` notebooks, which a Fabric administrator
runs by hand today. The scoring ladders here are the notebooks' ladders; where
this file deviates, the docstring says so and why.

What "elevated" means for this family, verified against the Fabric REST docs:

===========================================  ====================================
Data                                         Requirement
===========================================  ====================================
``/workspaces/{id}/roleAssignments``         **Member or higher** workspace role
                                             (Contributor and Viewer get 403)
``/connections``, ``/connections/{id}``      ``Connection.Read.All`` + a role on
                                             each connection
``/gateways``, ``/gateways/{id}/members``    ``Gateway.Read.All`` + a role on
                                             each gateway
``/items/{id}/dataAccessRoles``              ``OneLake.Read.All`` + workspace role
===========================================  ====================================

None of these is the tenant-admin API, so the signed-in user's own token is
enough — provided they hold the role. A user who does not gets N/A, never FAIL.

**Per workspace, not per run.** The notebooks aggregate across every workspace
they could read because each writes a single CSV row. The engine already runs a
check once per workspace and rolls the results up, so these are written
per-workspace: the notebook's "operations has access to 3 of 5 workspaces"
becomes three passes and two failures, which the roll-up turns back into the same
proportion — with the added benefit that the report names *which* two.

**Names come from project config.** Fabric cannot say which workspaces are
production or which group is the operations team, so those live in the project
YAML (``production_workspaces``, ``developer_groups``, ``operations_groups``,
``report_consumer_groups``). Unset means N/A: an unnamed group is not evidence of
a clean estate, and the notebooks refuse to score in exactly the same way.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from ....enums import AdminCategory, Pillar, Resource, Scope, Severity
from ....models import CheckContext, RoleAssignment
from ...helpers import Verdict, binary, graded, not_applicable
from ...registry import admin_check

#: Roles that can change what is in a workspace. Viewer is read-only.
_WRITE_ROLES = frozenset({"Admin", "Member", "Contributor"})

#: Roles that can open a workspace at all.
_READ_ROLES = frozenset({"Admin", "Member", "Contributor", "Viewer"})

#: Principal types Fabric uses for a non-human automation identity.
_AUTOMATION_TYPES = frozenset({"ServicePrincipal", "ServicePrincipalProfile", "ManagedIdentity"})

#: Shown whenever role assignments could not be read. Names the *cause*, because
#: "no data" and "no access granted" look identical in a report otherwise.
_ROLES_UNREADABLE = (
    "Workspace role assignments could not be read. Fabric requires the Member "
    "role or higher on the workspace for this, so a Viewer or Contributor "
    "sign-in cannot see them — this is unknown, not clean"
)


def _names(ctx: CheckContext, key: str) -> set[str]:
    """A project-config list of names, lowercased for comparison.

    Tolerates a comma-separated string as well as a list, because a YAML written
    by hand often carries one name without brackets.
    """
    raw = ctx.setting(key, []) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, Sequence):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _matching(assignments: Iterable[RoleAssignment], wanted: set[str]) -> list[RoleAssignment]:
    """Assignments whose principal name is one of ``wanted``."""
    return [a for a in assignments if (a.display_name or "").strip().lower() in wanted]


def _readable(ctx: CheckContext) -> list[RoleAssignment] | None:
    """This workspace's role assignments, or ``None`` when they could not be read."""
    if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
        return None
    return list(ctx.workspace.role_assignments)


# -- 6.1.2 --------------------------------------------------------------------

@admin_check(
    id="ADM-WS-GROUPS", ref="6.1.2",
    title="No individual user accounts for role assignments — security groups used",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def access_is_granted_to_groups(ctx: CheckContext) -> Verdict:
    """Workspace access is held by security groups, not by named people.

    A named individual is how stale access accumulates: when someone changes team
    nobody remembers to walk every workspace. A group is one place to change.

    Ladder (from the notebook): more than two named people is High, one or two is
    Medium, none is a pass. Service principals are expected and never counted —
    automation *should* hold a role.

    **What it cannot determine.** Whether the groups themselves are well
    governed. A single group containing the whole company scores 3 here.
    """
    assignments = _readable(ctx)
    if assignments is None:
        return not_applicable(_ROLES_UNREADABLE)
    if not assignments:
        return not_applicable("The workspace returned no role assignments to judge")

    people = [a for a in assignments if a.is_individual]
    groups = [a for a in assignments if a.principal_type == "Group"]
    if not people:
        return binary(
            True,
            f"All {len(assignments)} role assignment(s) are groups or service "
            f"identities ({len(groups)} group(s)); no named individuals",
        )
    named = ", ".join(a.display_name or a.principal_id or "?" for a in people[:5])
    if len(people) > 5:
        named += f", +{len(people) - 5} more"
    # 0 (High) above two people, 1 (Medium) for one or two — the notebook's bands.
    return graded(
        0 if len(people) > 2 else 1,
        f"{len(people)} of {len(assignments)} role assignment(s) are named "
        f"individuals rather than groups: {named}",
    )


# -- 6.1.3 --------------------------------------------------------------------

@admin_check(
    id="ADM-WS-AUTOMATION", ref="6.1.3",
    title="Automation runs as a service principal, scoped least-privilege",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def automation_identity_is_least_privileged(ctx: CheckContext) -> Verdict:
    """A service principal holds a role here, and it is not a workspace Admin.

    Two failures, one question. No service principal at all means the scheduled
    work is running as a person: it stops when they leave, and their name is on
    every action. A service principal holding **Admin** is the opposite problem —
    one leaked credential can then manage access, not just move data.

    Ladder (from the notebook): no SPN scores 0; an SPN holding Admin scores 1;
    an SPN below Admin scores 2. **It never scores 3**, because the remaining
    evidence — the app registration's Entra API permissions and whether it uses a
    certificate or an expiring secret — is not in the Fabric API. The notebook
    says the same: "confirm Entra permissions to reach 3".

    **What it cannot determine.** How far the principal reaches across *other*
    workspaces. That is a cross-workspace question and belongs in a group check.
    """
    assignments = _readable(ctx)
    if assignments is None:
        return not_applicable(_ROLES_UNREADABLE)

    automation = [a for a in assignments if a.principal_type in _AUTOMATION_TYPES]
    if not automation:
        return graded(
            0,
            "No service principal or managed identity holds a role here. If "
            "pipelines run on a schedule they are running as a person, whose "
            "access ends when they leave and whose name is on every action",
        )
    admins = [a for a in automation if a.role == "Admin"]
    if admins:
        named = ", ".join(a.display_name or a.principal_id or "?" for a in admins)
        return graded(
            1,
            f"{len(admins)} of {len(automation)} automation identity(ies) hold "
            f"workspace Admin: {named}. Admin can change who has access, which "
            f"moving data does not need",
        )
    return graded(
        2,
        f"{len(automation)} automation identity(ies) hold a role, none as Admin. "
        f"Full marks also need the Entra side — API permissions and whether the "
        f"app uses a certificate rather than an expiring secret — which the "
        f"Fabric API does not expose",
    )


# -- 10.4.3 -------------------------------------------------------------------

@admin_check(
    id="ADM-WS-OPS-ACCESS", ref="10.4.3",
    title="Operations team can open the workspace (not just developers)",
    pillar=Pillar.MONITORING, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def operations_team_has_access(ctx: CheckContext) -> Verdict:
    """A named operations group holds a role on this workspace.

    A monitoring dashboard nobody on the operations rota can open is a dashboard
    for the people who built it. This asks the readable half of that question:
    does the operations group have access to the workspace at all?

    Ladder (from the notebook): the notebook scores the *share* of workspaces the
    operations group can reach — all of them 3, half or more 2, fewer 1, none 0.
    Per workspace that share is all-or-nothing, so this scores 3 or 0 and the
    engine's roll-up reproduces the notebook's proportion across the run, while
    naming which workspaces are missing access.

    **Needs configuration.** ``operations_groups`` names the group; Fabric cannot
    infer it. Unset ⇒ N/A, exactly as the notebook refuses to score without
    ``OPS_GROUPS``.

    **What it cannot determine.** Whether the dashboard is *useful* — only that
    the team can reach the workspace holding it.
    """
    wanted = _names(ctx, "operations_groups")
    if not wanted:
        return not_applicable(
            "No operations group is named in the project's 'operations_groups' "
            "setting, so nothing identifies which principal is the operations "
            "team. An unnamed group is not evidence of a gap"
        )
    assignments = _readable(ctx)
    if assignments is None:
        return not_applicable(_ROLES_UNREADABLE)

    hits = [a for a in _matching(assignments, wanted) if a.role in _READ_ROLES]
    if hits:
        named = ", ".join(f"{a.display_name} ({a.role})" for a in hits)
        return graded(3, f"Operations access present: {named}")
    return graded(
        0,
        "None of the named operations group(s) holds a role on this workspace: "
        + ", ".join(sorted(wanted))
        + ". Check the names match Manage access before treating this as a gap",
    )


# -- 11.3.2 -------------------------------------------------------------------

@admin_check(
    id="ADM-WS-PROD-WRITE", ref="11.3.2",
    title="Production workspaces have restricted access (no developer write)",
    pillar=Pillar.DEVOPS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def developers_cannot_write_to_production(ctx: CheckContext) -> Verdict:
    """No developer group can write directly to a production workspace.

    Direct developer write to production is how an untested change reaches live
    data without passing the deployment pipeline. Viewer is enough to
    investigate; anything above it is a way round the release process.

    Ladder (from the notebook): a developer group holding Admin or Member scores
    0; more than one holding Contributor scores 1; one holding Contributor scores
    2; none scores 3.

    **Needs configuration.** ``production_workspaces`` and ``developer_groups``.
    A workspace not named as production is N/A — this check has no opinion about
    dev and test.
    """
    prod = _names(ctx, "production_workspaces")
    if not prod:
        return not_applicable(
            "No workspace is named in the project's 'production_workspaces' "
            "setting, so nothing marks this one as production"
        )
    this = (ctx.workspace.name or "").strip().lower()
    if this not in prod and (ctx.workspace.id or "").strip().lower() not in prod:
        return not_applicable(
            f"'{ctx.workspace.name}' is not named as a production workspace, so "
            f"developer write access here is not this check's concern"
        )

    developers = _names(ctx, "developer_groups")
    if not developers:
        return not_applicable(
            "No developer group is named in the project's 'developer_groups' "
            "setting, so nothing identifies which principals are developers"
        )
    assignments = _readable(ctx)
    if assignments is None:
        return not_applicable(_ROLES_UNREADABLE)

    writers = [a for a in _matching(assignments, developers) if a.role in _WRITE_ROLES]
    if not writers:
        return graded(3, "No named developer group can write to this production workspace")
    elevated = [a for a in writers if a.role in {"Admin", "Member"}]
    named = ", ".join(f"{a.display_name} ({a.role})" for a in writers)
    if elevated:
        return graded(
            0,
            f"{len(elevated)} developer assignment(s) hold Admin or Member on "
            f"production: {named}",
        )
    return graded(
        1 if len(writers) > 1 else 2,
        f"{len(writers)} developer assignment(s) hold Contributor on production: {named}",
    )


# -- 14.4.4 -------------------------------------------------------------------

@admin_check(
    id="ADM-WS-CONSUMER-ACCESS", ref="14.4.4",
    title="Report consumers hold no more than Viewer",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.ROLE_ASSIGNMENTS],
)
def report_consumers_are_read_only(ctx: CheckContext) -> Verdict:
    """A group that only reads reports holds Viewer and nothing more.

    Reading a report needs Viewer. Anything above it hands the whole reader
    population the ability to change what everyone else sees.

    Ladder (from the notebook): a consumer group holding Admin or Member scores
    0; more than one holding a write role scores 1; one scores 2. A clean result
    scores 3 only when ``app_access_reviewed`` confirms a human checked the app
    audiences — reports are often shared through an app whose audience the Fabric
    API does not expose, so workspace roles alone are half the answer.

    **Needs configuration.** ``report_consumer_groups``. Unset ⇒ N/A.
    """
    wanted = _names(ctx, "report_consumer_groups")
    if not wanted:
        return not_applicable(
            "No consumer group is named in the project's "
            "'report_consumer_groups' setting, so nothing identifies which "
            "principals are report readers"
        )
    assignments = _readable(ctx)
    if assignments is None:
        return not_applicable(_ROLES_UNREADABLE)

    consumers = _matching(assignments, wanted)
    if not consumers:
        return not_applicable(
            "None of the named consumer group(s) holds a role on this workspace: "
            + ", ".join(sorted(wanted))
            + ". Either a name is wrong, or the readers reach reports through an "
            "app and hold no workspace role at all — neither is a finding"
        )

    over = [a for a in consumers if a.role in _WRITE_ROLES]
    if not over:
        if not ctx.setting("app_access_reviewed", False):
            return graded(
                2,
                f"All {len(consumers)} consumer assignment(s) are Viewer. Full "
                f"marks also need the app audiences reviewed by hand — the "
                f"Fabric API does not expose them — recorded with the project's "
                f"'app_access_reviewed' setting",
            )
        return graded(
            3,
            f"All {len(consumers)} consumer assignment(s) are Viewer, and app "
            f"audiences were reviewed separately",
        )
    named = ", ".join(f"{a.display_name} ({a.role})" for a in over)
    if any(a.role in {"Admin", "Member"} for a in over):
        return graded(0, f"A report consumer group holds Admin or Member: {named}")
    return graded(
        1 if len(over) > 1 else 2,
        f"{len(over)} consumer assignment(s) hold more than Viewer: {named}",
    )


# -- connection credentials ----------------------------------------------------
#
# All five connection checks read ``Resource.CONNECTIONS``, which the provider
# already captures in full — ``credential_type``, ``connection_encryption`` and
# ``gateway_id`` all come from the List Connections response, so none of these
# needs a per-connection round trip.
#
# Fabric's credential types split cleanly into two families. An *identity* is
# rotated by the platform and dies with the principal; a *static secret* is
# typed in once and lives until somebody remembers it.

#: Credential types that rest on a static shared secret — a string somebody
#: typed, which may also exist outside Fabric and which nothing expires.
#: ``ServicePrincipal`` belongs here despite sounding managed: it authenticates
#: with a client secret that has to be rotated by hand. Taken verbatim from the
#: reviewed notebook's ``STATIC`` set.
_STATIC_SECRET = frozenset({
    "ServicePrincipal", "Key", "Basic", "SharedAccessSignature", "KeyPair",
})

#: Credential types backed by an identity the platform or the OS rotates.
#: The notebook's ``STRONG`` set.
_STRONG_CREDENTIAL = frozenset({
    "WorkspaceIdentity", "OAuth2", "Windows", "WindowsWithoutImpersonation",
})

#: Bearer strings that hand over access to whoever holds them.
_BEARER_CREDENTIAL = frozenset({"Key", "SharedAccessSignature"})

#: Connectivity modes tied to one person's machine or account — they cannot be
#: shared, handed over, or made redundant.
_PERSONAL_CONNECTIVITY = frozenset({"PersonalCloud", "OnPremisesGatewayPersonal"})

_CONNECTIONS_UNREADABLE = (
    "Fabric connections could not be read. This needs the Connection.Read.All "
    "scope plus a role on each connection, so an ordinary sign-in sees none — "
    "unknown, not clean"
)


def _connections(ctx: CheckContext) -> list[dict] | None:
    """Every readable connection, or ``None`` when the list could not be read."""
    if not ctx.workspace.has(Resource.CONNECTIONS):
        return None
    return list(ctx.workspace.connections)


def _describe(rows: Iterable[dict], limit: int = 5) -> str:
    """Name a few connections without ever quoting a credential."""
    names = [str(r.get("display_name") or r.get("id") or "?") for r in rows]
    shown = ", ".join(names[:limit])
    return shown + (f", +{len(names) - limit} more" if len(names) > limit else "")


def _judgeable(connections: list[dict], field: str) -> tuple[list[dict], int]:
    """Split connections into those that report ``field`` and a count of those that do not.

    Fabric leaves ``credentialType`` and ``connectionEncryption`` blank on a
    connection the caller cannot see the details of. Scoring those as compliant
    would quietly inflate every ratio — a workspace where two thirds of the
    connections are unreadable would look two-thirds clean. They are excluded
    from the judgement and reported instead, so the reader knows what the score
    actually covers.
    """
    known = [c for c in connections if c.get(field)]
    return known, len(connections) - len(known)


def _unknown_note(unknown: int, total: int) -> str:
    """Evidence fragment naming what the score does not cover."""
    if not unknown:
        return ""
    return (
        f". {unknown} of {total} connection(s) report nothing to judge and are "
        f"excluded from this score"
    )


@admin_check(
    id="ADM-CONN-CREDENTIALS", ref="1.3.7",
    title="Connection credentials use secure storage, not a static secret",
    pillar=Pillar.ARCHITECTURE, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.CONNECTIONS],
)
def connection_credentials_are_managed(ctx: CheckContext) -> Verdict:
    """Connections authenticate with an identity rather than a stored secret.

    Fabric encrypts both at rest, so this is not about storage — it is about
    whether anyone still has to remember to change them. A ``ServicePrincipal``
    counts as a *static* secret here despite the name: it authenticates with a
    client secret that expires and must be rotated by hand.

    Ladder (from the notebook): every connection strong **and** the code scan
    clean scores 3; every connection strong without that confirmation scores 2;
    at least half strong scores 1; below half scores 0.

    **This is half the checklist point.** A secret pasted into notebook or
    pipeline code never becomes a connection and is invisible here — the code
    scan (``NB-SECRETS`` 3.1.3, ``PL-SECRETS`` 6.4.2) covers that half, and its
    result is recorded with the project's ``code_scan_clean`` setting.
    """
    connections = _connections(ctx)
    if connections is None:
        return not_applicable(_CONNECTIONS_UNREADABLE)
    if not connections:
        return not_applicable("No Fabric connections were returned for this tenant")

    known, unknown = _judgeable(connections, "credential_type")
    if not known:
        return not_applicable(
            f"None of the {len(connections)} connection(s) reports its credential "
            f"type, so this cannot be judged from the API"
        )
    static = [c for c in known if c.get("credential_type") in _STATIC_SECRET]
    strong = len(known) - len(static)
    share = strong / len(known)
    tail = _unknown_note(unknown, len(connections))

    if static and share < 0.5:
        return graded(
            0,
            f"{len(static)} of {len(known)} readable connection(s) rest on a static "
            f"secret ({_describe(static)}){tail}",
        )
    if static:
        return graded(
            1,
            f"only {strong} of {len(known)} readable connection(s) are free of a "
            f"static secret; {_describe(static)} still hold one{tail}",
        )
    if not ctx.setting("code_scan_clean", False):
        return graded(
            2,
            f"All {len(known)} readable connection(s) authenticate with a managed "
            f"identity, but the code scan for secrets pasted into notebooks and "
            f"pipelines was not confirmed — record it with the project's "
            f"'code_scan_clean' setting{tail}",
        )
    return graded(
        3,
        f"All {len(known)} readable connection(s) authenticate with a managed "
        f"identity, and the code scan is clean{tail}",
    )


@admin_check(
    id="ADM-CONN-WORKSPACE-IDENTITY", ref="6.1.5",
    title="Workspace Identity used for Fabric data connections where supported",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.CONNECTIONS],
)
def connections_use_workspace_identity(ctx: CheckContext) -> Verdict:
    """Connections authenticate as the workspace itself rather than as a person.

    A user-delegated connection breaks when that person leaves or changes
    password, and every action it takes is logged as theirs. The Workspace
    Identity belongs to the workspace, so it survives staff changes and is
    rotated by Fabric.

    Ladder (from the notebook): three quarters or more on Workspace Identity
    scores 3, half scores 2, a quarter scores 1, below that scores 0. The bands
    are deliberately lenient because not every connector supports it.

    **What it cannot determine.** *Which* connectors support Workspace Identity.
    The notebook narrowed the denominator to eligible connections; Fabric does
    not report eligibility, so this scores over every readable connection and the
    evidence says so. A low score is a prompt to check which could be moved, not
    proof that all of them could.
    """
    connections = _connections(ctx)
    if connections is None:
        return not_applicable(_CONNECTIONS_UNREADABLE)
    if not connections:
        return not_applicable("No Fabric connections were returned for this tenant")

    known, unknown = _judgeable(connections, "credential_type")
    if not known:
        return not_applicable(
            f"None of the {len(connections)} connection(s) reports its credential "
            f"type, so this cannot be judged from the API"
        )
    using = [c for c in known if c.get("credential_type") == "WorkspaceIdentity"]
    share = len(using) / len(known)
    score = 3 if share >= 0.75 else 2 if share >= 0.5 else 1 if share >= 0.25 else 0
    others = [c for c in known if c not in using]
    return graded(
        score,
        f"{len(using)} of {len(known)} readable connection(s) use the Workspace "
        f"Identity"
        + (
            f"; the rest authenticate as a person or a stored secret: "
            f"{_describe(others)}. Not every connector supports Workspace "
            f"Identity, and Fabric does not report which do"
            if others else ""
        )
        + _unknown_note(unknown, len(connections)),
    )


@admin_check(
    id="ADM-CONN-ENCRYPTED", ref="6.3.1",
    title="Connections to source systems use encrypted channels",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.CONNECTIONS],
)
def connections_are_encrypted(ctx: CheckContext) -> Verdict:
    """Every connection requires an encrypted channel to its source.

    Fabric reports ``Encrypted``, ``NotEncrypted`` or ``Any`` per connection, and
    the notebook's ladder treats those very differently: a single
    ``NotEncrypted`` connection sends data in the clear and scores **0**
    outright, however good the rest are. ``Any`` is weaker but not open — it
    accepts whatever the server offers, so it is graded by how much of the estate
    could fall back.

    Ladder: any ``NotEncrypted`` → 0; more than a quarter on ``Any`` → 1; any
    ``Any`` → 2; all encrypted but some never connection-tested → 2; otherwise 3.
    """
    connections = _connections(ctx)
    if connections is None:
        return not_applicable(_CONNECTIONS_UNREADABLE)
    if not connections:
        return not_applicable("No Fabric connections were returned for this tenant")

    declared, unknown = _judgeable(connections, "connection_encryption")
    if not declared:
        return not_applicable(
            f"None of the {len(connections)} connection(s) reports an encryption "
            f"setting, so this cannot be judged from the API"
        )
    plain = [c for c in declared if c.get("connection_encryption") == "NotEncrypted"]
    fallback = [c for c in declared if c.get("connection_encryption") == "Any"]
    untested = [c for c in declared if c.get("skip_test_connection")]
    tail = _unknown_note(unknown, len(connections))

    if plain:
        return graded(
            0,
            f"{len(plain)} of {len(declared)} readable connection(s) send data "
            f"unencrypted: {_describe(plain)}{tail}",
        )
    if fallback:
        share = len(fallback) / len(declared)
        return graded(
            1 if share > 0.25 else 2,
            f"{len(fallback)} of {len(declared)} readable connection(s) can fall "
            f"back to plaintext ('Any' accepts whatever the source offers): "
            f"{_describe(fallback)}{tail}",
        )
    if untested:
        return graded(
            2,
            f"Encryption is required on all {len(declared)} readable "
            f"connection(s), but {len(untested)} skip the connection test, so it "
            f"was never proven against the source{tail}",
        )
    return graded(
        3,
        f"All {len(declared)} readable connection(s) require encryption, and each "
        f"was connection-tested{tail}",
    )


@admin_check(
    id="ADM-CONN-IDENTITY-OVER-KEYS", ref="6.4.5",
    title="Managed Identity preferred over SAS tokens or account keys",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.CONNECTIONS],
)
def identity_preferred_over_keys(ctx: CheckContext) -> Verdict:
    """No connection authenticates with a shared key or SAS token.

    An account key is a permanent, unscoped credential: whoever holds it has
    full access to the whole storage account, it grants no per-user audit trail,
    and nothing expires it. A SAS token is scoped but still a bearer string that
    outlives the person who created it.

    Ladder (from the notebook): none scores 3; up to a quarter scores 2; up to a
    half scores 1; a majority scores 0. Graded rather than pass/fail, because one
    legacy key in a large estate is a different problem from a tenant built on
    them.

    Narrower than 1.3.7 on purpose: that one counts every static secret, this one
    counts only the two that hand over an entire storage account.
    """
    connections = _connections(ctx)
    if connections is None:
        return not_applicable(_CONNECTIONS_UNREADABLE)
    if not connections:
        return not_applicable("No Fabric connections were returned for this tenant")

    known, unknown = _judgeable(connections, "credential_type")
    if not known:
        return not_applicable(
            f"None of the {len(connections)} connection(s) reports its credential "
            f"type, so this cannot be judged from the API"
        )
    bearer = [c for c in known if c.get("credential_type") in _BEARER_CREDENTIAL]
    tail = _unknown_note(unknown, len(connections))
    if not bearer:
        return graded(
            3,
            f"No readable connection uses a shared key or SAS token "
            f"({len(known)} checked){tail}",
        )
    share = len(bearer) / len(known)
    score = 2 if share <= 0.25 else 1 if share <= 0.5 else 0
    return graded(
        score,
        f"{len(bearer)} of {len(known)} readable connection(s) use a key or "
        f"shared access signature: {_describe(bearer)}{tail}",
    )


@admin_check(
    id="ADM-GATEWAY-CREDENTIALS", ref="6.4.4",
    title="Gateway / source credentials are shared, not personal accounts",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.CONNECTIONS],
)
def gateway_credentials_are_managed(ctx: CheckContext) -> Verdict:
    """No connection runs in personal mode, and none rests on a typed password.

    A **personal-mode** connection (``PersonalCloud`` or a personal on-premises
    gateway) is tied to one person's account or machine. It cannot be shared,
    cannot be handed over when they leave, and cannot be made redundant — which
    is why the notebook treats it as the severe case.

    A username-and-password credential is the milder one: it may well be a
    proper service account, but the API cannot tell that from a named person's
    login, so it caps the score at 2 pending confirmation.

    Ladder (from the notebook): more than one personal-mode connection scores 0;
    exactly one scores 1; any Basic credential scores 2; otherwise 3.
    """
    connections = _connections(ctx)
    if connections is None:
        return not_applicable(_CONNECTIONS_UNREADABLE)
    if not connections:
        return not_applicable("No Fabric connections were returned for this tenant")

    personal = [
        c for c in connections
        if c.get("connectivity_type") in _PERSONAL_CONNECTIVITY
    ]
    basic = [c for c in connections if c.get("credential_type") == "Basic"]

    if len(personal) > 1:
        return graded(
            0,
            f"{len(personal)} connection(s) run in personal mode, tied to one "
            f"person's account or machine: {_describe(personal)}",
        )
    if personal:
        return graded(
            1,
            f"One connection runs in personal mode and cannot be shared or handed "
            f"over: {_describe(personal)}",
        )
    if basic:
        return graded(
            2,
            f"No personal-mode connection, but {len(basic)} use a username and "
            f"password ({_describe(basic)}). Confirm each is a service account "
            f"rather than a named person's login to reach full marks",
        )
    return graded(
        3,
        f"No personal-mode connection and no username-and-password credential "
        f"across {len(connections)} connection(s)",
    )


# -- gateway estate ------------------------------------------------------------

@admin_check(
    id="ADM-GATEWAY-HA", ref="1.3.8",
    title="On-premises gateways are clustered, not a single point of failure",
    pillar=Pillar.ARCHITECTURE, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.GATEWAYS],
)
def gateways_are_highly_available(ctx: CheckContext) -> Verdict:
    """Every standard gateway has a second member machine behind it.

    A one-machine gateway is a single point of failure for every source behind
    it: patch that box and the estate's ingestion stops. Clustering is a member
    added in the gateway settings, not a rebuild.

    Ladder (from the notebook): personal gateways only → 0, they cannot be
    clustered at all. Personal gateways alongside clustered standard ones → 1.
    Otherwise: none clustered → 0; some → 1; all clustered → 3 when the machine
    sizing is confirmed, 2 when it is not — the API reports membership and
    version, never capacity.

    **What it cannot determine.** Whether the machines are big enough, patched,
    or in different failure domains.
    """
    if not ctx.workspace.has(Resource.GATEWAYS):
        return not_applicable(
            "Data gateways could not be read. This needs the Gateway.Read.All "
            "scope plus a role on each gateway — unknown, not clean"
        )
    gateways = list(ctx.workspace.gateways)
    if not gateways:
        return not_applicable(
            "No data gateway is visible to this account. Either the estate uses "
            "none, or this account administers none — confirm before reading "
            "this as cloud-only"
        )

    personal = [g for g in gateways if "Personal" in str(g.get("type") or "")]
    standard = [g for g in gateways if g not in personal]
    if not standard:
        return graded(
            0,
            f"{len(personal)} personal gateway(s) carry estate traffic and cannot "
            f"be made redundant: {_describe(personal)}",
        )
    clustered = [
        g for g in standard
        if (g.get("number_of_member_gateways") or len(g.get("members") or []) or 0) > 1
    ]
    if personal:
        return graded(
            1,
            f"{len(clustered)} of {len(standard)} standard gateway(s) are "
            f"clustered, but {len(personal)} personal gateway(s) also carry "
            f"estate traffic and cannot be made redundant",
        )
    if not clustered:
        return graded(
            0,
            f"None of the {len(standard)} gateway(s) has a second member machine, "
            f"so each is a single point of failure for every source behind it",
        )
    if len(clustered) < len(standard):
        return graded(
            1,
            f"{len(clustered)} of {len(standard)} gateway(s) are clustered; the "
            f"rest run on one machine",
        )
    if not ctx.setting("gateway_sizing_confirmed", False):
        return graded(
            2,
            f"All {len(standard)} gateway(s) are clustered, but the machine "
            f"sizing was not confirmed — the API reports membership and version, "
            f"never capacity",
        )
    return graded(
        3,
        f"All {len(standard)} gateway(s) are clustered and the machine sizing was "
        f"confirmed by the reviewer",
    )


# -- OneLake access ------------------------------------------------------------

@admin_check(
    id="ADM-ONELAKE-ACCESS", ref="6.2.6",
    title="OneLake data access is controlled by workspace or data access roles",
    pillar=Pillar.SECURITY_ACCESS, category=AdminCategory.WORKSPACE,
    scope=Scope.WORKSPACE, severity=Severity.HIGH,
    requires=[Resource.DATA_ACCESS_ROLES],
)
def onelake_access_is_governed(ctx: CheckContext) -> Verdict:
    """Each Lakehouse has a scoped data access role, held by groups not people.

    Without a data access role, everyone with a workspace role sees every folder
    and every table in the Lakehouse. A role narrows that to the folders an
    audience actually needs.

    Ladder (from the notebook): no Lakehouse scoped scores 0; some scoped scores
    1; all scoped but with individuals named directly in a role scores 2; all
    scoped with group-based membership scores 3.

    Fabric's own ``Default*`` roles do not count — they are created automatically
    and are not evidence that anyone scoped access.

    **What it cannot determine.** Whether the roles are *correct* — only that
    someone defined access at the data layer rather than leaving it to the
    workspace role alone. A role granting everything to everyone counts as scoped.
    """
    if not ctx.workspace.has(Resource.DATA_ACCESS_ROLES):
        return not_applicable(
            "OneLake data access roles could not be read. This needs the "
            "OneLake.Read.All scope plus a workspace role — unknown, not clean"
        )
    by_lakehouse = dict(ctx.workspace.data_access_roles)
    if not by_lakehouse:
        return not_applicable("No Lakehouse in this workspace, so there is no OneLake access to govern")

    # Only roles somebody defined count; Fabric's Default* roles are automatic.
    defined = {
        name: [role for role in roles if not role.get("built_in")]
        for name, roles in by_lakehouse.items()
    }
    scoped = [name for name, roles in defined.items() if roles]
    ungoverned = sorted(name for name, roles in defined.items() if not roles)
    total = len(defined)

    if not scoped:
        return graded(
            0,
            f"None of the {total} lakehouse(s) has a data access role, so workspace "
            f"access alone decides who reads the data: " + ", ".join(ungoverned[:5]),
        )
    if len(scoped) < total:
        return graded(
            1,
            f"{len(scoped)} of {total} lakehouse(s) have a scoped role; the rest "
            f"rely on workspace access alone: " + ", ".join(ungoverned[:5]),
        )
    individuals = sum(
        int(role.get("individuals") or 0)
        for roles in defined.values() for role in roles
    )
    if individuals:
        return graded(
            2,
            f"Every lakehouse has a scoped role, but {individuals} individual(s) are "
            f"named directly in a role rather than through a security group",
        )
    return graded(
        3,
        f"All {total} lakehouse(s) have scoped roles with group-based membership",
    )
