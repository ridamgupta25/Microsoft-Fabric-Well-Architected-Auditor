"""Read-only Fabric REST provider.

Every call is a GET, except the read-only ``getDefinition`` POST. Nothing here
ever writes.

Two behaviours matter and are easy to get wrong:

1. **The workspace itself is read first, and its HTTP status is checked.** A
   403 must raise, not yield an empty context — otherwise an inaccessible
   workspace scores zeros and looks like a badly configured one.
2. **A failed sub-resource call is recorded as *unknown*, not as *absent*.**
   ``git/connection`` returning a network error does not mean "Git is not
   connected"; the affected checks report N/A instead of failing.
"""
from __future__ import annotations

import base64
import contextlib
import json
import logging
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

import yaml

from ..core.enums import Layer, Resource
from ..core.models import Item, RoleAssignment, WorkspaceContext
from .base import ALL_RESOURCES
from .errors import WorkspaceAccessError
from .tmsl import parse_tmsl

log = logging.getLogger("auditfast.live")

#: How many job-run timestamps are retained per item for the cadence signal.
#: Enough to establish an interval; small enough that a chatty item cannot bloat
#: the knowledge-base snapshot.
_MAX_RETAINED_RUNS = 25


def _any_table_columns(ctx: WorkspaceContext) -> bool:
    """True when at least one captured table already carries a column schema."""
    return any(t.get("columns") for t in ctx.tables.values())


