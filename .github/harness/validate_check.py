#!/usr/bin/env python
"""Validate a freshly authored check — no live tenant required.

Run from ``backend/`` with the venv interpreter::

    ..\\.venv\\Scripts\\python.exe ..\\.github\\harness\\validate_check.py CHECK-ID

It answers the three questions the check-reviewer must confirm:

  1. Is the check registered? (the ``@check`` import side effect actually fired)
  2. Does its ``ref`` have remediation text? (tests enforce this repo-wide)
  3. For a workspace check, does it degrade to N/A — not FAIL — when the data it
     requires is unavailable? (the "never fail on a read we could not make" rule)

Exit code is non-zero only when the id is not registered, so it is a safe gate;
the other findings print as warnings for a human to weigh. To test the check
against a real workspace, use the auditfast MCP ``run_check`` tool or
``POST /api/v1/audit/check`` with a Fabric token — that step needs a tenant and
is deliberately not done here.
"""
from __future__ import annotations

import sys

import yaml

import auditfast.core.check  # noqa: F401 - importing the package registers every check
from auditfast.config.settings import BACKEND_ROOT
from auditfast.core.check.registry import REGISTRY
from auditfast.core.enums import Scope, Status
from auditfast.core.models import CheckContext, WorkspaceContext
from auditfast.core.scoring import status_from_score


def _statuses_with_data_absent(spec) -> list[Status]:
    """Run the check with everything it requires marked unavailable."""
    ws = WorkspaceContext(id="_smoke_", unavailable=set(spec.requires))
    ctx = CheckContext(workspace=ws, settings={}, obj_name=ws.name, obj=ws)
    outcome = spec.fn(ctx)
    verdicts = [] if outcome is None else (
        list(outcome) if isinstance(outcome, (list, tuple)) else [outcome]
    )
    return [v.status or status_from_score(v.score or 0) for v in verdicts]


def main(check_id: str) -> int:
    spec = REGISTRY.get(check_id)
    if spec is None:
        print(f"FAIL  {check_id}: not registered. Is its module an auto-imported "
              f"leaf (automated.py/roadmap.py), not a _-prefixed helper?")
        return 2

    print(f"OK    registered: {spec.id}  ref={spec.ref}  {spec.pillar.value} / "
          f"{spec.scope.value}  requires={sorted(r.value for r in spec.requires)}")

    remediation = yaml.safe_load(
        (BACKEND_ROOT / "config" / "remediation.yaml").read_text("utf-8")
    ) or {}
    if spec.ref in remediation:
        print(f"OK    remediation present for ref {spec.ref}")
    else:
        print(f"WARN  no remediation for ref {spec.ref} — add it to "
              f"config/remediation.yaml (a test enforces this repo-wide)")

    if spec.scope is Scope.WORKSPACE:
        try:
            statuses = _statuses_with_data_absent(spec)
        except Exception as exc:  # noqa: BLE001 - a crash is a finding, not a stop
            print(f"WARN  raised {type(exc).__name__} with required data absent: {exc}")
        else:
            if Status.FAIL in statuses:
                print("WARN  FAILs when its required data is unavailable — return "
                      "not_applicable() there, so a blocked read is N/A, not FAIL")
            else:
                print("OK    degrades to N/A (not FAIL) when required data is absent")
    else:
        print(f"INFO  {spec.scope.value}-scoped: confirm it returns not_applicable() "
              f"when ctx.obj lacks the data it reads")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python validate_check.py CHECK-ID")
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
