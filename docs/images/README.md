# Screenshots for the how-to-use guide

[`docs/how-to-use.md`](../how-to-use.md) references the images below. Drop a PNG
with the **exact file name** into this folder and it appears in the guide.

**How to capture (Windows):** open the app at <http://localhost:5173>, press
`Win`+`Shift`+`S` to snip the region, then save it here with the matching name.
Crop to just the relevant panel and blur any workspace names or GUIDs you don't
want to share with the client.

Some screens only render against a live Fabric tenant (sign-in, run audit, the
questionnaire, the report). Capture those while signed in to a real workspace.

| File | Screen | What it should show |
|------|--------|---------------------|
| `01-dashboard.png` | Dashboard (top bar) | The app name, the green **health badge** (check count), the **Dashboard / Run audit / Checks / Checklist / History** tabs, and the **Login** button. |
| `02-signin-page.png` | Sign-in page | The sign-in options with **"Running the app on this machine?"** expanded (Reuse Azure CLI session / Open sign-in window), and **"Troubleshoot access"** at the bottom. |
| `03-run-audit-workspaces.png` | Run audit | The workspace table (Audit checkbox + **Layer role** dropdown per row), the **"Add a workspace by name or ID"** box, and the **pillar** tick-boxes below. |
| `04-audit-running.png` | Run audit (running) | The **"Audit in progress"** panel with the spinner reading *"Auditing your workspaces…"* and the audit id on the right. |
| `05-questionnaire.png` | Run audit (questionnaire) | The **"Self-assessed checklist"** section: a couple of points, each with its question, the scored radio options, and the **"Skip this check"** option. |
| `06-questionnaire-skip.png` | Run audit (questionnaire) | The **"Submit answers & view report"** button with the note beneath it: *"Unanswered points are recorded as skipped (N/A)."* This illustrates skipping everything in one click. |
| `07-report-overall.png` | Report (top) | The workspace name, the big **overall %** with the rating badge, the **pass / partial / fail** summary line, and the **Markdown** and **Excel** download buttons. |
| `08-report-findings.png` | Report (findings) | The **Findings** table: check, severity, evidence, affected item, and recommended fix, with the filters above it. |
| `09-checks-catalog.png` | Checks tab | The searchable/filterable rule table showing ID, reference, pillar, scope, severity, title. |
| `10-checklist-page.png` | Checklist tab | The text box to describe a best-practice point and the result panel (a matching existing check, or a drafted proposal). |
| `11-history.png` | History tab | The table of past audits: id, submitted time, status, score; ids link to reports. |
