# Requirements & Prerequisites

What you need to install, configure, and verify before using the Microsoft
Fabric Well-Architected Auditor.

**Source-reviewed:** 2026-09-09, against the current `auditfast` 0.4.0 checkout.
This describes implemented capabilities, not a future deployment architecture.
The verification commands below are checks to run locally, not claimed test
results from this documentation review.

The repository root contains [backend](../backend/), [frontend](../frontend/),
and [docs](../docs/) directly; there is no additional `auditfast-core` directory.
All PowerShell examples start at that repository root unless stated otherwise.
This guide lives in `docs`; repository-root links start with `../`, while
links to other guides are relative to this directory.

> **Read-only means no changes to Fabric.** The application reads REST metadata
> and definitions, SQL metadata, and optional Power BI/OneLake data. Some read
> operations use POST, including `getDefinition` and Power BI query execution.
> It does write reports, caches, and generated custom-check artifacts locally.
>
> **AI is optional, but not limited to prose.** Built-in deterministic checks
> use the same engine across adapters. Optional AI can judge a separate advisory
> report or generate custom checks; those results are kept separate from the
> deterministic scorecard.

## 1. Choose the workflow first

Not every workflow needs a live tenant, sign-in, SQL driver, or model endpoint.

| Workflow | What it needs | Important boundary |
|---|---|---|
| Browse the catalog / assess checklist coverage | Installed backend; web UI optional | No Fabric token needed |
| Standard live audit | Fabric access, a working sign-in path, Microsoft network access | `source="live"` is the API default; may serve cached data and refresh it |
| Saved-KB audit | Saved archive or uploaded workspace JSON snapshots | `source="kb"` needs no Fabric sign-in and has no live fallback |
| Interactive self-assessment | Reviewer answers during an applicable standard audit | Answers are scored; skipped/unanswered points are N/A |
| Cross-workspace assessment | Workspace group and environment-level selections | Group checks have a separate registry, but scored non-advisory group findings contribute to the main score; environment weighting is opt-in |
| Elevated audit | Explicit category selection and the corresponding privileges/evidence | `check_set="admin"` has its own checks, cache, score, and report |
| Batch checklist over the KB | A checklist file and cached workspace snapshots | Offline evaluation of matched built-in checks; `--no-run` only assesses coverage |
| Custom checks | Cached workspace data; usable AI for new code generation | Separate 0-100 results, generated-code review, and approval workflow |
| Advisory judging | An existing audit and either external judgments or usable AI | Rebuilds the advisory report, not the deterministic scorecard |
| MCP / FabricIQ | MCP extra and a compatible client; tokens for live tools | Fabric and Power BI tools use different token audiences |
| Digital Twin CLI | Project YAML, workspace access, sign-in, writable snapshot directory | Builds a workspace graph; not a prerequisite for normal audits |

The current taxonomy has **13 scored, checklist-aligned pillars plus Foundation
(unscored)**, not the original seven-pillar catalog. Use the live catalog for
current names and counts. Standard, group, and elevated checks have separate
registries. Interactive checks are registered again; `roadmap` modules remain
in source but are intentionally **not loaded**.

Sources: [audit request schema](../backend/src/auditfast/schemas/audit.py),
[domain vocabulary](../backend/src/auditfast/core/enums.py),
[check loader](../backend/src/auditfast/core/check/__init__.py).

## 2. System prerequisites

| Requirement | Version / recommendation | Needed for |
|---|---|---|
| Python | **3.10+**; 3.12 is a practical local choice | Backend, CLI, MCP, tests |
| Node.js and npm | Use a maintained Node.js LTS release, such as 22, with its bundled npm | Building/running the React UI only |
| Git | Current supported version | Cloning and maintaining the checkout; not needed to execute an already-installed package |
| Azure CLI | Optional | Reusing `az login` on the machine running the backend |
| Microsoft ODBC Driver for SQL Server | Driver **18 recommended** | Live Lakehouse/Warehouse SQL metadata reads |
| Writable local storage | Space for reports, snapshots, and optional custom-check/vector data | All report-producing workflows |

The backend declares its Python minimum in
[pyproject.toml](../backend/pyproject.toml). The frontend uses Vite 5 and
TypeScript 5.6; [package.json](../frontend/package.json) does **not** declare a
Node/npm `engines` constraint. The old "Node 18 LTS / npm 9" guidance should
not be treated as a maintained deployment recommendation.

Windows installation options:

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Git.Git

