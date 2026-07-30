# Eventhouse & OneLake shortcuts reference

How the mirrored Azure Monitor tables become **queryable** in an Eventhouse / KQL
database, and the queryability requirement that must be verified before any
correlation logic.

## Eventhouse / KQL database

An Eventhouse hosts one or more KQL databases queried with KQL. The onboarding
flow lands Azure Monitor telemetry (and, via additional shortcuts, business
tables) into a KQL database so telemetry and business data can be correlated with
KQL.

- **Eventhouse / KQL database selection is a user decision.** Always present the
  discovered options and require explicit confirmation. Never auto-select, even
  when discovery has a recommended default.
- The Eventhouse query URI (`queryServiceUri` in Eventhouse properties) is where
  `getschema`, sampling, and validation queries run during schema verification.

## OneLake shortcuts — the queryability requirement (CRITICAL)

Making a mirrored table queryable requires **two** things:

1. **Exact source table name.** A renamed shortcut (e.g. `LAQS_AppTraces` for
   source `AppTraces`) creates the OneLake link but may NOT register as a
   queryable KQL table (e.g. `General_BadRequest: Request is invalid`). Keep the
   exact source table name.
2. **The table must be registered as a queryable/external table.** A OneLake link
   alone is not enough. The Fabric **UI "Add table"** flow both creates the
   shortcut and registers the external table, so it becomes queryable. Creating
   only the OneLake link means the table will not appear in
   `.show external tables` and queries return 400.
3. **Mirrored AzMon tables live under a `dbo` schema folder.** In the mirrored
   catalog item's OneLake, tables are nested at `Tables/dbo/<TableName>`, not
   directly under `Tables/<TableName>`. A shortcut's OneLake target path must
   therefore include the schema segment (`target.oneLake.path =
   "Tables/dbo/<TableName>"`). Confirm the exact folder by listing `Tables/`
   first — it typically contains a single `dbo` directory holding every table.

## Shortcut planning and creation (stage rules)

- **Shortcut planning happens before schema verification or join logic.** Present
  the plan and STOP for confirmation.
- **Shortcut creation requires explicit confirmation.**
- **Handle "already exists" idempotently.** If shortcut creation returns a
  conflict (e.g. HTTP 409 `EntityConflict` / `ShortcutsOperationNotAllowed` when
  the conflict policy is `Abort`), do **not** treat it as a failure and do **not**
  overwrite. A shortcut of that name already exists — verify it points to the
  intended source and is queryable (`.show external tables` + `TableName | take 1`),
  then proceed. Only recreate if the existing shortcut targets a different source.
- After creation, **verify queryability** with a lightweight query before building
  any correlation logic. Stage 11 validates **queryability, NOT table size**, so
  use a single-row probe rather than a full scan:

  ```kusto
  TableName
  | take 1
  ```

  (`TableName | limit 1` is equivalent.) Avoid `TableName | count` here — on
  large external Delta tables a `count` forces an unnecessary full scan, causing
  long execution times and validation delays.

- If a table is not queryable → **STOP** and return to shortcut planning /
  creation. Do not build correlation logic on screenshots or assumed schema.
- A brand-new AzMon item won't surface tables until its mirror has materialized
  them; wait/refresh before expecting queryable tables.

## Shortcut acceleration policy (SHOULD)

Query acceleration SHOULD be treated as the preferred configuration for newly
created shortcuts. Testing showed significant performance improvements once
acceleration was enabled — schema verification, queryability validation,
telemetry sampling, correlation analysis, and Operations Agent preparation all
became substantially faster.

When creating ANY shortcut:

1. Determine whether acceleration is **supported**.
2. Determine the **current** acceleration status.
3. **Enable** acceleration when supported and appropriate.
4. **Report** the acceleration status.

During Shortcut Planning (Stage 10), show for each shortcut: Shortcut Name,
Source, Target, Acceleration Supported (Yes/No), Acceleration Enabled (Yes/No). If
acceleration is disabled, explain the potential impact on queryability validation,
schema verification, telemetry sampling, correlation analysis, and Operations
Agent preparation.

This is a **SHOULD**, not a MUST — do not assume acceleration exists in every
environment.

## Verify external table registration

After adding tables, confirm registration and queryability:

```kusto
.show external tables
```

```kusto
TableName
| take 1
```

Stage 11 validates queryability, NOT table size. Prefer the single-row probe
`TableName | take 1` (or `TableName | limit 1`). Avoid `TableName | count`, which
forces a full scan of large external Delta tables and slows validation.

If a table is missing from `.show external tables`, it was linked but not
registered — re-add it via the UI "Add table" flow (which both links and
registers), keeping the exact source name.
