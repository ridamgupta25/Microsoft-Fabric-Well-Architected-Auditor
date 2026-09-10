# Standalone Fabric KB Crawlers

Optional, client-run utilities for engagements where the customer cannot grant
the auditor workspace access but can collect and approve evidence themselves.
This is **separate from the AuditFAST application**: it is not installed with the
backend, does not start a server, and does not execute checks or call AI.

The customer signs in locally, selects workspaces, and exports JSON snapshots.
The auditor then runs standard or elevated checks through the application's
existing saved-KB service. The scripts themselves never score checks.

## Choose the right script

| Script | Matching check family | Default output parent |
|---|---|---|
| [kb_crawl_cli.py](kb_crawl_cli.py) | Standard workspace checks | `fabric-kb` |
| [admin_kb_crawl_cli.py](admin_kb_crawl_cli.py) | **Workspace Admin**, not all admin families | `fabric-admin-kb` |
| [Capacity_kb_crawl_cli.py](Capacity_kb_crawl_cli.py) | **Capacity** | `fabric-capacity-kb` |
| [Tenant_kb_crawl_cli.py](Tenant_kb_crawl_cli.py) | **Tenant** | `fabric-tenant-kb` |

The elevated profiles follow the tool's three category-specific resource sets:

- **Workspace Admin:** workspace role assignments, account-visible connections
  and gateways/members, and Lakehouse OneLake data-access roles.
- **Capacity:** the normalized Capacity Metrics semantic-model observations.
- **Tenant:** tenant settings, domain/workspace membership, the preceding 14
  complete UTC days of activity events, and per-workspace scanner endorsement
  metadata.

Every profile reads workspace identity. Workspace Admin and Tenant also read
item inventory for their per-item evidence. Elevated profiles do **not** crawl
notebook/pipeline definitions, SQL endpoints, or OneLake Files listings.

## What to share

You can share this entire `kb-crawler` folder, containing the four Python files
and this README. The customer chooses which script to run; opening the folder
does not run any of them. Alternatively, share only the appropriate script and
this README:

- Each Python file is complete and independently runnable.
- [README.md](README.md): these instructions.

The customer does not need the repository, backend, frontend, or any build files.
None of the scripts imports another crawler file. Each includes `--self-test`.
Do not include your `.venv`, previous crawl outputs, credentials, or local test
data in the folder you send.

## Requirements

Use an approved machine with:

- Python 3.10 or newer.
- `requests`, `PyYAML`, and `msal` for browser/device-code sign-in.
- Access to the workspaces being collected and the relevant Microsoft endpoints.
- Writable local storage for exports.

Azure CLI is an alternative to MSAL sign-in. For SQL metadata, also install
`pyodbc` and Microsoft ODBC Driver 18, and allow SQL-audience authentication and
TCP 1433 connectivity. Missing SQL prerequisites reduce evidence coverage.
SQL prerequisites apply only to the standard script; the elevated scripts do
not request SQL or Storage-audience tokens.
Do not bypass organizational installation or execution policies.

Elevated permissions are not granted by choosing a script:

- **Workspace Admin:** Member/Admin on selected workspaces, appropriate access
  to each connection/gateway, and the required `Connection.Read.All`,
  `Gateway.Read.All`, and `OneLake.Read.All` permissions.
- **Tenant:** Fabric tenant administrator and the delegated permissions/tenant
  settings allowing the admin settings, domains, activity and scanner APIs.
- **Capacity:** access to the installed Metrics app and Read/Build permission
  for its model's `executeQueries` API, with applicable tenant policy. Capacity
  administrator status alone does not grant model access.

All three use the same production workspace identity read as the tool: the
caller must also be able to read each selected workspace. A tenant/capacity
admin who cannot read that workspace is not silently granted access. An API
refusal remains missing evidence or a visible workspace failure.

## Quick start: shared folder and a fresh VS Code

These steps assume the customer has **only the shared folder**, not the
AuditFAST repository. VS Code is an editor; it does **not** include Python.
The VS Code Python extension is optional for these terminal commands. Git,
Node.js, an AuditFAST installation, and a running API are not needed to crawl.

### 1. Extract and open the folder

1. If the folder arrived as a ZIP, extract it to an approved writable location,
   for example `C:\Fabric-KB\kb-crawler`. Do not run from inside the ZIP.
2. In VS Code, choose **File > Open Folder** and select the extracted
   `kb-crawler` folder itself.
