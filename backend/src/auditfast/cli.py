"""Command-line interface.

A thin adapter, exactly like the REST API: it parses arguments, calls the same
:mod:`auditfast.services` functions the API calls, and prints the result. No
auditing logic lives here, which is why CLI and API scores can never diverge.

Every audit reads the live tenant — there is no offline mode. ``run`` always
signs in via the device-code flow before auditing.

Commands::

    auditfast run       --project config/project.example.yaml
    auditfast serve     [--port 8000] [--reload]
    auditfast checks    [--pillar Security]
    auditfast checklist my-checklist.csv   [--no-run]
"""
from __future__ import annotations

import argparse
import os
import sys

from .reporting.console import print_summary
from .services import audit_service as service
from .services import catalog_service

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BAD_INPUT = 2


def cmd_run(args) -> int:
    """Sign in, run an audit, and write the report files."""
    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print(f"Project file not found: {project}", file=sys.stderr)
        return EXIT_BAD_INPUT

    pillars = [p.strip() for p in args.pillars.split(",") if p.strip()] or None

    # Device-code flow works in a terminal: it prints a URL and a code, and
    # blocks until the auditor completes sign-in in a browser.
    from .security.device_flow import acquire_token
    from .services.project import load_project

    auth = load_project(project).auth
    token = acquire_token(
        auth.get("tenant_id"),
        auth.get("client_id"),
        auth.get("scopes", ["https://api.fabric.microsoft.com/.default"]),
    )

    print("Running audit...")
    run = service.run_audit(
        project,
        pillars=pillars,
        out_dir=os.path.abspath(args.out),
        token=token,
        external_checks_csv=args.external_checks,
    )

    print_summary(run.project_name, run.aggregate)
    for error in run.errors:
        print(f"  ! {error.workspace}: {error.evidence}", file=sys.stderr)
    print(f"Report : {run.files.get('markdown')}")
    print(f"Excel  : {run.files.get('excel')}")
    return EXIT_OK


def cmd_serve(args) -> int:
    """Start the REST API with uvicorn."""
    import uvicorn

    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print(f"Project file not found: {project}", file=sys.stderr)
        return EXIT_BAD_INPUT

    # Settings are read from the environment, so point them at this project.
    os.environ.setdefault("AUDITFAST_DEFAULT_PROJECT", project)

    print(f"API      http://{args.host}:{args.port}{'':s}")
    print(f"Docs     http://{args.host}:{args.port}/docs")
    print("Press Ctrl+C to stop.\n")

    uvicorn.run(
        "auditfast.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=None,  # our own logging config is already installed
    )
    return EXIT_OK


def cmd_checks(args) -> int:
    """List the check catalog without running anything."""
    rows = catalog_service.list_checks(pillar=args.pillar, layer=args.layer, scope=args.scope)
    if not rows:
        print("No checks match those filters.")
        return EXIT_OK

    print(f"{'ID':<18}{'REF':<9}{'PILLAR':<24}{'SCOPE':<11}{'SEV':<9}TITLE")
    print("-" * 110)
    for row in rows:
        print(
            f"{row['id']:<18}{row['ref']:<9}{row['pillar']:<24}"
            f"{row['scope']:<11}{row['severity']:<9}{row['title']}"
        )
    print(f"\n{len(rows)} check(s).")
    return EXIT_OK


def cmd_twin(args) -> int:
    """Sign in, crawl a workspace, and build + persist its Digital Twin."""
    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print(f"Project file not found: {project}", file=sys.stderr)
        return EXIT_BAD_INPUT

    from .clients import LiveFabricProvider
    from .core.enums import Layer
    from .security.device_flow import acquire_token
    from .services import twin_service
    from .services.graph_store import GraphStore
    from .services.project import load_project

    auth = load_project(project).auth
    token = acquire_token(
        auth.get("tenant_id"),
        auth.get("client_id"),
        auth.get("scopes", ["https://api.fabric.microsoft.com/.default"]),
    )

    provider = LiveFabricProvider(token)
    store = GraphStore(os.path.abspath(args.store))

    print(f"Discovering workspace {args.workspace} ...")
    graph = twin_service.refresh_twin(
        args.workspace, provider, store, layer=Layer.parse(args.layer)
    )
    summary = twin_service.twin_summary(graph)

    print(f"\nDigital Twin for {graph.workspace_id}")
    print(f"  nodes: {summary['node_count']}   edges: {summary['edge_count']}")
    for node_type, count in sorted(summary["nodes_by_type"].items()):
        print(f"    {node_type:<22}{count}")
    if summary["access_findings"]:
        print("  access findings (could not read):")
        for finding in summary["access_findings"]:
            props = finding["properties"]
            print(f"    ! {props.get('resource')}: {props.get('audit_impact')}")
    print(f"\nSnapshot: {store.path_for(graph.workspace_id)}")
    return EXIT_OK


