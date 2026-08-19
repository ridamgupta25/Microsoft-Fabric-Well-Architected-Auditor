"""Read-only SQL analytics endpoint client — column schemas and security policies.

Some data a Well-Architected audit needs is **not in the Fabric REST API at all**.
Column names and types, and Warehouse row-level-security policies, live only behind
the SQL analytics endpoint that Fabric provisions for every Lakehouse and Warehouse.
This module is the transport for that.

Three things make it safe to run against a client tenant:

1. **Nothing is ever asked of the user.** The endpoint address is *discovered* over
   plain Fabric REST (``properties.sqlEndpointProperties.connectionString`` on a
   lakehouse, ``properties.connectionString`` on a warehouse) with the token the
   crawl already holds. No connection string, no PAT, no app registration.
2. **Every failure degrades to ``None``.** A blocked port 1433, a missing ODBC
   driver, an unconsented token audience, a throttled tenant — all of them return
   ``None`` with a recorded reason so the checks report **N/A**, never FAIL. The
   audit behaves exactly as it did before this module existed.
3. **Strictly read-only.** Only ``SELECT`` against ``INFORMATION_SCHEMA`` and
   ``sys.*`` catalog views. Nothing here writes, and the endpoint is read-only by
   design anyway.

Connection requirements that are easy to get wrong (see
``fabric-skills/common/SQLDW-CONSUMPTION-CORE.md``):

* Token audience is ``https://database.windows.net`` — a *different* audience from
  both the Fabric and Power BI tokens.
* ``Database`` must be the item's **display name**, not the server FQDN.
* ``Encrypt=Yes`` is required; **MARS must be off** (it is unsupported and fails in
  a confusing way).
"""
from __future__ import annotations

import contextlib
import logging
import struct
import time
from typing import Any

log = logging.getLogger("auditfast.sqlendpoint")

#: ODBC attribute that carries an Entra access token (``SQL_COPT_SS_ACCESS_TOKEN``).
_SQL_COPT_SS_ACCESS_TOKEN = 1256

#: Per-connection and per-query ceilings. A slow endpoint must not stall a crawl,
#: but the login timeout has to absorb Azure SQL's gateway redirect and Entra
#: token validation, which are slower than a plain SQL Server handshake.
_CONNECT_TIMEOUT_SECONDS = 30
_QUERY_TIMEOUT_SECONDS = 30

#: Microsoft's guidance: beyond roughly this many warehouses + SQL endpoints in one
#: workspace the Entra token can exceed its size limit. We read what we can and
#: record the rest as unread rather than failing the whole crawl.
MAX_ENDPOINTS_PER_WORKSPACE = 40

#: How many times to re-attempt one endpoint read before giving up, and how long
#: to wait between attempts.
#:
#: Without this a single transient failure lost a whole store's schema for the
#: entire audit: two crawls of the same 105-endpoint workspace a day apart read
#: 502 and then 307 tables, and the second lost Warehouse RLS completely. The
#: verdicts moved with it (4.2.5 went PARTIAL -> FAIL) purely because less data
#: was read, which reads as "the estate got worse" when nothing changed.
#:
#: Only *transient* failures are retried. A permission denial or a blocked port
#: will fail identically on a second attempt, so retrying it just doubles the
#: time a large workspace takes to crawl.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 2.0

#: Failure reasons worth a second attempt: throttling, a dropped connection, and
#: timeouts. Matched against :func:`_classify`'s output, so the vocabulary stays
#: in one place.
_RETRYABLE_REASONS = (
    "did not finish in time",
    "not reachable",
    "connection",
    "timeout",
    "timed out",
    "throttl",
    "too many requests",
    "transport",
)


def _is_auth_failure(reason: str) -> bool:
    """True when the reason looks like a rejected/expired token.

    An Entra access token lives about an hour. A large workspace takes longer
    than that to crawl, which is exactly why the Fabric REST client carries a
    refresher. pyodbc reports a dead token as ``Login failed for user
    '<token-identified principal>'``, which :func:`_classify` renders as an
    access problem - indistinguishable from a genuine permission denial, and
    deliberately *not* retryable, since retrying with the same dead token
    changes nothing. Re-minting the token is the only thing that helps.
    """
    return "no access to this database" in (reason or "").lower()