3. Choose **Terminal > New Terminal**. Select **PowerShell** as the terminal
   profile if necessary.
4. Verify that the terminal is in the folder containing the scripts:

```powershell
Get-ChildItem -File -Filter "*.py"
```

You should see the four script names from the table above. All customer commands
below run from **this folder**; do not add an `extras\kb-crawler` prefix.

### 2. Check or install approved Python

```powershell
python --version
```

The version must be **3.10 or newer**. If Python is missing, the command opens
the Microsoft Store, or an older version is shown, arrange an approved Python
installation through your organization's IT/software center. Make sure the
approved interpreter is available in the terminal, then restart VS Code and
check again. Installing the VS Code Python extension alone is not sufficient.

If your installation provides `py` instead of `python`, check `py -3 --version`
and use `py -3` in place of `python` consistently for installation and execution.

### 3. Install dependencies using the same Python

A virtual environment is **optional**, not a prerequisite. Use your approved
working `python` command. If these dependencies are not already installed:

```powershell
python -m pip install requests PyYAML msal
```

Using `python -m pip` keeps package installation tied to the same interpreter
used to run the crawler. Do not install with an unrelated `pip` command or
switch between system Python and a `.venv` halfway through setup.

For the **standard** crawler's SQL evidence, also arrange Microsoft ODBC Driver
18, the required SQL access/network connectivity, and:

```powershell
python -m pip install pyodbc
```

SQL support is not needed for the three elevated crawlers. If installation,
package downloads, or execution is blocked by policy, ask IT for an approved
environment or package source; do not bypass the restriction.

### 4. Choose one script and run its self-test

Set the filename for the desired row in the table. This example chooses the
standard crawler; change the value for Workspace Admin, Capacity, or Tenant:

```powershell
$crawler = ".\kb_crawl_cli.py"
python $crawler --help
python $crawler --self-test
```

`--help` works with only the standard library. `--self-test` uses synthetic
evidence and temporary files, not a real tenant. Its simulated 403, partial,
failure, and cancellation messages are expected; look for `SELF-TEST PASSED`.
The temporary self-test exports are deleted automatically.
These customer commands deliberately omit Python's `-I` isolation flag, which
would hide per-user packages that a normal `python` invocation can use.

### 5. Sign in and collect real evidence

In the same terminal, using the script selected above:

```powershell
python $crawler --auth browser
$LASTEXITCODE
```

Complete Microsoft sign-in with your own account, check the displayed tenant,
select the workspace numbers, and enter `y` to confirm. Start with one workspace.
The Capacity script also asks for its Metrics app and UTC working-hour inputs.
Use only a profile for which you hold the required permissions.

### 6. Review and return the output

The script prints the output path. Under this opened folder, look in the
appropriate default parent from the table (`fabric-kb`, `fabric-admin-kb`,
`fabric-capacity-kb`, or `fabric-tenant-kb`).

Review the selected timestamped run directory, including its workspace JSON
files and `collection-summary.json`, for missing evidence and sensitive content.
After approval, send **that run directory** through your approved transfer
channel. Do not send `.venv`, credentials, tokens, device codes, or unrelated
exports. The auditor performs the checks on their own machine afterward.

On later runs, reopen this same folder and run the chosen script again; no
environment recreation or dependency reinstall is normally needed.

### Optional: use a virtual environment

If your organization permits or requires an isolated environment, create one
locally and use its interpreter for **both** package installation and execution:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install requests PyYAML msal
$crawler = ".\kb_crawl_cli.py"
.\.venv\Scripts\python.exe $crawler --self-test
.\.venv\Scripts\python.exe $crawler --auth browser
```

Choose the appropriate script filename as before. The explicit `.venv` path
works only if that environment exists in the current folder and is usable.
Do not copy a virtual environment from another machine. No `Activate.ps1` or
execution-policy change is needed. If your approved `python` command already
works, there is no need to create or use `.venv`.

### Common setup issues

| Symptom | Action |
|---|---|
| `python` is not recognized or opens the Store | Arrange approved Python 3.10+, restart VS Code, and check the terminal command again; use `py -3` if that is your approved launcher |
| `can't open file` | Open the actual extracted folder and confirm the chosen script appears in `Get-ChildItem -File -Filter "*.py"` |
| Missing Python module | Use `python -m pip install requests PyYAML msal` with the same `python` that runs the script; use the `.venv` interpreter for both commands only if you chose the optional virtual environment |
| `.venv\Scripts\python.exe` fails but `python` works | These target potentially different environments. Use the working approved interpreter consistently; the `.venv` path is not required |
| Installation or execution blocked by policy | Contact IT; do not change security or execution policies to bypass it |
| 403/partial/failed messages during `--self-test` | Expected synthetic scenarios if the final result is `SELF-TEST PASSED` |
| 403/partial/failed messages during a real crawl | Review the summary and the account's permissions; these are real evidence gaps |

