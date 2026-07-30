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
* Every audit reads the live tenant, so ``list_workspaces``, ``run_check``,
  ``run_audit``, and ``summarize_findings`` all take a Fabric bearer ``token``
  explicitly — MCP has no browser to run an interactive sign-in through, so the
  caller (an agent, or whoever configured this server) must supply one, e.g.
  acquired via ``auditfast run`` once interactively, an ``az account
  get-access-token`` call, or a service principal.
* ``run_audit`` is synchronous here (unlike the REST API's fire-and-poll)
  because MCP calls are already request/response with a client-side timeout, and
  an agent has nowhere useful to put a job id.
* No tool ever returns the token it was given.

This server also exposes the six **FabricIQ** Power BI tools
(``discover_artifacts``, ``resolve_report_id_from_url``, ``get_report_metadata``,
``get_semantic_model_schema``, ``value_search``, ``execute_query``) — native,
read-only re-creations of Microsoft's hosted FabricIQ MCP server, wrapping
:mod:`auditfast.services.fabriciq_service`. They take a **Power BI-audience**
token (``https://analysis.windows.net/powerbi/api``), which is a different
audience from the Fabric token the audit tools use.
"""
from __future__ import annotations

from typing import Any

from ..config.settings import get_settings
from ..services import (
    audit_service,
    catalog_service,
    checklist_batch,
    fabriciq_service,
    intake_service,
)

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


# -- checklist intake: dedup a point, or assess a whole custom checklist -------

@mcp.tool()
def assess_checklist_point(point: str) -> dict:
    """Assess one best-practice point against the catalog (dedup), token-free.

    Returns whether the point is already covered by a registered check (and by
    which), or a draft ``@check`` proposal when it is not. Never registers a
    check or changes a score — the deterministic path to answer "does the tool
    already do this?" before authoring a new rule.
    """
    return intake_service.assess_point(point)


@mcp.tool()
def assess_checklist_batch(
    points: list[str] | None = None,
    content: str | None = None,
    filename: str | None = None,
    workspace_ids: list[str] | None = None,
    run_checks: bool = True,
) -> dict:
    """Assess a whole custom checklist and run the matches over the offline KB.

    Supply ``points`` (a list of statements) or ``content`` (raw CSV/JSON/Markdown
    file text, with ``filename`` to hint the format). Each point is deduped
    against the catalog; for a point already covered by an automated check, that
    check is evaluated over the on-disk knowledge base (token-free — no live read
    from here). Uncovered points return a draft proposal to author. This never
    registers a check and never changes a score.
    """
    if points:
        parsed = [checklist_batch.ChecklistPoint(point=p) for p in points if p and p.strip()]
    elif content:
        try:
            parsed = checklist_batch.parse_checklist(content, filename=filename)
        except checklist_batch.ChecklistParseError as exc:
            return {"error": str(exc)}
    else:
        return {"error": "Provide either 'points' or 'content'."}

    return checklist_batch.run_checklist(
        parsed, workspace_ids=workspace_ids, run_checks=run_checks, token=None
    )


# -- workspaces ----------------------------------------------------------------

@mcp.tool()
def list_declared_workspaces(project: str | None = None) -> list[dict]:
    """List the workspaces the project file declares, without contacting Fabric."""
    return audit_service.list_workspaces(_project(project))


@mcp.tool()
def list_workspaces(token: str) -> list[dict]:
    """List every workspace the given Fabric token can see."""
    return audit_service.list_live_workspaces(token)


# -- running -------------------------------------------------------------------

@mcp.tool()
def run_check(
    check_id: str,
    workspace_id: str,
    token: str,
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
        layer=layer,
        token=token,
    )


@mcp.tool()
def run_audit(
    token: str,
    pillars: list[str] | None = None,
    workspaces: list[dict] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Run a full audit against the live tenant and return the scorecard.

    ``workspaces`` takes ``[{"id": "...", "role": "Data Prep"}]``. Omit it to
    audit whatever the project file declares.
    """
    run = audit_service.run_audit(
        _project(project), pillars=pillars, workspaces=workspaces, token=token
    )
    report = audit_service.to_json(run)
    # Findings can run to hundreds of rows; an agent wants the shape first.
    report["results"] = report["results"][:50]
    report["results_truncated"] = len(run.results) > 50
    return report


@mcp.tool()
def summarize_findings(token: str, project: str | None = None) -> dict:
    """Run an audit and return only the failing and partial findings, worst first."""
    run = audit_service.run_audit(_project(project), token=token)
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


# -- FabricIQ: native, read-only Power BI tools --------------------------------
#
# These re-create Microsoft's hosted FabricIQ MCP tools directly on the Power BI
# REST API + DAX executeQueries endpoint, so the auditor owns the implementation
# and stays read-only. ``resolve_report_id_from_url`` is pure parsing and needs
# no token; the other five need a bearer token for the *Power BI* audience
# (``https://analysis.windows.net/powerbi/api``) — a DIFFERENT audience from the
# Fabric token the audit tools above use. Passing the wrong one yields 401.

@mcp.tool()
def resolve_report_id_from_url(url: str) -> dict:
    """Parse a Power BI/Fabric report URL into its workspace and report GUIDs.

    Pure string parsing, no sign-in. Flags workspace-app URLs, where the path
    report id is a per-app instance id rather than the published-report GUID.
    """
    return fabriciq_service.resolve_report_id_from_url(url)


@mcp.tool()
def discover_artifacts(
    token: str,
    search_query: str,
    artifact_types: list[str] | None = None,
    max_results: int = 50,
) -> dict:
    """Search accessible Power BI workspaces for reports and semantic models.

    Call this first when you have a name but no GUID. ``artifact_types`` narrows
    to ``["Report"]`` or ``["SemanticModel"]``; omit for both. Reports are
    returned before standalone models. Needs a Power BI-audience ``token``.
    """
    return fabriciq_service.discover_artifacts(
        token, search_query, artifact_types=artifact_types, max_results=max_results
    )


@mcp.tool()
def get_report_metadata(
    token: str, report_id: str, workspace_id: str | None = None
) -> dict:
    """Report properties, pages, and the underlying semantic model GUID.

    The ``semanticModel`` field is the id to pass to the schema/query/value
    tools. Needs a Power BI-audience ``token``.
    """
    return fabriciq_service.get_report_metadata(token, report_id, workspace_id)


@mcp.tool()
def get_semantic_model_schema(
    token: str, artifact_id: str, workspace_id: str | None = None
) -> dict:
    """Tables, columns, measures, and relationships of a semantic model.

    Built from read-only ``INFO.VIEW.*`` DAX. Read this before writing queries.
    Needs a Power BI-audience ``token``.
    """
    return fabriciq_service.get_semantic_model_schema(token, artifact_id, workspace_id)


@mcp.tool()
def value_search(
    token: str,
    artifact_id: str,
    search_terms: list[str],
    workspace_id: str | None = None,
    max_columns: int = 25,
    max_rows: int = 20,
) -> dict:
    """Find which column holds a value, and its exact spelling, before filtering.

    Scans visible text columns with case-insensitive DAX ``SEARCH`` so a filter
    uses the canonical value. Needs a Power BI-audience ``token``.
    """
    return fabriciq_service.value_search(
        token, artifact_id, search_terms,
        workspace_id=workspace_id, max_columns=max_columns, max_rows=max_rows,
    )


@mcp.tool()
def execute_query(
    token: str,
    artifact_id: str,
    dax_queries: list[str],
    max_rows: int = 250,
    workspace_id: str | None = None,
) -> dict:
    """Run 1–4 read-only DAX ``EVALUATE`` queries and return tabular results.

    Exactly one ``EVALUATE`` per query; up to 1,000 rows each (default 250).
    Needs a Power BI-audience ``token``.
    """
    return fabriciq_service.execute_query(
        token, artifact_id, dax_queries, max_rows=max_rows, workspace_id=workspace_id
    )


def main() -> None:
    """Entry point: ``python -m auditfast.mcp.server``."""
    mcp.run()


if __name__ == "__main__":
    main()
