# Azure Monitor → Fabric API reference

Guidance on which flows are **documented/supported** versus **UI-only** or
**internal/unsupported**, and the rules for the two connector modes. This file is
agent guidance — do not paste it verbatim to users.

## Supported vs UI-only vs internal — at a glance

| Capability | Status | Notes |
|-----------|--------|-------|
| Mirrored Catalog item CRUD | Documented REST API | See mirrored-catalog-reference.md |
| Mirrored Catalog item definition | Documented REST API | Item definition format |
| Mirrored Catalog discovery | Documented REST API | Browse scopes/tables |
| Mirrored Catalog monitoring | Documented REST API | Item/table mirroring status |
| Mirrored Catalog refresh / sync | Documented REST API | Trigger metadata sync |
| Operations Agent item CRUD + definition | Documented Fabric item APIs | `type: "OperationsAgent"` |
| **OAuth** Azure Monitor connector creation | **UI-only** | Created once in Fabric → Manage Connections. **No public API.** Detect + reuse only. |
| **Service Principal** connector create-or-reuse | Automated | Idempotent create-or-reuse; secrets from env/Key Vault only |
| OneLake shortcut creation | Documented, but see caveat | Link alone does not register a queryable KQL table — see eventhouse-shortcuts-reference.md |

## Portal fallback boundary

Portal-guided instructions are allowed ONLY for OAuth Azure Monitor connector creation.

Portal guidance must NOT be used as a generic fallback for:

- Log Analytics workspace validation
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

Before declaring a capability unavailable, the Skill should determine whether another supported execution path exists:

- Fabric REST APIs
- Azure REST APIs
- Fabric Actions
- Azure CLI
- Azure Resource Graph
- Fabric REST **read-only** discovery via authenticated `az rest --method get`
  against `https://api.fabric.microsoft.com/...`

MCP unavailability does not automatically imply capability unavailability.

### Fabric REST read-only discovery exception (bounded)

Arbitrary shell / CLI / `az` / PowerShell execution remains out of scope. There
is ONE narrow exception: the Skill MAY use an authenticated `az rest --method
get` call for Fabric REST **read-only discovery** only, when ALL of these hold:

- Endpoint is `https://api.fabric.microsoft.com/...`.
- HTTP method is **GET** only.
- The operation is discovery / read-only only.
- Nothing is created, updated, deleted, or modified — no Fabric items, shortcuts,
  mirrored catalog items, or connectors are created through the CLI.
- No tokens, secrets, raw auth headers, or sensitive payloads are exposed.
- The Skill clearly states the capability path used.

This exception is limited to Stage 3 (Fabric workspace discovery) and Stage 5
(Azure Monitor OAuth connection detection). It does not relax any other stage's
boundary, does not authorize non-GET `az rest` calls, and does not permit use of
the Kusto / KQL data-plane when disabled.

## Connector rules (authoritative)

Keep the two modes strictly separated. Never route OAuth through Service
Principal logic and never route Service Principal through OAuth/interactive
sign-in logic.

### OAuth mode (UI-guided only)

- OAuth connector **creation** is interactive in **Fabric → Manage Connections**.
- The Skill only **detects** and **reuses** an existing Azure Monitor OAuth
  connection. Detection is **non-destructive** (never create/update/delete).
- **Never** claim public-API support for OAuth Azure Monitor connector creation.
- **Never** document browser-inspected / internal connector endpoints, hidden
  payloads, `CredentialType` internals, OAuth codes, tokens, cookies, nonces, or
  redirect URLs as supported public APIs, and never surface them to the user.
- Reuse a connection ONLY when the data source path / LAW resource id matches the
  **same** Log Analytics workspace exactly. Any mismatch → treat as no match.
- If the user explicitly supplies a connection id, validate it (must be an Azure
  Monitor Mirrored Catalog connection whose data source path matches this
  workspace) before reusing. Reject mismatches.

User-facing message when no connection exists (keep it this simple):

```text
I couldn’t find an existing Azure Monitor connection for this workspace.
Please create it once in Fabric Manage Connections, then come back and continue.
```

### Service Principal mode (automated create-or-reuse)

- Automated, non-interactive: no user login, no UI step.
- **Idempotent**: reuse an existing matching Azure Monitor Service Principal
  connector for the same Log Analytics workspace (same data source path +
  Service Principal credential type) instead of creating a duplicate.