def _is_retryable(reason: str) -> bool:
    """True when a failure reason describes a condition that may clear on retry.

    A permission problem ("no access") and the Entra token-size limit are
    deliberately excluded: they are deterministic for a given token, so a second
    attempt costs time and changes nothing.
    """
    text = (reason or "").lower()
    if "no access" in text or "system limit" in text or "too many warehouses" in text:
        return False
    return any(marker in text for marker in _RETRYABLE_REASONS)


class SqlEndpoint:
    """One queryable SQL database: where it is, what it is called, and its kind.

    ``kind`` matters beyond bookkeeping: a Lakehouse SQL endpoint maps every Delta
    ``string`` column to ``varchar(8000)`` whatever the author intended, so a
    declared width is only a *design choice* on a Warehouse. Checks that judge
    column widths need to know which they are looking at.
    """

    __slots__ = ("kind", "name", "server", "status")

    def __init__(self, kind: str, name: str, server: str, status: str = "Success"):
        self.kind = kind          # "Lakehouse" | "Warehouse"
        self.name = name          # database name == item display name
        self.server = server      # FQDN, no port
        self.status = status

    @property
    def queryable(self) -> bool:
        """True once Fabric has finished provisioning the endpoint."""
        return (self.status or "").strip().lower() == "success"

    @property
    def host(self) -> str:
        """Bare FQDN, with any ``tcp:`` prefix or ``,port`` suffix removed.

        Fabric returns a plain host today, but a connection string is allowed to
        carry either. Appending ``,1433`` to a value that already has a port
        yields ``host,1433,1433``, which the driver cannot parse and which
        surfaces only as a connection timeout - indistinguishable from a blocked
        port. Normalising here makes that impossible.
        """
        text = (self.server or "").strip()
        if text.lower().startswith("tcp:"):
            text = text[4:]
        return text.split(",")[0].strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SqlEndpoint({self.kind} {self.name!r} @ {self.server[:40]}…)"


def discover_endpoints(get_json, workspace_id: str) -> list[SqlEndpoint]:
    """Every SQL endpoint in a workspace, via the Fabric REST API.

    ``get_json`` is a ``(path) -> dict`` callable owned by the caller, so this
    module never does its own HTTP or auth. Type-specific list endpoints are used
    deliberately: the generic ``/items`` list does not carry connection strings.

    A discovery failure yields an empty list, not an exception — no endpoints is
    simply "no column data", which the checks already handle as N/A.
    """
    endpoints: list[SqlEndpoint] = []

    try:
        for item in (get_json(f"/workspaces/{workspace_id}/lakehouses") or {}).get("value", []):
            props = (item.get("properties") or {}).get("sqlEndpointProperties") or {}
            server = (props.get("connectionString") or "").strip()
            if server:
                endpoints.append(SqlEndpoint(
                    "Lakehouse", item.get("displayName") or "", server,
                    props.get("provisioningStatus") or "",
                ))
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        log.warning("sql endpoint discovery (lakehouses) failed for %s: %s - "
                    "those lakehouses will have no column data", workspace_id, exc)

    try:
        for item in (get_json(f"/workspaces/{workspace_id}/warehouses") or {}).get("value", []):
            props = item.get("properties") or {}
            server = (props.get("connectionString") or "").strip()
            if server:
                endpoints.append(SqlEndpoint(
                    "Warehouse", item.get("displayName") or "", server, "Success",
                ))
    except Exception as exc:  # noqa: BLE001
        log.warning("sql endpoint discovery (warehouses) failed for %s: %s - "
                    "Warehouse security policies will not be read", workspace_id, exc)

    return [e for e in endpoints if e.queryable and e.name]


def _token_struct(access_token: str) -> bytes:
    """Pack an Entra token the way the ODBC driver expects it."""
    raw = access_token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


