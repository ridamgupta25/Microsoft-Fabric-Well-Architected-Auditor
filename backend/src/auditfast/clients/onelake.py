"""Read-only OneLake **Table** (Unity-Catalog) API client — column schemas over HTTPS.

Column names and types for a Lakehouse Delta table are not in the Fabric REST
API. They are exposed by the OneLake Table endpoint over plain HTTPS — **no ODBC
driver, no port 1433**. OneLake only accepts a *Storage*-audience token
(``https://storage.azure.com``), a different audience from the Fabric token the
crawl uses, so the provider passes a dedicated ``get`` that carries it. This is
the *primary* column source; the SQL/TDS endpoint (:mod:`.sqlendpoint`) remains
the fallback for SQL type widths (``varchar(8000)``) and Warehouse RLS.

Everything is best-effort: any failure returns ``None`` (or skips a table) so the
column checks report **N/A**, never FAIL, and the crawl degrades to exactly the
pre-OneLake behaviour. Nothing here writes.

Surface (see ``fabric-skills/common/SPARK-CONSUMPTION-CORE.md``)::

    GET {host}/delta/{workspaceId}/{lakehouseId}/api/2.1/unity-catalog/schemas?catalog_name=<lh>.Lakehouse
    GET {host}/delta/{workspaceId}/{lakehouseId}/api/2.1/unity-catalog/tables?catalog_name=<lh>.Lakehouse&schema_name=<schema>
    GET {host}/delta/{workspaceId}/{lakehouseId}/api/2.1/unity-catalog/tables/<lh>.Lakehouse.<schema>.<table>

The last call carries the ``columns`` array. A ``400`` on ``schemas`` means the
lakehouse is not schema-enabled — the ``dbo`` default is used instead.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

log = logging.getLogger("auditfast.onelake")

#: Global OneLake Table (Delta / Unity-Catalog) host. Regional hosts
#: (``<region>-onelake.table.fabric.microsoft.com``) also exist; the global host
#: redirects, so a single base works everywhere.
_HOST = "https://onelake.table.fabric.microsoft.com"
_API = "api/2.1/unity-catalog"

#: Upper bound on the number of tables whose schema is fetched per lakehouse. The
#: schema read is one HTTP call per table, so a 1000-table lakehouse is 1000
#: calls; the cap keeps a first (uncached) crawl bounded while still covering the
#: population the table checks sample over.
_MAX_TABLES = 2000

#: A ``(absolute_url) -> (status, body)`` callable owned by the provider. Passing
#: it keeps this module free of its own HTTP stack and auth — the provider wires
#: in a callable that carries the OneLake *Storage*-audience token.
GetUrl = Callable[[str], "tuple[int | None, Any]"]


class OneLakeTableReader:
    """Reads Lakehouse table column schemas over the OneLake Table API."""

    def __init__(self, get: GetUrl):
        self._get = get
        #: ``lakehouse name -> reason its columns could not be read`` (diagnostics
        #: only; never a scored finding).
        self.failures: dict[str, str] = {}

    def columns(self, workspace_id: str, lakehouse_id: str,
                lakehouse_name: str) -> dict[str, list[dict[str, Any]]] | None:
        """``table name -> [{name, type, nullable, source_kind}]`` for a lakehouse.

        ``None`` means the API could not be reached or authorised (the caller
        should fall back to TDS); an empty dict means it was reachable but the
        lakehouse holds no readable tables.
        """
        catalog = f"{lakehouse_name}.Lakehouse"
        schemas = self._schemas(workspace_id, lakehouse_id, catalog, lakehouse_name)
        if schemas is None:
            return None
        out: dict[str, list[dict[str, Any]]] = {}
        for schema in schemas:
            for table in self._tables(workspace_id, lakehouse_id, catalog, schema):
                if len(out) >= _MAX_TABLES:
                    return out
                cols = self._table_columns(workspace_id, lakehouse_id, catalog,
                                           schema, table)
                if cols:
                    out[table] = cols
        return out

    # -- endpoints ------------------------------------------------------------
    def _base(self, workspace_id: str, lakehouse_id: str) -> str:
        return f"{_HOST}/delta/{workspace_id}/{lakehouse_id}/{_API}"

    def _schemas(self, ws: str, lh: str, catalog: str,
                 lakehouse_name: str) -> list[str] | None:
        """Schema names in the lakehouse, or ``None`` when the API is unreachable."""
        url = f"{self._base(ws, lh)}/schemas?catalog_name={quote(catalog)}"
        status, body = self._get(url)
        if status == 400:
            # Not schema-enabled: the tables live under the default ``dbo`` schema.
            return ["dbo"]
        if status != 200 or not isinstance(body, dict):
            self.failures[lakehouse_name] = _reason(status)
            return None
        names = [s.get("name") for s in (body.get("schemas") or []) if s.get("name")]
        return names or ["dbo"]

    def _tables(self, ws: str, lh: str, catalog: str, schema: str) -> list[str]:
        """Table names in one schema, following ``next_page_token`` pagination."""
        base = (f"{self._base(ws, lh)}/tables"
                f"?catalog_name={quote(catalog)}&schema_name={quote(schema)}")
        names: list[str] = []
        token: str | None = None
        pages = 0
        while pages < 1000:
            pages += 1
            url = base + (f"&page_token={quote(token)}" if token else "")
            status, body = self._get(url)
            if status != 200 or not isinstance(body, dict):
                break
            names.extend(t.get("name") for t in (body.get("tables") or []) if t.get("name"))
            token = body.get("next_page_token")
            if not token:
                break
        return names

    def _table_columns(self, ws: str, lh: str, catalog: str, schema: str,
                       table: str) -> list[dict[str, Any]]:
        """The column schema of one table, or ``[]`` when it could not be read."""
        full = quote(f"{catalog}.{schema}.{table}", safe="")
        status, body = self._get(f"{self._base(ws, lh)}/tables/{full}")
        if status != 200 or not isinstance(body, dict):
            return []
        return _parse_columns(body)


def _parse_columns(body: dict) -> list[dict[str, Any]]:
    """Map a Unity-Catalog table response's ``columns`` to the check-facing shape.

    The shape mirrors what :meth:`.sqlendpoint.SqlEndpointReader.columns` yields,
    so the table checks consume either source unchanged. ``source_kind`` is always
    ``Lakehouse`` — the OneLake Table API only serves lakehouses.
    """
    cols = body.get("columns")
    if not isinstance(cols, list):
        return []
    out: list[dict[str, Any]] = []
    for col in cols:
        if not isinstance(col, dict):
            continue
        name = col.get("name")
        if not name:
            continue
        out.append({
            "name": str(name),
            "type": _col_type(col),
            "nullable": bool(col.get("nullable", True)),
            "source_kind": "Lakehouse",
        })
    return out


def _col_type(col: dict) -> str:
    """Best-available Delta type string from a Unity-Catalog column entry.

    ``type_text`` is the rendered form (``string``, ``decimal(18,2)``,
    ``timestamp``); ``type_name`` is the coarser enum (``STRING``) used as a
    fallback.
    """
    for key in ("type_text", "type_name"):
        val = col.get(key)
        if val:
            return str(val).lower()
    return ""


def _reason(status: int | None) -> str:
    """A short, actionable reason the OneLake Table API could not be read."""
    if status in (401, 403):
        return "no OneLake read access to this lakehouse (or the token audience is wrong)"
    if status is None:
        return "the OneLake Table endpoint was not reachable"
    return f"the OneLake Table endpoint returned HTTP {status}"