To see which interpreter and package installation the direct commands use:

```powershell
python -c "import sys; print(sys.executable)"
python -m pip --version
```

## Sign-in and workspace options

Browser sign-in opens Microsoft authentication. **A tenant GUID is optional**:
the script uses organizational sign-in, identifies the tenant from the account,
and displays its GUID before workspace discovery. Subsequent token requests use
a tenant-specific client sharing the in-memory MSAL cache; there is no second
interactive sign-in.

Select workspace numbers such as `1,3`, ranges such as `2-4`, or explicitly
`all`, then confirm. Blank input does not select everything. Enter `q` or decline
confirmation to cancel without creating an export.

Other options:

```powershell
# List visible workspaces without collecting.
python .\kb_crawl_cli.py --auth browser --list-workspaces

# Device-code sign-in (also the default when --auth is omitted).
python .\kb_crawl_cli.py --auth device-code

# Reuse an approved Azure CLI sign-in and its active tenant.
az login
python .\kb_crawl_cli.py --auth azure-cli

# Target a specific customer/guest tenant. Replace the placeholder.
python .\kb_crawl_cli.py --auth browser --tenant-id "<tenant-guid>"

# Select explicit workspaces instead of using the picker.
python .\kb_crawl_cli.py --auth browser --workspace-id "<workspace-guid-1>" --workspace-id "<workspace-guid-2>"
```

Organizational sign-in does not enumerate every tenant where an account is a
guest. If expected workspaces are missing, verify the displayed tenant and use
`--tenant-id` for the intended customer. Single-tenant app registrations may
also need an explicit tenant. Use `--client-id` for an approved public-client
app with browser/device-code authentication, not with Azure CLI.

`--layer` sets one initial layer for all selected workspaces (default: `Mixed`).
The auditor can override it separately for each workspace during offline replay.
`--output-dir` changes the export parent; `--timeout` is a per-request/definition
poll timeout, not a deadline for the whole crawl.

### Elevated runs

From the folder containing the chosen standalone file:

```powershell
python .\admin_kb_crawl_cli.py --auth browser
python .\Capacity_kb_crawl_cli.py --auth browser
python .\Tenant_kb_crawl_cli.py --auth browser
```

Run only the script appropriate to the person's privileges. All support the
same optional tenant GUID, workspace picker, `--workspace-id`, `--list-workspaces`,
`--output-dir`, and sign-in choices. Discovery/list-only does not fetch elevated
evidence, and missing elevated permissions are not silently treated as a pass.

**Capacity inputs:** after workspace selection, the Capacity script prompts for
the Metrics app workspace hint, model-name substring (default: `Capacity Metrics`),
and peak start/end hours (default: 08:00-18:00 UTC). Convert local working hours
to UTC; a window crossing midnight is supported, but equal start/end is invalid.
These correspond to the existing tool's capacity inputs.

Supply all four flags to avoid these prompts:

```powershell
python .\Capacity_kb_crawl_cli.py --auth browser --workspace-id "<workspace-guid>" --capacity-metrics-workspace "Metrics workspace" --capacity-metrics-model "Capacity Metrics" --capacity-peak-start-hour 8 --capacity-peak-end-hour 18
```

The workspace value is a **search-order hint, not a scope filter**. As in the
tool, the first accessible model whose name matches is used; other accessible
Power BI workspaces may be searched. Its normalized metrics are repeated on
the selected workspace snapshots. This is not a separate capacity-id filter,
and selecting several workspaces does not select several capacity models.
Review `capacity_model_id` and `capacity_model_workspace_id` in the summary;
use separate runs and the correct inputs for separate Metrics app installations.

Tenant settings/domains/activity are likewise tenant-wide, not restricted to
the selected workspace IDs. Scanner requests use only the selected workspaces.
The scanner POST and Capacity DAX `EVALUATE` POSTs are read-only operations;
the scripts do not change settings or start notebooks/pipelines.

## Estimated crawl time

For the **standard workspace crawler**, use the artifact count **per workspace**
and the number of selected workspaces to find an approximate duration below.
These ranges assume responsive Microsoft APIs, ordinary network connectivity,
and a mix of artifacts comparable to the trial runs.