class SqlEndpointReader:
    """Runs read-only catalog queries against SQL endpoints.

    Constructed with the SQL-audience token. Every public method returns ``None``
    on any failure and records why in :attr:`failures`, so a caller can report an
    accurate reason instead of an empty result that looks like "nothing found".
    """

    def __init__(self, sql_token: str | None, token_provider=None):
        self._token = sql_token
        #: Re-mints the SQL-audience token when the current one is rejected.
        #: Optional: without it the reader behaves exactly as before.
        self._token_provider = token_provider
        #: ``endpoint name -> reason it could not be read``.
        self.failures: dict[str, str] = {}
        self._driver: str | None = None
        self._unavailable: str | None = None
        if not sql_token:
            self._unavailable = (
                "no SQL-audience token (the sign-in did not yield a "
                "https://database.windows.net token)"
            )

    # -- availability ---------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when a token and an ODBC driver are both present."""
        return self._unavailable is None and self._resolve_driver() is not None

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable or "unknown"

    def _resolve_driver(self) -> str | None:
        """Newest installed SQL Server ODBC driver, or None (recording why)."""
        if self._driver is not None:
            return self._driver
        try:
            import pyodbc
        except ImportError:
            self._unavailable = "pyodbc is not installed on the server"
            return None
        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
        if not drivers:
            self._unavailable = "no 'ODBC Driver for SQL Server' is installed"
            return None
        self._driver = next((d for d in drivers if "18" in d), drivers[-1])
        return self._driver

    # -- queries --------------------------------------------------------------

    def columns(self, endpoint: SqlEndpoint) -> dict[str, list[dict[str, Any]]] | None:
        """``table key -> [{name, type, ...}]`` for one endpoint, or None.

        Warehouse keys retain their SQL schema as ``<schema>.<table>``. A
        Lakehouse keeps its bare table name so SQL columns merge with the table
        inventory already read from the Fabric REST API.

        ``type`` is rendered the way the table checks expect: ``varchar(8000)``,
        ``decimal(18,2)``, ``int``. ``source_kind`` travels with every column so a
        check can tell a Lakehouse's forced ``varchar(8000)`` from a width a
        Warehouse author actually chose.

        ``is_masked`` comes from ``sys.columns`` and records whether Dynamic Data
        Masking is applied. It is read with a ``LEFT JOIN`` so a Lakehouse
        endpoint - where the catalog view may be absent or empty - still returns
        every column, just with ``is_masked`` false.

        ``schema`` carries ``INFORMATION_SCHEMA.TABLE_SCHEMA``. It is recorded on
        the column rather than folded into the table key: the key is the join
        point between this reader and the REST item listing, and re-shaping it to
        ``schema.table`` would invalidate every snapshot already in the knowledge
        base. Checks that need the schema read it off any column.
        """
        rows = self._query(endpoint, """
            SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
                   c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE,
                   c.IS_NULLABLE, c.ORDINAL_POSITION,
                   COALESCE(sc.is_masked, 0) AS is_masked,
                   c.TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.COLUMNS AS c
            LEFT JOIN sys.columns AS sc
                   ON sc.object_id = OBJECT_ID(
                          QUOTENAME(c.TABLE_SCHEMA) + '.' + QUOTENAME(c.TABLE_NAME))
                  AND sc.name = c.COLUMN_NAME
            ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
        """)
        if rows is None:
            # ``sys.columns`` is not guaranteed on every endpoint kind. Fall back
            # to the plain projection rather than losing every column schema:
            # masking is one check, columns feed a dozen.
            rows = self._query(endpoint, """
                SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                       IS_NULLABLE, ORDINAL_POSITION, 0 AS is_masked, TABLE_SCHEMA
                FROM INFORMATION_SCHEMA.COLUMNS
                ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
            """)
        if rows is None:
            return None
        tables: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            schema = str(row[0] or "")
            table_name = str(row[1] or "")
            if not table_name:
                continue
            table = (
                f"{schema}.{table_name}"
                if endpoint.kind == "Warehouse" and schema
                else table_name
            )
            column: dict[str, Any] = {
                "name": str(row[2] or ""),
                "type": _render_type(row[3], row[4], row[5], row[6]),
                "nullable": str(row[7] or "").upper() == "YES",
                "source_kind": endpoint.kind,
            }
            # Only recorded when true, so a snapshot does not grow by a false
            # flag on every one of tens of thousands of columns.
            if len(row) > 9 and row[9]:
                column["is_masked"] = True
            if len(row) > 9 and row[9]:
                column["schema"] = str(row[9])
            tables.setdefault(table, []).append(column)
        return tables

    def security_policies(self, endpoint: SqlEndpoint) -> list[dict[str, Any]] | None:
        """Row-level-security policies on a Warehouse, or None if unreadable.

        An empty list is a real answer - "this warehouse defines no RLS policy" -
        and is different from ``None``, which means we could not look.
        """
        rows = self._query(endpoint, """
            SELECT p.name, p.is_enabled, OBJECT_NAME(d.target_object_id)
            FROM sys.security_policies AS p
            LEFT JOIN sys.security_predicates AS d
                   ON d.object_id = p.object_id
        """)
        if rows is None:
            return None
        return [
            {"policy": str(r[0] or ""), "enabled": bool(r[1]), "table": str(r[2] or "")}
            for r in rows
        ]

    def metadata(self, endpoint: SqlEndpoint) -> dict[str, list[tuple]]:
        """Every remaining catalog read for one endpoint, in a **single** connection.

        **Why one batch.** A connection costs far more than a query here: each
        pays a login handshake plus the Azure SQL gateway redirect, which is why
        :meth:`_attempt` opens a fresh connection per call and why crawl time
        tracks connection *count*, not row count. Issuing these reads separately
        would multiply the expensive part by the number of statements. Sending
        one batch and walking the result sets with ``nextset()`` pays the
        handshake **once** and adds only query time - milliseconds each, since
        every statement reads a catalog view rather than user data.

        Returns ``{name: rows}`` for whatever the endpoint answered; a name is
        absent when its statement returned nothing usable. Never raises: a
        connection failure yields ``{}`` and is already recorded by
        :meth:`_attempt`, because this is *additional* metadata - losing it must
        not cost the column schemas that the same crawl depends on.
        """
        statements = _METADATA_STATEMENTS
        if endpoint.kind == "Warehouse":
            # Foreign keys and key constraints are documented for Fabric
            # Warehouse only; a Lakehouse SQL endpoint does not expose them.
            statements = statements + _WAREHOUSE_METADATA_STATEMENTS
        batch = ";\n".join(sql.strip() for _name, sql in statements)
        rows_per_set = self._query_batch(endpoint, batch, len(statements))
        if rows_per_set is None:
            return {}
        return {
            name: rows
            for (name, _sql), rows in zip(statements, rows_per_set, strict=False)
            if rows
        }

    def _query_batch(self, endpoint: SqlEndpoint, sql: str,
                     expected: int) -> list[list[tuple]] | None:
        """Run a multi-statement batch, returning one row list per result set.

        Uses the same retry and token-refresh path as :meth:`_query`. A statement
        the endpoint rejects aborts the batch, so this returns whatever arrived
        before the failure rather than nothing - a Lakehouse endpoint missing one
        view should still yield the reads that preceded it.
        """
        return self._query(endpoint, sql, multi=True, expected=expected)

    def _query(self, endpoint: SqlEndpoint, sql: str, *, multi: bool = False,
               expected: int = 1):
        """Run one read-only query, retrying a transient failure. None on failure.

        With ``multi``, ``sql`` is a batch of statements and the result is a list
        of row lists - one per result set - rather than a single row list.

        A transient failure - a throttle, a dropped connection, a slow gateway -
        previously lost a whole store's schema for the entire audit. Each attempt
        opens a fresh connection, because a half-open one is often what failed.

        An *expired* token is handled separately: it is not transient (retrying
        with the same dead token fails identically), so it gets one re-mint and
        one extra attempt, outside the retry budget. Without this a crawl longer
        than the token's ~1h life reads the first N endpoints and loses every one
        after, which looks exactly like throttling but is not.
        """
        last_reason = ""
        refreshed = False
        # Only pass the batch kwargs when a batch was asked for. A plain read
        # then calls ``_attempt`` with its original signature, so a test double
        # (or any other caller) written against that signature keeps working -
        # threading a new kwarg through unconditionally broke eight of them.
        extra = {"multi": True, "expected": expected} if multi else {}
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            rows = self._attempt(endpoint, sql, **extra)
            if rows is not None:
                if attempt > 1:
                    # The endpoint recovered, so it is no longer a failure.
                    self.failures.pop(endpoint.name, None)
                    log.info("sql endpoint read for %s succeeded on attempt %d",
                             endpoint.name, attempt)
                return rows
            last_reason = self.failures.get(endpoint.name, "")

            if not refreshed and self._token_provider and _is_auth_failure(last_reason):
                refreshed = True
                if self._refresh_token():
                    rows = self._attempt(endpoint, sql)
                    if rows is not None:
                        self.failures.pop(endpoint.name, None)
                        return rows
                    last_reason = self.failures.get(endpoint.name, "")

            if attempt == _MAX_ATTEMPTS or not _is_retryable(last_reason):
                break
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            log.info("retrying sql endpoint read for %s (attempt %d of %d) - %s",
                     endpoint.name, attempt + 1, _MAX_ATTEMPTS, last_reason)
        return None

    def _refresh_token(self) -> bool:
        """Re-mint the SQL token. True when a *different* token was obtained."""
        try:
            new_token = self._token_provider()
        except Exception as exc:  # noqa: BLE001 - a failed refresh must not crash
            log.warning("sql token refresh failed: %s", exc)
            return False
        if not new_token or new_token == self._token:
            log.warning("sql token refresh returned no new token")
            return False
        self._token = new_token
        self._unavailable = None
        log.info("sql token refreshed, resuming endpoint reads")
        return True

    def _attempt(self, endpoint: SqlEndpoint, sql: str, *, multi: bool = False,
                 expected: int = 1):
        """One connection + query. Any failure returns None and records why.

        With ``multi``, every result set in the batch is walked via ``nextset()``
        and returned as a list of row lists. A statement that fails part-way
        through aborts the batch, so what arrived before the failure is returned
        rather than discarded - the reads are independent of one another.
        """
        if not self.available:
            self.failures[endpoint.name] = self.unavailable_reason
            return None
        try:
            import pyodbc
        except ImportError:  # pragma: no cover - guarded by .available
            return None

        conn_str = (
            f"Driver={{{self._driver}}};Server={endpoint.host},1433;"
            f"Database={endpoint.name};Encrypt=Yes;TrustServerCertificate=No;"
            f"MultipleActiveResultSets=False;"
            f"Connection Timeout={_CONNECT_TIMEOUT_SECONDS};"
        )
        conn = None
        try:
            # No ``timeout=`` kwarg: that sets SQL_ATTR_CONNECTION_TIMEOUT, which
            # bounds the *whole* connection including the login handshake and the
            # gateway redirect Azure SQL performs. Fabric's redirect routinely
            # exceeds a short value, and the abort surfaces as a plain timeout -
            # indistinguishable from a blocked port. ``Connection Timeout`` in the
            # string (SQL_ATTR_LOGIN_TIMEOUT) is the correct knob and is set above.
            conn = pyodbc.connect(
                conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _token_struct(self._token)},
            )
            # ``timeout`` is a *Connection* attribute in pyodbc, not a Cursor one.
            # Setting it on the cursor raises AttributeError, and because that
            # message contains the word "timeout" it used to be misreported as a
            # blocked port - a connection that had in fact already succeeded.
            conn.timeout = _QUERY_TIMEOUT_SECONDS
            cursor = conn.cursor()
            cursor.execute(sql)
            if not multi:
                return [tuple(r) for r in cursor.fetchall()]
            return _collect_result_sets(cursor, expected)
        except Exception as exc:  # noqa: BLE001 - every failure must degrade to N/A
            self.failures[endpoint.name] = _classify(exc)
            log.info("sql endpoint read failed for %s (%s): %s",
                     endpoint.name, endpoint.kind, exc)
            return None
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()