# Optional: Azure CLI sign-in and SQL metadata coverage
winget install -e --id Microsoft.AzureCLI
winget install -e --id Microsoft.msodbcsql.18
```

Close and reopen the terminal after installation, then verify:

```powershell
python --version
node --version
npm --version
git --version
az version  # Only if using Azure CLI
```

For a fresh Windows clone, enable Git long-path support for that clone:

```powershell
git clone -c core.longpaths=true https://github.com/ridamgupta25/Microsoft-Fabric-Well-Architected-Auditor.git
Set-Location .\Microsoft-Fabric-Well-Architected-Auditor
```

Docker, an external database, Redis, and a hosted vector database are **not**
required for local use. Installing the `db` extra does not enable durable API
history; the application still uses an in-memory job repository.

## 3. Fabric access and authentication

### Standard versus elevated access

For live discovery, start with access to the workspaces you intend to audit
(Viewer is a baseline, **not a guarantee of full coverage**). Item definitions,
role assignments, connections, gateways, SQL metadata, and other resources can
require additional item/workspace permissions, consent, or resource-specific roles.

A standard audit does **not** require tenant-admin access. Elevated checks are
now implemented separately rather than all being described as future roadmap
coverage:

| Elevated category | Access / input to arrange |
|---|---|
| `Tenant` | Appropriate Fabric/Power BI tenant-admin access and any evidence required by the selected checks |
| `Capacity` | Access to the relevant capacity administration/metrics data, plus the workspace and report name that locate the Capacity Metrics app |
| `Workspace Admin` | Sufficient workspace privileges (Member or higher where required), plus access to relevant connections/gateways |

Selecting an elevated category does not grant permissions. Some checks also
need reviewer inputs such as production workspace names, security-group names,
or explicit attestations; the capacity checks additionally need the app named,
because no API reports where it is installed or what it was called. Missing
access or evidence must not be interpreted as successful compliance.

When a check's required data cannot be read, it reports **N/A rather than FAIL**.
This is not a promise that every operation succeeds: invalid configuration,
failed sign-in, invalid snapshots, or a wholly inaccessible workspace can still
produce a request error or a visible audit access error.

### Delegated Fabric scopes

The default custom-app scope list in
[auth_service.py](../backend/src/auditfast/services/auth_service.py) contains
**five** scopes. Each name below is prefixed with
`https://api.fabric.microsoft.com/`.

| Scope | Why it is requested |
|---|---|
| `Workspace.Read.All` | Workspace metadata/discovery |
| `Item.ReadWrite.All` | Item definitions through the read-only `getDefinition` operation |
| `OneLake.Read.All` | OneLake-related Fabric reads, including shortcuts |
| `Connection.Read.All` | Cloud connection metadata |
| `Gateway.Read.All` | On-premises gateway metadata |

The project can override the scope list. The built-in Azure CLI public-client
path uses Fabric `.default` instead of requesting this list explicitly.
Effective permissions still depend on the token, consent, tenant policy, and
the signed-in user's access.

> The `ReadWrite` scope does not mean the auditor modifies items. It is used for
> definition reads. A 401 is not proof of one specific missing scope: also check
> expiry, token audience, consent, and the selected sign-in flow.

Additional reads need **separate audience tokens**, not more scopes on the same
Fabric token:

| Audience | Used for |
|---|---|
| `https://analysis.windows.net/powerbi/api` | Power BI metadata, refresh information, FabricIQ tools |
| `https://database.windows.net` | SQL analytics endpoint |
| `https://storage.azure.com` | OneLake ADLS Gen2 file listings |

The web audit runner attempts to obtain these from the signed-in session.
Acquisition can fail; the affected coverage then remains unavailable. A raw
Fabric token alone is not sufficient for these other services. Adapter scores
are comparable for the **same snapshots, settings, and answers**; different
token capabilities can produce different available evidence.

### Sign-in options

1. **Azure CLI reuse:** run `az login` on the **backend machine**, then choose
   *Reuse my Azure CLI session*. A login on another machine is not available to
   a remote API.
2. **Local interactive browser:** opens sign-in on the machine running the API.
   Suitable for local development, not remote browser redirection.
3. **Device code:** available for headless use; the `run` and `twin` CLI commands
   use this flow.
4. **Own public-client Entra app:** configure `auth.tenant_id`, `auth.client_id`,
   and delegated `auth.scopes` in a copied project YAML. Use it when required by
   tenant policy or when the built-in client's effective permissions are insufficient.
5. **Hosted redirect sign-in:** register the frontend callback
   `<frontend-origin>/auth/callback`, then configure `AUDITFAST_AUTH_CLIENT_ID`
   and `AUDITFAST_AUTH_TENANT_ID`. Configure `AUDITFAST_AUTH_CLIENT_SECRET` for a
   confidential web app. The redirect URI must exactly match the registration;
   obtain any consent required by your tenant.

Fabric access tokens stay on the backend in the web sign-in flow; the browser
uses an opaque session id. Sessions are process-local and do not survive a
backend restart. MCP live tools instead accept an explicit token from their
caller; do not paste tokens into tracked configuration or logs.

## 4. Network requirements

These are runtime destinations; package installation and optional model
downloads additionally need access to the relevant package/model repositories.

