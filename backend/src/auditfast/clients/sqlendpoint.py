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
from typing import Any

log = logging.getLogger("auditfast.sqlendpoint")

#: ODBC attribute that carries an Entra access token (``SQL_COPT_SS_ACCESS_TOKEN``).
_SQL_COPT_SS_ACCESS_TOKEN = 1256

#: Per-connection and per-query ceilings. A slow endpoint must not stall a crawl.
_CONNECT_TIMEOUT_SECONDS = 15
_QUERY_TIMEOUT_SECONDS = 30

#: Microsoft's guidance: beyond roughly this many warehouses + SQL endpoints in one
#: workspace the Entra token can exceed its size limit. We read what we can and
#: record the rest as unread rather than failing the whole crawl.
MAX_ENDPOINTS_PER_WORKSPACE = 40


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
        log.info("sql endpoint discovery (lakehouses) failed for %s: %s", workspace_id, exc)

    try:
        for item in (get_json(f"/workspaces/{workspace_id}/warehouses") or {}).get("value", []):
            props = item.get("properties") or {}
            server = (props.get("connectionString") or "").strip()
            if server:
                endpoints.append(SqlEndpoint(
                    "Warehouse", item.get("displayName") or "", server, "Success",
                ))
    except Exception as exc:  # noqa: BLE001
        log.info("sql endpoint discovery (warehouses) failed for %s: %s", workspace_id, exc)

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

    def __init__(self, sql_token: str | None):
        self._token = sql_token
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
        """``table name -> [{name, type, ...}]`` for one endpoint, or None.

        ``type`` is rendered the way the table checks expect: ``varchar(8000)``,
        ``decimal(18,2)``, ``int``. ``source_kind`` travels with every column so a
        check can tell a Lakehouse's forced ``varchar(8000)`` from a width a
        Warehouse author actually chose.
        """
        rows = self._query(endpoint, """
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE,
                   CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE,
                   IS_NULLABLE, ORDINAL_POSITION
            FROM INFORMATION_SCHEMA.COLUMNS
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """)
        if rows is None:
            return None
        tables: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            table = str(row[0] or "")
            if not table:
                continue
            tables.setdefault(table, []).append({
                "name": str(row[1] or ""),
                "type": _render_type(row[2], row[3], row[4], row[5]),
                "nullable": str(row[6] or "").upper() == "YES",
                "source_kind": endpoint.kind,
            })
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

    def _query(self, endpoint: SqlEndpoint, sql: str) -> list[tuple] | None:
        """Run one read-only query. Any failure returns None and records why."""
        if not self.available:
            self.failures[endpoint.name] = self.unavailable_reason
            return None
        try:
            import pyodbc
        except ImportError:  # pragma: no cover - guarded by .available
            return None

        conn_str = (
            f"Driver={{{self._driver}}};Server={endpoint.server},1433;"
            f"Database={endpoint.name};Encrypt=Yes;TrustServerCertificate=No;"
            f"MultipleActiveResultSets=False;"
            f"Connection Timeout={_CONNECT_TIMEOUT_SECONDS};"
        )
        conn = None
        try:
            conn = pyodbc.connect(
                conn_str, attrs_before={_SQL_COPT_SS_ACCESS_TOKEN: _token_struct(self._token)},
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            cursor = conn.cursor()
            cursor.timeout = _QUERY_TIMEOUT_SECONDS
            cursor.execute(sql)
            return [tuple(r) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001 - every failure must degrade to N/A
            self.failures[endpoint.name] = _classify(exc)
            log.info("sql endpoint read failed for %s (%s): %s",
                     endpoint.name, endpoint.kind, exc)
            return None
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()


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
    """A short, actionable reason a SQL endpoint read failed."""
    text = str(exc).lower()
    if "system limit" in text:
        return ("the workspace has too many warehouses/SQL endpoints for one Entra "
                f"token (Microsoft's limit is about {MAX_ENDPOINTS_PER_WORKSPACE})")
    if "login failed" in text or "cannot open" in text:
        return "the signed-in user has no access to this database"
    if "timeout" in text or "timed out" in text:
        return "the endpoint did not respond (port 1433 may be blocked)"
    if "tcp provider" in text or "network-related" in text or "10060" in text:
        return "port 1433 is not reachable from the server running the audit"
    if "driver" in text:
        return "the ODBC driver could not be loaded"
    return f"{type(exc).__name__}: {str(exc)[:160]}"
