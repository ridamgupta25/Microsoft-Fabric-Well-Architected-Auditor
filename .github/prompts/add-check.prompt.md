---
description: "Add a Fabric Well-Architected checklist point to the auditor end-to-end: dedup, research, implement a deterministic @check, add remediation, validate with the harness, and test it on a real workspace."
argument-hint: "The checklist point to add, e.g. 'Delta tables are OPTIMIZE-compacted'"
---
Add this checklist point to the auditor: ${input:point}

The complete lookup for every decision below — pillar/layer/scope folders, `requires[]`, verdict helpers, the context API, id/ref conventions, and pinned counts — is [check-authoring-cookbook.instructions.md](../instructions/check-authoring-cookbook.instructions.md) (auto-attached under `core/check/**`).

Work the check-authoring loop — do not skip the dedup or the harness:

1. **Dedup.** Call `POST /api/v1/checklist/assess` (or `intake_service.assess_point`) with the point. If it returns `covered`, stop and report the existing check id — do not add a duplicate.
2. **Author.** Delegate to the **Checklist Author** agent, which orchestrates check-researcher → check-implementer → check-reviewer. It picks pillar/layer/scope, writes the `@check` in `backend/src/auditfast/core/check/<pillar>/<layer>/automated.py`, and adds remediation to `backend/config/remediation.yaml`.
3. **Validate (offline).** From `backend/`, run `..\.venv\Scripts\python.exe ..\.github\harness\validate_check.py <NEW-ID>`, then `..\.venv\Scripts\python.exe -m pytest -q` and `..\.venv\Scripts\python.exe -m ruff check src`. Update any pinned counts.
4. **Test on the workspace.** Use the auditfast MCP `run_check` tool (or `POST /api/v1/audit/check`) with the new check id, a workspace id, and a Fabric token, to see its live verdict.

Report: covered-or-new, the new check id + ref, files changed, the harness result, and the live verdict from step 4.