| Destination | Port | Needed for |
|---|---|---|
| `login.microsoftonline.com` and the Microsoft sign-in pages used by your flow | HTTPS 443 | Live Microsoft sign-in |
| `api.fabric.microsoft.com` | HTTPS 443 | Fabric REST reads and remote FabricIQ MCP |
| `api.powerbi.com` | HTTPS 443 | Power BI reads/query tools |
| `onelake.dfs.fabric.microsoft.com` | HTTPS 443 | OneLake file-layout evidence |
| The discovered `*.datawarehouse.fabric.microsoft.com` SQL endpoint | TCP 1433 | Live SQL metadata reads |
| Your approved AI provider/gateway | Usually HTTPS 443; use its configured endpoint | Advisory judging and custom-check AI stages |

Default local ports are **8000** for the API and **5173** for Vite. Explicit
saved-KB replay does not need Microsoft network access. Optional AI actions can
still make model calls; leave AI off if the workflow must stay fully offline.

Model calls can include checklist text, code, and selected workspace evidence.
Use only an endpoint approved for that data; do not enable AI for confidential
engagement data without the appropriate approval.

With usable process-configured AI enabled, the standard audit's advisory stage
can call the model automatically, including after saved-KB evaluation. The
separately requested advisory-judging action is not the only model-call path.
Snapshot replay guarantees no **Fabric** fallback, not a global block on AI.

## 5. Dependencies and optional extras

The installable package is defined by
[backend/pyproject.toml](../backend/pyproject.toml), not a requirements-text file.

| Runtime dependency | Minimum | Purpose |
|---|---|---|
| `fastapi` | 0.115 | REST API |
| `uvicorn[standard]` | 0.30 | ASGI server |
| `pydantic` | 2.7 | Data validation |
| `pydantic-settings` | 2.3 | Configuration |
| `msal` | 1.28 | Microsoft sign-in |
| `requests` | 2.31 | HTTP clients |
| `pyodbc` | 5.1 | SQL endpoint client |
| `openpyxl` | 3.1 | Excel reports |
| `PyYAML` | 6.0 | Project/remediation configuration |
| `rich` | 13.7 | Console output |

| Extra | Packages | When to install |
|---|---|---|
| `dev` | `pytest>=8`, `pytest-asyncio>=0.23`, `httpx>=0.27`, `ruff>=0.6` | Tests and backend lint |
| `mcp` | `mcp>=1.2,<2` | Local MCP adapter; uses the 1.x `FastMCP` API |
| `ai` | `openai>=1.40`, `tiktoken>=0.7` | Optional model-backed features |
| `guardrails` | `guardrails-ai>=0.5` | Additional custom-input validation |
| `qdrant` | `qdrant-client>=1.7` | Persistent local vector storage |
| `graph` | `langgraph>=0.2` | Optional programmatic graph wrapper; not the default API execution path |
| `db` | `sqlalchemy>=2`, `alembic>=1.13`, `asyncpg>=0.29` | Reserved persistence dependencies; not wired into the running API |

`pyodbc` is always installed, but the **system ODBC driver is separate**.
SQL coverage also requires port 1433, a SQL-audience token, and SQL access.
Driver installation alone does not establish that coverage.

Optional embedding support imports **`fastembed`**, which is **not included in
any declared extra**. Install it separately if you want local semantic matching.
The configured default model is `BAAI/bge-small-en-v1.5`; arrange its initial
download/cache when needed. Embeddings run only when AI is enabled and the
runtime/model is available; otherwise matching falls back to deterministic
matching. Qdrant alone does not install/enable those embeddings.

The `guardrails` extra installs the base package, not every validator. Optional
validators are separate PyPI packages (for example, `guardrails-ai-detect-pii`).
The built-in deterministic input checks remain active when these are absent;
optional validation must not be treated as a security guarantee.

Frontend dependencies are React/React DOM 18, React Router 6, and Axios 1;
development uses Vite 5, TypeScript 5.6, Tailwind 3.4, PostCSS, Autoprefixer,
and the React/Node type packages. See
[frontend/package.json](../frontend/package.json).

## 6. Installation

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"

# Only if you want the web UI
Push-Location .\frontend
npm install
Pop-Location
```

For execution without development tools, install `-e backend` instead.
Install only the optional features you need; extras can be combined:

```powershell
# Example: AI features and the local MCP server, plus development tools
.\.venv\Scripts\python.exe -m pip install -e "backend[dev,ai,mcp]"