def _collect_result_sets(cursor, expected: int) -> list[list[tuple]]:
    """Every result set from a batch, as one row list each.

    Walks ``nextset()`` until the cursor is exhausted. A statement that produced
    no result set contributes an empty list, so the caller's zip against the
    statement table stays aligned. Stops at ``expected`` sets so a stray extra
    set cannot shift the mapping.
    """
    collected: list[list[tuple]] = []
    while len(collected) < expected:
        try:
            collected.append([tuple(r) for r in cursor.fetchall()])
        except Exception:  # noqa: BLE001 - a statement with no rows to fetch
            collected.append([])
        try:
            if not cursor.nextset():
                break
        except Exception:  # noqa: BLE001 - no further sets
            break
    while len(collected) < expected:
        collected.append([])
    return collected


#: Catalog reads issued against **every** endpoint kind, as ``(name, sql)``.
#:
#: All are metadata reads - catalog views, never user data - so their cost is
#: dominated by the connection they share rather than by the queries themselves.
#: Fetching them together means a future check finds its data already in the
#: knowledge base instead of needing another crawl change.
#:
#: Availability was verified against Microsoft's Fabric T-SQL surface-area
#: documentation. Deliberately **not** attempted, because Microsoft documents
#: them as unavailable on Fabric Warehouse / SQL analytics endpoint:
#: ``INFORMATION_SCHEMA.TABLE_CONSTRAINTS``, ``KEY_COLUMN_USAGE``,
#: ``REFERENTIAL_CONSTRAINTS``, ``sys.indexes``, ``sys.index_columns``,
#: ``sys.views``, ``sys.dm_db_partition_stats``, ``OBJECT_DEFINITION()``.
_METADATA_STATEMENTS: tuple[tuple[str, str], ...] = (
    # Object inventory: identifies views, procedures and constraints, and carries
    # create/modify dates that INFORMATION_SCHEMA does not expose.
    ("objects", """
        SELECT o.object_id, s.name, o.name, o.type,
               CONVERT(varchar(33), o.create_date, 126),
               CONVERT(varchar(33), o.modify_date, 126)
        FROM sys.objects AS o
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF', 'PK', 'F', 'UQ')
    """),
    # Approximate row counts from partition metadata - never by scanning rows.
    # ``index_id IN (0, 1)`` is the heap/clustered row set; other index ids would
    # count the same rows again.
    ("row_counts", """
        SELECT p.object_id, SUM(p.rows)
        FROM sys.partitions AS p
        WHERE p.index_id IN (0, 1)
        GROUP BY p.object_id
    """),
    # View definitions, capped so one enormous view cannot bloat the snapshot.
    ("views", """
        SELECT TABLE_SCHEMA, TABLE_NAME, LEFT(VIEW_DEFINITION, 4000)
        FROM INFORMATION_SCHEMA.VIEWS
    """),
    # Stored procedures and functions - the load logic the Warehouse checks want
    # to inspect for TRY/CATCH, incremental patterns and statistics maintenance.
    ("routines", """
        SELECT ROUTINE_SCHEMA, ROUTINE_NAME, ROUTINE_TYPE,
               LEFT(ROUTINE_DEFINITION, 8000)
        FROM INFORMATION_SCHEMA.ROUTINES
    """),
    # Statistics objects, for the statistics-maintenance checks.
    #
    # ``no_recompute`` and ``STATS_DATE`` are what make this an audit rather than
    # an assumption. Fabric creates and refreshes statistics automatically
    # (learn.microsoft.com/fabric/data-warehouse/statistics), so the absence of a
    # manual UPDATE STATISTICS is not a finding - but ``no_recompute = 1``
    # switches that automatic refresh *off* for a statistics object, which is the
    # one way an estate genuinely ends up with stale statistics. Without reading
    # it a check can only assert that the platform is doing its job; with it, the
    # check can verify that nothing has been disabled.
    #
    # ``STATS_DATE`` returns the UTC time of the last refresh (NULL if never), so
    # the same read also shows whether the automatic maintenance is actually
    # happening on this estate.
    ("stats", """
        SELECT object_id, name, auto_created, user_created, no_recompute,
               STATS_DATE(object_id, stats_id) AS last_updated
        FROM sys.stats
    """),
    # The database-level automatic-statistics switches. These are the genuinely
    # auditable setting: a user CAN turn them off
    # (learn.microsoft.com/sql/t-sql/statements/alter-database-transact-sql-set-options?view=fabric
    # lists AUTO_CREATE_STATISTICS and AUTO_UPDATE_STATISTICS in the Fabric
    # Warehouse syntax), and Microsoft says OFF "can cause suboptimal query plans
    # and degraded query performance".
    #
    # This is the read that lets the statistics checks *verify* rather than
    # assume. NORECOMPUTE looked like the equivalent signal but Fabric rejects
    # the option outright - "INCREMENTAL, MAXDOP, SAMPLE x ROWS options, and
    # filter clause are not supported statistics options" - so no_recompute is
    # always 0 and proves nothing. sys.dm_db_stats_properties, which would give
    # modification_counter for real staleness, is not available on Warehouse.
    ("database_options", """
        SELECT is_auto_create_stats_on, is_auto_update_stats_on,
               is_auto_update_stats_async_on
        FROM sys.databases
        WHERE name = DB_NAME()
    """),
    # Database-scoped principals and role membership. Workspace role assignments
    # come from Fabric REST and frequently need a permission the sign-in lacks;
    # this is the database-level view of the same question, with no admin needed.
    ("principals", """
        SELECT principal_id, name, type_desc, authentication_type_desc
        FROM sys.database_principals
        WHERE type <> 'R'
    """),
    ("role_members", """
        SELECT rm.role_principal_id, rm.member_principal_id
        FROM sys.database_role_members AS rm
    """),
)

