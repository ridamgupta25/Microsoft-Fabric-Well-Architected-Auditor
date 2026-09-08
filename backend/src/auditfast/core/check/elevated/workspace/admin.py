"""Workspace-administration checks — ``AdminCategory.WORKSPACE``.

**No checks are registered here yet — Phase 1 ports them.** This is the slot for
the twelve verified admin checks (`Setup3-Admin`): workspace role assignments,
connection credentials, gateways, and OneLake data-access roles.

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
enough — provided they hold the role. A user who does not simply gets N/A.

To populate it, register with ``category=AdminCategory.WORKSPACE``::

    from ...registry import admin_check
    from ....enums import AdminCategory, Pillar, Resource, Scope, Severity
    from ..helpers import Verdict, covered, not_applicable

    @admin_check(
        id="ADM-PROD-WRITE", ref="11.3.2",
        title="Production workspaces have restricted access (no developer write)",
        pillar=Pillar.DEVOPS, category=AdminCategory.WORKSPACE,
        scope=Scope.WORKSPACE, severity=Severity.CRITICAL,
        requires=[Resource.ROLE_ASSIGNMENTS],
    )
    def production_write_access(ctx) -> Verdict:
        if not ctx.workspace.has(Resource.ROLE_ASSIGNMENTS):
            return not_applicable(
                "Workspace role assignments could not be read — this needs the "
                "Member role or higher"
            )
        ...

Three rules apply when porting a notebook check here:

* **N/A, never FAIL, on an unreadable role.** The notebooks already do this — a
  blocked workspace lands in ``CouldNotCheck`` and forces ``score=None``. Keep
  that: a Contributor running the tool must not make a correct estate look broken.
* **Dedup against the standard library.** ``WS-ROLES-GROUPS`` (13.2.2) already
  asks 6.1.2's question verbatim, and ``WS-SPN`` (1.3.5) overlaps 6.1.3. Port the
  distinct part, or drop the duplicate — two checks must not both claim the same
  evidence.
* **Cross-workspace points belong in a group check.** 6.1.3 scores a service
  principal's reach *across* workspaces, which is a ``@group_check`` shape, not a
  per-workspace one.
"""
from __future__ import annotations