# Optional semantic matching and persistent local vector storage
.\.venv\Scripts\python.exe -m pip install fastembed
.\.venv\Scripts\python.exe -m pip install -e "backend[qdrant]"
```

Do not overwrite an existing local configuration when upgrading. For a first
setup, copy and edit the project example:

```powershell
Copy-Item .\backend\config\project.example.yaml .\backend\config\my-project.yaml
```

For optional environment settings, copy
[backend/.env.example](../backend/.env.example) to `backend\.env` and edit it.
That example is not an exhaustive settings reference; see section 10.

## 7. Project configuration

Use real workspace GUIDs and assign their layer roles. A minimal project:

```yaml
project:
  name: "Sales Analytics"
  client: "Contoso"
  naming_convention: '^[A-Za-z]+-(Dev|Test|Prod)-[A-Za-z]+$'
  pipeline_naming_convention: '^PL_[A-Za-z0-9_]+$'
  orphan_days: 90
  max_admins: 2
  minimum_spark_version: "3.5"

remediation: "config/remediation.yaml"

workspaces:
  - id: "<workspace-guid>"
    role: "Data Prep"
```

Valid layer roles: `Data Prep`, `Data Storage`, `Data Logs`, `Data Operations`,
`Reporting / Semantic`, and `Mixed`.

The [project example](../backend/config/project.example.yaml) also includes
Spark efficiency/idle/shuffle/runtime thresholds and elevated inputs.

Workspace-Admin inputs: `production_workspaces`, `developer_groups`,
`operations_groups`, `report_consumer_groups`, `app_access_reviewed`,
`gateway_sizing_confirmed`, and `code_scan_clean`.

Capacity inputs: `capacity_metrics_workspace`, `capacity_metrics_model`,
`capacity_peak_start_hour`, `capacity_peak_end_hour`, `metrics_app_monitored`,
`analysis_documented`, and `throttling_tracked`.

Keep attestations false until actually verified. The capacity settings are not
all attestations: the first four locate and interpret the Capacity Metrics app.
`capacity_metrics_workspace` only orders the search, but
`capacity_metrics_model` is a **filter** matched against the semantic model's
name, so a value matching nothing reports the app as not deployed even when it
is installed under another name. The peak/off-peak hours are **UTC**, matching
the times in the metrics model, and must be shifted for a client on another
clock; the window may wrap past midnight.

For a custom sign-in app, add the `auth` block from that example and replace
its placeholders. Local project YAML files are ignored by Git except the
provided examples, but verify the ignore rules for any new location.

Cross-workspace grouping and `environment_level` (1-10) are supported on API/UI
workspace selections. `weight_by_environment` defaults to false. Do not assume
that putting these fields into YAML enables them: the project loader builds
workspace targets from **id and role**, while grouping/weighting is carried by
the audit request.

External CSV imports are another explicit scoring input. The current full-audit
service **appends** their results; it does not replace matching built-in
findings despite older schema text. Avoid importing duplicate observations if
you do not intend them to contribute twice.

For reproducibility, also read the
[questionnaire and weighting caveats](scoring.md#questionnaire-and-external-input-caveats).
For example, a run without accepted answers can omit questionnaire result rows,
and merged answer rows use base rather than environment-adjusted weights.

## 8. Run the application

Use separate terminals, each initially at the repository root.

**Terminal 1 - API:**

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m auditfast serve --project .\config\my-project.yaml --port 8000
Pop-Location
```

**Terminal 2 - frontend:**

```powershell
Push-Location .\frontend
npm run dev
Pop-Location
```

Open **http://localhost:5173**. The API does not hot-reload by default; restart
after backend changes or pass `--reload` during development. A restart also
discards in-memory sign-in sessions and API job history.

| URL | Purpose |
|---|---|
| http://localhost:5173 | Web application |
| http://127.0.0.1:8000/docs | Generated API reference |
| http://127.0.0.1:8000/api/v1/health | Process health and standard registry count |
| http://127.0.0.1:8000/api/v1/health/live | Liveness, HTTP 204 |
| http://127.0.0.1:8000/api/v1/health/ready | Readiness response |
| http://127.0.0.1:8000/api/v1/catalog/summary | Current standard catalog coverage |

For CLI use, from the repository root:

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m auditfast checks --pillar "Security & Access Control"
..\.venv\Scripts\python.exe -m auditfast run --project .\config\my-project.yaml
Pop-Location
```

Current commands, from [cli.py](../backend/src/auditfast/cli.py):

| Command | Purpose | Key inputs |
|---|---|---|
| `run` | Device-code sign-in, live audit, reports | `--project` required; `--out`, `--pillars`, `--external-checks` |
| `serve` | API server | `--project`, `--host`, `--port`, `--reload` |
| `checks` | Catalog | `--pillar`, `--layer`, `--scope` |
| `twin` | Build/persist a workspace graph | `--project` and `--workspace` required; `--layer`, `--store` |
| `checklist` | Assess a file; optionally run matches over the cache | Positional `.csv/.json/.md/.txt` file; `--workspaces`, `--no-run`, `--out` |
| `advisory-apply` | Apply externally judged verdicts | `--verdicts` required; `--bundle`, `--out`, `--no-report` |
| `advisory-score` | Score object labels against judging jobs | `--run`, `--jobs`, `--labels`, `--out`, `--no-report` |

There are no standalone `advisory` or `score` subcommands. Use `--help` for
details. The `run` CLI does not expose the API's saved-KB or elevated-mode flags;
use the API/UI for those workflows.

### Saved-KB replay

Choose saved/uploaded KB data in the audit UI, or submit `POST /api/v1/audit`
with `source: "kb"`. Select workspaces present in the archive or include their
JSON objects in `snapshots`. Both raw archived workspace contexts and TTL-cache
files wrapped in `context` are accepted; the limit is **100 uploaded snapshots
per request**.

No auth session is required and missing snapshots do not trigger live fetching.
The result describes the captured data, not the current tenant. Saved-archive
replay and the custom-check/batch-checklist workflows are different: the latter
read the standard TTL cache, so an archived snapshot is not automatically
available to every workflow.

### MCP

After installing the `mcp` extra:

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m auditfast.mcp.server
Pop-Location
```

