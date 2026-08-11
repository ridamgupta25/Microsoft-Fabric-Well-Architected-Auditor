# Reading Lakehouse table column schemas — OneLake Table (Delta / Unity-Catalog) API

> **Offline reference.** Captured from the official Microsoft Fabric REST API
> documentation on Microsoft Learn (see [Sources](#sources)). This is the
> authoritative basis for how the auditor reads **table column schemas**, which
> the Fabric REST API does *not* expose. Kept in-repo so check authors and the
> crawl transport can be verified without a network round-trip.

---

## 1. The problem this solves

`GET /v1/workspaces/{ws}/lakehouses/{lh}/tables` (the Fabric REST *List Tables*
API) returns only each table's **name, type, format and location** — never its
**columns**:

```json
{
  "data": [
    {
      "type": "Managed",
      "name": "Table1",
      "location": "abfss://…@onelake.dfs.fabric.microsoft.com/…/Tables/Table1",
      "format": "Delta"
    }
  ]
}
```

Column names, data types and nullability therefore have to come from a **second
surface**. There are two, and the auditor uses them in this order:

1. **OneLake Table (Delta / Unity-Catalog) API** — plain HTTPS, no driver, no
   open port. **Primary source.** (This document.)
2. **SQL analytics endpoint (TDS, port 1433)** — needs an ODBC driver + `pyodbc`
   and an open 1433. **Fallback only**, and the only source of SQL type *widths*
   (`varchar(8000)`) and Warehouse row-level security. See
   [tls-evidence.md](tls-evidence.md).

When neither can be read, the column-level checks report **N/A (with a reason)** —
never FAIL. "We could not read it" ≠ "it is misconfigured".

---

## 2. The decisive fact — token audiences

Fabric surfaces are split across **four Microsoft Entra token audiences**. Using
the wrong audience is the single most common cause of a `401 Unauthorized`.

| Surface | Audience (scope) | Used for |
|---|---|---|
| **Fabric REST** | `https://api.fabric.microsoft.com/.default` | Workspaces, items, role assignments, `getDefinition`, List Tables |
| **OneLake (DFS / Blob / Table)** | `https://storage.azure.com/.default` | **OneLake Table API — reading column schemas** (this doc), DFS/Blob file access |
| **SQL analytics endpoint (TDS)** | `https://database.windows.net/.default` | Column type widths, Warehouse RLS over 1433 |
| **Power BI** | `https://analysis.windows.net/powerbi/api/.default` | Semantic-model refresh recency |

> **OneLake only accepts tokens in the `Storage` audience** — a *different* token
> from the Fabric bearer the crawl otherwise holds. The Fabric token is rejected
> by `onelake.table.fabric.microsoft.com`. This is the root cause the auditor
> originally missed: the OneLake reader must be handed a
> `https://storage.azure.com` token, not the Fabric one.

The Azure CLI public client (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) is
pre-authorized for all four, so every audience is minted **silently** from the
one sign-in refresh token — the reviewer is never prompted twice.

---

## 3. The OneLake Table API

Read-only metadata operations for **Delta** tables, compatible with the
[Unity Catalog open API standard](https://github.com/unitycatalog/unitycatalog/tree/main/api).

- **Base URL**: `https://onelake.table.fabric.microsoft.com/delta`
- **API version path**: `api/2.1/unity-catalog`
- **Auth**: `Authorization: Bearer <storage-audience token>` (§2)
- **Item address** — either form works:
  - GUIDs: `<workspaceId>/<dataItemId>` (safest — survives names with spaces)
  - Names: `<workspaceName>/<itemName>.Lakehouse` (only when names have no
    special characters)
- **Scope**: **schema-enabled lakehouses**. For a non-schema lakehouse, `schemas`
  returns `400` and the default `dbo` schema is assumed.

### 3.1 List schemas

```bash
GET {base}/{workspace}/{item}/api/2.1/unity-catalog/schemas?catalog_name={lh}.Lakehouse
Authorization: Bearer <storageToken>
```

```json
200 OK
{ "schemas": [ { "name": "dbo", "catalog_name": "testlh.Lakehouse",
                 "full_name": "testlh.Lakehouse.dbo" } ],
  "next_page_token": null }
```

`400` ⇒ the lakehouse is **not** schema-enabled → fall back to a single `dbo`
schema.

### 3.2 List tables (requires `catalog_name` **and** `schema_name`)

```bash
GET {base}/{workspace}/{item}/api/2.1/unity-catalog/tables?catalog_name={lh}.Lakehouse&schema_name=dbo
Authorization: Bearer <storageToken>
```

```json
200 OK
{ "tables": [ { "name": "product_table", "schema_name": "dbo",
                "data_source_format": "DELTA", "columns": null } ],
  "next_page_token": null }
```

> **`columns` is `null` here.** *List tables* never carries the schema; page with
> `next_page_token` → `&page_token=…`.

### 3.3 Get table — the call that carries `columns`

The table's four-part name is `<catalog>.<schema>.<table>`, where the catalog is
itself `<lakehouseName>.Lakehouse` (so the full name has a dot inside the catalog
segment, e.g. `testlh.Lakehouse.dbo.product_table`). URL-encode the whole
segment.

```bash
GET {base}/{workspace}/{item}/api/2.1/unity-catalog/tables/testlh.Lakehouse.dbo.product_table
Authorization: Bearer <storageToken>
```

```json
200 OK
{
  "name": "product_table",
  "data_source_format": "DELTA",
  "columns": [
    { "name": "product_id",   "type_text": null, "type_name": "string",
      "type_precision": 0, "type_scale": 0, "position": 0, "nullable": true },
    { "name": "product_name", "type_text": null, "type_name": "string",
      "type_precision": 0, "type_scale": 0, "position": 1, "nullable": true }
  ]
}
```

Per-column fields the auditor consumes:

| Field | Meaning | Auditor use |
|---|---|---|
| `name` | Column name | `name` |
| `type_text` | Rendered type (`decimal(18,2)`, `timestamp`) — **may be `null`** | preferred `type` |
| `type_name` | Coarse enum (`string`, `long`) | `type` fallback when `type_text` is null |
| `nullable` | Nullability | `nullable` |

> In practice `type_text` is often `null` and only `type_name` is populated, so
> the reader **prefers `type_text`, then falls back to `type_name`** (both
> lower-cased).

---

## 4. How the auditor uses it

| Concern | Where |
|---|---|
| API client (schemas → tables → get-table; `400`→`dbo`; pagination; parse `columns`) | [clients/onelake.py](../backend/src/auditfast/clients/onelake.py) |
| Storage-token transport + wiring; OneLake tried first, TDS fallback | [clients/live.py](../backend/src/auditfast/clients/live.py) (`_get_onelake`, `_read_onelake_columns`) |
| Mint the `https://storage.azure.com` token silently | [services/auth_service.py](../backend/src/auditfast/services/auth_service.py) (`onelake_token_for`) |
| Thread the token into the provider each run + background refresh | [services/audit_runner.py](../backend/src/auditfast/services/audit_runner.py), [services/audit_service.py](../backend/src/auditfast/services/audit_service.py) (`build_provider`, `run_audit`) |

**Fail-safe chain** (never a false FAIL):

```
OneLake Table API (storage token)
  └─ no token / 401 / 400-not-schema / network error / no lakehouses
       └─ SQL/TDS endpoint (pyodbc + 1433)
            └─ still nothing → column checks report N/A (with reason)
```

OneLake failures are **diagnostic only** — they are logged and never written to
`WorkspaceContext.read_failures`, so they cannot poison `is_complete` or cause an
incomplete snapshot to be cached.

### Delegated scopes / permissions

- Reading columns needs OneLake **read** access to the lakehouse (a workspace
  role such as Viewer/Contributor is enough) plus a `storage.azure.com` token.
- No tenant-admin, capacity-metrics or audit-log permission is required.
- *List Tables* (Fabric REST, used for discovery) needs `Lakehouse.Read.All` or
  `Lakehouse.ReadWrite.All`.

### Why "I can see the columns in my workspace" but the crawl couldn't

The data exists and the signed-in user can read it — the gap was purely the
**auditor's transport**: it was sending the Fabric-audience token to
`onelake.table.fabric.microsoft.com`, which only accepts a Storage-audience
token, so every OneLake call returned `401` and the columns silently fell through
to the (uninstalled) TDS path. Minting and using the Storage token fixes it.

> **Note:** `auditfast serve` does **not** hot-reload — restart the API after this
> change, then re-crawl (or force `refresh=True`) so a fresh KB snapshot is built
> with the corrected transport.

---

## 5. Sources

Captured from Microsoft Learn (Microsoft Fabric REST API documentation):

- **Getting started with OneLake table APIs for Delta** — base URL, `schemas` /
  `tables` / get-table requests + responses, the `columns` array shape.
  <https://learn.microsoft.com/en-us/fabric/onelake/table-apis/delta-table-apis-get-started>
- **How do I connect to OneLake?** — "OneLake only supports tokens in the
  `Storage` audience"; `Get-AzAccessToken -ResourceTypeName Storage`.
  <https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api>
- **Lakehouse schemas** — schema-enabled lakehouses; "To list tables, schemas, or
  get table details, use the OneLake table APIs for Delta."
  <https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas>
- **Tables - List Tables (Lakehouse REST API)** — the Fabric surface that returns
  name/type/location but **not** columns; scopes `Lakehouse.Read.All`.
  <https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/tables/list-tables>
- **Fabric REST API scopes / token audiences** —
  <https://learn.microsoft.com/en-us/rest/api/fabric/articles/scopes>
- Vendored companion: `fabric-skills/common/SPARK-CONSUMPTION-CORE.md`
  ("OneLake Table APIs (Schema-enabled Lakehouses)") and
  `fabric-skills/common/COMMON-CORE.md` (token-audience table).
