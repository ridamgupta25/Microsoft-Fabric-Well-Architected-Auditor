"""Shared audit service used by both the CLI and the web UI.

Keeps a single run path so the console, files, and browser all show identical
numbers. No web framework here - just plain functions returning Python objects.
"""
from __future__ import annotations

import json
import os

from ..core.engine import run_audit as _run_checks
from ..core.models import CheckResult
from ..core.scoring import aggregate


def _load_yaml(path: str) -> dict:
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _resolve(path: str, base: str) -> str:
    if os.path.isabs(path) and os.path.exists(path):
        return path
    for candidate in (path, os.path.join(base, path)):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(base, path)


def load_project(project_path: str):
    proj_path = os.path.abspath(project_path)
    base = os.path.dirname(os.path.dirname(proj_path))  # config/.. -> project root
    proj = _load_yaml(proj_path)
    settings = proj.get("project", {})
    return proj, settings, base, proj_path


def list_workspaces(project_path: str, mode: str = "mock") -> list[dict]:
    """Return the workspaces available for selection in the UI."""
    proj, _settings, base, _ = load_project(project_path)
    role_by_id = {w["id"]: w.get("role", "") for w in proj.get("workspaces", [])}
    proj_ids = list(role_by_id.keys())

    if mode == "mock":
        tenant_file = _resolve(proj.get("mock", {}).get("tenant_file", "sample_data/tenant.json"), base)
        with open(tenant_file, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = []
        for w in data.get("workspaces", []):
            if proj_ids and w["id"] not in proj_ids:
                continue
            rows.append({
                "id": w["id"],
                "name": w.get("displayName", w["id"]),
                "role": role_by_id.get(w["id"], w.get("role", "")),
                "items": len(w.get("items", [])),
                "pipelines": len(w.get("pipelines", {})),
            })
        return rows

    # live: we cannot enumerate contents without a token, so show what the
    # project declares (names resolved once the audit runs).
    return [{"id": w["id"], "name": w["id"], "role": w.get("role", ""),
             "items": None, "pipelines": None} for w in proj.get("workspaces", [])]


def list_live_workspaces(token: str) -> list[dict]:
    """Enumerate every workspace the signed-in user can access."""
    from ..clients import LiveFabricClient
    return LiveFabricClient(token).list_workspaces()


def diagnose(token: str) -> dict:
    """Probe Fabric connectivity so we can see what the token can actually read."""
    from ..clients import LiveFabricClient
    return LiveFabricClient(token).probe()


def _build_client(proj: dict, base: str, mode: str, auth_token: str | None):
    if mode == "live":
        from ..clients import LiveFabricClient
        return LiveFabricClient(auth_token)
    from ..clients import MockFabricClient
    tenant_file = _resolve(proj.get("mock", {}).get("tenant_file", "sample_data/tenant.json"), base)
    return MockFabricClient(tenant_file)


def run_audit(project_path: str, mode: str = "mock", pillars: list[str] | None = None,
              workspace_ids: list[str] | None = None, workspaces_spec: list[dict] | None = None,
              out_dir: str | None = None, auth_token: str | None = None) -> dict:
    from ..core.checks.base import set_remediation

    proj, settings, base, _ = load_project(project_path)
    rem_path = _resolve(proj.get("remediation", "config/remediation.yaml"), base)
    set_remediation(_load_yaml(rem_path) if os.path.exists(rem_path) else {})

    if workspaces_spec:
        workspaces = [(w["id"], w.get("role", "")) for w in workspaces_spec if w.get("id")]
    else:
        workspaces = [(w["id"], w.get("role", "")) for w in proj.get("workspaces", [])]
        if workspace_ids:
            wanted = set(workspace_ids)
            workspaces = [(i, r) for (i, r) in workspaces if i in wanted]

    client = _build_client(proj, base, mode, auth_token)
    results = _run_checks(client, workspaces, settings)

    if pillars:
        wanted_p = {p.lower() for p in pillars}
        results = [r for r in results if r.pillar.lower() in wanted_p or not r.scored]

    agg = aggregate(results)
    project_name = settings.get("name", "Fabric Project")

    files: dict[str, str] = {}
    if out_dir:
        from ..reporting.excel_report import build_excel
        from ..reporting.markdown_report import build_markdown
        os.makedirs(out_dir, exist_ok=True)
        md_path = os.path.join(out_dir, "audit-report.md")
        xlsx_path = os.path.join(out_dir, "audit-report.xlsx")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(build_markdown(project_name, agg, results, mode))
        build_excel(xlsx_path, project_name, agg, results, mode)
        files = {"markdown": md_path, "excel": xlsx_path}

    return {"project_name": project_name, "mode": mode, "agg": agg,
            "results": results, "files": files}


def _result_dict(r: CheckResult) -> dict:
    return {
        "check_id": r.check_id, "ref": r.ref, "title": r.title, "pillar": r.pillar,
        "status": r.status.value, "score": r.score, "coverage": r.coverage,
        "evidence": r.evidence, "recommendation": r.recommendation,
        "severity": r.severity.value, "workspace": r.workspace,
        "workspace_role": r.workspace_role, "obj": r.obj, "scored": r.scored,
        "common": not r.obj,  # workspace-level checks are common to every project
    }


def to_json(res: dict) -> dict:
    """Serialize a run result for the web API."""
    agg = res["agg"]
    # Split workspace-access errors out of the check results so they are shown
    # as explicit warnings (not counted as ordinary failing checks).
    errors = [r for r in res["results"] if r.check_id == "WS-ACCESS"]
    clean = [r for r in res["results"] if r.check_id != "WS-ACCESS"]
    counts: dict[str, int] = {}
    for r in clean:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return {
        "project_name": res["project_name"],
        "mode": res["mode"],
        "overall": agg["overall"],
        "by_pillar": agg["by_pillar"],
        "by_workspace": agg["by_workspace"],
        "counts": counts,
        "total_scored": agg["total_scored"],
        "results": [_result_dict(r) for r in clean],
        "errors": [{"workspace": r.workspace, "role": r.workspace_role,
                    "message": r.evidence, "recommendation": r.recommendation}
                   for r in errors],
        "files": {k: os.path.basename(v) for k, v in res["files"].items()},
    }