This is a **stdio** MCP server, intended to be launched by an MCP client, not a
second HTTP API. The supplied [VS Code config](../.vscode/mcp.json) and
[MCP config](../.mcp.json) currently launch `python`; ensure that command selects
the interpreter where `auditfast[mcp]` is installed, or configure the venv
interpreter explicitly. The remote FabricIQ server is a separate entry.

## 9. Verify the installation

Do not use a historical hard-coded check total as an installation requirement.
The loaded catalog and the current tests are authoritative.

**Registry and CLI**, from the repository root:

```powershell
.\.venv\Scripts\python.exe -c "from collections import Counter; from auditfast.core.check.registry import REGISTRY, ADMIN_REGISTRY, GROUP_REGISTRY; print('standard:', len(REGISTRY)); print(dict(Counter(s.automation.value for s in REGISTRY))); print('elevated:', len(ADMIN_REGISTRY), 'group:', len(GROUP_REGISTRY))"
.\.venv\Scripts\python.exe -m auditfast --help
```

Expect a nonempty standard registry, with automated and interactive checks.
Elevated/group counts are separate and are not included in health's
`checks_registered`. Import errors need fixing even if some checks load.

**Existing backend tests and lint**, from the repository root:

```powershell
Push-Location .\backend
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check src
Pop-Location
```

Use the current test summary and inspect failures/skips; the old
`189 passed, 7 skipped` figure is not an acceptance criterion. The tests use
offline fixtures/mocks rather than requiring a signed-in tenant.

**Frontend build**, from the repository root:

```powershell
Push-Location .\frontend
npm run build
Pop-Location
```

Expect TypeScript checking and Vite build to finish successfully. The declared
frontend `lint` script references ESLint, but ESLint is not currently declared
in the package manifest; it is not a reliable fresh-install verification step.

**Health**, with the API running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Expect `status: "ok"` and a nonzero `checks_registered`. The implementation
reports `degraded` when the standard registry is **empty**; it does not prove
every module loaded, nor does it test Fabric, SQL, or AI connectivity.

**Optional SQL driver check:**

```powershell
.\.venv\Scripts\python.exe -c "import pyodbc; print(pyodbc.drivers())"
```

Expect an installed SQL Server driver. Then verify actual SQL-backed coverage
in a live audit; driver presence alone is not an end-to-end test.

## 10. Configuration reference

[settings.py](../backend/src/auditfast/config/settings.py) is authoritative.
Settings are read at startup from the environment and a `.env` in the
**process working directory**. With the commands above that is `backend\.env`.
Configured relative application paths resolve against the backend root.
CLI arguments such as `--project`, `--out`, and `--store` use the CLI working
directory instead.

### Core and caches

