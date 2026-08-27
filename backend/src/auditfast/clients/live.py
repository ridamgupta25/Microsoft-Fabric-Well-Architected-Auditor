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
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any
from urllib.parse import quote

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

#: Why a semantic model's refresh schedule could not be read. Recorded on the
#: snapshot so the gap explains itself without anyone reading a crawl log - and
#: so a network blip is never mistaken for a permission denial.
_SCHEDULE_FORBIDDEN_REASON = (
    "Power BI rejected the token (HTTP 401/403) - the sign-in lacks "
    "Dataset.Read.All, or the tenant setting for it is off"
)
_SCHEDULE_TRANSIENT_REASON = (
    "Power BI was unreachable or throttled (DNS failure, reset connection, "
    "timeout, 429 or 5xx) - retryable, and not a permission problem"
)

#: Schemas the platform owns. Their views and routines ship with every SQL
#: endpoint, so counting them would report an abstraction layer nobody built.
_PLATFORM_SCHEMAS: frozenset[str] = frozenset({
    "sys", "queryinsights", "information_schema", "guest",
    "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
})

#: Database principals every SQL endpoint ships with. Recording them would make
#: an empty estate look populated, so the access checks judge only real grants.
#: Lower-cased: SQL identifiers are case-insensitive, and the filter compares
#: against a lower-cased name.
_SYSTEM_PRINCIPALS: frozenset[str] = frozenset({
    "public", "dbo", "guest", "sys", "information_schema",
    "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
    "db_backupoperator", "db_datareader", "db_datawriter",
    "db_denydatareader", "db_denydatawriter",
})


def _bounded_int_env(name: str, default: int, low: int, high: int) -> int:
    """Read an int from the environment, clamped to ``[low, high]``."""
    try:
        return max(low, min(int(os.environ.get(name, default)), high))
    except (TypeError, ValueError):
        return default


def _retry_after_seconds(response, attempt: int, cap: float = 30.0) -> float:
    """Seconds to wait before a retry, honoring the server's ``Retry-After``.

    Falls back to exponential backoff when the header is absent, and never waits
    longer than ``cap`` so a hostile value cannot stall the crawl.
    """
    header = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    try:
        if header is not None:
            return min(float(header), cap)
    except (TypeError, ValueError):
        pass
    return min(2.0 * (attempt + 1), cap)


def _failure_detail(item: Item, failure: str, reason: str = "") -> dict[str, str]:
    detail = {
        "id": item.id,
        "name": item.display_name or item.id,
        "failure": failure,
    }
    if reason:
        detail["reason"] = reason
    return detail


#: Per-workspace fan-out: how many item definitions are fetched at once while
#: crawling a single workspace. Each getDefinition is a slow long-running
#: operation, so fetching them concurrently is the crawl's biggest speed-up.
_ITEM_FETCH_WORKERS = _bounded_int_env("AUDITFAST_MAX_PARALLEL_ITEM_FETCHES", 4, 1, 16)

#: Process-wide ceiling on concurrent item-definition calls, shared across every
#: workspace and audit, so parallel crawls together never exceed what the Fabric
#: APIs tolerate (beyond which throttling would erase the gain).
_DEFINITION_GATE = threading.BoundedSemaphore(
    _bounded_int_env("AUDITFAST_MAX_INFLIGHT_ITEM_FETCHES", 32, 1, 32)
)