| Artifacts per workspace | Estimated time: 1 workspace | Estimated total: 2 similar workspaces | Estimated total: 5 similar workspaces |
|---|---:|---:|---:|
| 1-30 artifacts, such as a workspace with 10 artifacts | 2-5 minutes | 4-10 minutes | 10-25 minutes |
| 31-100 artifacts | 3-10 minutes | 6-20 minutes | 15-50 minutes |
| 101-250 artifacts | 3-15 minutes | 6-30 minutes | 15-75 minutes |
| 251-500 artifacts | 10-30 minutes | 20-60 minutes | 50-150 minutes |

**Example:** two small workspaces containing about 10 artifacts each have a
rough estimated total crawl time of **4-10 minutes**.

"Artifacts" means Fabric inventory items such as notebooks, pipelines, reports,
semantic models, Lakehouses and Warehouses. Do not add nested tables or columns
to the artifact count. Workspaces are collected sequentially, so durations
roughly add up; some reads within each workspace run concurrently. For differently
sized workspaces, add their individual ranges rather than using one size for all.

### What can change the estimate?

| Workspace or connection characteristic | Effect on estimated time |
|---|---|
| Mostly reports and simple inventory metadata | More likely to be near the lower end |
| Many or large notebook, pipeline and semantic-model definitions | More API reads and parsing; may approach or exceed the upper end |
| Many Lakehouses/Warehouses, SQL endpoints or large OneLake listings | More metadata discovery and listing work; can exceed the range even with few top-level artifacts |
| Slow network, throttling, retries or token acquisition delays | Can substantially increase duration; the table is not a timeout limit |
| Unreadable resources or missing permissions | May finish sooner with partial evidence; a fast crawl does not prove completeness |

**Basis and limits:** the available standard-CLI measurements are **29 artifacts
in 2 min 18 sec** and **112 artifacts in 2 min 55 sec**, each in a separate
single-workspace run. The second run had 2 empty model definitions and 10 failed
refresh-schedule reads. The table's size bands and multi-workspace totals are
rough extrapolations, not measured benchmarks or guaranteed completion times.
For more than 500 artifacts or unusually heavy workspaces, an initial sample
crawl is needed for a meaningful estimate.

Durations cover collection and export, not dependency setup, initial interactive
sign-in, workspace selection, or the later audit/check execution. Actual elapsed
time is `captured_until - captured_from` in the collection summary; per-workspace
time is `capture_finished_at - capture_started_at`. `--timeout` limits individual
requests/polls, not the whole crawl.

The table does **not** estimate Workspace Admin, Capacity or Tenant crawls:
gateway counts, Metrics model discovery/DAX and activity-log volume/scanner
polling drive those profiles instead. Their live durations have not yet been
benchmarked.

## Output and coverage

The standard script writes a new directory under `fabric-kb` by default:

```text
fabric-kb/
  fabric-kb_<UTC timestamp>_<unique suffix>/
    <workspace-uuid>.json
    collection-summary.json
```

Each readable workspace has a raw snapshot compatible with the production
`WorkspaceContext` serializer. The separate summary records counts, source
hashes, per-file SHA-256 checksums, unavailable resources, read failures, and
crawler-owned truncation flags. Repeated runs do not overwrite previous exports.

Elevated output uses the corresponding parent/prefix from the table, with the
same raw workspace JSON shape and a separate collection summary. The summary
also records `check_set: "admin"`, the matching `admin_categories`,
evidence counts and capacity collection inputs under `admin_settings`.
The summary is metadata, not a workspace snapshot.

