"""MCP server — the same services exposed as agent-callable tools.

This is a third adapter alongside the REST API and the CLI. It contains no
auditing logic; every tool is a thin wrapper over :mod:`auditfast.services`, so
what an agent sees and what the API returns cannot drift.

Two uses:

* **mcp-inspector** — browse and invoke each tool individually while developing.
* **Agents** — let an assistant run audits and read findings directly.

Requires the optional extra::

    pip install -e "backend[mcp]"
    mcp dev auditfast.mcp.server

Design notes:

* Catalog tools need no tenant and no sign-in, so they are safe to call freely.
* ``run_audit`` is synchronous here (unlike the REST API's fire-and-poll)
  because MCP calls are already request/response with a client-side timeout, and
  an agent has nowhere useful to put a job id.
* No tool ever returns a Fabric access token.
"""
from __future__ import annotations

from typing import Any

from ..config.settings import get_settings
from ..services import audit_service, catalog_service

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "The MCP adapter needs the optional 'mcp' extra. "
        'Install it with: pip install -e "backend[mcp]"'
    ) from exc

mcp = FastMCP("fabric-well-architected-auditor")


def _project(project: str | None) -> str:
    settings = get_settings()
    return str(settings.resolve(project) if project else settings.project_path)


# -- catalog: no tenant, no sign-in, instant -----------------------------------

@mcp.tool()
def list_pillars() -> list[dict]:
    """List the Well-Architected pillars and how many checks each has."""
    return catalog_service.list_pillars()


@mcp.tool()
def list_layers() -> list[dict]:
    """List the architecture layers a workspace can be tagged with."""
    return catalog_service.list_layers()


@mcp.tool()
def list_checks(
    pillar: str | None = None,
    layer: str | None = None,
    scope: str | None = None,
) -> list[dict]:
    """List the rule library, optionally filtered by pillar, layer, or object kind."""
    return catalog_service.list_checks(pillar=pillar, layer=layer, scope=scope)


@mcp.tool()
def describe_check(check_id: str) -> dict:
    """Full metadata for one check: what it inspects, its pillar, and its severity."""
    spec = catalog_service.describe_check(check_id)
    return spec or {"error": f"No check registered with id {check_id!r}."}


@mcp.tool()
def catalog_summary() -> dict:
    """Coverage at a glance: total checks, grouped by pillar and object kind."""
    return catalog_service.catalog_summary()


# -- workspaces ----------------------------------------------------------------

@mcp.tool()
def list_workspaces(mode: str = "mock", project: str | None = None) -> list[dict]:
    """List the workspaces available to audit. Mock mode needs no sign-in."""
    return audit_service.list_workspaces(_project(project), mode)


# -- running -------------------------------------------------------------------

@mcp.tool()
def run_check(
    check_id: str,
    workspace_id: str,
    mode: str = "mock",
    layer: str | None = None,
    project: str | None = None,
) -> list[dict]:
    """Run exactly one check against one workspace and return its result.

    The fastest way to see what a specific rule does: it fetches only the data
    that one check declares it needs.
    """
    return audit_service.run_check(
        check_id=check_id,
        workspace_id=workspace_id,
        project_path=_project(project),
        mode=mode,
        layer=layer,
    )


@mcp.tool()
def run_audit(
    mode: str = "mock",
    pillars: list[str] | None = None,
    workspaces: list[dict] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Run a full audit and return the scorecard.

    ``workspaces`` takes ``[{"id": "...", "role": "Data Prep"}]``. Omit it to
    audit whatever the project file declares.
    """
    run = audit_service.run_audit(
        _project(project), mode=mode, pillars=pillars, workspaces=workspaces
    )
    report = audit_service.to_json(run)
    # Findings can run to hundreds of rows; an agent wants the shape first.
    report["results"] = report["results"][:50]
    report["results_truncated"] = len(run.results) > 50
    return report


@mcp.tool()
def summarize_findings(mode: str = "mock", project: str | None = None) -> dict:
    """Run an audit and return only the failing and partial findings, worst first."""
    run = audit_service.run_audit(_project(project), mode=mode)
    findings = [r for r in run.results if r.status.value in {"FAIL", "PARTIAL"}]
    findings.sort(key=lambda r: (r.score if r.score is not None else 9))
    return {
        "overall": run.aggregate.get("overall"),
        "total_findings": len(findings),
        "findings": [
            {
                "check_id": r.check_id, "title": r.title, "pillar": r.pillar.value,
                "severity": r.severity.value, "workspace": r.workspace,
                "obj": r.obj, "evidence": r.evidence,
                "recommendation": r.recommendation,
            }
            for r in findings[:40]
        ],
    }


def main() -> None:
    """Entry point: ``python -m auditfast.mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()
