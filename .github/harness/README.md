# Harness — validate a generated check

Run this after `check-implementer` adds a check and before you claim it done. A
check must not regress the suite and must degrade to **N/A (not FAIL)** when its
data is missing. All commands run from `backend/` on Windows PowerShell (use `;`,
never `&&`).

## 0. Validate the new check (one command)
[`validate_check.py`](validate_check.py) checks registration, remediation, and
N/A-not-FAIL behaviour in one shot:
```powershell
..\.venv\Scripts\python.exe ..\.github\harness\validate_check.py <NEW-ID>
```
It exits non-zero only when the id is not registered, and warns (not fails) on a
missing remediation ref or a check that FAILs when its required data is absent.

## 1. Registry loaded and count known
```powershell
..\.venv\Scripts\python.exe -c "from auditfast.core.check.registry import REGISTRY; print(len(REGISTRY), 'checks')"
```
The number must equal the `checks_registered` value pinned in `tests/test_api.py`.
If you added one automated check, both go up by exactly one.

## 2. Tests (fully offline)
```powershell
..\.venv\Scripts\python.exe -m pytest -q
```
If a **pinned** assertion changed (`checks_registered`, `EXPECTED_OVERALL`,
`EXPECTED_SCORED_CHECKS`, `EXPECTED_RESULT_ROWS`, or the evaluated counts in
`test_engine.py`), update it to the true new value — never weaken the test.

## 3. Lint
```powershell
..\.venv\Scripts\python.exe -m ruff check src
```

## 4. N/A-not-FAIL spot check
Confirm the new check returns N/A when its `requires` resource is unavailable —
add a targeted case, or run one check against the fixture:
```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_engine.py -q
```

## Go / no-go
Green pytest + green ruff + registry count matching the pinned value = **go**.
Anything red = **no-go**; fix the check, do not touch the harness.

## 6. Test on a real workspace (needs a tenant)
The offline harness proves the check is *wired correctly*; to see its live
verdict, run it against one workspace with a Fabric token — this is the "test on
the workspace using Copilot" step:
- MCP: call the `run_check` tool (auditfast server) with `check_id`, `workspace_id`, `token`.
- REST: `POST /api/v1/audit/check` with `{ check_id, workspace_id, auth_session, layer }`.

Confirm the verdict is what you expect, and that it is **N/A (not FAIL)** on any
workspace where the data it reads could not be fetched.