Unrequested resources are explicitly marked unavailable. A narrow profile can
therefore have `cache_completeness: false` (the full-workspace cache's rule) even
when its own requested evidence was collected. Judge its collection status and
requested-resource gaps, not the full-cache flag alone.

| Exit code | Meaning |
|---|---|
| `0` | No recorded collection gaps; also used for successful help, listing, self-test, or picker cancellation |
| `2` | Collection completed with partial evidence; also argparse's code for invalid command-line arguments |
| `1` | Collection/authentication/export failed; completed files may remain |
| `130` | Interrupted by the user; do not treat the run as complete |

Collection completeness is **not** a guarantee that every check has applicable
evidence. Inspect the summary and compare the inventory with the workspace.
Pipeline `allowDataTruncation` settings are configuration evidence, not signs
that the crawl was truncated.

### What "compatible KB" means

Each script contains collection and serialization code originally copied from
the production sources. It does not import AuditFAST at runtime or automatically
sync with later tool changes. The **standard** script uses a fixed workspace non-admin
profile, not every resource a standard or elevated tool crawl might request.
The following are excluded and explicitly marked unavailable:

- Tenant settings and domains.
- Admin scanning and activity.
- Capacity metrics.
- Gateways and OneLake data-access roles.

The three elevated scripts collect their respective subsets of those resources.
None is a substitute for a full standard snapshot. Their collection sets are
the category's declared requirements plus the provider's workspace identity and
inventory dependencies, not an automatic crawl of all admin categories.

Workspace visibility does not guarantee definition or metadata read permission.
For example, Contributor supports many definition reads, while role assignments
need Member or higher; item permissions and token consent still apply.

The provider also reads **account-visible connection metadata**; this is not
filtered to just the selected workspaces. Definitions may include proprietary
code, embedded secrets, and notebook outputs. The script does not automatically
redact evidence or upload it anywhere. Review and approve all exported content
before transferring it through an approved channel. Never share access tokens,
device codes, credentials, or authentication caches.

Inherited provider limits still apply. In particular, some best-effort reads
do not record every gap (for example, a failed OneLake Tables listing after a
successful Files listing). A clean summary alone is not proof of complete
coverage, and running the same crawl twice can observe different runtime data.

## Using the KB for an offline audit

### Standard KB

On the auditor's machine:

1. Select **Saved KB (offline)** in the audit UI; no Fabric sign-in is needed.
2. Upload the workspace UUID JSON files, **not** the collection summary.
3. Assign layers, groups, and environment levels as needed.
4. Select checks and run the audit. Review missing evidence and N/A results.

Upload every participating workspace together for cross-workspace checks.
They use the same per-workspace snapshot format; no separate group KB is needed.
The API accepts at most 100 inline uploaded snapshots per run.

Saved-KB replay has no live Fabric fallback. It does not automatically seed the
standard TTL cache used by the separate batch-checklist/custom-check workflows.
AI/advisory settings belong to the receiving tool, not this crawler.

### Elevated KB

The current **Admin Checks page is live-only**, while the API already supports
offline elevated audits. Uploading to the ordinary Saved KB page and running
standard checks will **not** run the intended elevated category.

Use `POST /api/v1/audit` with:

- `source: "kb"` (no Fabric auth session required).
- `check_set: "admin"`.
- `admin_categories: ["Workspace Admin"]`, `["Capacity"]`, or `["Tenant"]`,
  matching the script's summary.
- `snapshots`: the raw workspace JSON objects, not the collection summary.
- `workspaces`: the corresponding IDs and layer roles.
- `admin_settings`: capacity inputs from the summary and any reviewer answers.

Example on the auditor's machine, with the API already running. Replace the
run-directory placeholder with **one elevated export directory**:

```powershell
$runDir = "C:\approved-kb\<elevated-run-directory>"
$capture = Get-Content -LiteralPath (Join-Path $runDir "collection-summary.json") -Raw | ConvertFrom-Json
if ($capture.check_set -ne "admin") { throw "Select an elevated collection summary." }

$snapshots = @(
    foreach ($entry in $capture.workspaces) {
        if ($entry.status -in @("collected", "partial")) {
            if ($entry.file -ne "$($entry.workspace_id).json") { throw "Unexpected snapshot filename." }
            $path = Join-Path $runDir $entry.file
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
                throw "Snapshot checksum does not match the summary."
            }
            Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json
        }
    }
)
if ($snapshots.Count -eq 0) { throw "No readable workspace snapshots." }

$reviewerSettings = @{}
foreach ($setting in $capture.admin_settings.PSObject.Properties) {
    $reviewerSettings[$setting.Name] = $setting.Value
}
# Add only answers the reviewer can actually confirm, for example:
# $reviewerSettings["operations_groups"] = @("Actual operations group name")

$body = @{
    source = "kb"
    check_set = "admin"
    admin_categories = @($capture.admin_categories)
    admin_settings = $reviewerSettings
    workspaces = @($snapshots | ForEach-Object { @{ id = $_.id; role = $_.layer } })
    snapshots = $snapshots
} | ConvertTo-Json -Depth 100

$accepted = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/audit" -ContentType "application/json; charset=utf-8" -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/audit/$($accepted.audit_id)"
```

Poll the returned audit ID until `succeeded` or `failed`, then retrieve/review its
report. The normal request limit of 100 uploaded snapshots still applies.
Run each profile separately: snapshots from different crawlers can share the
same workspace ID, and supplying both in one request does not merge their data.

Some checks need answers that no API can discover. Supply these at audit time,
not as invented facts in the KB:

- Workspace Admin: `production_workspaces`, `developer_groups`,
  `operations_groups`, `report_consumer_groups`, and applicable confirmations
  `app_access_reviewed`, `gateway_sizing_confirmed`, `code_scan_clean`.
- Tenant: `production_workspaces` for production-change checks.
- Capacity: `metrics_app_monitored`, `analysis_documented`, `throttling_tracked`.

The scripts do not attest to these. Leaving an answer unset retains the existing
check's N/A or score ceiling. Changing peak-hour settings during replay does not
recalculate already captured metrics; collect again for a different window.

## Testing more workspaces

The utility is ready for broader controlled testing, not a blanket certification
of all workspace types or permissions. Use this checklist:

1. Start with a small workspace, then one containing Lakehouses/Warehouses,
   notebooks/pipelines, and semantic models/reports.
2. Compare exported counts and representative definitions with the selected
   workspace. Counts can differ where the provider normalizes or enriches data.
3. Check unavailable resources, read failures, truncation flags, and checksums.
4. Test a restricted account to confirm missing evidence stays visible.
5. Test multiple selected workspaces, cancellation, and repeat runs.
6. Upload the snapshots and verify that the offline audit produces reports
   without customer credentials.

Run every script's built-in self-test before a live test. The elevated self-tests
exercise their real bundled providers with synthetic HTTP, their category-only
resource sets, denied access, snapshot round-trips, checksums, caching and the
capacity input rules. Tenant scanner network failures remain unavailable;
failed Capacity workspace/model discovery cannot masquerade as an absent app.
These tests do not verify actual tenant consent or live API behavior.

From the shared folder, on an approved Python machine:

```powershell
python .\kb_crawl_cli.py --self-test
python .\admin_kb_crawl_cli.py --self-test
python .\Capacity_kb_crawl_cli.py --self-test
python .\Tenant_kb_crawl_cli.py --self-test
```

For a strict parity test, feed the bundled and production collectors identical
recorded responses, resource selections, and settings, then compare normalized
snapshots and deterministic check results. Separate live runs are not a reliable
byte-for-byte comparison because the tenant and runtime metrics can change.

## Maintaining this add-on

This section is for repository maintainers. Customers with only the shared
folder can ignore it; their commands are in the quick-start section above.

Everything specific to the crawler lives here:

```text
extras/kb-crawler/
  README.md
  kb_crawl_cli.py
  admin_kb_crawl_cli.py
  Capacity_kb_crawl_cli.py
  Tenant_kb_crawl_cli.py
```

Edit the relevant script directly. The separate builder, template and pytest
suite were removed to keep this add-on to scripts plus documentation. The four
files intentionally contain their own runtime so each can be handed off alone.
Common authentication/provider fixes must be applied to every affected copy;
the three elevated copies differ only in their fixed category, title and version.
Built-in `--self-test` remains, but is not a replacement for production-parity
testing. No scoring/check modules or application runtime were changed to add them.

Changes to the main tool's providers or snapshot schema are **not propagated
automatically**. Review and port relevant changes explicitly, update
`COLLECTOR_VERSION` when behavior changes, and verify the exported KB against
the receiving tool.

The embedded source/build hashes preserve the original generated baseline,
including paths to former build inputs. Those files are not read at runtime.
The hashes do not prove that later manual edits still match production.

From the repository root, using an approved Python environment with the
dependencies listed above:

```powershell
python .\extras\kb-crawler\kb_crawl_cli.py --help
python .\extras\kb-crawler\kb_crawl_cli.py --self-test
python .\extras\kb-crawler\kb_crawl_cli.py --auth browser
```

After a change, repeat the workspace checks and offline replay described above.
No AuditFAST installation is needed on the machine running this script.

### Source-control boundary

Commit the four scripts and README in this folder, plus the repository
`.gitignore` update excluding default `fabric-kb`, `fabric-admin-kb`,
`fabric-capacity-kb` and `fabric-tenant-kb` output. Do not commit customer
JSON, collection summaries, reports, credentials, virtual environments, or local
transfer patches. A custom `--output-dir` inside the repository needs its own
ignore rule or an equally careful exclusion from staging.

The main application requirements and runtime need no crawler-specific changes.