class LiveFabricProvider:
    """Reads a live Fabric tenant with a delegated, read-only OAuth2 token."""

    BASE = "https://api.fabric.microsoft.com/v1"

    def __init__(self, token: str, timeout: int = 60, token_refresher=None,
                 powerbi_token: str | None = None, sql_token: str | None = None,
                 storage_token: str | None = None, sql_token_refresher=None,
                 github_repository_security_token: str | None = None,
                 azure_devops_repository_security_token: str | None = None):
        import requests  # imported lazily so offline mode needs no HTTP stack

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})
        # One connection pool wide enough that parallel workspace crawls share it
        # without serialising on a too-small default (10).
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        #: Guards the shared session's auth header and the lazily-built sub-clients
        #: so several workspaces can be crawled concurrently on one provider.
        self._lock = threading.Lock()
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
        #: Re-mints the SQL token when it is rejected mid-crawl. The Fabric token
        #: already has ``token_refresher`` for exactly this reason: a large
        #: workspace takes longer to crawl than an Entra token lives. Without the
        #: same treatment the SQL reads silently stop partway through.
        self._sql_token_refresher = sql_token_refresher
        self._sql_reader = None
        #: A Storage-audience token (``https://storage.azure.com``), used only for
        #: OneLake ADLS Gen2 Files listings. Without it, file-layout checks report
        #: N/A rather than treating unreadable Files sections as empty.
        self._storage_token = storage_token
        self._onelake_client = None
        self._github_repository_security_token = github_repository_security_token
        self._azure_devops_repository_security_token = azure_devops_repository_security_token
        self._repository_security_cache: dict[tuple[str, str, str, str], dict] = {}

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
            with self._lock:
                self._session.headers.update({"Authorization": f"Bearer {new_token}"})
            log.info("token refreshed, resuming crawl")
            return True
        log.warning("token refresh failed, could not acquire new token")
        return False

    def _fetch_items_parallel(self, items: list, worker):
        """Run ``worker(item)`` for every item concurrently, results in input order.

        Bounded by the process-wide definition gate so several workspaces (and
        several audits) crawling at once never exceed the Fabric APIs' tolerance.
        ``worker`` handles its own errors and returns a value; it must not raise.
        """
        results: list = [None] * len(items)
        if not items:
            return results
        max_workers = min(_ITEM_FETCH_WORKERS, len(items))

        def _run(index: int, obj):
            with _DEFINITION_GATE:
                return index, worker(obj)

        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="item-fetch") as pool:
            futures = [pool.submit(_run, i, obj) for i, obj in enumerate(items)]
            for future in as_completed(futures):
                index, value = future.result()
                results[index] = value
        return results

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
                    time.sleep(_retry_after_seconds(None, attempt))
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
                time.sleep(_retry_after_seconds(response, attempt))
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

    def _spark_settings(self, workspace_id: str) -> dict:
        """The workspace's default Spark runtime, from the Spark settings API.

        ``GET /workspaces/{id}/spark/settings`` returns
        ``environment.runtimeVersion`` - the runtime a notebook inherits when it
        binds to no named Environment, which is the commonest setup. Documented
        as an ordinary delegated read (Viewer is enough), not tenant-admin.

        Only the runtime and the default-environment name are kept: pool sizes
        and session timeouts are settings no check reads, and the KB must not
        grow with data nobody uses. A failure yields ``{}`` so the runtime check
        falls back to its other evidence rather than raising.
        """
        status, body = self._get(f"/workspaces/{workspace_id}/spark/settings")
        if status != 200 or not isinstance(body, dict):
            log.info("workspace spark settings unavailable for %s (status %s) - "
                     "notebooks with no bound Environment will have no runtime",
                     workspace_id, status)
            return {}
        environment = body.get("environment") or {}
        settings = {
            "runtime_version": str(environment.get("runtimeVersion") or ""),
            "default_environment": str(environment.get("name") or ""),
        }
        return settings if settings["runtime_version"] else {}

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

        want_columns = Resource.TABLE_COLUMNS in wanted
        want_security = Resource.WAREHOUSE_SECURITY in wanted

        def get_json(path: str):
            status, body = self._get(path)
            return body if status == 200 and isinstance(body, dict) else {}

        endpoints = discover_endpoints(get_json, workspace_id)
        if not endpoints:
            # A workspace with no Lakehouse/Warehouse never had a SQL endpoint to
            # read, so this is not a limitation to report — mark the resources N/A
            # silently, exactly as before the SQL endpoint was wired in.
            has_store = any(
                (item.type or "") in ("Lakehouse", "Warehouse") for item in ctx.items
            )
            if not has_store:
                for resource in (Resource.TABLE_COLUMNS, Resource.WAREHOUSE_SECURITY):
                    if resource in wanted:
                        ctx.unavailable.add(resource)
                log.info("fetch %s: no Lakehouse/Warehouse — SQL endpoint reads skipped",
                         workspace_id)
                return
            reason = ("no provisioned SQL analytics endpoint was discovered in this "
                      "workspace (a newly created Lakehouse/Warehouse provisions one "
                      "asynchronously, and a paused capacity serves none)")
            self._record_environment_gap(ctx, wanted, reason, len(endpoints))
            log.info("fetch %s: no provisioned SQL endpoints discovered", workspace_id)
            return

        reader = SqlEndpointReader(self._sql_token,
                                   token_provider=self._sql_token_refresher)
        if not reader.available:
            # The reason lives on the reader, and used to reach the log only - so
            # the single most common cause of "no Lakehouse columns" (a server with
            # no ODBC driver) produced a snapshot that could not explain itself.
            # Persisting it means the report says *why*, with no log to scrape.
            self._record_environment_gap(
                ctx, wanted, reader.unavailable_reason, len(endpoints))
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
                    # One extra connection per endpoint, carrying every remaining
                    # catalog read as a single batch. Attempted only where the
                    # column read already succeeded: if that failed, this one has
                    # no better prospect and would just double the wasted time.
                    self._store_endpoint_metadata(ctx, reader, endpoint)
            if want_security and endpoint.kind == "Warehouse":
                sec_attempted += 1
                policies = reader.security_policies(endpoint)
                if policies is not None:
                    sec_read += 1
                    ctx.warehouse_security[endpoint.name] = policies

        # None readable means "we could not look", which must not read as "none
        # configured". Some readable is a partial gap, recorded but still usable.
        # The per-endpoint reasons ride along so the snapshot explains itself.
        reason_counts: dict[str, int] = {}
        for reason in reader.failures.values():
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if want_columns:
            self._record_failures(ctx, Resource.TABLE_COLUMNS, col_attempted,
                                  col_read, 0, col_attempted - col_read,
                                  reasons=reason_counts)
        if collisions:
            log.info("fetch %s: %d table name(s) exist in more than one store; "
                     "the duplicates are keyed '<store>.<table>'",
                     workspace_id, collisions)
        if want_security and sec_attempted:
            self._record_failures(ctx, Resource.WAREHOUSE_SECURITY, sec_attempted,
                                  sec_read, 0, sec_attempted - sec_read,
                                  reasons=reason_counts)
        elif want_security:
            # No Warehouse in the workspace: nothing to read, nothing to report.
            ctx.unavailable.add(Resource.WAREHOUSE_SECURITY)

        # The per-endpoint reasons are the only way to tell a blocked port from a
        # permission gap, so surface the distinct ones rather than burying them.
        for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
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
        connected = state != "NotConnected" if state else bool(provider)
        return {
            "connected": connected,
            "state": state,
            "provider": provider.get("gitProviderType", ""),
            # Azure DevOps reports organizationName; GitHub reports ownerName.
            "organization": provider.get("organizationName") or provider.get("ownerName", ""),
            "project": provider.get("projectName", ""),
            "repository": provider.get("repositoryName", ""),
            "repository_id": provider.get("repositoryId") or provider.get("id"),
            "repository_url": provider.get("repositoryUrl") or provider.get("url", ""),
            "branch": provider.get("branchName", ""),
            "directory": provider.get("directoryName", ""),
            "head": sync.get("head"),
            "last_sync_time": sync.get("lastSyncTime"),
            "secret_scanning": LiveFabricProvider._secret_scanning_status(
                provider.get("gitProviderType", ""), connected
            ),
        }

    @staticmethod
    def _unverified_security(reason: str) -> dict:
        return {"enabled": None, "push_protection": None, "verified": False,
                "reason": reason}

    def _discover_repository_security(self, details: dict) -> dict:
        """Read normalized provider security settings once per repository."""
        provider = str(details.get("provider") or "").strip().lower()
        organization = str(details.get("organization") or "").strip()
        project = str(details.get("project") or "").strip()
        repository = str(details.get("repository") or "").strip()
        repository_id = str(details.get("repository_id") or "").strip()
        if not provider or not organization or not repository:
            return self._unverified_security(
                "repository owner, provider, or name is missing from Fabric Git metadata"
            )

        cache_key = (provider, organization, project, repository_id or repository)
        with self._lock:
            cached = self._repository_security_cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        if provider == "github":
            token = self._github_repository_security_token
            url = f"https://api.github.com/repos/{quote(organization)}/{quote(repository)}"
            headers = {"Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            metadata = self._fetch_repository_security(url, headers, "GitHub")
        elif provider in {"azuredevops", "azure devops", "ado"}:
            token = self._azure_devops_repository_security_token
            if not token:
                metadata = self._unverified_security(
                    "Azure DevOps repository-security token not provided"
                )
            elif not repository_id:
                metadata = self._unverified_security(
                    "Azure DevOps repository id is missing from Fabric Git metadata"
                )
            else:
                url = ("https://advsec.dev.azure.com/"
                       f"{quote(organization)}/{quote(project)}/_apis/management/"
                       f"repositories/{quote(repository_id)}/enablement?api-version=7.2-preview.1")
                metadata = self._fetch_repository_security(
                    url, {"Authorization": f"Bearer {token}",
                          "Accept": "application/json"}, "Azure DevOps")
        else:
            metadata = self._unverified_security(f"Unsupported Git provider: {provider}")

        with self._lock:
            self._repository_security_cache[cache_key] = dict(metadata)
        return metadata

    def _fetch_repository_security(self, url: str, headers: dict, provider: str) -> dict:
        try:
            response = self._session.get(url, headers=headers, timeout=self._timeout)
            if response.status_code in (401, 403):
                return self._unverified_security(
                    f"{provider} repository security returned HTTP {response.status_code}")
            if response.status_code == 404:
                return self._unverified_security(
                    f"{provider} repository was not found or security metadata is unavailable")
            if response.status_code != 200:
                return self._unverified_security(
                    f"{provider} repository security returned HTTP {response.status_code}")
            payload = response.json() or {}
            if provider == "GitHub":
                analysis = payload.get("security_and_analysis") or {}
                enabled = (analysis.get("secret_scanning") or {}).get("status")
                push_protection = (analysis.get("secret_scanning_push_protection") or {}).get("status")
            else:
                enabled = payload.get("status")
                push_protection = payload.get("pushProtectionStatus")
            verified = isinstance(enabled, str)
            return {"enabled": enabled == "enabled" if verified else None,
                    "push_protection": push_protection == "enabled" if isinstance(push_protection, str) else None,
                    "verified": verified,
                    "reason": None if verified else "security status was absent"}
        except Exception:
            log.warning("%s repository security read failed", provider)
            return self._unverified_security(f"{provider} repository security read failed")

    @staticmethod
    def _secret_scanning_status(provider_type: str, connected: bool) -> dict:
        """Record what is known about the repo's secret-scanning posture.

        Secret scanning / push protection is a *Git-provider* control (GitHub
        Advanced Security, Azure DevOps push protection) that the Fabric
        ``git/connection`` API does not expose, and reading it needs a
        repo-security token the auditor is not granted. So the honest, read-only
        answer is ``enabled: None`` (unknown, not verified) with the reason, which
        lets a check report the environment as *unverified* rather than assume a
        pass or a fail. The shape stays stable so a future provider integration
        can fill ``enabled`` in without a schema change.
        """
        if not connected:
            return {
                "provider": provider_type,
                "verified": False,
                "enabled": None,
                "push_protection": None,
                "reason": "workspace is not connected to source control",
            }
        return {
            "provider": provider_type,
            "verified": False,
            "enabled": None,
            "push_protection": None,
            "reason": (
                "secret scanning / push protection is a Git-provider security "
                "control not exposed by the Fabric git/connection API and needs a "
                "repo-security token to read; not verifiable read-only"
            ),
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

    def _reflex_definition(self, workspace_id: str, item_id: str) -> tuple[dict | None, str]:
        """Read a Data Activator (Reflex) definition and reduce it to rule counts.

        Returns ``(summary, failure)`` — see :meth:`_definition_parts`. The
        ``ReflexEntities.json`` part is a flat list of typed entities; only the
        bounded counts a check needs are kept, never the rule bodies.
        """
        parts, failure = self._definition_parts(workspace_id, item_id)
        for part in parts:
            if not str(part.get("path") or "").endswith("ReflexEntities.json"):
                continue
            try:
                payload = base64.b64decode(part["payload"]).decode("utf-8")
                entities = json.loads(payload)
            except Exception:
                return None, ""
            if isinstance(entities, list):
                return self._reflex_summary(entities), ""
            return None, ""
        return None, failure

    @staticmethod
    def _reflex_summary(entities: list) -> dict:
        """Count rules, active rules, sources and actions in a ReflexEntities list."""
        rules = active = sources = actions = 0
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            etype = str(entity.get("type") or "")
            payload = entity.get("payload") if isinstance(entity.get("payload"), dict) else {}
            if etype == "timeSeriesView-v1":
                definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
                if str(definition.get("type") or "") == "Rule":
                    rules += 1
                    settings = definition.get("settings") if isinstance(definition.get("settings"), dict) else {}
                    if settings.get("shouldRun"):
                        active += 1
            elif etype.endswith("Source-v1"):
                sources += 1
            elif etype == "fabricItemAction-v1":
                actions += 1
        return {"rules": rules, "active_rules": active, "sources": sources, "actions": actions}

    @staticmethod
    def _annotate_partitions(ctx: WorkspaceContext, onelake, workspace_id: str, item) -> None:
        """Record each Delta table's partition columns, and that we managed to look.

        ``partitions_listed`` is what lets a check treat "no partition columns" as
        a finding rather than as missing data.
        """
        partitions, failure = onelake.lakehouse_table_partitions(workspace_id, item.id)
        if failure:
            return
        store = item.display_name or item.id
        for table_name, partition_columns in partitions.items():
            entry = ctx.tables.get(table_name) or ctx.tables.get(f"{store}.{table_name}")
            if entry is None:
                continue
            entry["partitionColumns"] = partition_columns
            entry["partitions_listed"] = True

    @staticmethod
    def _record_failures(ctx: WorkspaceContext, resource: Resource,
                         attempted: int, read: int, forbidden: int, transient: int,
                         empty: int = 0, reasons: dict[str, int] | None = None,
                         artifacts: list[dict[str, str]] | None = None) -> None:
        """Record a one-per-item read outcome on the context.

        When some items of a type could not be read, store the counts so the gap
        is visible ("N of M could not be read"). When *none* could be read, also
        mark the resource unavailable so its checks report N/A with the reason.

        ``empty`` counts items whose definition call returned but carried nothing
        usable. Those are a real coverage gap, but re-crawling will not fix them,
        so they are reported without making the snapshot un-cacheable.

        ``reasons`` is a ``reason -> count`` histogram. Counts alone say *how
        many* reads failed but never *why*, and the why used to exist only in the
        crawl log - which is discarded once the process exits, leaving a snapshot
        that cannot explain itself. Persisting it makes a blocked port
        distinguishable from a throttled tenant after the fact.
        """
        if attempted and (forbidden or transient or empty):
            stat = {
                "attempted": attempted,
                "read": read,
                "failed": forbidden + transient + empty,
                "forbidden": forbidden,
                "transient": transient,
                "empty": empty,
            }
            if reasons:
                stat["reasons"] = dict(
                    sorted(reasons.items(), key=lambda kv: -kv[1])
                )
            if artifacts:
                stat["artifacts"] = [dict(artifact) for artifact in artifacts]
            ctx.read_failures[resource.value] = stat
            if read == 0:
                ctx.unavailable.add(resource)

    def _store_endpoint_metadata(self, ctx: WorkspaceContext, reader,
                                 endpoint) -> None:
        """Fold one endpoint's catalog batch into the workspace context.

        **Bounded by construction.** Nothing here scales with the *data* in the
        estate: row counts are integers read from partition metadata, foreign
        keys are a small edge list, and view/procedure bodies are already capped
        by the query. Per the knowledge-base rule, no row content is ever stored.

        Everything is keyed by the same table name the column reader uses, so a
        check reads ``table["row_count"]`` or ``table["references"]`` without
        knowing the endpoint existed.
        """
        try:
            sets = reader.metadata(endpoint)
        except Exception as exc:  # noqa: BLE001 - extra metadata is best-effort
            log.info("sql metadata batch failed for %s: %s", endpoint.name, exc)
            return
        if not sets:
            return

        # object_id -> (schema, name, type) for everything the endpoint declared.
        objects: dict[int, tuple[str, str, str]] = {}
        for row in sets.get("objects", ()):
            try:
                objects[int(row[0])] = (str(row[1] or ""), str(row[2] or ""),
                                        str(row[3] or "").strip())
            except (TypeError, ValueError):
                continue

        def table_entry(object_id) -> dict | None:
            """The context table an object id belongs to, if it is one we hold.

            **The key must be built the way the column reader built it**, not
            guessed at. ``SqlEndpointReader.columns`` files a Warehouse table as
            ``<schema>.<table>`` and a Lakehouse table under its bare name, so
            looking up the bare name alone matched nothing on a Warehouse: the
            IDENTITY, foreign-key, key-constraint and row-count reads all
            returned their rows and attached none of them, silently, on every
            Warehouse ever crawled.

            The store-prefixed spelling is tried last because the same reader
            falls back to ``<store>.<table>`` when two stores hold a table of the
            same name.
            """
            info = objects.get(int(object_id)) if object_id is not None else None
            if not info:
                return None
            schema, name = info[0], info[1]
            qualified = f"{schema}.{name}" if schema else name
            primary = qualified if endpoint.kind == "Warehouse" else name
            for key in (primary, name, qualified,
                        f"{endpoint.name}.{primary}", f"{endpoint.name}.{name}"):
                entry = ctx.tables.get(key)
                if entry is not None:
                    return entry
            return None

        for row in sets.get("row_counts", ()):
            entry = table_entry(row[0])
            if entry is not None and row[1] is not None:
                # Approximate by definition - partition metadata, not a COUNT(*).
                entry["row_count"] = int(row[1])

        # Declared foreign keys, stored as a name-level edge list on the
        # referencing table. This is what lets a check tell a fact from a
        # dimension structurally instead of reading the table's name.
        for row in sets.get("foreign_keys", ()):
            parent = table_entry(row[1])
            referenced = objects.get(int(row[2])) if row[2] is not None else None
            if parent is None or not referenced:
                continue
            parent.setdefault("references", [])
            if referenced[1] not in parent["references"]:
                parent["references"].append(referenced[1])

        for row in sets.get("key_constraints", ()):
            entry = table_entry(row[1])
            if entry is not None:
                entry["has_declared_key"] = True

        # IDENTITY columns: the table *declares* which column the engine
        # generates. A check reading this no longer has to infer "generated"
        # from a column name. Stored per table as a list of column names, since
        # a check needs to know which column, not merely that one exists.
        for row in sets.get("identity_columns", ()):
            entry = table_entry(row[0])
            if entry is None or not row[1]:
                continue
            identity = entry.setdefault("identity_columns", [])
            if row[1] not in identity:
                identity.append(str(row[1]))

        # Database-level automatic-statistics switches. Off is a real
        # misconfiguration a user can reach and we can read - unlike NORECOMPUTE,
        # which Fabric refuses to set at all.
        #
        # Keyed by the database name the row carries, not by the endpoint: one
        # SQL endpoint exposes several databases (its own plus `master` and the
        # dataflow staging stores), and an earlier `WHERE name = DB_NAME()`
        # filter matched none of them, so this read silently returned nothing
        # while `sys.stats` on the same connection worked. `master` is skipped -
        # it is a system database nobody configures.
        for row in sets.get("database_options", ()):
            database = str(row[0] or "").strip()
            if not database or database.lower() == "master":
                continue
            ctx.warehouse_options[database] = {
                "auto_create_stats": bool(row[1]) if row[1] is not None else None,
                "auto_update_stats": bool(row[2]) if row[2] is not None else None,
                "auto_update_stats_async": bool(row[3]) if len(row) > 3 and row[3] is not None else None,
            }

        for row in sets.get("stats", ()):
            entry = table_entry(row[0])
            if entry is None:
                continue
            entry["statistics"] = int(entry.get("statistics", 0)) + 1
            # `no_recompute = 1` means someone switched automatic refresh off for
            # this statistic - the only way stale statistics survive on Fabric,
            # and the one thing worth reporting now the engine maintains the rest.
            if len(row) > 4 and row[4]:
                entry["statistics_norecompute"] = int(
                    entry.get("statistics_norecompute", 0)) + 1
            # Newest refresh across the table's statistics, so a check can see
            # whether automatic maintenance is actually running here.
            if len(row) > 5 and row[5]:
                stamp = str(row[5])
                if stamp > str(entry.get("statistics_last_updated") or ""):
                    entry["statistics_last_updated"] = stamp

        # Views and routines are workspace-level, not per table: they describe the
        # load logic, which several Warehouse checks need and none can read today.
        # Platform schemas are excluded - every SQL endpoint ships hundreds of
        # ``sys``/``queryinsights`` view definitions that nobody wrote. Storing
        # them bloats the snapshot (1,171 arrived on one real crawl, all platform)
        # and would let a check report an abstraction layer nobody built.
        views = [
            {"schema": str(r[0] or ""), "name": str(r[1] or ""),
             "definition": str(r[2] or ""), "store": endpoint.name}
            for r in sets.get("views", ())
            if str(r[0] or "").lower() not in _PLATFORM_SCHEMAS
        ]
        routines = [
            {"schema": str(r[0] or ""), "name": str(r[1] or ""),
             "type": str(r[2] or ""), "definition": str(r[3] or ""),
             "store": endpoint.name}
            for r in sets.get("routines", ())
            if str(r[0] or "").lower() not in _PLATFORM_SCHEMAS
        ]
        if views:
            ctx.sql_views.extend(views)
        if routines:
            ctx.sql_routines.extend(routines)

        # Database-scoped principals: the same "who has access" question as
        # workspace role assignments, from a source that needs no admin.
        # Compared case-insensitively - SQL identifiers are not case-sensitive,
        # so a ``DBO`` or ``Public`` would otherwise slip past the filter and
        # make an empty estate look populated.
        principals = [
            {"name": str(r[1] or ""), "type": str(r[2] or ""),
             "authentication": str(r[3] or ""), "store": endpoint.name}
            for r in sets.get("principals", ())
            if str(r[1] or "").strip().lower() not in _SYSTEM_PRINCIPALS
        ]
        if principals:
            ctx.sql_principals.extend(principals)

    @staticmethod
    def _record_environment_gap(ctx: WorkspaceContext, wanted: set[Resource],
                                reason: str, endpoints: int) -> None:
        """Persist a whole-resource SQL gap, with its reason, onto the context.

        Distinct from :meth:`_record_failures`, which reports per-item outcomes.
        Here nothing was attempted at all - the reader could not start - so the
        count is the number of endpoints that *would* have been read. Recording it
        the same way means the report's crawl-completeness section explains the
        gap ("no ODBC driver installed") instead of silently showing no columns.

        This matters most where the operator cannot see the server console: a
        hosted deployment, or a colleague running the tool on another machine.
        """
        for resource in (Resource.TABLE_COLUMNS, Resource.WAREHOUSE_SECURITY):
            if resource not in wanted:
                continue
            ctx.unavailable.add(resource)
            ctx.read_failures[resource.value] = {
                "attempted": endpoints,
                "read": 0,
                "failed": endpoints,
                "forbidden": 0,
                "transient": 0,
                "empty": 0,
                "reasons": {reason: endpoints or 1},
            }

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
        with self._lock:
            if self._powerbi_client is None:
                self._powerbi_client = PowerBIClient(self._powerbi_token, timeout=self._timeout)
        return self._powerbi_client

    def _onelake(self):
        """Return a OneLake ADLS Gen2 client, or None without a Storage token."""
        if self._onelake_client is not None:
            return self._onelake_client
        if not self._storage_token:
            return None
        from .onelake import OneLakeClient
        with self._lock:
            if self._onelake_client is None:
                self._onelake_client = OneLakeClient(self._storage_token, timeout=self._timeout)
        return self._onelake_client

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

    def _workspace_reports(self, workspace_id: str) -> tuple[list[dict], bool]:
        """Read the workspace's report → semantic-model bindings.

        Served by the Power BI *Get Reports In Group* API, whose audience differs
        from the Fabric crawl token, so without a Power BI token the bindings are
        *unknown* (``readable=False``) rather than "no report exists" — the
        distinction that lets the reuse checks report N/A instead of inventing a
        finding. Needs the ordinary delegated ``Report.Read.All`` scope, not
        tenant-admin.

        Only the four fields the checks read are kept: report definitions, pages
        and visuals are deliberately never fetched. ``datasetId`` is absent for a
        paginated (RDL) report, which is a real answer — those reports are
        excluded by the checks, never failed.
        """
        pbi = self._powerbi()
        if pbi is None:
            return [], False
        try:
            rows, known = pbi.list_reports_known(group_id=workspace_id)
        except Exception as exc:
            log.warning("fetch %s: report list error: %s", workspace_id, exc)
            return [], False
        reports = [
            {
                "id": row.get("id") or "",
                "name": row.get("name") or "",
                "dataset_id": row.get("datasetId") or "",
                "dataset_workspace_id": row.get("datasetWorkspaceId") or "",
            }
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]
        return reports, known

    def _report_bindings_from_definitions(self, ctx: WorkspaceContext,
                                          workspace_id: str) -> list[dict]:
        """Report → semantic-model bindings from the *Fabric* item definitions.

        The Power BI *Get Reports In Group* API needs a Power BI-audience token,
        which a Fabric-only sign-in does not have - so the reuse checks reported
        N/A on a workspace whose bindings were perfectly readable another way.
        A Report item's ``definition.pbir`` carries ``datasetReference`` as
        either ``byPath`` (``"../Sales.SemanticModel"``, same workspace) or
        ``byConnection`` (``"semanticmodelid=<guid>"``), and ``getDefinition``
        serves it on the Fabric token the crawl already holds.

        Used only as a fallback: the Power BI listing is preferred because it
        also carries the *owning workspace* of the model, which is what tells a
        cross-workspace (central-hub) binding apart from a local one.
        """
        bindings: list[dict] = []
        for item in ctx.items:
            if item.type not in ("Report", "PaginatedReport"):
                continue
            parts, _failure = self._definition_parts(workspace_id, item.id)
            dataset_id = ""
            for part in parts:
                if not str(part.get("path") or "").lower().endswith("definition.pbir"):
                    continue
                try:
                    payload = base64.b64decode(part["payload"]).decode("utf-8")
                    document = json.loads(payload)
                except (KeyError, ValueError, UnicodeDecodeError):
                    continue
                reference = (document.get("datasetReference") or {})
                by_connection = reference.get("byConnection") or {}
                connection = str(by_connection.get("connectionString") or "")
                match = re.search(r"semanticmodelid\s*=\s*([0-9a-fA-F-]{36})", connection)
                if match:
                    dataset_id = match.group(1)
                    break
                by_path = reference.get("byPath") or {}
                path = str(by_path.get("path") or "")
                if path:
                    # A relative path names the model by *name*, not id; resolve it
                    # against the item list so the checks still see a stable key.
                    wanted = path.rsplit("/", 1)[-1].removesuffix(".SemanticModel")
                    for candidate in ctx.items:
                        if (candidate.type == "SemanticModel"
                                and candidate.display_name == wanted):
                            dataset_id = candidate.id
                            break
                    break
            if dataset_id:
                bindings.append({
                    "id": item.id,
                    "name": item.display_name or item.id,
                    "dataset_id": dataset_id,
                    # getDefinition names the model, never its owning workspace.
                    # Left blank rather than guessed: the reuse check treats an
                    # unknown owner as local, which is the conservative reading.
                    "dataset_workspace_id": "",
                })
        return bindings

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
        # access, and its status is how we detect an unreadable workspace. A 429
        # here would otherwise drop the whole workspace, so it is retried with the
        # server's Retry-After before giving up.
        status, workspace = self._get(f"/workspaces/{workspace_id}")
        if status == 401 and self._try_refresh_token():
            status, workspace = self._get(f"/workspaces/{workspace_id}")
        for attempt in range(3):
            if status != 429:
                break
            time.sleep(_retry_after_seconds(None, attempt))
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

        # Report → semantic-model bindings (Power BI Get Reports In Group). One
        # list call, no per-report fetch: only id/name/datasetId are retained.
        # A Fabric-native fallback runs after the item list below, for a sign-in
        # that yielded no Power BI token.
        if Resource.REPORTS in wanted:
            ctx.reports, readable = self._workspace_reports(workspace_id)
            if not readable:
                ctx.unavailable.add(Resource.REPORTS)
            log.info("fetch %s: %d report binding(s) read (readable=%s)",
                     workspace_id, len(ctx.reports), readable)

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
            Resource.LAKEHOUSE_FILES,
            Resource.ACTIVATOR_DEFINITIONS,
        }:
            rows, known = self._values(f"/workspaces/{workspace_id}/items")
            ctx.items = [Item.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ITEMS)
            log.info("fetch %s: %d items by type %s", workspace_id,
                     len(ctx.items), dict(Counter(i.type for i in ctx.items)))

            # Fabric-native fallback for report bindings. definition.pbir carries
            # the semantic-model reference on the Fabric token the crawl already
            # holds, so a sign-in with no Power BI token no longer forces the
            # model-reuse checks to N/A. Runs here because it needs the item list.
            if Resource.REPORTS in wanted and not ctx.reports:
                bindings = self._report_bindings_from_definitions(ctx, workspace_id)
                if bindings:
                    ctx.reports = bindings
                    ctx.unavailable.discard(Resource.REPORTS)
                    log.info("fetch %s: %d report binding(s) recovered from Fabric "
                             "item definitions (no Power BI token needed)",
                             workspace_id, len(bindings))

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
                if ctx.git_connected:
                    security = self._discover_repository_security(ctx.git_details)
                    ctx.git_details["secret_scanning"] = security
                    ctx.git_details["repository_security"] = {"secret_scanning": security}
            elif git_status in (400, 404):
                ctx.git_connected = False  # genuinely not connected
            else:
                # 401/403/500/transport failure: we could not determine it.
                ctx.unavailable.add(Resource.GIT)

        if Resource.ENVIRONMENT_DEFINITIONS in wanted:
            environments = [i for i in ctx.items if i.type == "Environment"]
            fetched = self._fetch_items_parallel(
                environments, lambda it: self._environment_definition(workspace_id, it.id))
            attempted = read = forbidden = transient = 0
            failed_artifacts = []
            for item, (definition, failure) in zip(environments, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                else:
                    read += 1
                    if definition:
                        record = dict(definition)
                        record["id"] = item.id
                        record["display_name"] = item.display_name
                        ctx.environments[item.id] = record
                        ctx.environments[item.display_name] = record
            self._record_failures(ctx, Resource.ENVIRONMENT_DEFINITIONS,
                                  attempted, read, forbidden, transient,
                                  artifacts=failed_artifacts)

        # The workspace's *default* Spark runtime, for notebooks that bind to no
        # named Environment. Deliberately outside the ENVIRONMENT_DEFINITIONS
        # block above: a workspace with no Environment items still has a default
        # runtime, and that is exactly the case this exists to answer. Keyed on
        # the notebook resource because it is the notebook checks that read it.
        # One call per workspace, not per item.
        if Resource.NOTEBOOK_DEFINITIONS in wanted or Resource.ENVIRONMENT_DEFINITIONS in wanted:
            ctx.spark_settings = self._spark_settings(workspace_id)

        # The expensive one — one call per pipeline. Only paid for when a
        # selected check actually reads a pipeline definition.
        if Resource.PIPELINE_DEFINITIONS in wanted:
            pipelines = [i for i in ctx.items if i.type == "DataPipeline"]
            fetched = self._fetch_items_parallel(
                pipelines, lambda it: self._pipeline_definition(workspace_id, it.id))
            attempted = read = forbidden = transient = empty = 0
            failed_artifacts = []
            for item, (definition, failure) in zip(pipelines, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                elif definition:
                    read += 1
                    key = self._unique_key(ctx.pipelines, item.display_name or item.id, item.id)
                    ctx.pipelines[key] = definition
                else:
                    empty += 1
                    failed_artifacts.append(_failure_detail(item, "empty"))
            self._record_failures(ctx, Resource.PIPELINE_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty,
                                  artifacts=failed_artifacts)

        # Notebook definitions: same one-call-per-item getDefinition pattern.
        if Resource.NOTEBOOK_DEFINITIONS in wanted:
            found = [i for i in ctx.items if i.type == "Notebook"]

            def _notebook_bundle(it):
                definition, failure = self._notebook_definition(workspace_id, it.id)
                monitoring = self._notebook_monitoring(workspace_id, it.id) if definition else {}
                return definition, failure, monitoring

            fetched = self._fetch_items_parallel(found, _notebook_bundle)
            attempted = read = forbidden = transient = empty = 0
            failed_artifacts = []
            for item, (definition, failure, monitoring) in zip(found, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                elif definition:
                    read += 1
                    binding = self._environment_binding(definition)
                    environment = ctx.environments.get(binding)
                    if environment:
                        definition["_auditfast_environment"] = {
                            "id": environment.get("id", binding),
                            "name": environment.get("display_name", binding),
                            "runtime_version": environment.get("runtime_version"),
                        }
                    if monitoring:
                        definition["_auditfast_monitoring"] = monitoring
                    ctx.notebooks[item.display_name or item.id] = definition
            self._record_failures(ctx, Resource.NOTEBOOK_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: %d notebooks found, %d definitions read",
                     workspace_id, len(found), len(ctx.notebooks))

        # Lakehouse table listing (name/type/format). Column schemas need the SQL
        # analytics endpoint and are left empty here; column-level checks report
        # N/A rather than failing when they are absent.
        if Resource.TABLE_SCHEMAS in wanted:
            lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
            fetched = self._fetch_items_parallel(
                lakehouses, lambda it: self._lakehouse_tables(workspace_id, it.id))
            attempted = read = forbidden = transient = 0
            failed_artifacts = []
            # ``item`` is intentionally unused: a Lakehouse table is stored under
            # its own name, not the Lakehouse's. Two Lakehouses holding a table
            # of the same name therefore collide here - the second wins - which
            # the SQL column reader repairs by re-filing collisions under
            # ``<store>.<table>``. Keeping the unpacking symmetrical with every
            # other parallel-fetch loop is worth more than renaming this one.
            for _item, (tables, failure) in zip(lakehouses, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(_item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(_item, failure))
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
                                  attempted, read, forbidden, transient,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: %d lakehouses, %d tables read",
                     workspace_id, len(lakehouses), len(ctx.tables))

        # Column schemas + Warehouse RLS, over the SQL analytics endpoint. Not in
        # the Fabric REST API at all - only reachable over TDS (port 1433). Every
        # failure here leaves the data absent, which the checks already report as
        # N/A, so a blocked port degrades to the pre-SQL behaviour rather than
        # failing the crawl.
        if Resource.TABLE_COLUMNS in wanted or Resource.WAREHOUSE_SECURITY in wanted:
            self._read_sql_endpoints(ctx, workspace_id, wanted)

        # OneLake Files listing per Lakehouse. The ADLS Gen2 API can return very
        # large listings, so the OneLake client aggregates during fetch and caps
        # enumeration. The KB stores only bounded counts/buckets, not file paths.
        if Resource.LAKEHOUSE_FILES in wanted:
            lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
            attempted = read = forbidden = transient = 0
            failed_artifacts = []
            onelake = self._onelake()
            if lakehouses and onelake is None:
                ctx.unavailable.add(Resource.LAKEHOUSE_FILES)
                forbidden = len(lakehouses)
                attempted = len(lakehouses)
                log.warning(
                    "fetch %s: %d lakehouse Files listing(s) need a Storage-audience "
                    "token; lakehouse file checks will be N/A",
                    workspace_id, len(lakehouses),
                )
            elif onelake is not None:
                fetched = self._fetch_items_parallel(
                    lakehouses,
                    lambda it: onelake.lakehouse_files_summary(workspace_id, it.id))
                for item, (summary, failure) in zip(lakehouses, fetched, strict=True):
                    attempted += 1
                    if failure == "forbidden":
                        forbidden += 1
                        failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                    elif failure == "transient":
                        transient += 1
                        failed_artifacts.append(_failure_detail(item, failure))
                    else:
                        read += 1
                        key = self._unique_key(
                            ctx.lakehouse_files, item.display_name or item.id, item.id
                        )
                        ctx.lakehouse_files[key] = summary
                        # Delta table data lives under Tables/, not Files/. Summarising
                        # only Files/ measured whatever loose files sat in the landing
                        # area and reported "0 of 3 data files in band" for a Lakehouse
                        # whose actual Delta data was never looked at.
                        tables_summary, tables_failure = onelake.lakehouse_tables_summary(
                            workspace_id, item.id)
                        if not tables_failure:
                            ctx.lakehouse_tables_files[key] = tables_summary
                        self._annotate_partitions(ctx, onelake, workspace_id, item)
            self._record_failures(ctx, Resource.LAKEHOUSE_FILES,
                                  attempted, read, forbidden, transient,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: lakehouse Files summaries read for %d of %d lakehouse(s)",
                     workspace_id, read, len(lakehouses))

        # Warehouse SQL audit *configuration* — plain Fabric REST, one call per
        # Warehouse, gated on the Audit permission of the item (not tenant-admin).
        # No audit rows are ever read: this is a configuration audit, not a log
        # pull, so runtime data never enters the knowledge base.
        if Resource.WAREHOUSE_AUDIT in wanted:
            warehouses = [i for i in ctx.items if i.type == "Warehouse"]
            fetched = self._fetch_items_parallel(
                warehouses, lambda it: self._warehouse_audit(workspace_id, it.id))
            attempted = read = forbidden = transient = empty = 0
            failed_artifacts = []
            for item, (settings, failure) in zip(warehouses, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                elif settings is None:
                    empty += 1
                    failed_artifacts.append(_failure_detail(item, "empty"))
                else:
                    read += 1
                    key = self._unique_key(
                        ctx.warehouse_audit, item.display_name or item.id, item.id
                    )
                    ctx.warehouse_audit[key] = settings
            self._record_failures(ctx, Resource.WAREHOUSE_AUDIT,
                                  attempted, read, forbidden, transient, empty,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: sql audit settings read for %d of %d warehouse(s)",
                     workspace_id, read, attempted)

        # Data Activator (Reflex) rule definitions — one getDefinition per Reflex
        # item, decoded to bounded rule counts. Same Item.ReadWrite scope as any
        # getDefinition; when it is denied the definitions are unreadable and the
        # trigger-depth check reports N/A rather than failing a present Activator.
        if Resource.ACTIVATOR_DEFINITIONS in wanted:
            reflexes = [i for i in ctx.items if i.type in ("Reflex", "Activator")]
            fetched = self._fetch_items_parallel(
                reflexes, lambda it: self._reflex_definition(workspace_id, it.id))
            attempted = read = forbidden = transient = empty = 0
            failed_artifacts = []
            for item, (summary, failure) in zip(reflexes, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                elif summary is not None:
                    read += 1
                    key = self._unique_key(
                        ctx.activators, item.display_name or item.id, item.id
                    )
                    ctx.activators[key] = summary
                else:
                    empty += 1
                    failed_artifacts.append(_failure_detail(item, "empty"))
            self._record_failures(ctx, Resource.ACTIVATOR_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: activator definitions read for %d of %d reflex item(s)",
                     workspace_id, read, attempted)

        # OneLake shortcuts per lakehouse (governance/lineage: external references).
        if Resource.SHORTCUTS in wanted:
            shortcut_lakehouses = [i for i in ctx.items if i.type == "Lakehouse"]
            fetched = self._fetch_items_parallel(
                shortcut_lakehouses,
                lambda it: self._item_shortcuts(workspace_id, it.id))
            total = 0
            attempted = failed = 0
            for item, (shortcuts, known) in zip(shortcut_lakehouses, fetched, strict=True):
                attempted += 1
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
            models = [i for i in ctx.items if i.type == "SemanticModel"]
            fetched = self._fetch_items_parallel(
                models, lambda it: self._semantic_model_definition(workspace_id, it.id))
            attempted = read = forbidden = transient = empty = 0
            failed_artifacts = []
            for item, (model, failure) in zip(models, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    failed_artifacts.append(_failure_detail(item, failure, "HTTP 401/403"))
                elif failure == "transient":
                    transient += 1
                    failed_artifacts.append(_failure_detail(item, failure))
                elif model:
                    read += 1
                    key = self._unique_key(
                        ctx.semantic_models, item.display_name or item.id, item.id
                    )
                    ctx.semantic_models[key] = model
                else:
                    empty += 1
                    failed_artifacts.append(_failure_detail(item, "empty"))
            self._record_failures(ctx, Resource.SEMANTIC_MODEL_DEFINITIONS,
                                  attempted, read, forbidden, transient, empty,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: %d semantic models parsed", workspace_id, len(ctx.semantic_models))

        # Semantic-model refresh *schedule configuration* — one Power BI Datasets
        # API GET per model, delegated scope only (no tenant-admin). Carries
        # ``notifyOption``, which is how "a refresh failure alerts the owning
        # team" is actually configured. No refresh rows are read.
        if Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE in wanted:
            schedule_models = [i for i in ctx.items if i.type == "SemanticModel"]
            fetched = self._fetch_items_parallel(
                schedule_models,
                lambda it: self._semantic_model_refresh_schedule(workspace_id, it.id))
            attempted = read = forbidden = transient = 0
            schedule_reasons: dict[str, int] = {}
            failed_artifacts = []
            for item, (schedule, failure) in zip(schedule_models, fetched, strict=True):
                attempted += 1
                if failure == "forbidden":
                    forbidden += 1
                    reason = _SCHEDULE_FORBIDDEN_REASON
                elif failure == "transient":
                    transient += 1
                    reason = _SCHEDULE_TRANSIENT_REASON
                else:
                    # A model with no schedule read cleanly: absence from the map
                    # is the finding, so it must not count as a failure.
                    read += 1
                    if schedule is not None:
                        key = self._unique_key(
                            ctx.refresh_schedules, item.display_name or item.id, item.id
                        )
                        ctx.refresh_schedules[key] = schedule
                    continue
                failed_artifacts.append(_failure_detail(item, failure, reason))
                schedule_reasons[reason] = schedule_reasons.get(reason, 0) + 1
            self._record_failures(ctx, Resource.SEMANTIC_MODEL_REFRESH_SCHEDULE,
                                  attempted, read, forbidden, transient,
                                  reasons=schedule_reasons,
                                  artifacts=failed_artifacts)
            log.info("fetch %s: refresh schedule read for %d of %d semantic model(s) "
                     "(%d forbidden, %d transient)",
                     workspace_id, read, attempted, forbidden, transient)

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
