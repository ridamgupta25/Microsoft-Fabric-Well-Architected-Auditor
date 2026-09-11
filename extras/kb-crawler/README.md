# Fabric KB Crawlers

Standalone, read-only scripts to export Fabric Knowledge Base (KB) snapshots.
No AuditFAST installation is needed. These scripts collect evidence; they do
not run audit checks.

## Prerequisites

- Python **3.10+** on an approved machine with network access to Microsoft services.
- Access to the selected workspaces and the permissions for the chosen script.
- **Standard SQL metadata only:** Microsoft ODBC Driver 18, SQL access and TCP
  1433 connectivity. The Python SQL package is included below; the ODBC driver
  must be installed separately. Elevated scripts do not need SQL support.

## Install dependencies

Open a terminal in this folder. Install the Python packages listed in
[requirements.txt](requirements.txt) using the same Python that will run the script:

```powershell
python --version
python -m pip install -r .\requirements.txt
```

## Run the script for your access

| Your access | Script | Command |
|---|---|---|
| Standard workspace access | [kb_crawl_cli.py](kb_crawl_cli.py) | `python .\kb_crawl_cli.py --auth browser` |
| Workspace Admin/Member, plus connection and gateway access | [admin_kb_crawl_cli.py](admin_kb_crawl_cli.py) | `python .\admin_kb_crawl_cli.py --auth browser` |
| Fabric tenant administrator | [Tenant_kb_crawl_cli.py](Tenant_kb_crawl_cli.py) | `python .\Tenant_kb_crawl_cli.py --auth browser` |
| Capacity administrator with Metrics model Read/Build access | [Capacity_kb_crawl_cli.py](Capacity_kb_crawl_cli.py) | `python .\Capacity_kb_crawl_cli.py --auth browser` |

Sign in, select workspace numbers (for example `1,3` or `2-4`), and confirm with
`y`. Tenant ID is detected automatically; add `--tenant-id "<tenant-guid>"` to
target a specific customer/guest tenant. Choosing a script does not grant access.

The **Capacity** script also asks for the Metrics app workspace, model name and
peak hours in **UTC**. Capacity admin alone does not grant access to that model.

For an offline self-test, replace `--auth browser` with `--self-test` and look
for `SELF-TEST PASSED`. Simulated failures during the self-test are expected.

## Output

Output is created **inside the `kb-crawler` folder**, beside the scripts,
regardless of the terminal's current folder. Each run gets a new timestamped
subfolder containing workspace JSON files and `collection-summary.json`:

| Profile | Default output folder |
|---|---|
| Standard | `output\normal\<run-folder>` |
| Workspace Admin | `output\admin\<run-folder>` |
| Tenant | `output\tenant\<run-folder>` |
| Capacity | `output\capacity\<run-folder>` |

The final message is **green for completed**, **yellow for partial**, or **red
for failed** in a color-enabled terminal, followed by the output path. Redirected
output stays plain text. `--output-dir` can explicitly override the default location.

**After execution completes, review and ZIP the timestamped run folder shown
in the terminal, then share the ZIP through your approved channel.** Include
the workspace JSON files and summary. ZIP creation is manual.

Exports can contain sensitive code, identities and tenant-wide metadata.
Review any partial evidence before sharing; never include credentials or unrelated runs.

**For the auditor:** use Saved KB (offline) for standard snapshots. Elevated
snapshots use the API with `source="kb"`, `check_set="admin"` and the matching
`admin_categories` value from the summary; the Admin Checks UI is live-only.

## Rough crawl time: standard script

| Artifacts per workspace | 1 workspace | 2 similar workspaces |
|---|---:|---:|
| 1-30 | 2-5 min | 4-10 min |
| 31-100 | 3-10 min | 6-20 min |
| 101-250 | 3-15 min | 6-30 min |
| 251-500 | 10-30 min | 20-60 min |

Rough extrapolations from two trials (29 items: 2 min 18 sec; 112 items:
2 min 55 sec, with partial evidence), not guaranteed timings. Artifact types,
SQL reads, network speed and retries affect duration. Setup/sign-in time is
excluded; elevated profiles have not been benchmarked.