#: Reads documented for Fabric **Warehouse** only - a Lakehouse SQL analytics
#: endpoint does not expose foreign-key metadata, so asking there would fail
#: every time. Splitting the tables keeps the batch honest rather than relying on
#: an error path.
_WAREHOUSE_METADATA_STATEMENTS: tuple[tuple[str, str], ...] = (
    # Declared (NOT ENFORCED) foreign keys. This is the structural evidence for
    # "which table is a fact and which is a dimension": a referenced table is a
    # dimension, a referencing one a fact. It replaces guessing from a table name.
    ("foreign_keys", """
        SELECT fk.object_id, fk.parent_object_id, fk.referenced_object_id, fk.name
        FROM sys.foreign_keys AS fk
    """),
    ("foreign_key_columns", """
        SELECT fkc.constraint_object_id, fkc.parent_object_id, fkc.parent_column_id,
               fkc.referenced_object_id, fkc.referenced_column_id
        FROM sys.foreign_key_columns AS fkc
    """),
    # Declared primary/unique key constraints.
    ("key_constraints", """
        SELECT kc.object_id, kc.parent_object_id, kc.name, kc.type
        FROM sys.key_constraints AS kc
    """),
    # IDENTITY columns - Microsoft's documented way to generate a surrogate key
    # in Fabric Warehouse: "IDENTITY columns enable automatic generation of these
    # surrogate keys when inserting new rows into a table"
    # (learn.microsoft.com/fabric/data-warehouse/identity). Reading this turns
    # "the column is named _sk, so it is probably generated" into a declared
    # fact. Warehouse only - a Lakehouse Delta table has no IDENTITY concept and
    # returns nothing, which is why this sits in the Warehouse-only batch.
    ("identity_columns", """
        SELECT ic.object_id, c.name
        FROM sys.identity_columns AS ic
        INNER JOIN sys.columns AS c
            ON ic.object_id = c.object_id AND ic.column_id = c.column_id
    """),
)


