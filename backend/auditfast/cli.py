"""Command-line interface for AuditFAST Core (Phase 1)."""
from __future__ import annotations

import argparse
import os

from .reporting.console_report import print_summary
from .services import audit_service as service


def cmd_run(args) -> int:
    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print(f"Project file not found: {project}")
        return 2
    mode = "live" if args.live else "mock"
    pillars = [p.strip() for p in args.pillars.split(",") if p.strip()] or None

    auth_token = None
    if mode == "live":
        from .security.device_flow import acquire_token
        proj, _settings, _base, _ = service.load_project(project)
        auth = proj.get("auth", {})
        auth_token = acquire_token(
            auth.get("tenant_id"), auth.get("client_id"),
            auth.get("scopes", ["https://api.fabric.microsoft.com/.default"]),
        )

    print(f"Running AuditFAST Core ({mode} mode)...")
    res = service.run_audit(project, mode=mode, pillars=pillars,
                            out_dir=os.path.abspath(args.out), auth_token=auth_token)

    print_summary(res["project_name"], res["agg"], res["mode"])
    print(f"Report : {res['files'].get('markdown')}")
    print(f"Excel  : {res['files'].get('excel')}")
    return 0


def cmd_serve(args) -> int:
    import threading
    import webbrowser

    from .web import create_app

    project = os.path.abspath(args.project)
    if not os.path.exists(project):
        print(f"Project file not found: {project}")
        return 2

    app = create_app(project)
    url = f"http://127.0.0.1:{args.port}"
    print(f"AuditFAST Core UI (Flask) running at {url}   (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # threaded=True so the interactive sign-in poll can run while a browser
    # sign-in is in progress. reloader off to keep a single clean process.
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="auditfast",
        description="AuditFAST Core — rule-based Fabric Well-Architected Auditor (Phase 1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run an audit and write reports (CLI)")
    run.add_argument("--project", required=True, help="Path to the project YAML file")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--mock", action="store_true", help="Use offline sample fixtures (default)")
    mode.add_argument("--live", action="store_true", help="Sign in (OAuth2) and read the live tenant")
    run.add_argument("--out", default="output", help="Output directory (default: output)")
    run.add_argument("--pillars", default="", help="Comma-separated pillar subset to score")

    serve = sub.add_parser("serve", help="Launch the interactive web UI")
    serve.add_argument("--project", default="config/project.example.yaml",
                       help="Default project YAML the UI opens with")
    serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    serve.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser")

    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    p.print_help()
    return 1