| Variable | Default | Purpose |
|---|---|---|
| `AUDITFAST_DEFAULT_PROJECT` | `config/project.example.yaml` | Default project |
| `AUDITFAST_OUTPUT_DIR` | `output` | API report root |
| `AUDITFAST_ENVIRONMENT` | `local` | Environment label, not a deployment-security switch |
| `AUDITFAST_LOG_LEVEL` | `INFO` | Log verbosity |
| `AUDITFAST_LOG_JSON` | `false` | Structured logging |
| `AUDITFAST_CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | Allowed browser origins |
| `AUDITFAST_FABRIC_API_TIMEOUT_SECONDS` | `180` | Service-created provider HTTP / definition-poll timeout |
| `AUDITFAST_SQL_ENDPOINT_ENABLED` | `true` | Attempt SQL metadata reads when a SQL token is available |
| `AUDITFAST_CACHE_ENABLED` | `true` | Enable live-run cache reuse |
| `AUDITFAST_CACHE_DIR` | `kb-cache` | Standard workspace cache |
| `AUDITFAST_ADMIN_CACHE_DIR` | `kb-cache-admin` | Separate elevated cache |
| `AUDITFAST_CACHE_TTL_SECONDS` | `86400` | Hard TTL |
| `AUDITFAST_CACHE_SOFT_SECONDS` | `3600` | Threshold for scheduling provider-level refresh |
| `AUDITFAST_CACHE_BACKGROUND_REFRESH` | `true` | Provider-level background refresh |
| `AUDITFAST_KB_ARCHIVE_ENABLED` | `true` | Archive standard live/cache-served runs |
| `AUDITFAST_KB_ARCHIVE_DIR` | `Fabric workspace kb` | Permanent standard archive / saved-KB replay source |

Live workspace and item-definition reads are already parallelized and retry
transient failures with bounded waits that honor `Retry-After`. Three controls
are read **directly from the process environment at module import**, not from
the Settings `.env` loader:

| Variable | Default | Accepted range |
|---|---|---|
| `AUDITFAST_MAX_PARALLEL_WORKSPACES` | `8` | 1-8 workspace fetches across the process |
| `AUDITFAST_MAX_PARALLEL_ITEM_FETCHES` | `4` | 1-16 workers per workspace |
| `AUDITFAST_MAX_INFLIGHT_ITEM_FETCHES` | `32` | 1-32 concurrent definition reads across the process |

Set them before starting the backend, for example:

```powershell
$env:AUDITFAST_MAX_PARALLEL_WORKSPACES = "8"
$env:AUDITFAST_MAX_PARALLEL_ITEM_FETCHES = "4"
$env:AUDITFAST_MAX_INFLIGHT_ITEM_FETCHES = "32"
```

The direct `LiveFabricProvider` constructor still defaults to 60 seconds
(including callers such as `twin` that do not override it); ordinary audit
services pass the configured **180-second** default. Neither value is a
whole-audit timeout.

### Hosted sign-in

| Variable | Default | Purpose |
|---|---|---|
| `AUDITFAST_AUTH_CLIENT_ID` | Unset | Redirect-flow app id |
| `AUDITFAST_AUTH_TENANT_ID` | Unset | Tenant id / supported tenant authority |
| `AUDITFAST_AUTH_CLIENT_SECRET` | Unset | Confidential-client secret; server-side |

### AI and custom checks

| Variable | Default | Purpose |
|---|---|---|
| `AUDITFAST_AI_ENABLED` | `false` | Enable process-configured AI |
| `AUDITFAST_AI_PROVIDER` | `azure` | `azure` or `openai` |
| `AUDITFAST_AZURE_OPENAI_ENDPOINT` | Unset | Azure endpoint |
| `AUDITFAST_AZURE_OPENAI_DEPLOYMENT` | Unset | Azure deployment |
| `AUDITFAST_OPENAI_BASE_URL` | Unset | OpenAI-compatible gateway |
| `AUDITFAST_OPENAI_API_KEY` | Unset | OpenAI-compatible provider key |
| `AUDITFAST_OPENAI_MODEL` | Unset | Gateway model |
| `AUDITFAST_AI_REQUEST_TIMEOUT_SECONDS` | `30` | Per-model-call timeout |
| `AUDITFAST_GUARDRAIL_MAX_PROMPT_CHARS` | `2000` | Per-prompt input limit |
| `AUDITFAST_CUSTOM_CHECKS_ARCHIVE_ENABLED` | `true` | Archive generated code, KB, and fetch records |
| `AUDITFAST_CUSTOM_CHECKS_ARCHIVE_DIR` | `custom-checks-runs` | Custom-check archive root |
| `AUDITFAST_CUSTOM_CHECKS_MEMORY_ENABLED` | `true` | Persist code reuse and decisions |
| `AUDITFAST_CUSTOM_CHECKS_MEMORY_FILE` | `custom-checks-runs/memory.json` | Cross-run memory |
| `AUDITFAST_CUSTOM_CHECKS_LIVE_FETCH_ENABLED` | `false` | Allow additional Fabric reads with an authenticated live provider |
| `AUDITFAST_CUSTOM_CHECKS_EXECUTE_FETCH_CODE` | `false` | Use validated AI-generated fetch code rather than the curated endpoint provider |
| `AUDITFAST_CUSTOM_CHECKS_LIVE_FETCH_MAX_CALLS` | `20` | Live-fetch call budget |
| `AUDITFAST_CUSTOM_CHECKS_LIVE_FETCH_MAX_BYTES` | `2000000` | Per-response serialized size cap |
| `AUDITFAST_CUSTOM_CHECKS_MAX_MEMORY_MB` | `0` | Optional generated-check allocation ceiling; 0 disables it |

For process-configured Azure OpenAI, the SDK uses its own credentials, commonly
the **unprefixed process environment variable `AZURE_OPENAI_API_KEY`**.
`AUDITFAST_OPENAI_API_KEY` is for the OpenAI-compatible provider, not that Azure
path. Merely adding the unprefixed key to the Settings `.env` file does not
export it into the SDK's process environment.

Custom checks and advisory judging also support **per-request AI credentials**
from the UI/API. A usable per-request configuration takes precedence and can
enable a model call even when `AUDITFAST_AI_ENABLED=false`. Therefore that flag
is the default for process-configured AI, not a global prohibition on AI calls.
The `ai` extra is still required. The custom-check AI verification endpoint
makes a small model request; it is not an offline format check.

Custom checks evaluate generated code for preview before report approval.
Approval controls inclusion in their final report; it is **not** a gate before
every generated-code execution. New code generation needs usable AI, while
previously generated code may be reused from memory.

When memory holds approved checks, standard API audit finalization can also
re-evaluate them against the standard TTL cache and attach a separate custom
section to JSON/UI and an Excel `Custom Checks` sheet, without a new model call.
That cached evidence is distinct from any uploaded snapshots used by saved-KB
replay. The custom section's 0-100 scores do not enter the main aggregate.

Live custom-check fetching requires both its enable flag and a signed-in
provider. With `EXECUTE_FETCH_CODE=false`, enabled live reads use curated
endpoints. With it true, generated fetch code is the sole live path; failure
does not silently fall back to those endpoints.

### Optional semantic matching

| Variable | Default | Purpose |
|---|---|---|
| `AUDITFAST_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local FastEmbed model |
| `AUDITFAST_VECTOR_STORE_BACKEND` | `memory` | `memory` or local `qdrant` |
| `AUDITFAST_VECTOR_STORE_DIR` | `kb-cache/vector_store` | Persistent local Qdrant data |
| `AUDITFAST_ROUTER_REUSE_THRESHOLD` | `0.45` | Deterministic reuse threshold |
| `AUDITFAST_ROUTER_RETRIEVE_THRESHOLD` | `0.70` | Semantic candidate threshold |
| `AUDITFAST_ROUTER_SEMANTIC_THRESHOLD` | `0.85` | Semantic duplicate threshold |
| `AUDITFAST_ROUTER_TOP_K` | `5` | Candidate count |
| `AUDITFAST_KB_IDENTIFIER_MIN_CONFIDENCE` | `0.30` | Low-confidence KB-field flag |

