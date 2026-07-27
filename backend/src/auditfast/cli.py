"""Command-line interface.

A thin adapter, exactly like the REST API: it parses arguments, calls the same
:mod:`auditfast.services` functions the API calls, and prints the result. No
auditing logic lives here, which is why CLI and API scores can never diverge.

Every audit reads the live tenant — there is no offline mode. ``run`` always
signs in via the device-code flow before auditing.

Commands::

    auditfast run     --project config/project.example.yaml
    auditfast serve   [--port 8000] [--reload]
    auditfast checks  [--pillar Security]
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
        project, pillars=pillars, out_dir=os.path.abspath(args.out), token=token
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "serve": cmd_serve, "checks": cmd_checks}
    handler = handlers.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces the choice
        build_parser().print_help()
        return EXIT_USAGE
    return handler(args)
