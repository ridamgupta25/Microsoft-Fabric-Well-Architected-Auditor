---
name: azmon-mirroredcatalogs-operations-cli
description: "Onboard Log Analytics, Application Insights, and Azure Monitor telemetry into Microsoft Fabric as a Mirrored Catalog, then turn it into business-impact insights by correlating telemetry with business data via Eventhouse shortcuts, verified schemas, and ready-to-use Operations Agent instructions. Triggers: onboard Log Analytics telemetry, connect Application Insights to a Mirrored Catalog, correlate App Insights telemetry with business data, build an Operations Agent for business-impact alerting, determine if availability or latency impacted bookings orders or revenue."
---

> **Update Check — ONCE PER SESSION (mandatory)**
> The first time this skill is used in a session, run the **check-updates** skill before proceeding.
> - **GitHub Copilot CLI / VS Code**: invoke the `check-updates` skill.
> - **Claude Code / Cowork / Cursor / Windsurf / Codex**: compare local vs remote package.json version.
> - Skip if the check was already performed earlier in this session.

# azmon-mirroredcatalogs-operations-cli

Guide a user end-to-end to (1) onboard Azure Monitor / Application Insights /
Log Analytics observability data into Microsoft Fabric as a Mirrored Catalog
(AzMon) item, and (2) turn that telemetry into **business-impact insights** by
correlating observability signals with business data (bookings, orders,
customers, flights, payments, revenue, tenants, accounts, subscriptions, usage
KPIs, SLA/availability KPIs), ending in ready-to-paste **Operations Agent**
instructions.

This is a **self-contained Skills-for-Fabric package**. It does **not** depend on
any MCP server or tool controller as the execution mechanism. Product/API
knowledge, supported flows, guardrails, and modeling rules live in this file and
in `references/*.md`.

## Prerequisite Knowledge

Before running this skill, read the shared common guidance:

- [Authentication & token acquisition](../../common/COMMON-CORE.md#authentication--token-acquisition)
- [Authentication recipes](../../common/COMMON-CLI.md#authentication-recipes)

## Trigger phrases

- onboard Azure Monitor data into Fabric
- create Azure Monitor item in Fabric
- connect Application Insights to Fabric
- correlate App Insights telemetry with business data
- onboard my LA workspace to Fabric
- onboard Log Analytics workspace to Fabric
- connect Log Analytics workspace to Fabric
- understand if service availability impacted bookings
- understand if latency impacted conversion
- correlate exceptions with revenue or orders
- build Operations Agent for Azure Monitor business impact
- create business impact insights from Log Analytics data

## When to use this skill (and related skills)

`azmon-mirroredcatalogs-operations-cli` is for onboarding Azure Monitor /
Application Insights / Log Analytics telemetry into Microsoft Fabric
(mirroredCatalogs endpoint), correlating that telemetry with business data, and
generating Operations Agent instructions. For general Eventhouse / KQL querying
unrelated to Azure Monitor onboarding, use `eventhouse-consumption-cli`; for
authoring Eventhouse items and databases, use `eventhouse-authoring-cli`.

## Reference index

Read these when the corresponding stage needs product/API detail. Do not paste
them wholesale into user responses — they are guidance for you, the agent.

| Reference | Use it for |
|-----------|-----------|
| [references/azmon-fabric-api-reference.md](references/azmon-fabric-api-reference.md) | Supported vs UI-only flows; connector modes; Fabric item/agent surfaces |
| [references/mirrored-catalog-reference.md](references/mirrored-catalog-reference.md) | Mirrored Catalog item CRUD, definition, discovery, monitoring, refresh |
| [references/eventhouse-shortcuts-reference.md](references/eventhouse-shortcuts-reference.md) | Eventhouse/KQL, OneLake shortcuts, queryability requirement |
| [references/operations-agent-reference.md](references/operations-agent-reference.md) | Operations Agent instruction template, validation, troubleshooting |
| [references/app-insights-table-reference.md](references/app-insights-table-reference.md) | App Insights / Azure Monitor telemetry tables and business meaning |
| [references/app-insights-dynamic-fields-reference.md](references/app-insights-dynamic-fields-reference.md) | Dynamic fields (Properties/CustomDimensions) and hidden business keys |
| [references/business-correlation-patterns.md](references/business-correlation-patterns.md) | Signal → business-impact patterns and candidate keys |
| [references/business-insight-modeling-reference.md](references/business-insight-modeling-reference.md) | Bins, derived entities, direct join vs time-window, thresholds |

## Secrecy & scope guardrails

- Do NOT expose Azure Log Analytics backend APIs to the user.
- Do NOT expose Fabric / DMTS / Gateway connection internals or internal
  endpoints to the user.
- Do NOT request or disclose tokens, OAuth redirect codes, OAuth nonce values,
  cookies, secrets, or internal implementation details.
- Do NOT mention MCP, MCP servers, or MCP connectivity/troubleshooting anywhere
  in the user-facing flow. This Skill is self-contained.
- Do NOT present undocumented / browser-inspected / internal connector APIs as
  supported public APIs.
- Do NOT claim OAuth Azure Monitor connector creation is available through a
  public API — OAuth connector creation is **UI-guided only**.
- Do NOT fabricate workspace names, table names, schema, item IDs, connection
  IDs, or query results. Use only values returned by real discovery/queries; use
  clearly-labelled placeholders otherwise.
- Do NOT ask the user for JOIN logic, KQL, bins, or thresholds upfront.
- If the request is about SQL / Data Warehouse or Lakehouse ingestion, warehouse
  performance, or general Fabric DW best practices **unrelated** to Azure Monitor /
  Application Insights / Log Analytics onboarding, state that it is **out of scope**
  for this Azure Monitor skill and point the user to the appropriate warehouse
  skill; do NOT act on it, run queries, or create resources.

### Domain-agnostic rule

Business entities named anywhere in this Skill or its references — bookings,
orders, customers, flights, revenue, tenants, payments, and similar — are
**EXAMPLES ONLY** (non-normative illustrations). The Skill is **domain-agnostic**
and MUST NOT infer or assume the user's business domain from these examples, from
table/column names that resemble an example, or from any prior context. The
user's actual business entities MUST be discovered from real Fabric data
(Eventhouse / KQL database / Warehouse / Lakehouse / shortcuts) and confirmed with
the user before use. Until discovered and confirmed, refer to them generically —
as *business entities*, *business datasets*, *business KPIs*, or *business
outcomes*.

## EXECUTION CAPABILITY POLICY

The Skill is a guided staged workflow; actual execution depends on capabilities
available in the current environment. Portal-guided instructions are allowed ONLY
for OAuth Azure Monitor connector creation — the Skill MUST NOT switch the entire
onboarding flow to portal guidance as a generic fallback. For all non-OAuth
stages, the Skill MUST first discover whether a supported execution path exists.

Supported execution paths may include:

- Fabric REST APIs
- Azure REST APIs
- Fabric Actions
- Azure CLI
- Azure Resource Graph
- Fabric REST **read-only** discovery via authenticated `az rest --method get`
  against `https://api.fabric.microsoft.com/...` (discovery/read-only only)
- Other documented supported capabilities available to the agent

**Log Analytics REST API reference (agent-facing).** When the Skill needs to perform or validate Azure Log Analytics operations, it may consult the official [Log Analytics REST APIs](https://learn.microsoft.com/en-us/rest/api/loganalytics/) reference to identify supported Log Analytics management, workspace, table, ingestion, and query APIs. This is agent-facing guidance only and does not relax the secrecy rule above.

These execution paths are distinct and MUST NOT be conflated:

1. **Kusto / KQL data-plane execution** — telemetry queries; optional, and MUST
   NOT be used when disabled.
2. **Azure ARM control-plane discovery** — resource/metadata enumeration.
3. **Fabric REST control-plane discovery** — a surfaced Fabric REST / Fabric
   Actions capability.
4. **Arbitrary shell / CLI execution** — out of scope (see Out-of-scope
   constraints).
5. **Fabric REST read-only discovery via authenticated `az rest --method get`** —
   a narrow GET-only exception for Fabric discovery/read against
   `https://api.fabric.microsoft.com/...`; never mutates anything and never
   exposes tokens, secrets, or auth headers. NOT general shell/CLI access.

MCP unavailability alone does NOT mean execution capability is unavailable.
Unavailability of any single execution path does NOT imply the capability is
unavailable. Before declaring a capability unavailable, the Skill MUST evaluate
ALL supported execution paths listed above and confirm none is available.

If no supported execution path exists, the Skill MUST: stop; identify the missing
capability; explain why it is required; identify which stage is blocked; and wait
for user confirmation.

The Skill MUST NOT replace validation, Mirrored Catalog creation, discovery,
monitoring, refresh, shortcut creation, schema verification, or Operations Agent
creation with portal guidance, and MUST NOT claim an action completed when
execution capability is unavailable.


## PORTAL GUIDANCE POLICY

Prefer automated execution paths (Fabric REST, Azure REST, Fabric Actions, Azure
CLI, other supported automation) over UI-guided instructions. Before providing
UI-guided instructions the Skill MUST: (1) evaluate the available execution paths,
(2) attempt supported ones where available, (3) explain which were evaluated and
why they cannot be used. OAuth Azure Monitor connector creation is the one
explicitly supported UI-guided scenario and is exempt from this evaluation.


## STRICT STAGED WORKFLOW CONTROLLER (ENFORCED)

The Skill MUST operate as a strict staged workflow controller.

### Stages

1. Intent and scope
2. Log Analytics workspace selection
3. Fabric workspace selection
4. Validation
5. Connection resolution
6. AzMon / Mirrored Catalog item creation or reuse
7. **Business Insight Capture** (immediately after item creation/reuse)
8. Azure Monitor table discovery
9. Eventhouse / KQL database selection
10. Shortcut planning
11. Shortcut creation
12. Schema and data verification
13. Business data discovery and scoring
14. Correlation planning
15. Operations Agent instruction generation
16. Optional Operations Agent creation / validation

### Execution rules

- The Skill MUST track and enforce the current stage.
- The Skill MUST NOT skip stages.
- The Skill MUST NOT move to the next stage without completing the current one.

### Stage visibility (REQUIRED in every user-facing response)

Begin every response with exactly this structure:

```text
Current stage: <stage name>
What I found:
<short summary>

Next step:
<one clear action>

Waiting for your confirmation to continue.
```

If the current stage is unclear → **STOP** and ask the user where to resume.

**Stage 15 exemption (ready-to-paste block).** The Stage 15 Operations Agent
instructions are delivered as ONE self-contained, ready-to-paste fenced block.
For that response only, place the stage-visibility header and the
`Waiting for your confirmation to continue.` line OUTSIDE and AROUND the fenced
block — the header (and a one-line summary) immediately before it, the
confirmation line immediately after it. Do NOT insert the header, summary,
`What I found` / `Next step` labels, or the confirmation line INSIDE the
ready-to-paste block: the fenced block must contain only the Operations Agent
instructions so the user can copy it verbatim.

### Hard stop behavior

After presenting any step that requires confirmation:

- **STOP.**
- **WAIT** for explicit user confirmation.
- Do **not** continue automatically.

### Confirmation gates (explicit confirmation REQUIRED before)

- Creating or reusing any resource that modifies Fabric.
- Selecting an Eventhouse / KQL database.
- Creating shortcuts.
- Proceeding from schema verification (Stage 12) to correlation planning
  (Stage 14).
- Generating final Operations Agent instructions if the correlation model has
  not been confirmed.
- Creating or modifying an Operations Agent.

### Stage guardrails

- Shortcut planning (Stage 10) MUST occur before schema verification or join
  logic. If schema/join is attempted early → STOP and return to shortcut
  planning.
- Shortcut creation (Stage 11) MUST complete before schema verification.
- Schema verification (Stage 12) MUST complete before correlation planning
  (Stage 14).
- If data is not queryable in Eventhouse via shortcuts → STOP → return to
  shortcut planning / creation.
- Correlation planning MUST NOT begin until data queryability via shortcuts is
  confirmed.
- Business Insight Capture (Stage 7) MUST be satisfied (intent provided OR a
  suggested direction explicitly selected) before correlation planning. Never
  assume intent.

### Out-of-scope constraints

The Skill MUST NOT run arbitrary shell/CLI/az/PowerShell commands, perform network
debugging, investigate server connectivity, or execute infrastructure
troubleshooting. These are out of scope unless explicitly part of the current
stage.

**Narrow exception — Fabric REST read-only discovery.** The Skill MAY use an
authenticated `az rest --method get` call ONLY for Fabric REST read-only
discovery, and ONLY when ALL hold: endpoint is
`https://api.fabric.microsoft.com/...`; method is **GET** only; the operation is
discovery/read-only; nothing is created, updated, deleted, or modified; no tokens,
secrets, auth headers, or sensitive payloads are exposed; and the Skill states the
capability path used. This exception does NOT permit arbitrary shell/CLI
execution, non-GET `az rest` calls, or use of the Kusto / KQL data-plane when
disabled.

### Response style (ENFORCED)

Behave like a **guided product experience**, not a backend debugger.

- Use concise, business-friendly language.
- Never show internal implementation steps (CLI, REST, tokens, API calls) unless
  the user explicitly asks and the API is documented/supported.
- Never expose internal limitations ("public API limitation", "CredentialType
  not supported", "headless OAuth failure") in user-facing output.
- Never ask for secrets, OAuth codes, cookies, redirect URLs, tokens, or nonce
  values.
- Summarize findings. Do NOT expose endpoint experimentation, API probing,
  OpenAPI / schema exploration, retry investigations, or low-level debugging
  details in user-facing output unless the user explicitly asks for them.
- After each stage: present a short summary, ask for explicit confirmation,
  STOP and WAIT.

---

## Stage 1 — Intent and scope

Confirm what the user wants: onboard observability data into Fabric, explore a
business insight, or both. Capture (in plain language) any workspace names or
business outcome they already mention — but do not yet drive correlation.

## Stage 2 — Log Analytics workspace selection

Application Insights telemetry is queried through its backing Log Analytics
workspace (workspace-based Application Insights). Help the user pick the correct
Log Analytics / Application Insights-backed workspace.

- If the user did not provide a workspace, ask for the subscription, then present
  a concise list of supported workspaces (name + resource group + location).
  Never expose raw API responses.
- Prefer a case-insensitive name filter over listing everything when the
  subscription has many workspaces.

### Exact-name-not-found fallback (REQUIRED)

When the user names a workspace and no **exact** match exists, the Skill MUST NOT
fail. Instead:

1. Ask for the subscription if not provided (do not guess).
2. Offer similar workspaces (names that **contain** the term or are a close
   case-insensitive/partial match). Broaden the search if a narrow filter returns
   nothing.
3. Present candidates as a concise numbered list (name + resource group +
   location), then STOP and wait for the user to pick.
4. If exactly one similar workspace is found, still confirm before proceeding.
5. If none is found, say so plainly and ask for a different subscription or term.

Never fabricate a workspace name or GUID — only offer real discovered
workspaces.

## Stage 3 — Fabric workspace selection

Help the user choose the target Fabric workspace (display name + id). Use a
case-insensitive substring filter when helpful. Read-only; nothing is created
here. Never expose raw API responses or tokens.

### Fabric Workspace Discovery & Capability Resolution Policy (REQUIRED)

Fabric workspaces are **not** ARM resources, so missing automatic enumeration does
NOT mean no workspace exists. Follow the full required policy in
[references/azmon-fabric-api-reference.md](references/azmon-fabric-api-reference.md#fabric-workspace-discovery--capability-resolution):
discover all surfaced Fabric mechanisms first (Fabric REST/Actions, OneLake/Power
BI, and read-only `az rest --method get` against `https://api.fabric.microsoft.com/...`);
never terminate on missing enumeration; ask the user for a workspace Name / ID /
URL; validate before continuing; and mark Stage 3 BLOCKED only after ALL discovery
AND user-supplied paths are exhausted. UI-guided selection is the final fallback only.

## Stage 4 — Validation

Before any creation, verify:

- The workspace exists.
- The workspace is **not Sentinel** or otherwise unsupported.
- The caller has the required Log Analytics access.
- The caller has sufficient Fabric workspace permission to create the item.

If validation fails, summarize which checks passed/failed in user terms, explain
the missing capability, and offer to try another workspace or request access.
See `references/azmon-fabric-api-reference.md` for supported-scope rules.

### Sentinel detection source hierarchy (REQUIRED)

Sentinel detection is a **control-plane** check and MUST NOT be treated as
dependent on Kusto / KQL data-plane availability. Follow the full required
hierarchy in
[references/azmon-fabric-api-reference.md](references/azmon-fabric-api-reference.md#sentinel-detection-source-hierarchy):
enumerate ARM resources / `Microsoft.OperationsManagement/solutions`
`SecurityInsights(<workspaceName>)` / `Microsoft.SecurityInsights/*` /
feature metadata, then classify as **Sentinel / blocked**, **Not Sentinel
(control-plane verified)**, or — only if all checks are unavailable —
**not verifiable**. Never fabricate Sentinel status.

### Validation capability discovery (REQUIRED)

Apply the [EXECUTION CAPABILITY POLICY](#execution-capability-policy): evaluate ALL
supported execution paths (Fabric REST, Azure REST, Fabric Actions, Azure CLI,
Azure Resource Graph) before declaring validation capability unavailable. MCP is
only one possible path.

## Stage 5 — Connection resolution

Two connection modes are supported. Keep them **separate**. Never route OAuth
through Service Principal logic, and never route Service Principal through OAuth /
interactive sign-in logic. See
[references/azmon-fabric-api-reference.md](references/azmon-fabric-api-reference.md)
for the authoritative connector rules.

### Portal guidance boundary

Portal-guided instructions are permitted ONLY for OAuth Azure Monitor connector creation.

Portal guidance is NOT an allowed fallback for:

- LAW validation
- Fabric workspace validation
- Connection detection
- Connection reuse
- Service Principal connector creation
- Mirrored Catalog item creation
- Discovery
- Monitoring
- Refresh
- Eventhouse shortcut creation
- Schema verification
- Operations Agent creation

If execution capability for these actions is unavailable, the Skill MUST stop and identify the missing capability.

### Mode A — OAuth (UI-guided only)

- OAuth connector **creation** is interactive, done once in **Fabric → Manage
  Connections**. The Skill does **not** create OAuth connectors through any API.
- The Skill **detects and reuses** an existing Azure Monitor OAuth connection for
  this workspace (read-only, non-destructive).
- Before classifying OAuth connection detection as "not verifiable", attempt the
  available Fabric REST **read-only** connection discovery paths, in order:
  1. A surfaced Fabric REST / Fabric Actions capability, if available.
  2. Authenticated Fabric REST read-only discovery via `az rest --method get`
     against `https://api.fabric.microsoft.com/...`, if Azure CLI execution is
     available and permitted under the read-only Fabric REST discovery exception
     (GET-only, no modifications, no secret/token/header exposure, capability
     path stated). This detection is read-only; OAuth connector **creation**
     remains UI-guided only.
- Reuse a connection ONLY if it belongs to the **same** Log Analytics workspace
  (exact data-source-path / LAW resource-id match). Any mismatch → treat as "no
  matching connection".
- If exactly one match → reuse automatically and continue.
- If multiple matches → show display names and ask the user to choose (never
  auto-pick).
- If no match → guide the user with this exact, simple message and then WAIT:

```text
I couldn’t find an existing Azure Monitor connection for this workspace.
Please create it once in Fabric Manage Connections, then come back and continue.

1. Open Fabric.
2. Go to Manage Connections.
3. Select New connection → Azure Monitor.
4. Sign in with your organizational account.
5. Use the same Log Analytics Workspace.
6. When done, come back here and type: Done.
```

When the user resumes, re-detect and continue automatically ("Connection
detected. Continuing setup.").

### Mode B — Service Principal (automated create-or-reuse)

Present this as **"connect using Service Principal"** — the automated,
non-interactive path (no user login, no UI step).

- **Idempotent create-or-reuse**: if a matching Azure Monitor Service Principal
  connector already exists for the same Log Analytics workspace (same data source
  path + Service Principal credential type), reuse it — never create a duplicate.
- Only **one** connector is created per run.
- **Never** reuse a non-Service-Principal (e.g. OAuth) connector in this mode.
- **Never** ask the user to paste a client secret into chat. Secrets come from
  **environment variables or Key Vault references** only, are never echoed,
  logged, exposed, or included in generated instructions.
- If required Service Principal inputs are missing, describe **what** is missing
  (tenant id, app/client id, and a securely-provided secret reference) using
  presence checks only — never request the secret value in chat.

Automation boundary: infrastructure (connector create-or-reuse, mirrored item
creation) is automated; **business decisions** (Eventhouse/KQL DB selection,
shortcut creation) always require explicit user confirmation.

## Stage 6 — AzMon / Mirrored Catalog item creation or reuse

Create the Azure Monitor **Mirrored Catalog** item in the target Fabric
workspace, or reuse an existing matching item. This is a Fabric-modifying action
→ confirm first. Supported Mirrored Catalog operations (item CRUD, definition,
discovery, monitoring, refresh) are documented in
[references/mirrored-catalog-reference.md](references/mirrored-catalog-reference.md).

After the item is created or reused, immediately proceed to Business Insight
Capture (Stage 7).

## Stage 7 — Business Insight Capture (MANDATORY, happens here — early)

Immediately after the AzMon item is created or reused, ask the user **what
business insight they want to explore**, so discovery and table selection are
guided by the business outcome.

Ask in business language, e.g.:

- Did service availability issues impact bookings?
- Did request latency reduce customer conversions?
- Did exceptions affect revenue or orders?
- Did dependency failures impact specific customers, tenants, regions, or
  flights?
- Did incidents affect SLA, usage, or customer activity?
- Did traffic drops correlate with usage KPI degradation?

### Enforcement

The Skill MUST NOT proceed to business correlation without either:

- (a) user-provided business intent, OR
- (b) explicit user selection from suggested directions.

Never assume intent.

### Fallback (when the user is unsure / vague / no exact match)

Suggest 3–5 directions, each framed as **observability signal → business
impact**, and ask the user to choose one:

1. Availability failures → booking completion impact.
2. Request latency → conversion or checkout drop.
3. Exceptions → failed orders or revenue at risk.
4. Dependency failures → customer / tenant / region impact.
5. Traffic drops → usage KPI degradation.

### Important distinction

Capturing intent early is allowed and required. But the Skill MUST NOT generate
correlation logic yet. Correlation logic only comes after shortcuts exist, schema
is verified, data is queryable, dynamic fields are inspected, join candidates are
validated, and data freshness is checked (Stages 10–14).

## Stage 8 — Azure Monitor table discovery

Browse candidate Azure Monitor / Application Insights tables relevant to the
captured business goal. Use only real discovered scope/table values — never
fabricate table names. Use
[references/app-insights-table-reference.md](references/app-insights-table-reference.md)
to explain what each table means in business terms and which tables best fit the
stated goal.

### Discovery API fallback policy (REQUIRED)

The **primary** discovery mechanism is the Mirrored Catalog Discovery APIs. If
discovery appears incomplete, do NOT immediately conclude tables are missing or
switch to alternative metadata paths. First:

1. Verify mirror status.
2. Verify refresh / sync status.
3. Verify discovery scope.
4. Retry discovery.

Only after these checks may the Skill evaluate alternative metadata paths. See
[references/mirrored-catalog-reference.md](references/mirrored-catalog-reference.md).

## Stage 9 — Eventhouse / KQL database selection

Before asking the user to choose an Eventhouse, run **Eventhouse Recommendation
Mode**:

1. **Discover** available Eventhouses.
2. **Inspect** their available contents (tables, shortcuts, KQL databases).
3. **Score** each Eventhouse.
4. **Present** a recommendation.

Score each Eventhouse by:

- Relevant business tables
- Relevant telemetry tables
- Existing shortcuts
- Queryable tables
- KQL database availability
- Data freshness
- Alignment to the selected business goal

Present the result in this format:

```text
Recommended Eventhouse:
<EventHouseName>

Reason:
<why this Eventhouse was chosen>

Alternative Eventhouses:
<list>
```

The Skill MUST NOT auto-select, even when one Eventhouse scores highest. This is a
user decision (confirmation gate) — present the recommendation and **require
explicit confirmation** before proceeding. See
[references/eventhouse-shortcuts-reference.md](references/eventhouse-shortcuts-reference.md).

## Stage 10 — Shortcut planning

Plan OneLake shortcuts from the AzMon item's tables into the selected
Eventhouse / KQL database **before** any schema verification or join logic.
Present the plan and STOP for confirmation.

Key rules (see the shortcuts reference for detail):

- Keep source table names **exact** — a renamed shortcut may create the OneLake
  link but fail to register as a queryable KQL table.
- A OneLake link alone is **not** enough — the table must be registered as an
  external/queryable table.

### Shortcut acceleration (SHOULD)

Query acceleration on shortcuts SHOULD be treated as the preferred configuration
because testing showed schema verification, queryability validation, telemetry
sampling, correlation analysis, and Operations Agent preparation became
substantially faster when acceleration was enabled. Present the shortcut plan with
an acceleration column:

| Shortcut Name | Source | Target | Acceleration Supported (Yes/No) | Acceleration Enabled (Yes/No) |
|---------------|--------|--------|---------------------------------|-------------------------------|

If acceleration is disabled, explain the potential impact on: queryability
validation, schema verification, telemetry sampling, correlation analysis, and
Operations Agent preparation. Do NOT assume acceleration exists in every
environment — use SHOULD, not MUST. See
[references/eventhouse-shortcuts-reference.md](references/eventhouse-shortcuts-reference.md).

## Stage 11 — Shortcut creation

Only after explicit confirmation, create the planned shortcuts. Then verify each
table is actually **queryable** in Eventhouse (e.g. `<Table> | take 1`). Do not
assume a OneLake link means the table is queryable. If a table is not queryable →
STOP and return to shortcut planning / creation.

When creating ANY shortcut, apply the **Shortcut Acceleration Policy**:

1. Determine whether acceleration is supported for the shortcut.
2. Determine its current acceleration status.
3. Enable acceleration when supported and appropriate (preferred configuration).
4. Report the acceleration status back to the user.

## Stage 12 — Schema and data verification (REQUIRED before correlation)

Never build correlation logic on assumptions or screenshots. Before proposing any
join, bin, or threshold, verify against the **actual** KQL database.

### Preconditions

1. Operational telemetry tables are available in the Eventhouse / KQL database.
2. Business tables are available in the same database or queryable via shortcuts.
3. Tables are **queryable**, not just visible in OneLake.
4. Schema retrieved via `getschema`.
5. Dynamic fields inspected and sampled.
6. Candidate join keys extracted from top-level **and** dynamic columns.
7. Join keys validated with **non-zero** match results (when a direct join is
   proposed).
8. Data freshness verified.
9. Time window aligned to the real data range.
10. Relevant categorical values confirmed from real data where rules depend on
    them.

### Steps

1. **Retrieve real schema.** For each table:
   `TableName | getschema | project ColumnName, ColumnType`. Use authoritative
   column names/types — not names guessed from screenshots or table names.
2. **Inspect and sample dynamic fields.** Business join keys are often nested in
   dynamic columns (`Properties`, `CustomDimensions`, `Details`, `Measurements`,
   `Payload`, `Context`). Sample rows and inspect keys. See
   [references/app-insights-dynamic-fields-reference.md](references/app-insights-dynamic-fields-reference.md).
3. **Extract candidate business identifiers** with explicit KQL
   (`tostring(Properties.BookingId)`, with casing fallbacks via `coalesce`).
4. **Validate join keys against real data.** Prove a candidate joins — run the
   join and confirm non-zero matches:

   ```kusto
   AppEvents
   | extend BookingId = tostring(Properties.BookingId)
   | where isnotempty(BookingId)
   | join kind=inner (Bookings | project BookingId = tostring(BookingId)) on BookingId
   | summarize MatchedRows=count(), DistinctBookings=dcount(BookingId)
   ```

   Non-zero → **direct join, high confidence**. Zero → the key is wrong or data
   doesn't overlap; find the real one.
5. **Check freshness and align the window.**
   `TableName | summarize Rows=count(), MinTime=min(TimeGenerated), MaxTime=max(TimeGenerated)`.
   Do not assume `ago(1h)`; use a window covering the actual data range and
   explain it in user terms.
6. **Confirm categorical values** used by rules
   (`Table | summarize count() by <field>`) so impact rules use real categories.

### Telemetry source selection framework (REQUIRED)

Telemetry source selection MUST be **data-driven**. Before selecting a correlation
model, the Skill MUST inspect ALL candidate telemetry sources discovered (e.g.
AppEvents, AppExceptions, AppRequests, AppDependencies, AppTraces, AppPageViews,
AppBrowserTimings, AvailabilityResults, and any other telemetry source present) —
not just one.

Score each candidate telemetry source by:

1. Business identifiers discovered.
2. Dynamic-field richness.
3. Direct-join confidence.
4. Validated match count.
5. Business-process context.
6. Relevance to the selected business goal.

Select the highest-scoring telemetry source. The Skill MUST NOT automatically
prioritize AppExceptions, and MUST NOT automatically prioritize AppEvents — the
winner is whichever source scores highest against real data.

### Exit criteria (MANDATORY)

Before Stage 14, ALL must hold: schema retrieved via `getschema`; join keys
validated with non-zero matches (when a direct join is used); freshness verified
and window aligned; relevant categorical values confirmed. If any fails → STOP,
do not proceed.

### Handoff (MANDATORY)

Present a concise summary (verified join keys, match results, business impact if
any, data time window). Then ask: "Do you want to generate Operations Agent
instructions based on this verified model?" HARD STOP and wait.

## Stage 13 — Business data discovery and scoring

Business tables are **not** expected to exist in Log Analytics. They may live in
an Eventhouse, KQL database, Warehouse, Lakehouse, or via Fabric shortcuts.
Absence of business tables in Log Analytics MUST NOT be interpreted as absence of
business data — discover them across the available Fabric data surfaces.

If the exact business table or join is not known, do **not** fail. Score
candidate business databases/tables by likely relevance to the stated goal,
present a ranked list with explanation, propose shortcut creation for the top
candidates, and ask the user to choose. Never conclude "no correlation is
possible" without presenting options. Scoring hints are in
[references/business-correlation-patterns.md](references/business-correlation-patterns.md).

## Stage 14 — Correlation planning

The user should not need to know joins, KQL, thresholds, or bins. Infer
candidates from verified top-level schema, verified dynamic fields, sampled
values, business table schema, actual join test results, and data freshness. When
asking for confirmation, ask about **business meaning**, not SQL/KQL, e.g.:

> "I found `BookingId` inside `AppEvents.Properties` and it matches
> `Bookings.BookingId`. Should I treat this as the booking identifier for
> business impact?"

Present a correlation plan (see
[references/business-insight-modeling-reference.md](references/business-insight-modeling-reference.md))
including: operational signal; business impact table; verified join keys;
whether the join came from top-level columns or dynamic fields; match
count/validation result; time window + freshness status; proposed bin size;
metrics and thresholds. Then ask for confirmation and STOP.

Default modeling guidance: prefer **direct joins** when verified identifiers
exist; use **time-window correlation** only when no direct identifier is found
(and state clearly it shows correlation, not causality); default bin size **5
minutes** (larger when data is sparse or KPIs are slower); default derived entity
**IncidentBin**.

### Correlation planning validation (REQUIRED)

When finalizing the correlation model, the Skill MUST explain **why** the selected
telemetry source was chosen, justified using real data. Include:

- Match counts.
- Business identifiers discovered.
- Direct-join confidence.
- Validation results.

## Stage 15 — Operations Agent instruction generation

Only after the correlation model is confirmed. Produce a single clean copy/paste
block using the template in
[references/operations-agent-reference.md](references/operations-agent-reference.md).

> **Wrapper exemption:** deliver the instructions as ONE self-contained fenced
> block. Put the stage-visibility header and the
> `Waiting for your confirmation to continue.` line OUTSIDE the block (before and
> after it) — never inside — so the copy/paste block stays clean. See
> [Stage visibility](#stage-visibility-required-in-every-user-facing-response).

The instructions MUST include **explicit KQL**, not conceptual descriptions:

- One verbatim **IncidentBin materialization query** whose **output columns are
  exactly the alert fields** (includes the `tostring(Properties.x)` extraction,
  the explicit `join`, and the exact aggregations).
- A per-field KQL definition list mapping each output column to a one-line
  expression.
- Alert rules that reference **actual output columns**.

Include **both** threshold sets:

- **Production-like:** e.g. ErrorCount increases by more than 20% versus the
  previous-hour baseline; AffectedCustomers ≥ configured business threshold;
  RevenueAtRisk ≥ configured business threshold.
- **Relaxed POC / debug:** e.g. `ErrorCount >= 1`; `ErrorCount >= 5 and
  AffectedBusinessRecords >= 1`; `AffectedCustomers >= 1`.

Required sections: Goal, Data sources, Field mapping definitions, Dynamic field
extraction logic, Derived analysis entity, IncidentBin materialization query,
Metric definitions, Correlation logic, Impact logic (production + POC), Alert
behavior, Output requirements, Validation / POC mode.

### PLAYBOOK MATERIALIZATION REQUIREMENT (MANDATORY)

Testing demonstrated that instructions and KQL **text alone are NOT sufficient**
for playbook generation — the Fabric Playbook Generator must be able to discover
the alert fields directly from a real schema object. Before generating Operations
Agent rules, the Skill MUST:

1. **Materialize IncidentBins** as a real, queryable schema object (a physical
   table or a function-backed table in the KQL database).
2. **Expose the alert fields as physical output columns** on that object.
3. **Verify the schema exists** (e.g. `IncidentBins | getschema`).

Required output columns:

- IncidentBin
- ErrorCount
- AffectedCustomers
- AffectedBookings
- RevenueAtRisk
- TopImpactedEntities
- TopImpactedSteps

The Playbook Generator MUST be able to discover these fields directly from the
materialized schema. KQL instructions alone are NOT sufficient. Business-specific
column names (for example `AffectedBookings`, `TopImpactedFlights`) adapt to the
business entities actually discovered in the data.

## Stage 16 — Optional Operations Agent creation / validation

Offer to create the Operations Agent in the user's Fabric workspace and attach the
Eventhouse / KQL database that holds the verified data. Confirm target workspace
and KQL database before creating anything. Then guide validation (start with POC
thresholds so an alert fires on sparse seed data, then switch to production
thresholds). See
[references/operations-agent-reference.md](references/operations-agent-reference.md)
for creation surface and troubleshooting.

## Troubleshooting (user-facing)

- **"No playbook generated" / cannot compute a field** → the instructions
  described fields conceptually. Add the explicit KQL materialization query, add
  per-field KQL definitions, ensure alert rules reference actual output columns,
  add dynamic-field extraction, and clarify join keys/identifiers. Do not just
  reword prose.
- **Start succeeds but no Teams alert arrives** → likely no data matched the
  rule. Switch to POC/debug thresholds; explain no data may have matched. Do not
  imply platform failure without evidence.

## Must / Prefer / Avoid

### Must
- Enforce stages, hard stops, and confirmation gates.
- Keep OAuth (UI-guided) and Service Principal (automated create-or-reuse)
  strictly separate.
- Validate Sentinel and permissions before creation.
- Verify schema, dynamic fields, join matches, and freshness before correlation.
- Provide explicit KQL in Operations Agent instructions.

### Prefer
- Direct joins over time-window correlation.
- Real discovered values over anything guessed.
- Business-language confirmation over technical questions.

### Avoid
- Do not depend on or mention MCP in the user-facing flow.
- Do not present internal/undocumented APIs as supported.
- Do not claim OAuth connector creation via public API.
- Do not fabricate workspaces, tables, schema, IDs, or query results.
- Do not ask for secrets, OAuth codes, tokens, cookies, or nonces.
- Do not claim causality when only time-window correlation exists.

## Examples

### Example 1 — Onboard Azure Monitor observability data, then check business impact
**User:** "In my `Observability` workspace, onboard our Azure Monitor / Log Analytics observability data (it holds our Application Insights tables) into Fabric, then tell me whether last week's latency spike hurt checkout conversions."

**Skill behavior:** Runs the staged workflow — confirms the target workspace and checks onboarding prerequisites (a reachable Azure Monitor / Log Analytics source or connection), stopping with an explicit list of what is missing if any prerequisite is absent. Once the observability data (including the Application Insights telemetry tables) is onboarded and queryable, it discovers the real business dataset in the workspace, correlates the latency signal against the conversion KPI using discovered keys, and reports a specific business-impact conclusion (or an error if the required data is unavailable). It never fabricates workspace names, tables, or query results, and never exposes tokens or connection internals.

### Example 2 — Out-of-scope authoring request
**User:** "Create a new Spark notebook and build a Delta table pipeline to load my business dataset."

**Skill behavior:** Declines the out-of-scope authoring request, creates nothing in Fabric, and directs the user to the appropriate authoring skill (for example `spark-authoring-cli`) rather than taking over the task.