Qdrant runs in local mode; no Qdrant server/token is required. The `graph` extra
provides an optional LangGraph wrapper for code-level use, not an environment
switch that replaces the default pipeline.

### Frontend

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | Empty | Build-time API origin; empty uses same-origin `/api` |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite development proxy target |

See [frontend/.env.example](../frontend/.env.example). The current
[Vite config](../frontend/vite.config.ts) reads the proxy target from
`process.env`, so set it in the frontend terminal before `npm run dev`:

```powershell
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:8001"
```

Rebuild the frontend after changing its production API base URL. Never put
secrets in `VITE_` variables; those values are client-visible.

## 11. Coverage when optional prerequisites are absent

| Missing prerequisite | Effect |
|---|---|
| Fabric sign-in / live network access | Live audit cannot start; catalog and saved-KB workflows remain usable |
| Item-definition access | Affected code-based checks have unavailable evidence / N/A |
| SQL driver, SQL token, SQL permission, or port 1433 | SQL-dependent schemas, keys, constraints, RLS and other metadata coverage unavailable |
| Power BI-audience token | Power BI-only evidence such as refresh information unavailable |
| Storage-audience token / OneLake access | OneLake file-layout evidence unavailable |
| Elevated privileges or reviewer inputs | Affected elevated checks cannot assess the missing evidence |
| AI configuration / `ai` extra | No new model-generated evaluations; deterministic audits still work |
| FastEmbed/runtime/model | Deterministic matching fallback instead of semantic embeddings |
| Saved workspace snapshot | No evidence for offline evaluation; KB replay never fetches it live |

## 12. Disk, reports, and deployment boundaries

Default paths below are under the backend root unless noted.

| Path | Contents / behavior |
|---|---|
| `output/<workspace-or-project>_<timestamp>/` | Per-audit reports: Markdown, Excel, HTML; advisory outputs and judging artifacts when produced |
| `kb-cache/` | Latest standard workspace snapshots |
| `kb-cache-admin/` | Elevated snapshots with resource-coverage metadata; not interchangeable with the standard cache |
| `Fabric workspace kb/<workspace>/<workspace>_<timestamp>/` | Permanent standard workspace snapshot and summary |
| `custom-checks-runs/` | Timestamped generated code, updated KB, fetch records, and cross-run memory |
| `kb-cache/vector_store/` | Optional local Qdrant store |
| `twins/` | CLI default graph store, relative to its working directory; use `--store` to choose a protected location |

