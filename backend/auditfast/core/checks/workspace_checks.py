"""Workspace-level best-practice checks (Section 6.1 of the design).

These are the "Common" checks that always run for every registered workspace.
All operate on the normalized workspace-context dict produced by the client.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import COST, FOUNDATION, OPEX, SECURITY, Severity
from .base import (binary_result, coverage_result, info_result, scored_result,
                   workspace_check)

# Which Fabric item types each layer role is expected to contain.
EXPECTED_TYPES = {
    "Data Prep": {"DataPipeline", "Notebook", "Dataflow"},
    "Data Storage": {"Lakehouse", "Warehouse", "SQLDatabase"},
    "Data Logs": {"Eventhouse", "KQLDatabase", "KQLDashboard"},
    "Data Operations": {"DataPipeline", "Reflex"},
    "Reporting / Semantic": {"SemanticModel", "Report", "Dashboard", "PaginatedReport"},
}


@workspace_check
def ws_naming(ws, s):
    pattern = s.get("naming_convention")
    name = ws.get("displayName", "")
    ok = bool(pattern) and re.match(pattern, name) is not None
    return binary_result(
        check_id="WS-NAME", ref="1.1.7", title="Workspace naming convention",
        pillar=OPEX, ok=ok, workspace=name, ws_role=ws.get("role", ""),
        evidence=(f"'{name}' matches convention" if ok
                  else f"'{name}' does not match convention {pattern!r}"),
        fail_severity=Severity.LOW,
    )


@workspace_check
def ws_roles_groups(ws, s):
    ra = ws.get("roleAssignments", [])
    individuals = [a for a in ra if a.get("principalType") == "User"]
    coverage = 1.0 - (len(individuals) / len(ra)) if ra else 1.0
    return coverage_result(
        check_id="WS-ROLES-GROUPS", ref="6.1.2",
        title="Roles assigned to security groups (not individuals)", pillar=SECURITY,
        coverage=coverage, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"{len(individuals)} of {len(ra)} role assignments are individual users",
        fail_severity=Severity.HIGH,
    )


@workspace_check
def ws_least_privilege(ws, s):
    ra = ws.get("roleAssignments", [])
    admins = [a for a in ra if a.get("role") == "Admin"]
    max_admins = int(s.get("max_admins", 2))
    n = len(admins)
    score = 3 if n <= max_admins else (1 if n <= max_admins + 2 else 0)
    return scored_result(
        check_id="WS-LEASTPRIV", ref="6.1.8",
        title="Least-privilege admin grants", pillar=SECURITY, score=score,
        workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"{n} Admin grant(s) (target <= {max_admins})",
        fail_severity=Severity.HIGH,
    )


@workspace_check
def ws_guests(ws, s):
    ra = ws.get("roleAssignments", [])
    guests = [a for a in ra
              if a.get("principalType") == "Guest" or "#EXT#" in (a.get("displayName") or "")]
    ok = len(guests) == 0
    names = ", ".join(a.get("displayName", "?") for a in guests) or "none"
    return binary_result(
        check_id="WS-GUESTS", ref="6.1", title="No unmanaged external/guest access",
        pillar=SECURITY, ok=ok, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"External/guest principals: {names}",
        fail_severity=Severity.HIGH,
    )


@workspace_check
def ws_sensitivity_labels(ws, s):
    items = ws.get("items", [])
    labeled = [i for i in items if i.get("sensitivityLabel")]
    coverage = (len(labeled) / len(items)) if items else 1.0
    return coverage_result(
        check_id="WS-LABELS", ref="6.2.4", title="Sensitivity labels applied to items",
        pillar=SECURITY, coverage=coverage, workspace=ws.get("displayName", ""),
        ws_role=ws.get("role", ""),
        evidence=f"{len(labeled)} of {len(items)} items carry a sensitivity label",
        fail_severity=Severity.MEDIUM,
    )


@workspace_check
def ws_git(ws, s):
    ok = bool(ws.get("gitConnected"))
    return binary_result(
        check_id="WS-GIT", ref="11.1.2", title="Git integration enabled", pillar=OPEX,
        ok=ok, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence="Workspace is connected to Git" if ok else "Workspace is not connected to Git",
        fail_severity=Severity.MEDIUM,
    )


@workspace_check
def ws_deployment_pipeline(ws, s):
    ok = bool(ws.get("deploymentPipeline"))
    return binary_result(
        check_id="WS-DEPLOY", ref="11.2", title="Deployment pipeline configured", pillar=OPEX,
        ok=ok, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence="Assigned to a deployment pipeline" if ok else "No deployment pipeline assigned",
        fail_severity=Severity.MEDIUM,
    )


@workspace_check
def ws_capacity(ws, s):
    ok = bool(ws.get("capacityId"))
    return binary_result(
        check_id="WS-CAPACITY", ref="12.1", title="Capacity assigned", pillar=COST,
        ok=ok, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"capacityId={ws.get('capacityId')}" if ok else "No capacity assigned",
        fail_severity=Severity.HIGH,
    )


@workspace_check
def ws_orphans(ws, s):
    items = ws.get("items", [])
    orphan_days = int(s.get("orphan_days", 90))
    now = datetime.now(timezone.utc)

    def is_orphan(item) -> bool:
        lr = item.get("lastRunUtc")
        if not lr:
            return True
        try:
            d = datetime.fromisoformat(str(lr).replace("Z", "+00:00"))
        except ValueError:
            return True
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (now - d).days > orphan_days

    orphans = [i for i in items if is_orphan(i)]
    coverage = 1.0 - (len(orphans) / len(items)) if items else 1.0
    return coverage_result(
        check_id="WS-ORPHAN", ref="12.3.4", title="No orphaned / stale items", pillar=COST,
        coverage=coverage, workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=f"{len(orphans)} of {len(items)} items have no run/refresh in {orphan_days} days",
        fail_severity=Severity.LOW,
    )


@workspace_check
def ws_inventory(ws, s):
    items = ws.get("items", [])
    counts: dict[str, int] = {}
    for i in items:
        counts[i.get("type", "Unknown")] = counts.get(i.get("type", "Unknown"), 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "no items"
    return info_result(
        check_id="WS-INVENTORY", ref="1.1", title="Item inventory", pillar=FOUNDATION,
        workspace=ws.get("displayName", ""), ws_role=ws.get("role", ""),
        evidence=summary,
    )


def _ws_types(ws) -> set:
    types = {i.get("type") for i in ws.get("items", []) if i.get("type")}
    if ws.get("pipelines"):
        types.add("DataPipeline")
    return types


@workspace_check
def ws_layer_content(ws, s):
    """Role-specific: does this workspace contain the items its layer should?"""
    role = ws.get("role", "")
    name = ws.get("displayName", "")
    expected = EXPECTED_TYPES.get(role)
    if not expected:
        return info_result(
            check_id="WS-LAYER-CONTENT", ref="1.1.2", title="Layer content expectation",
            pillar=OPEX, workspace=name, ws_role=role,
            evidence=f"role '{role or 'untagged'}' has no layer-specific expectation")
    types = _ws_types(ws)
    present = types & expected
    return binary_result(
        check_id="WS-LAYER-CONTENT", ref="1.1.2",
        title=f"Contains expected '{role}' items", pillar=OPEX,
        ok=bool(present), workspace=name, ws_role=role,
        evidence=f"expected any of {sorted(expected)}; found {sorted(types) or ['none']}",
        fail_severity=Severity.MEDIUM)


@workspace_check
def ws_layer_separation(ws, s):
    """Role-specific: is this workspace free of other layers' item types?"""
    role = ws.get("role", "")
    name = ws.get("displayName", "")
    expected = EXPECTED_TYPES.get(role)
    if not expected:
        return info_result(
            check_id="WS-LAYER-SEP", ref="1.1.2", title="Layer separation",
            pillar=OPEX, workspace=name, ws_role=role,
            evidence=f"role '{role or 'untagged'}' has no separation rule")
    other = set().union(*[v for k, v in EXPECTED_TYPES.items() if k != role]) - expected
    types = _ws_types(ws)
    foreign = types & other
    return binary_result(
        check_id="WS-LAYER-SEP", ref="1.1.2",
        title=f"Only '{role}' concerns in this workspace", pillar=OPEX,
        ok=not foreign, workspace=name, ws_role=role,
        evidence=f"foreign item types found: {sorted(foreign) or ['none']}",
        fail_severity=Severity.MEDIUM)