def cmd_checklist(args) -> int:
    """Assess a user-supplied checklist file and run the matches over the KB.

    Offline by default: covered points are evaluated against the on-disk
    knowledge base (the ``kb-cache`` snapshots) with no sign-in. This is the
    "the client handed us their own checklist" path — separate from a full audit.
    """
    import json

    from .services import checklist_batch

    path = os.path.abspath(args.file)
    if not os.path.exists(path):
        print(f"Checklist file not found: {path}", file=sys.stderr)
        return EXIT_BAD_INPUT

    with open(path, encoding="utf-8-sig") as fh:
        content = fh.read()
    try:
        points = checklist_batch.parse_checklist(content, filename=path)
    except checklist_batch.ChecklistParseError as exc:
        print(f"Could not parse the checklist: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    workspace_ids = [w.strip() for w in (args.workspaces or "").split(",") if w.strip()] or None
    result = checklist_batch.run_checklist(
        points,
        workspace_ids=workspace_ids,
        run_checks=not args.no_run,
    )

    summary = result["summary"]
    print(f"\nAssessed {summary['total_points']} point(s): "
          f"{summary['covered']} covered, {summary['not_covered']} not covered, "
          f"{summary['invalid']} invalid.")
    print(f"Evaluated {summary['evaluated_points']} point(s) over "
          f"{summary['workspaces']} workspace(s) in the knowledge base.")
    if summary["verdicts"]:
        verdicts = ", ".join(f"{s}: {c}" for s, c in sorted(summary["verdicts"].items()))
        print(f"Workspace verdicts: {verdicts}")

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, "checklist-report.md")
    json_path = os.path.join(out_dir, "checklist-report.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(checklist_batch.render_markdown(result))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"\nReport : {md_path}")
    print(f"JSON   : {json_path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auditfast",
        description="Microsoft Fabric Well-Architected Auditor",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Sign in, run an audit, and write reports")
    run.add_argument("--project", required=True, help="Path to the project YAML file")
    run.add_argument("--out", default="output", help="Output directory (default: output)")
    run.add_argument("--pillars", default="", help="Comma-separated pillar subset")
    run.add_argument("--external-checks", default=None,
                     help="Path to external checks CSV (e.g., AdminChecks.csv)")

    serve = sub.add_parser("serve", help="Start the REST API")
    serve.add_argument("--project", default="config/project.example.yaml",
                       help="Project the API opens with")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

    checks = sub.add_parser("checks", help="List the check catalog")
    checks.add_argument("--pillar", default=None, help="Filter by pillar")
    checks.add_argument("--layer", default=None, help="Filter by layer role")
    checks.add_argument("--scope", default=None, help="Filter by object kind")

    twin = sub.add_parser("twin", help="Build a workspace's Digital Twin (knowledge graph)")
    twin.add_argument("--project", required=True, help="Path to the project YAML file")
    twin.add_argument("--workspace", required=True, help="Workspace id to crawl")
    twin.add_argument("--layer", default="Mixed", help="Layer role (default: Mixed)")
    twin.add_argument("--store", default="twins", help="Directory for twin snapshots")

    checklist = sub.add_parser(
        "checklist",
        help="Assess a custom checklist file (CSV/JSON/Markdown) over the offline KB",
    )
    checklist.add_argument("file", help="Path to the checklist file (.csv/.json/.md/.txt)")
    checklist.add_argument("--workspaces", default="",
                           help="Comma-separated workspace ids (default: every cached workspace)")
    checklist.add_argument("--out", default="output", help="Output directory (default: output)")
    checklist.add_argument("--no-run", action="store_true",
                           help="Only assess/dedup the points; do not evaluate them over the KB")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "serve": cmd_serve, "checks": cmd_checks,
                "twin": cmd_twin, "checklist": cmd_checklist}
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        build_parser().print_help()
        return EXIT_USAGE
    return handler(args)