Audit reports use per-run directories, not shared fixed files. The current
Excel format includes `Summary`, `Area Detail`, `Checklist`, `Findings`,
`Risk Register`, `Invent`, and per-workspace object-detail sheets.
Advisory/judged-advisory formats are also downloadable when generated.

The batch `checklist` CLI is different: it writes `checklist-report.md` and
`checklist-report.json` directly into `--out`. Use a separate output directory
for each batch if you need to retain previous results.

Important operational limits:

- Live standard cache reuse requires a complete snapshot within the hard TTL.
  Incomplete snapshots **can be saved**, but are not reused as complete live
  cache hits. Explicit KB replay can still assess captured partial evidence.
- A cache-served API audit can be re-run live in the background and its stored
  report updated. `CACHE_BACKGROUND_REFRESH` controls the provider's soft-TTL
  refresh; it does not disable the runner's separate post-cache refresh path.
- Elevated live crawls fetch the selected resources into their own cache;
  they are not written into the full standard workspace archive.
- Snapshots and reports survive on disk, but **API job/history records and
  sign-in sessions are in memory**. Restarting does not automatically rebuild
  the API history from saved reports. The `db` extra / `DATABASE_URL` setting
  does not change this.
- Use a single API process/replica for local operation. In-flight work, sessions,
  caches, and job storage are not a distributed deployment solution.
- Reports and KB/custom archives have no general automatic retention policy.
  Size depends on workspace definitions and run volume; budget disk and set an
  engagement-appropriate retention/access policy.
- Default report/cache/archive directories are Git-ignored. The default
  `twins` directory has no dedicated ignore rule; check ignore coverage before
  saving tenant data there or using any custom output location.
- Fabric sign-in is not service-level authorization. Do not expose the API
  publicly merely by changing `--host` or CORS; hosted use needs appropriate
  authentication/authorization and transport/storage controls.

See [report downloads](../backend/src/auditfast/api/v1/reports.py),
[cache providers](../backend/src/auditfast/services/context_store.py),
[application startup](../backend/src/auditfast/main.py), and
[ignore rules](../.gitignore).

## 13. Troubleshooting

| Symptom | What to check |
|---|---|
| Python opens the Microsoft Store / is not found | Install Python, reopen the terminal, and use the venv interpreter |
| `ModuleNotFoundError: auditfast` | Install with `.\.venv\Scripts\python.exe -m pip install -e "backend[dev]"` from the repository root |
| Empty registry / health `degraded` | Run the registry import command directly and inspect errors; health is not a full catalog-integrity check |
| UI shows API unreachable | Start the API; confirm port and frontend proxy environment |
| Port 8000 already in use | Select another `--port`; set the frontend proxy to match |
| TypeScript cannot find Node types | Restore frontend dependencies with `npm install` in the frontend directory |
| Live audit returns 401 | Sign in again; verify audience, expiry, consent, and API session |
| Definition/resource reads return 401/403 | Check effective scopes **and** item/workspace/resource permissions; use Troubleshoot access |
| Azure CLI reuse fails | Run `az login` on the backend machine under the account running the API |
| SQL-backed checks are N/A | Check driver, SQL token, SQL access, endpoint availability, and outbound 1433 |
| KB replay cannot find a workspace | Select an archived workspace or upload its valid snapshot; a cache-only file can be uploaded explicitly |
| Custom checks have no workspaces/evidence | Populate the standard KB cache; an archive-only replay does not populate it automatically |
| New custom code remains pending / AI unavailable | Install the `ai` extra and provide usable model credentials; inspect AI verification diagnostics |
| Semantic matching is absent | Check `fastembed`, local model availability, and AI activation; Qdrant alone is insufficient |
| Setting changes have no effect | Restart the API; verify `.env` location and the directly-read process variables |
| API history disappears after restart | Expected with the current in-memory repository; saved files remain on disk |
| First crawl is slow | Definitions are bounded-parallel, not sequential; item count, polling, throttling, and SQL reads still take time |

## 14. Read next

These documents provide more detail. For a different checkout or running
deployment, confirm its behavior against the linked implementation and catalog.

| Document | Purpose |
|---|---|
| [README](../README.md) | Overview and quick setup |
| [Getting started](getting-started.md) | Setup and sign-in |
| [How to use](how-to-use.md) | UI walkthrough |
| [Managing checks](managing-checks.md) | Check management and authoring |
| [Custom-check flow](custom-checks-flow.md) | Custom-check workflow |
| [Advisory judging](advisory-judging.md) | Judged advisory reports |
| [Architecture](architecture.md) | Domain and service design |
| [Checks](checks.md) | Check conventions |
| [Scoring](scoring.md) | Scoring rules |
| [API reference](api.md) | API concepts; generated Swagger is the endpoint source of truth |
| [AGENTS](../AGENTS.md) | Engineer/agent orientation |

This guide is maintained with the repository documentation. Keep engagement
snapshots, credentials, and client-specific configuration outside tracked docs.
