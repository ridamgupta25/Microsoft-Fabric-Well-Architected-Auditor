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
from typing import Any

import yaml

from ..core.enums import Layer, Resource
from ..core.models import Item, RoleAssignment, WorkspaceContext
from .base import ALL_RESOURCES
from .errors import WorkspaceAccessError
from .tmsl import parse_tmsl

log = logging.getLogger("auditfast.live")


class LiveFabricProvider:
    """Reads a live Fabric tenant with a delegated, read-only OAuth2 token."""

    BASE = "https://api.fabric.microsoft.com/v1"

    def __init__(self, token: str, timeout: int = 60, token_refresher=None):
        import requests  # imported lazily so offline mode needs no HTTP stack

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})
        self._timeout = timeout
        self._token_refresher = token_refresher

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
            self._session.headers.update({"Authorization": f"Bearer {new_token}"})
            log.info("token refreshed, resuming crawl")
            return True
        log.warning("token refresh failed, could not acquire new token")
        return False

    #: getDefinition statuses worth retrying — transient throttling / 5xx.
    _RETRYABLE = frozenset({429, 500, 502, 503, 504})

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
        }:
            rows, known = self._values(f"/workspaces/{workspace_id}/items")
            ctx.items = [Item.from_api(row) for row in rows]
            if not known:
                ctx.unavailable.add(Resource.ITEMS)
            log.info("fetch %s: %d items by type %s", workspace_id,
                     len(ctx.items), dict(Counter(i.type for i in ctx.items)))

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