def _render_type(data_type: Any, max_len: Any, precision: Any, scale: Any) -> str:
    """``varchar(8000)`` / ``decimal(18,2)`` / ``int`` from INFORMATION_SCHEMA parts."""
    base = str(data_type or "").strip().lower()
    if not base:
        return ""
    if max_len is not None and base in {"varchar", "nvarchar", "char", "nchar", "binary",
                                        "varbinary"}:
        width = "max" if int(max_len) < 0 else str(int(max_len))
        return f"{base}({width})"
    if precision is not None and base in {"decimal", "numeric"}:
        return f"{base}({int(precision)},{int(scale or 0)})"
    return base



def _classify(exc: Exception) -> str:
    """A short, actionable reason a SQL endpoint read failed.

    Driver errors are matched on their message; anything that is *not* a driver
    error is reported as an internal fault rather than being pattern-matched.
    Keyword matching on an arbitrary exception is how an ``AttributeError`` whose
    message merely contained the word "timeout" was once reported as a blocked
    port - a wrong answer that sent a diagnosis in entirely the wrong direction.
    """
    if not _is_driver_error(exc):
        return (f"internal error in the SQL reader - {type(exc).__name__}: "
                f"{str(exc)[:160]}")
    text = str(exc).lower()
    if "system limit" in text:
        return ("the workspace has too many warehouses/SQL endpoints for one Entra "
                f"token (Microsoft's limit is about {MAX_ENDPOINTS_PER_WORKSPACE})")
    if "login failed" in text or "cannot open" in text:
        return "the signed-in user has no access to this database"
    if "login timeout" in text or "tcp provider" in text or "network-related" in text \
            or "10060" in text:
        return "the endpoint is not reachable - port 1433 may be blocked"
    if "query timeout" in text or "timeout" in text or "timed out" in text:
        return "the endpoint accepted the connection but the query did not finish in time"
    if "driver" in text:
        return "the ODBC driver could not be loaded"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def _is_driver_error(exc: Exception) -> bool:
    """True when the exception came from pyodbc rather than from this module."""
    try:
        import pyodbc
    except ImportError:  # pragma: no cover - only reachable without pyodbc
        return False
    return isinstance(exc, pyodbc.Error)
