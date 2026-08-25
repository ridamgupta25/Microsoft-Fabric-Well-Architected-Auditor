# Debugging

How to run the backend, the tests, and the frontend under a debugger in VS Code —
set breakpoints, step through code, and inspect variables.

The configurations live in [`.vscode/launch.json`](../../.vscode/launch.json) at the
**workspace root** (`dev/`). Open **Run and Debug** (`Ctrl+Shift+D`), pick a config
from the dropdown, and press **F5**.

---

## Prerequisites

| For… | You need |
|------|----------|
| Backend + tests (`debugpy` configs) | The **Python** extension (bundles the Python Debugger) |
| Frontend (`msedge` config) | The built-in **JavaScript Debugger** (no install) |
| Both | The `Python312` interpreter with `auditfast` installed editable (`pip install -e backend`) |

The configs point at
`C:\Users\v-saumghosh\AppData\Local\Programs\Python\Python312\python.exe`. If your
interpreter differs, update the `python` field in each backend config.

---

## The configurations

| Config | Use it for |
|--------|------------|
| **Backend: FastAPI (debug)** | Debugging Python. Runs uvicorn **without** `--reload`, so breakpoints reliably hit (reload runs the app in a worker subprocess the debugger does not attach to). |
| **Backend: FastAPI (reload, no breakpoints)** | Normal dev with auto-reload. Convenient, but breakpoints in the worker may not stop. |
| **Backend: Pytest (current file)** | Debug the test file currently open in the editor. |
| **Frontend: Edge (localhost:5173)** | Debug the React app in the browser. Breakpoints in `.tsx`/`.ts`. |
| **Full stack (backend + Edge)** | Launches the debuggable backend and opens the browser together. |

---

## Debug the backend

1. **Stop any backend you started manually** so port `8000` is free — the debugger
   starts its own instance.
2. `Ctrl+Shift+D` → select **Backend: FastAPI (debug)** → **F5**.
3. Set a breakpoint by clicking the gutter (left of a line number) in any `.py`
   file. A good first breakpoint is inside `run_custom_checks` in
   [`services/custom_checks_service.py`](../backend/src/auditfast/services/custom_checks_service.py).
4. Trigger it — e.g. run a custom check from the UI, or call the endpoint. Execution
   pauses at the breakpoint.

At a breakpoint you can:

- **Step**: Step Over `F10`, Step Into `F11`, Step Out `Shift+F11`, Continue `F5`.
- **Inspect**: hover a variable, or use the **Variables** / **Watch** panels.
- **Evaluate**: type expressions in the **Debug Console** (bottom panel).
- **Call stack**: see how you got here in the **Call Stack** panel.

> Breakpoints not hitting? Make sure you launched **Backend: FastAPI (debug)** (the
> non-reload config), and that `justMyCode` is `false` (it is, in these configs) so
> you can also step into library code.

---

## Debug the tests

1. Open the test file (e.g.
   [`tests/test_custom_checks_api.py`](../backend/tests/test_custom_checks_api.py)).
2. Set a breakpoint in the test or in the code it exercises.
3. `Ctrl+Shift+D` → **Backend: Pytest (current file)** → **F5**.

It runs `pytest <the open file> -vv` under the debugger. To debug a single test,
temporarily add `::test_name` — or set the breakpoint and let pytest reach it.

---

## Debug the frontend

1. Start the dev server (it is **not** launched by the debugger):

   ```powershell
   npm.cmd --prefix "Microsoft-Fabric-Well-Architected-Auditor\frontend" run dev
   ```

2. `Ctrl+Shift+D` → **Frontend: Edge (localhost:5173)** → **F5**. A debugging Edge
   window opens.
3. Set breakpoints in `.tsx`/`.ts` (e.g.
   [`pages/CustomChecksPage.tsx`](../frontend/src/pages/CustomChecksPage.tsx)) and
   interact with the app to hit them.

Source maps are served by Vite, so you debug the original TypeScript, not the
bundle.

---

## Debug both at once

Use **Full stack (backend + Edge)** to start the debuggable backend and open the
browser together. Start the Vite dev server first (as above) so the Edge target has
something to load; the compound only launches the browser and the backend.

---

## Attach to an already-running backend (optional)

If you prefer to keep running uvicorn yourself, start it under `debugpy` and attach:

```powershell
cd Microsoft-Fabric-Well-Architected-Auditor\backend
python -m debugpy --listen 5678 --wait-for-client -m uvicorn auditfast.main:app --host 127.0.0.1 --port 8000
```

Then add an attach config to `.vscode/launch.json`:

```json
{
  "name": "Backend: Attach (debugpy 5678)",
  "type": "debugpy",
  "request": "attach",
  "connect": { "host": "127.0.0.1", "port": 5678 }
}
```

`--wait-for-client` holds startup until the debugger attaches, so no request is
missed.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `debugpy` / Python debugger not found | Install the **Python** extension; reload VS Code. |
| `Address already in use` on `:8000` | Stop the manually-run backend first (or change the `--port` in the config). |
| Breakpoints show hollow / "unbound" | You launched the **reload** config — use **Backend: FastAPI (debug)** instead. |
| `ModuleNotFoundError: auditfast` | Wrong interpreter. Update `python` in the config, or `pip install -e backend`. |
| Frontend breakpoints don't bind | The dev server isn't running, or the URL differs — confirm it's serving on `http://localhost:5173`. |
