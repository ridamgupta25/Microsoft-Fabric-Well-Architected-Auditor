"""Operations & Reliability · Data Operations — workspace ops hygiene.

Naming, source control, and promotion gating for the operational estate.
"""
from __future__ import annotations

import re

from auditfast.core.check.helpers import Verdict, binary, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext


@check(
    id="WS-NAME", ref="1.1.7", title="Workspace naming convention",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.LOW,
    requires=[Resource.WORKSPACE], required=False,
)
def naming_convention(ctx: CheckContext) -> Verdict:
    """The workspace name matches the org convention configured for the project."""
    pattern = ctx.setting("naming_convention")
    name = ctx.workspace.display_name
    ok = bool(pattern) and re.match(pattern, name) is not None
    return binary(
        ok,
        f"'{name}' matches convention" if ok
        else f"'{name}' does not match convention {pattern!r}",
    )


@check(
    id="WS-GIT", ref="11.1.2", title="Git integration enabled",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.GIT], required=True,
)
def git_connected(ctx: CheckContext) -> Verdict:
    """The workspace is connected to Git so its items are source-controlled."""
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable("Git connection state could not be read from Fabric")
    ok = ctx.workspace.git_connected
    return binary(ok, "Workspace is connected to Git" if ok
                  else "Workspace is not connected to Git")


@check(
    id="WS-DEPLOY", ref="11.2", title="Deployment pipeline configured",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE], required=True,
)
def deployment_pipeline(ctx: CheckContext) -> Verdict:
    """The workspace is assigned to a deployment pipeline gating promotion."""
    ok = ctx.workspace.deployment_pipeline
    return binary(ok, "Assigned to a deployment pipeline" if ok
                  else "No deployment pipeline assigned")