- Only **one** connector per run.
- **Never** reuse a non-Service-Principal (e.g. OAuth) connector in this mode.
- **Never** ask the user to paste a raw client secret into chat.
- Secrets come from **environment variables or Key Vault references** only; they
  are never echoed, logged, exposed, or included in generated instructions.
- If required inputs are missing, describe **what** is missing (tenant id, app /
  client id, and a securely-provided secret reference) using presence checks
  only. Never request the secret value in chat.

## Supported-scope / validation rules

Before creating anything, verify: the workspace exists; it is **not Sentinel** or
otherwise unsupported; the caller has required Log Analytics access; the caller
can create the item in the target Fabric workspace. Surface pass/fail in user
terms; never expose raw API responses.

### Sentinel detection source hierarchy

Sentinel detection is a **control-plane** check and MUST NOT default to requiring
Kusto / KQL data-plane availability. Before classifying Sentinel status as "not
verifiable", attempt all available control-plane / metadata checks, in order:

1. Resource group inventory / ARM resource enumeration.
2. `Microsoft.OperationsManagement/solutions` named `SecurityInsights(<workspaceName>)`.
3. `Microsoft.SecurityInsights/*` resources, including `onboardingStates` if surfaced.
4. Workspace / resource feature metadata, if available.

Indicators found → **Sentinel / blocked**. Indicators absent after complete
enumeration → **Not Sentinel, verified via control-plane**. Only if all these
checks are unavailable or inconclusive → **not verifiable**. Kusto may be an
additional path but is never a required dependency for Sentinel detection. This
hierarchy does NOT generalize to Log Analytics data-plane/query permission (may
require a KQL/data-plane path) or Fabric item-creation permission (may require a
Fabric control-plane capability).

## Fabric workspace discovery & capability resolution

Fabric workspaces are **not** Azure Resource Manager resources. They cannot be
enumerated through Azure resource listing, Azure Resource Graph, or `az`
resource commands, so an ARM-only environment reaching Azure resources does
**not** imply a Fabric control-plane path exists — and the reverse also holds:
lack of automatic enumeration does NOT mean no Fabric workspace exists.

Stage 3 (Fabric workspace selection) MUST follow this policy (SKILL.md links here
for the full detail):

- Discover all surfaced Fabric mechanisms first: Fabric REST APIs, Fabric
  Actions, Fabric / OneLake / Power BI execution capabilities, authenticated
  Fabric REST read-only discovery via `az rest --method get` against
  `https://api.fabric.microsoft.com/...` (GET-only, read-only, no modifications,
  no secret/token/header exposure, capability path stated), or any other
  documented capability provider available to the agent environment.
- If automatic enumeration is unavailable, do NOT terminate. Distinguish "no
  Fabric control-plane capability at all" from "enumeration unavailable but the
  workflow can continue with user-provided workspace info".
- Ask the user for a **Fabric Workspace Name**, **Workspace ID**, or **Workspace
  URL**, then validate it as far as the available capabilities allow.
- Never fabricate a workspace, workspace id, or validation result, and never
  claim a Fabric action succeeded when the required execution capability is
  unavailable.
- Mark Stage 3 BLOCKED only after every programmatic discovery path AND every
  user-supplied resolution path (Name / ID / URL) is exhausted. UI-guided
  selection is permitted only as the final fallback.

## Fabric Operations Agent surface

Operations Agent is a first-class Fabric item (`type: "OperationsAgent"`). It can
be created and populated through documented Fabric item APIs (create item, then
set the definition with the instructions and the KQL database data source). Teams
notifications are a built-in action — no custom channel binding is required for
basic alerts. See operations-agent-reference.md.

## External references

- [Items - REST API (MirroredCatalog)](https://learn.microsoft.com/en-us/rest/api/fabric/mirroredcatalog/items)
- [Mirrored Catalog item definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/mirrored-catalog-definition)
- [Discovery - REST API (MirroredCatalog)](https://learn.microsoft.com/en-us/rest/api/fabric/mirroredcatalog/discovery)
- [Monitoring - REST API (MirroredCatalog)](https://learn.microsoft.com/en-us/rest/api/fabric/mirroredcatalog/monitoring)
- [Refresh - REST API (MirroredCatalog)](https://learn.microsoft.com/en-us/rest/api/fabric/mirroredcatalog/refresh)

> The Mirrored Catalog references above cover item CRUD, item definition,
> discovery, monitoring, and refresh/sync operations. They do **not** document
> OAuth Azure Monitor connector creation. Treat OAuth connector creation as
> UI-guided only.