class LiveFabricProvider:
    """Reads a live Fabric tenant with a delegated, read-only OAuth2 token."""

    BASE = "https://api.fabric.microsoft.com/v1"

    def __init__(self, token: str, timeout: int = 60, token_refresher=None,
                 powerbi_token: str | None = None, sql_token: str | None = None,
                 onelake_token: str | None = None):
        import requests  # imported lazily so offline mode needs no HTTP stack

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})
        self._timeout = timeout
        self._token_refresher = token_refresher
        #: A Power BI-audience token (``https://analysis.windows.net/powerbi/api``)
        #: — a *different* audience from the Fabric token above. It is optional and
        #: used only to read semantic-model refresh recency, which lives on the
        #: Power BI Datasets API, not the Fabric surface. Absent ⇒ that one signal
        #: is left unknown, never guessed.
        self._powerbi_token = powerbi_token
        self._powerbi_client = None
        #: A SQL-analytics-endpoint token (``https://database.windows.net``) — a
        #: third audience. Column schemas and Warehouse RLS policies exist nowhere
        #: in the Fabric REST API, only over TDS on port 1433. Absent, or the port
        #: blocked ⇒ those reads are skipped and the affected checks report N/A,
        #: exactly as they did before the endpoint was wired in.
        self._sql_token = sql_token
        self._sql_reader = None
        #: A OneLake *Storage*-audience token (``https://storage.azure.com``) - a
        #: fourth audience. Lakehouse table **column schemas** are served by the
        #: OneLake Table (Unity-Catalog) API, which rejects the Fabric token above
        #: and accepts only a Storage-audience token. Absent, columns fall back to
        #: the SQL/TDS endpoint, or the affected checks report N/A.
        self._onelake_token = onelake_token

    # -- transport -------------------------------------------------------------
    def _get(self, path: str) -> tuple[int | None, Any]:
        """GET a path, returning ``(status, body)``.

        A status of ``None`` means the request never completed — a transport
        failure, which callers must treat as *unknown* rather than *empty*.
        """
        try:
            response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
        except Exception:
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _get_url(self, url: str) -> tuple[int | None, Any]:
        """GET an absolute URL (used to follow a pagination continuation link)."""
        try:
            response = self._session.get(url, timeout=self._timeout)
        except Exception:
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _get_onelake(self, url: str) -> tuple[int | None, Any]:
        """GET an absolute OneLake Table URL with the *Storage*-audience token.

        OneLake only accepts tokens in the ``https://storage.azure.com`` audience
        - a different audience from the Fabric token ``self._session`` carries -
        so this issues the request with its own Authorization header rather than
        reusing the crawl session.
        """
        import requests

        try:
            response = requests.get(
                url, headers={"Authorization": f"Bearer {self._onelake_token}"},
                timeout=self._timeout)
        except Exception:
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _values(self, path: str) -> tuple[list, bool]:
        """GET a collection endpoint, following continuation to the last page.

        Returns ``(rows, known)``. ``known`` is False only when the *first* call
        failed outright, so the caller can tell "no rows" from "could not ask".
        Fabric list endpoints page with ``continuationUri``; every page is
        gathered so a large workspace is never silently truncated.
        """
        status, body = self._get(path)
        if status != 200 or not isinstance(body, dict):
            return [], False
        rows = list(body.get("value") or [])
        next_uri = body.get("continuationUri")
        pages = 0
        while next_uri and pages < 1000:
            pages += 1
            status, body = self._get_url(next_uri)
            if status != 200 or not isinstance(body, dict):
                break
            rows.extend(body.get("value") or [])
            next_uri = body.get("continuationUri")
        return rows, True

    @staticmethod
    def _json(response) -> Any:
        """Parse a response body as JSON, or ``None`` if it is not JSON."""
        try:
            return response.json()
        except ValueError:
            return None

    def _try_refresh_token(self) -> bool:
        """Attempt a silent token refresh. Returns True if a new token was set."""
        if not self._token_refresher:
            return False
        log.info("access token expired, attempting silent refresh")
        new_token = self._token_refresher()
        if new_token:
            self._session.headers.update({"Authorization": f"Bearer {new_token}"})
            log.info("token refreshed, resuming crawl")
            return True
        log.warning("token refresh failed, could not acquire new token")
        return False

    #: getDefinition statuses worth retrying — transient throttling / 5xx.
    _RETRYABLE = frozenset({429, 500, 502, 503, 504})

    #: Item types that expose a run/refresh history: pipelines, notebooks,
    #: dataflows and Spark jobs via the Fabric job scheduler, and semantic models
    #: via the Power BI refresh history (a different API). Types that never run
    #: (reports, dashboards, lakehouses, warehouses, …) are skipped rather than
    #: probed for nothing.
    _JOB_ITEM_TYPES = frozenset({
        "Notebook", "DataPipeline", "SparkJobDefinition", "SemanticModel",
        "Dataflow",
    })

    def _definition_parts(
        self, workspace_id: str, item_id: str, fmt: str | None = None
    ) -> tuple[list[dict], str]:
        """Read an item's definition parts via the read-only getDefinition LRO.

        Returns ``(parts, failure)`` where ``failure`` is:

        * ``""`` — the read completed (``parts`` may still be empty for a
          genuinely empty item);
        * ``"forbidden"`` — a 403 permission denial (Fabric gates
          getDefinition behind the Item.ReadWrite scope). It will not recover
          without a different token, so the caller records a known gap;
        * ``"transient"`` — a throttling/5xx/timeout/transport error that
          survived retries. It *may* recover, so a partial crawl carrying one
          must be re-fetched rather than cached.

        A 401 (token expired) is distinguished from 403 (permission denied):
        on 401 the provider attempts a silent token refresh and retries the call.

        getDefinition is a long-running operation: 200 with the body inline for
        small items, or 202 with a ``Location`` to poll until it completes.
        """
        url = f"{self.BASE}/workspaces/{workspace_id}/items/{item_id}/getDefinition"
        if fmt:
            url += f"?format={fmt}"
        for attempt in range(3):
            try:
                response = self._session.post(url, timeout=self._timeout)
            except Exception as exc:
                if attempt < 2:
                    time.sleep(min(2.0 * (attempt + 1), 5.0))
                    continue
                log.warning("item %s getDefinition transport error: %s", item_id, exc)
                return [], "transient"
            status = response.status_code
            if status == 200:
                body = self._json(response)
            elif status == 202:
                body = self._await_operation(response)
                if body is None:
                    return [], "transient"  # the LRO failed or timed out
            elif status == 401:
                # Token expired — attempt silent refresh and retry this item
                if self._try_refresh_token():
                    continue
                log.warning("item %s getDefinition -> HTTP 401 (token expired, refresh failed)", item_id)
                return [], "forbidden"
            elif status == 403:
                log.warning("item %s getDefinition -> HTTP 403 (permission denied)", item_id)
                return [], "forbidden"
            elif status in self._RETRYABLE and attempt < 2:
                time.sleep(min(2.0 * (attempt + 1), 5.0))
                continue
            else:
                log.warning("item %s getDefinition -> HTTP %s", item_id, status)
                return [], "transient"
            parts = ((body or {}).get("definition") or {}).get("parts") or []
            if not parts:
                log.warning("item %s getDefinition returned no definition parts", item_id)
            return parts, ""
        return [], "transient"

    def _await_operation(self, response) -> Any:
        """Poll a 202 long-running operation to completion, returning its result body."""
        location = response.headers.get("Location")
        if not location:
            log.warning("getDefinition 202 without a Location header")
            return None
        delay = 1.0
        with contextlib.suppress(ValueError):
            delay = min(float(response.headers.get("Retry-After") or 1.0), 10.0)
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            time.sleep(min(delay, 5.0))
            try:
                op = self._session.get(location, timeout=self._timeout)
            except Exception:
                return None
            state = str((self._json(op) or {}).get("status", "")).lower()
            if state == "succeeded":
                try:
                    result = self._session.get(
                        location.rstrip("/") + "/result", timeout=self._timeout
                    )
                except Exception:
                    return None
                return self._json(result)
            if state == "failed":
                log.warning("getDefinition operation failed: %s", location)
                return None
        log.warning("getDefinition operation timed out: %s", location)
        return None

    def _pipeline_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Read a pipeline's content (getDefinition) as parsed JSON.

        Returns ``(definition, failure)`` — see :meth:`_definition_parts`.
        """
        parts, failure = self._definition_parts(workspace_id, item_id)
        for part in parts:
            if part.get("path", "").endswith(("pipeline-content.json", "pipelineContent.json")):
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    return json.loads(payload), ""
                except Exception:
                    return None, ""
        return None, failure

    def _notebook_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Read a notebook's content (getDefinition) as an .ipynb dict.

        Returns ``(definition, failure)`` — see :meth:`_definition_parts`.
        """
        parts, failure = self._definition_parts(workspace_id, item_id, fmt="ipynb")
        for part in parts:
            path = part.get("path", "")
            if not path.endswith((".ipynb", "notebook-content.py")):
                continue
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
            except Exception:
                return None, ""
            if path.endswith(".ipynb"):
                try:
                    return json.loads(payload), ""
                except Exception:
                    return None, ""
            # A .py export: wrap the raw source as a single code cell.
            return {"cells": [{"cell_type": "code", "source": payload}]}, ""
        return None, failure

    def _environment_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Read an Environment's Sparkcompute settings from its definition."""
        parts, failure = self._definition_parts(workspace_id, item_id)
        result: dict[str, Any] = {}
        for part in parts:
            if not str(part.get("path") or "").lower().endswith("sparkcompute.yml"):
                continue
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
                document = yaml.safe_load(payload) or {}
            except (KeyError, ValueError, yaml.YAMLError):
                return None, ""
            if isinstance(document, dict):
                result.update(document)
        return (result or None), failure

    @staticmethod
    def _environment_binding(definition: dict) -> str:
        """Find an Environment binding across known notebook metadata shapes."""
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if (
                        str(key).lower() in {"environmentartifactid", "environmentid", "environment_id"}
                        and isinstance(child, str)
                        and child.strip()
                    ):
                        found.append(child.strip())
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(definition or {})
        return found[0] if found else ""

    @staticmethod
    def _first_identifier(row: dict, *names: str) -> str:
        """Return the first non-empty identifier across Fabric response variants."""
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip():
                return str(value)
        return ""

    def _notebook_monitoring(self, workspace_id: str, item_id: str) -> dict:
        """Read latest Spark session metrics for a notebook, or return no evidence.

        Monitoring is best-effort enrichment of ``NOTEBOOK_DEFINITIONS``. A
        missing session, unsupported endpoint, or permission denial remains an
        empty dict so checks report N/A rather than turning an observability gap
        into a configuration failure.
        """
        base = f"/workspaces/{workspace_id}/notebooks/{item_id}/livySessions"
        status, body = self._get(base)
        if status != 200 or not isinstance(body, dict):
            return {}
        sessions = body.get("value") or body.get("data") or []
        if not isinstance(sessions, list) or not sessions:
            return {}
        session = sessions[0] if isinstance(sessions[0], dict) else {}
        livy_id = self._first_identifier(session, "livyId", "id", "sessionId")
        if not livy_id:
            return {}

        detail_status, detail = self._get(f"{base}/{livy_id}")
        if detail_status != 200 or not isinstance(detail, dict):
            detail = session
        app_id = self._first_identifier(
            detail, "appId", "applicationId", "sparkApplicationId"
        ) or self._first_identifier(session, "appId", "applicationId", "sparkApplicationId")

        monitoring: dict[str, Any] = {
            "livy_id": livy_id,
            "app_id": app_id,
            "session": detail,
        }
        if not app_id:
            return monitoring

        app_base = f"{base}/{livy_id}/applications/{app_id}"
        for key, suffix in (
            ("advice", "advice"),
            ("resource_usage", "resourceUsage"),
            ("stages", "stages"),
        ):
            metric_status, metric = self._get(f"{app_base}/{suffix}")
            if metric_status == 200 and metric is not None:
                monitoring[key] = metric
        return monitoring

    def _read_onelake_columns(self, ctx: WorkspaceContext, workspace_id: str) -> None:
        """Fill lakehouse table column schemas from the OneLake Table API.

        The primary column source: plain HTTPS with the OneLake *Storage*-audience
        token (``https://storage.azure.com``), so no ODBC driver or open port 1433
        is needed. No token, a rejected token, a non-schema-enabled lakehouse or a
        network error simply leaves the columns absent — the SQL/TDS fallback in
        :meth:`_read_sql_endpoints` runs next, and the column checks report N/A
        rather than failing.
        """
        if not self._onelake_token:
            log.info("fetch %s: no OneLake Storage token; columns via TDS fallback only",
                     workspace_id)
            return
        from .onelake import OneLakeTableReader

        lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
        if not lakehouses:
            return
        reader = OneLakeTableReader(self._get_onelake)
        read = 0
        for item in lakehouses:
            name = item.display_name or item.id
            tables = reader.columns(workspace_id, item.id, name)
            if not tables:
                continue
            read += 1
            for table_name, cols in tables.items():
                existing = ctx.tables.get(table_name)
                if existing is not None:
                    existing["columns"] = cols
                    existing.setdefault("store", name)
                    existing.setdefault("store_kind", "Lakehouse")
                else:
                    ctx.tables[table_name] = {
                        "type": "Managed", "format": "delta", "columns": cols,
                        "store": name, "store_kind": "Lakehouse",
                    }
        for lh, reason in reader.failures.items():
            log.info("fetch %s: OneLake columns for lakehouse %s skipped - %s",
                     workspace_id, lh, reason)
        log.info("fetch %s: OneLake columns read for %d/%d lakehouse(s)",
                 workspace_id, read, len(lakehouses))

    def _read_sql_endpoints(self, ctx: WorkspaceContext, workspace_id: str,
                            wanted: set) -> None:
        """Enrich the context with column schemas and Warehouse RLS over TDS.

        Discovery is plain Fabric REST, so nothing is ever asked of the user - the
        connection string comes from the lakehouse/warehouse item itself. The TDS
        reads are strictly best-effort: no token, no ODBC driver, a blocked port
        1433 or a throttled endpoint all leave the data absent and mark the
        resource unavailable, so the affected checks report **N/A with a reason**
        rather than failing a workspace for something we could not look at.
        """
        from .sqlendpoint import MAX_ENDPOINTS_PER_WORKSPACE, SqlEndpointReader, discover_endpoints

        # Columns come from OneLake first (see _read_onelake_columns); only fall
        # back to TDS for them when OneLake produced nothing, so a snapshot that
        # OneLake already populated is never re-marked unavailable by the fallback.
        want_columns = Resource.TABLE_COLUMNS in wanted and not _any_table_columns(ctx)
        want_security = Resource.WAREHOUSE_SECURITY in wanted

        def get_json(path: str):
            status, body = self._get(path)
            return body if status == 200 and isinstance(body, dict) else {}

        endpoints = discover_endpoints(get_json, workspace_id)
        if not endpoints:
            if want_columns:
                ctx.unavailable.add(Resource.TABLE_COLUMNS)
            if want_security:
                ctx.unavailable.add(Resource.WAREHOUSE_SECURITY)
            log.info("fetch %s: no provisioned SQL endpoints discovered", workspace_id)
            return

        reader = SqlEndpointReader(self._sql_token)
        if not reader.available:
            if want_columns:
                ctx.unavailable.add(Resource.TABLE_COLUMNS)
            if want_security:
                ctx.unavailable.add(Resource.WAREHOUSE_SECURITY)
            log.warning("fetch %s: SQL endpoint unavailable - %s",
                        workspace_id, reader.unavailable_reason)
            return

        # Beyond Microsoft's per-workspace guidance the Entra token can exceed its
        # size limit. Read what we can rather than losing the workspace entirely.
        if len(endpoints) > MAX_ENDPOINTS_PER_WORKSPACE:
            log.warning("fetch %s: %d SQL endpoints exceeds the ~%d guidance; "
                        "reads may fail on the Entra token size limit",
                        workspace_id, len(endpoints), MAX_ENDPOINTS_PER_WORKSPACE)

        col_attempted = col_read = 0
        sec_attempted = sec_read = 0
        collisions = 0
        for endpoint in endpoints:
            if want_columns:
                col_attempted += 1
                tables = reader.columns(endpoint)
                if tables is not None:
                    col_read += 1
                    for table_name, cols in tables.items():
                        existing = ctx.tables.get(table_name)
                        if existing is not None and existing.get("store"):
                            # Two stores hold a table of the same name. The flat
                            # key cannot represent both, so the second is filed
                            # under "<store>.<table>" and the first keeps the bare
                            # name that the REST listing established.
                            collisions += 1
                            ctx.tables[f"{endpoint.name}.{table_name}"] = {
                                "type": "Managed", "format": "", "columns": cols,
                                "store": endpoint.name, "store_kind": endpoint.kind,
                            }
                            continue
                        if existing is not None:
                            existing["columns"] = cols
                            existing["store"] = endpoint.name
                            existing["store_kind"] = endpoint.kind
                        else:
                            # A table the REST listing did not return (a Warehouse
                            # table, say) is still worth recording.
                            ctx.tables[table_name] = {
                                "type": "Managed", "format": "",
                                "columns": cols,
                                "store": endpoint.name,
                                "store_kind": endpoint.kind,
                            }
            if want_security and endpoint.kind == "Warehouse":
                sec_attempted += 1
                policies = reader.security_policies(endpoint)
                if policies is not None:
                    sec_read += 1
                    ctx.warehouse_security[endpoint.name] = policies

        # None readable means "we could not look", which must not read as "none
        # configured". Some readable is a partial gap, recorded but still usable.
        if want_columns:
            self._record_failures(ctx, Resource.TABLE_COLUMNS, col_attempted,
                                  col_read, 0, col_attempted - col_read)
        if collisions:
            log.info("fetch %s: %d table name(s) exist in more than one store; "
                     "the duplicates are keyed '<store>.<table>'",
                     workspace_id, collisions)
        if want_security and sec_attempted:
            self._record_failures(ctx, Resource.WAREHOUSE_SECURITY, sec_attempted,
                                  sec_read, 0, sec_attempted - sec_read)
        elif want_security:
            # No Warehouse in the workspace: nothing to read, nothing to report.
            ctx.unavailable.add(Resource.WAREHOUSE_SECURITY)

        # The per-endpoint reasons are the only way to tell a blocked port from a
        # permission gap, so surface the distinct ones rather than burying them.
        if reader.failures:
            reasons: dict[str, int] = {}
            for reason in reader.failures.values():
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                log.warning("fetch %s: %d SQL endpoint read(s) failed - %s",
                            workspace_id, count, reason)

        log.info("fetch %s: SQL endpoints - columns %d/%d, warehouse security %d/%d%s",
                 workspace_id, col_read, col_attempted, sec_read, sec_attempted,
                 f" ({len(reader.failures)} endpoint(s) failed)" if reader.failures else "")

    def _lakehouse_tables(self, workspace_id: str, item_id: str) -> tuple[list[dict], str]:
        """List a lakehouse's tables via REST (name/type/format; no columns).

        Returns ``(rows, failure)`` — see :meth:`_definition_parts` for the
        ``failure`` values. The Fabric *List Tables* endpoint returns rows under
        ``data`` (not the usual ``value`` collection key), so it is read directly.
        """
        status, body = self._get(
            f"/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
        )
        if status == 401:
            if self._try_refresh_token():
                status, body = self._get(
                    f"/workspaces/{workspace_id}/lakehouses/{item_id}/tables"
                )
            else:
                log.warning("lakehouse %s list-tables -> HTTP 401 (token expired, refresh failed)", item_id)
                return [], "forbidden"
        if status == 403:
            log.warning("lakehouse %s list-tables -> HTTP 403 (permission denied)", item_id)
            return [], "forbidden"
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            log.warning("lakehouse %s list-tables -> HTTP %s (transient)", item_id, status)
            return [], "transient"
        if status != 200 or not isinstance(body, dict):
            return [], ""  # e.g. 404 — no SQL endpoint / no tables; not a read failure
        return body.get("data") or [], ""

    @staticmethod
    def _connection_metadata(row: dict) -> dict:
        """Keep connection metadata useful to checks without persisting secrets."""
        details = row.get("connectionDetails") or {}
        credentials = row.get("credentialDetails") or {}
        recency = row.get("connectionRecency") or {}
        return {
            "id": row.get("id", ""),
            "display_name": row.get("displayName", ""),
            "gateway_id": row.get("gatewayId"),
            "connectivity_type": row.get("connectivityType", ""),
            "connection_type": details.get("type", ""),
            "endpoint": details.get("path", ""),
            "credential_type": credentials.get("credentialType", ""),
            "single_sign_on_type": credentials.get("singleSignOnType", ""),
            "connection_encryption": credentials.get("connectionEncryption", ""),
            "skip_test_connection": credentials.get("skipTestConnection"),
            "created_date_time": recency.get("createdDateTime"),
            "last_bound_date_time": recency.get("lastBoundDateTime"),
            "last_credential_used_date_time": recency.get("lastCredentialUsedDateTime"),
            "minimum_tls_version": None,
            "status": "unknown",
        }

    def _item_shortcuts(self, workspace_id: str, item_id: str) -> tuple[list[dict], bool]:
        """List an item's OneLake shortcuts (name/path/target type), all pages.

        Returns ``(shortcuts, known)``; ``known`` is False when the list call
        itself failed, so "could not ask" is never recorded as "has none".
        """
        rows, known = self._values(
            f"/workspaces/{workspace_id}/items/{item_id}/shortcuts"
        )
        shortcuts = []
        for row in rows:
            target = row.get("target") or {}
            shortcuts.append({
                "name": row.get("name", ""),
                "path": row.get("path", ""),
                "target_type": target.get("type", ""),
            })
        return shortcuts, known

    def _semantic_model_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Fetch a semantic model's TMSL definition and reduce it to model facts.

        Returns ``(model, failure)`` — see :meth:`_definition_parts`.
        """
        parts, failure = self._definition_parts(workspace_id, item_id, fmt="TMSL")
        for part in parts:
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
                document = json.loads(payload)
            except Exception:
                continue
            if isinstance(document, dict) and ("model" in document or "tables" in document):
                return parse_tmsl(document), ""
        return None, failure

    @staticmethod
    def _git_connection(body: Any) -> dict:
        """Normalize a ``git/connection`` body into the provider/repo/branch facts.

        Fabric answers 200 with ``gitConnectionState: "NotConnected"`` for a
        workspace that has no repository, so the *state* — not the HTTP status —
        decides whether the workspace is really Git-connected.
        """
        if not isinstance(body, dict):
            return {}
        provider = body.get("gitProviderDetails") or {}
        sync = body.get("gitSyncDetails") or {}
        state = body.get("gitConnectionState") or ""
        return {
            "connected": state != "NotConnected" if state else bool(provider),
            "state": state,
            "provider": provider.get("gitProviderType", ""),
            # Azure DevOps reports organizationName; GitHub reports ownerName.
            "organization": provider.get("organizationName") or provider.get("ownerName", ""),
            "project": provider.get("projectName", ""),
            "repository": provider.get("repositoryName", ""),
            "branch": provider.get("branchName", ""),
            "directory": provider.get("directoryName", ""),
            "head": sync.get("head"),
            "last_sync_time": sync.get("lastSyncTime"),
        }

    @staticmethod
    def _unique_key(store: dict, name: str, item_id: str) -> str:
        """A key that will not overwrite an item already stored under ``name``.

        Fabric permits two items of the same type to share a display name, so
        keying purely by name silently loses all but the last one.
        """
        if name not in store:
            return name
        return f"{name} ({item_id[:8]})"

    @staticmethod
    def _record_failures(ctx: WorkspaceContext, resource: Resource,
                         attempted: int, read: int, forbidden: int, transient: int,
                         empty: int = 0) -> None:
        """Record a one-per-item read outcome on the context.

        When some items of a type could not be read, store the counts so the gap
        is visible ("N of M could not be read"). When *none* could be read, also
        mark the resource unavailable so its checks report N/A with the reason.

        ``empty`` counts items whose definition call returned but carried nothing
        usable. Those are a real coverage gap, but re-crawling will not fix them,
        so they are reported without making the snapshot un-cacheable.
        """
        if attempted and (forbidden or transient or empty):
            ctx.read_failures[resource.value] = {
                "attempted": attempted,
                "read": read,
                "failed": forbidden + transient + empty,
                "forbidden": forbidden,
                "transient": transient,
                "empty": empty,
            }
            if read == 0:
                ctx.unavailable.add(resource)

    def _run_stamps(self, workspace_id: str, item_id: str) -> tuple[list[str], str]:
        """Return one item's job-run timestamps (newest first) and a failure classifier.

        Reads a single page of the item's job-instance history. Every run on that
        page contributes its ``endTimeUtc`` (falling back to ``startTimeUtc`` for a
        run still in flight), so the caller gets both the latest run *and* the
        intervals between runs from one call — no extra request is made for the
        cadence signal. ``failure`` follows the :meth:`_definition_parts`
        convention: ``""`` read, ``"forbidden"`` a permission/expired-token
        denial, ``"transient"`` a throttling/5xx/network error. A 400/404 (the
        item type keeps no job history) is *not* a failure — it simply yields no
        timestamps.

        ISO-8601 UTC (``…Z``) stamps sort lexicographically, so a plain reverse
        sort is chronological without any date parsing.
        """
        path = f"/workspaces/{workspace_id}/items/{item_id}/jobs/instances"
        status, body = self._get(path)
        if status == 401 and self._try_refresh_token():
            status, body = self._get(path)
        if status in (401, 403):
            log.warning("item %s jobs/instances -> HTTP %s (permission denied)", item_id, status)
            return [], "forbidden"
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            log.warning("item %s jobs/instances -> HTTP %s (transient)", item_id, status)
            return [], "transient"
        if status != 200 or not isinstance(body, dict):
            return [], ""  # 400/404 — no job history for this item type
        stamps = [
            row.get("endTimeUtc") or row.get("startTimeUtc")
            for row in (body.get("value") or [])
            if isinstance(row, dict) and (row.get("endTimeUtc") or row.get("startTimeUtc"))
        ]
        return sorted((str(s) for s in stamps), reverse=True), ""

    def _latest_run(self, workspace_id: str, item_id: str) -> tuple[str | None, str]:
        """Return one item's most recent job-run time and a failure classifier.

        A thin wrapper over :meth:`_run_stamps` kept for callers that only need
        the recency signal.
        """
        stamps, failure = self._run_stamps(workspace_id, item_id)
        return (stamps[0] if stamps else None), failure

    def _warehouse_audit(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Read one Warehouse's SQL audit *configuration*.

        ``GET …/warehouses/{id}/settings/sqlAudit`` returns the audit state, the
        configured action groups, and the retention. It needs the Audit
        permission on the Warehouse item — **not** tenant-admin — so it is an
        ordinary delegated read. Only the configuration is returned; audit rows
        (``sys.fn_get_audit_file_v2``) are runtime data and are never fetched.

        Returns ``(settings, failure)`` with the same ``failure`` classifiers as
        :meth:`_definition_parts`. A 400/404 (the Warehouse does not support the
        setting) yields ``(None, "")`` — readable, simply not offered — which the
        caller records as unread rather than as "auditing is off".
        """
        path = f"/workspaces/{workspace_id}/warehouses/{item_id}/settings/sqlAudit"
        status, body = self._get(path)
        if status == 401 and self._try_refresh_token():
            status, body = self._get(path)
        if status in (401, 403):
            log.warning("warehouse %s sqlAudit -> HTTP %s (permission denied)", item_id, status)
            return None, "forbidden"
        if status is None or status == 429 or (isinstance(status, int) and status >= 500):
            log.warning("warehouse %s sqlAudit -> HTTP %s (transient)", item_id, status)
            return None, "transient"
        if status != 200 or not isinstance(body, dict):
            return None, ""
        return self._sql_audit_settings(body), ""

    @staticmethod
    def _sql_audit_settings(body: dict) -> dict:
        """Normalise a ``settings/sqlAudit`` payload into the shape checks read.

        Fabric has spelled the payload more than one way (``state`` vs
        ``auditState``, ``auditActionsAndGroups`` vs ``auditActionGroups``), and
        the state arrives as ``Enabled``/``Disabled`` in mixed case. Normalising
        here keeps every variation out of the check bodies, which must stay pure.
        """
        raw = body.get("properties") if isinstance(body.get("properties"), dict) else body
        state = ""
        for key in ("state", "auditState", "sqlAuditState", "status"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                state = str(value).strip()
                break
        groups: list[str] = []
        for key in ("auditActionsAndGroups", "auditActionGroups", "actionsAndGroups", "actionGroups"):
            value = raw.get(key)
            if isinstance(value, list):
                groups = [str(g).strip() for g in value if str(g).strip()]
                break
        retention = None
        for key in ("retentionDays", "retentionInDays", "auditRetentionDays"):
            value = raw.get(key)
            if isinstance(value, (int, float)):
                retention = int(value)
                break
        return {
            "state": state,
            "enabled": state.lower() in {"enabled", "enable", "on", "true"},
            "action_groups": groups,
            "retention_days": retention,
        }

    def _powerbi(self):
        """Return a lazily-built Power BI client, or ``None`` without a PBI token.

        Semantic-model refresh history is served only by the Power BI Datasets
        API, whose audience differs from the Fabric crawl token — so it needs a
        separately-minted token. A test may pre-set ``_powerbi_client``.
        """
        if self._powerbi_client is not None:
            return self._powerbi_client
        if not self._powerbi_token:
            return None
        from .powerbi import PowerBIClient
        self._powerbi_client = PowerBIClient(self._powerbi_token, timeout=self._timeout)
        return self._powerbi_client

    def _semantic_model_last_refresh(self, workspace_id: str, item_id: str) -> tuple[str | None, str]:
        """Return a semantic model's last-refresh time and a failure classifier.

        Reads the Power BI refresh history (latest first) for the model. Without
        a Power BI token the recency is *unknown* (``"forbidden"``) rather than
        "never refreshed", so it is excluded from staleness instead of counted
        as stale. A ``None`` timestamp with no failure means the model has no
        refresh history yet.
        """
        pbi = self._powerbi()
        if pbi is None:
            return None, "forbidden"
        try:
            return pbi.dataset_last_refresh(item_id, group_id=workspace_id)
        except Exception as exc:
            log.warning("semantic model %s refresh history error: %s", item_id, exc)
            return None, "transient"

    def _semantic_model_refresh_schedule(
        self, workspace_id: str, item_id: str
    ) -> tuple[dict | None, str]:
        """Read one semantic model's refresh *schedule configuration*.

        Served only by the Power BI Datasets API, whose audience differs from the
        Fabric crawl token — so without a Power BI token the schedule is
        *unknown* (``"forbidden"``), never "not configured". That distinction is
        what lets the alerting check report N/A instead of failing a workspace
        whose token simply lacked the scope.

        Returns ``(schedule, failure)``. ``(None, "")`` means the model genuinely
        has no refresh schedule (Direct Lake / push / pipeline-driven refresh).
        """
        pbi = self._powerbi()
        if pbi is None:
            return None, "forbidden"
        try:
            return pbi.dataset_refresh_schedule(item_id, group_id=workspace_id)
        except Exception as exc:
            log.warning("semantic model %s refresh schedule error: %s", item_id, exc)
            return None, "transient"

    def _enrich_run_history(self, ctx: WorkspaceContext, workspace_id: str) -> None:
        """Fill ``Item.last_run_utc`` from each runnable item's run/refresh history.

        One call per runnable item: semantic models are read from the Power BI
        refresh history (a different API audience); pipelines, notebooks,
        dataflows and Spark jobs from the Fabric job scheduler. Other types are
        skipped. ``Item`` is frozen, so a dated item is rebuilt with
        :func:`dataclasses.replace`.

        Recency is an optional, LOW-severity signal: when every runnable item's
        history is unreadable the resource is marked *unavailable* (so the
        staleness check reports N/A), but it is deliberately **not** added to
        ``read_failures`` — a run-history gap must never force a re-crawl or block
        the workspace from being cached.
        """
        attempted = read = failed = 0
        enriched: list[Item] = []
        semantic_models = sum(1 for i in ctx.items if i.type == "SemanticModel")
        if semantic_models and not self._powerbi_token:
            log.warning(
                "fetch %s: %d semantic model(s) present but no Power BI token — refresh "
                "recency will be N/A (sign-in did not yield a Power BI-audience token)",
                workspace_id, semantic_models,
            )
        for item in ctx.items:
            if item.type not in self._JOB_ITEM_TYPES or not item.id:
                enriched.append(item)
                continue
            attempted += 1
            if item.type == "SemanticModel":
                stamp, failure = self._semantic_model_last_refresh(workspace_id, item.id)
            else:
                stamps, failure = self._run_stamps(workspace_id, item.id)
                stamp = stamps[0] if stamps else None
                if not failure and len(stamps) > 1:
                    # Retained so an *observed cadence* can be derived. Capped:
                    # the interval only needs a handful of runs, and the snapshot
                    # should not grow with a chatty item's history.
                    ctx.run_history[item.id] = stamps[:_MAX_RETAINED_RUNS]
            if failure:
                failed += 1
                enriched.append(item)
            else:
                read += 1
                enriched.append(replace(item, last_run_utc=stamp) if stamp else item)
        ctx.items = enriched
        if attempted and read == 0 and failed:
            ctx.unavailable.add(Resource.ITEM_RUN_HISTORY)
        log.info("fetch %s: run history read for %d of %d runnable item(s)",
                 workspace_id, read, attempted)

    def _enrich_created_dates(self, ctx: WorkspaceContext, workspace_id: str) -> None:
        """Set ``Item.created_date`` on semantic models from the Power BI datasets API.

        ``GET /groups/{ws}/datasets`` exposes each model's ``createdDate`` in one
        call (no admin or capacity), so this is the reliable "when was it created"
        signal even for models with no refresh history. Needs a Power BI token;
        without one it is a silent no-op. Only semantic models carry a dataset
        created date, so other item types are left untouched.
        """
        pbi = self._powerbi()
        if pbi is None:
            return
        try:
            created = pbi.dataset_created_dates(group_id=workspace_id)
        except Exception as exc:
            log.warning("fetch %s: dataset created-dates read error: %s", workspace_id, exc)
            return
        if not created:
            return
        ctx.items = [
            replace(item, created_date=created[item.id])
            if item.type == "SemanticModel" and not item.created_date and item.id in created
            else item
            for item in ctx.items
        ]
        log.info("fetch %s: created date set for %d of %d semantic model(s)", workspace_id,
                 sum(1 for i in ctx.items if i.type == "SemanticModel" and i.created_date),
                 sum(1 for i in ctx.items if i.type == "SemanticModel"))

    # -- the provider contract -------------------------------------------------
    def fetch(
        self,
        workspace_id: str,
        layer: Layer = Layer.MIXED,
        resources: Iterable[Resource] = ALL_RESOURCES,
    ) -> WorkspaceContext:
        wanted = set(resources)

        # The workspace itself is always read: it establishes both identity and
        # access, and its status is how we detect an unreadable workspace.
        status, workspace = self._get(f"/workspaces/{workspace_id}")
        if status == 401 and self._try_refresh_token():
            status, workspace = self._get(f"/workspaces/{workspace_id}")
        if status != 200 or not isinstance(workspace, dict):
            raise WorkspaceAccessError(workspace_id, status)

        ctx = WorkspaceContext(
            id=workspace_id,
            display_name=workspace.get("displayName", workspace_id),
            layer=layer,
            capacity_id=workspace.get("capacityId"),
            deployment_pipeline=bool(workspace.get("assignedToDeploymentPipeline")),
        )

        if Resource.CONNECTIONS in wanted:
            rows, known = self._values("/connections")
            ctx.connections = [
                self._connection_metadata(row)
                for row in rows
                if isinstance(row, dict) and row.get("id")
            ]
            if not known:
                ctx.unavailable.add(Resource.CONNECTIONS)
            log.info("fetch %s: %d connection records read", workspace_id, len(ctx.connections))

        # Pipeline, notebook, and table reads all walk the item list, so fetch it
        # whenever any item-derived resource was asked for.
        if wanted & {
            Resource.ITEMS,
            Resource.PIPELINE_DEFINITIONS,
            Resource.NOTEBOOK_DEFINITIONS,
            Resource.ENVIRONMENT_DEFINITIONS,
            Resource.TABLE_SCHEMAS,
            Resource.SHORTCUTS,
            Resource.SEMANTIC_MODEL_DEFINITIONS,
            Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
            Resource.ITEM_RUN_HISTORY,
            Resource.WAREHOUSE_AUDIT,
        }:
            rows, known = self._values(f"/workspaces/{workspace_id}/items")
            ctx.items = [Item.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ITEMS)
            log.info("fetch %s: %d items by type %s", workspace_id,
                     len(ctx.items), dict(Counter(i.type for i in ctx.items)))

        # Per-item run/refresh recency (last_run_utc) plus the per-workspace
        # semantic-model created date. Fetched whenever a recency-needing resource
        # is selected — the KB crawl requests it for every workspace.
        if Resource.ITEM_RUN_HISTORY in wanted and ctx.items:
            self._enrich_run_history(ctx, workspace_id)
            self._enrich_created_dates(ctx, workspace_id)

        if Resource.ROLE_ASSIGNMENTS in wanted:
            rows, known = self._values(f"/workspaces/{workspace_id}/roleAssignments")
            ctx.role_assignments = [RoleAssignment.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ROLE_ASSIGNMENTS)

        if Resource.GIT in wanted:
            git_status, git_body = self._get(f"/workspaces/{workspace_id}/git/connection")
            if git_status == 200:
                ctx.git_details = self._git_connection(git_body)
                ctx.git_connected = bool(ctx.git_details.get("connected"))
            elif git_status in (400, 404):
                ctx.git_connected = False  # genuinely not connected
            else:
                # 401/403/500/transport failure: we could not determine it.
                ctx.unavailable.add(Resource.GIT)

        if Resource.ENVIRONMENT_DEFINITIONS in wanted:
            environments = [i for i in ctx.items if i.type == "Environment"]
            attempted = read = forbidden = transient = 0
            for item in environments:
                attempted += 1
                definition, failure = self._environment_definition(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                else:
                    read += 1
                    if definition:
                        record = dict(definition)
                        record["id"] = item.id
                        record["display_name"] = item.display_name
                        ctx.environments[item.id] = record
                        ctx.environments[item.display_name] = record
            self._record_failures(ctx, Resource.ENVIRONMENT_DEFINITIONS,
                                  attempted, read, forbidden, transient)

        # The expensive one — one call per pipeline. Only paid for when a
        # selected check actually reads a pipeline definition.
        if Resource.PIPELINE_DEFINITIONS in wanted:
            attempted = read = forbidden = transient = empty = 0
            for item in ctx.items:
                if item.type != "DataPipeline":
                    continue
                attempted += 1
                definition, failure = self._pipeline_definition(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                elif definition:
                    read += 1
                    key = self._unique_key(ctx.pipelines, item.display_name or item.id, item.id)
                    ctx.pipelines[key] = definition
                else:
                    empty += 1
            self._record_failures(ctx, Resource.PIPELINE_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty)

        # Notebook definitions: same one-call-per-item getDefinition pattern.
        if Resource.NOTEBOOK_DEFINITIONS in wanted:
            found = [i for i in ctx.items if i.type == "Notebook"]
            attempted = read = forbidden = transient = empty = 0
            for item in found:
                attempted += 1
                definition, failure = self._notebook_definition(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                elif definition:
                    read += 1
                    if definition:
                        binding = self._environment_binding(definition)
                        environment = ctx.environments.get(binding)
                        if environment:
                            definition["_auditfast_environment"] = {
                                "id": environment.get("id", binding),
                                "name": environment.get("display_name", binding),
                                "runtime_version": environment.get("runtime_version"),
                            }
                        monitoring = self._notebook_monitoring(workspace_id, item.id)
                        if monitoring:
                            definition["_auditfast_monitoring"] = monitoring
                        ctx.notebooks[item.display_name or item.id] = definition
            self._record_failures(ctx, Resource.NOTEBOOK_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty)
            log.info("fetch %s: %d notebooks found, %d definitions read",
                     workspace_id, len(found), len(ctx.notebooks))

        # Lakehouse table listing (name/type/format). Column schemas need the SQL
        # analytics endpoint and are left empty here; column-level checks report
        # N/A rather than failing when they are absent.
        if Resource.TABLE_SCHEMAS in wanted:
            lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
            attempted = read = forbidden = transient = 0
            for item in lakehouses:
                attempted += 1
                tables, failure = self._lakehouse_tables(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                else:
                    read += 1
                    for tbl in tables:
                        name = tbl.get("name")
                        if name:
                            ctx.tables[name] = {
                                "type": tbl.get("type", ""),
                                "format": tbl.get("format", ""),
                                "columns": [],
                            }
            self._record_failures(ctx, Resource.TABLE_SCHEMAS,
                                  attempted, read, forbidden, transient)
            log.info("fetch %s: %d lakehouses, %d tables read",
                     workspace_id, len(lakehouses), len(ctx.tables))

        # Column schemas — the OneLake Table API first (plain HTTPS, a OneLake
        # Storage-audience token, no ODBC/port 1433), then the SQL/TDS endpoint as
        # a fallback and for the SQL type widths and Warehouse RLS OneLake does not
        # expose. Every failure leaves the data absent, which the checks report N/A.
        if Resource.TABLE_COLUMNS in wanted:
            self._read_onelake_columns(ctx, workspace_id)
        if Resource.TABLE_COLUMNS in wanted or Resource.WAREHOUSE_SECURITY in wanted:
            self._read_sql_endpoints(ctx, workspace_id, wanted)

        # Warehouse SQL audit *configuration* — plain Fabric REST, one call per
        # Warehouse, gated on the Audit permission of the item (not tenant-admin).
        # No audit rows are ever read: this is a configuration audit, not a log
        # pull, so runtime data never enters the knowledge base.
        if Resource.WAREHOUSE_AUDIT in wanted:
            warehouses = [i for i in ctx.items if i.type == "Warehouse"]
            attempted = read = forbidden = transient = empty = 0
            for item in warehouses:
                attempted += 1
                settings, failure = self._warehouse_audit(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                elif settings is None:
                    empty += 1
                else:
                    read += 1
                    key = self._unique_key(
                        ctx.warehouse_audit, item.display_name or item.id, item.id
                    )
                    ctx.warehouse_audit[key] = settings
            self._record_failures(ctx, Resource.WAREHOUSE_AUDIT,
                                  attempted, read, forbidden, transient, empty)
            log.info("fetch %s: sql audit settings read for %d of %d warehouse(s)",
                     workspace_id, read, attempted)

        # OneLake shortcuts per lakehouse (governance/lineage: external references).
        if Resource.SHORTCUTS in wanted:
            total = 0
            attempted = failed = 0
            for item in ctx.items:
                if item.type != "Lakehouse":
                    continue
                attempted += 1
                shortcuts, known = self._item_shortcuts(workspace_id, item.id)
                if not known:
                    failed += 1
                    continue
                if shortcuts:
                    ctx.shortcuts[item.display_name or item.id] = shortcuts
                    total += len(shortcuts)
            # Every listing failed: "could not ask" must not read as "has none".
            if attempted and failed == attempted:
                ctx.unavailable.add(Resource.SHORTCUTS)
            log.info("fetch %s: %d shortcuts read (%d of %d listings failed)",
                     workspace_id, total, failed, attempted)

        # Semantic-model measures + relationships, parsed from the TMSL definition.
        if Resource.SEMANTIC_MODEL_DEFINITIONS in wanted:
            attempted = read = forbidden = transient = empty = 0
            for item in ctx.items:
                if item.type != "SemanticModel":
                    continue
                attempted += 1
                model, failure = self._semantic_model_definition(workspace_id, item.id)
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                elif model:
                    read += 1
                    key = self._unique_key(
                        ctx.semantic_models, item.display_name or item.id, item.id
                    )
                    ctx.semantic_models[key] = model
                else:
                    empty += 1
            self._record_failures(ctx, Resource.SEMANTIC_MODEL_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty)
            log.info("fetch %s: %d semantic models parsed", workspace_id, len(ctx.semantic_models))

        # Semantic-model refresh *schedule configuration* — one Power BI Datasets
        # API GET per model, delegated scope only (no tenant-admin). Carries
        # ``notifyOption``, which is how "a refresh failure alerts the owning
        # team" is actually configured. No refresh rows are read.
        if Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE in wanted:
            attempted = read = forbidden = transient = 0
            for item in ctx.items:
                if item.type != "SemanticModel":
                    continue
                attempted += 1
                schedule, failure = self._semantic_model_refresh_schedule(
                    workspace_id, item.id
                )
                if failure == "forbidden":
                    forbidden += 1
                elif failure == "transient":
                    transient += 1
                else:
                    # A model with no schedule read cleanly: absence from the map
                    # is the finding, so it must not count as a failure.
                    read += 1
                    if schedule is not None:
                        key = self._unique_key(
                            ctx.refresh_schedules, item.display_name or item.id, item.id
                        )
                        ctx.refresh_schedules[key] = schedule
            self._record_failures(ctx, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
                                  attempted, read, forbidden, transient)
            log.info("fetch %s: refresh schedule read for %d of %d semantic model(s)",
                     workspace_id, read, attempted)

        return ctx

    def list_workspaces(self) -> list[dict]:
        rows, _known = self._values("/workspaces")
        return [
            {
                "id": row.get("id"),
                "name": row.get("displayName", row.get("id")),
                "layer": "",
                "items": None,
                "pipelines": None,
            }
            for row in rows
        ]

    # -- diagnostics -----------------------------------------------------------
    def probe(self, max_workspaces: int = 3) -> dict:
        """Report what this token can actually read, per sub-resource.

        Used by the Diagnose button when a live run returns less than expected —
        it surfaces partial permissions (for example: items readable, role
        assignments forbidden) that would otherwise look like clean passes.
        """
        result: dict[str, Any] = {"list_status": None, "count": 0, "samples": [], "error": None}
        status, body = self._get("/workspaces")
        result["list_status"] = status
        if status != 200 or not isinstance(body, dict):
            result["error"] = f"Listing workspaces returned HTTP {status}."
            return result

        workspaces = body.get("value") or []
        result["count"] = len(workspaces)
        for workspace in workspaces[:max_workspaces]:
            workspace_id = workspace.get("id")
            items_status, items_body = self._get(f"/workspaces/{workspace_id}/items")
            roles_status, _ = self._get(f"/workspaces/{workspace_id}/roleAssignments")
            items = (items_body or {}).get("value", []) if items_status == 200 else []
            result["samples"].append({
                "name": workspace.get("displayName", workspace_id),
                "items_status": items_status,
                "items": len(items),
                "pipelines": sum(1 for i in items if i.get("type") == "DataPipeline"),
                "roles_status": roles_status,
            })
        return result
