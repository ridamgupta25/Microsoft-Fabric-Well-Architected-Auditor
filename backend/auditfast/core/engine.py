"""Audit engine: run the registered checks across all project workspaces."""
from __future__ import annotations

from .checks.base import PIPELINE_CHECKS, WORKSPACE_CHECKS
from ..clients import FabricClient, WorkspaceAccessError
from .models import FOUNDATION, CheckResult, Severity, Status

# Pipeline checks only apply to layers expected to contain pipelines.
# Storage / Logs / Reporting layers are audited by the workspace + layer checks.
PIPELINE_ROLES = {"Data Prep", "Data Operations", "Mixed", ""}


def _access_error(ws_id: str, role: str, message: str) -> CheckResult:
    """A visible, non-scored result so an unreadable workspace shows up in the
    report as an explicit error instead of silently running the checklist."""
    return CheckResult(
        check_id="WS-ACCESS", ref="-", title="Workspace could not be read",
        pillar=FOUNDATION, status=Status.FAIL, score=None, coverage=None,
        evidence=message,
        recommendation=("Confirm the workspace name/ID is correct and that the "
                        "signed-in user has at least Viewer access, then re-run."),
        severity=Severity.CRITICAL, workspace=ws_id, workspace_role=role,
        obj="", scored=False,
    )


def run_audit(client: FabricClient, workspaces: list[tuple[str, str]],
              settings: dict) -> list[CheckResult]:
    """workspaces = list of (workspace_id, layer_role)."""
    results: list[CheckResult] = []

    for ws_id, role in workspaces:
        try:
            ctx = client.get_workspace_context(ws_id, role)
        except WorkspaceAccessError as exc:
            print(f"  ! {ws_id}: {exc}")
            results.append(_access_error(ws_id, role, str(exc)))
            continue
        except KeyError:
            msg = (f"Workspace '{ws_id}' was not found (it is not in the mock "
                   "tenant, or the name/ID is wrong).")
            print(f"  ! {msg}")
            results.append(_access_error(ws_id, role, msg))
            continue
        except Exception as exc:  # unexpected read failure
            msg = f"Could not read workspace '{ws_id}': {exc}"
            print(f"  ! {msg}")
            results.append(_access_error(ws_id, role, msg))
            continue

        for fn in WORKSPACE_CHECKS:
            out = fn(ctx, settings)
            results.extend(out if isinstance(out, list) else [out])

        if role in PIPELINE_ROLES:
            for pl_name, definition in (ctx.get("pipelines") or {}).items():
                for fn in PIPELINE_CHECKS:
                    res = fn(ctx, pl_name, definition, settings)
                    if res:
                        results.append(res)

    return results
