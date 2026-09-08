"""Read-only Power BI REST client — the transport behind the FabricIQ tools.

This is the data-plane counterpart to :class:`~auditfast.clients.live.LiveFabricProvider`.
Where that provider reads Fabric *control-plane* metadata, this client reads
Power BI *artifacts* (workspaces, semantic models, reports) and runs read-only
DAX through the ``executeQueries`` endpoint.

Two things are easy to get wrong and are handled deliberately here:

1. **Token audience.** These endpoints require a bearer token for
   ``https://analysis.windows.net/powerbi/api`` — *not* the Fabric API audience
   the auditor's own token carries. The caller supplies it; see
   :mod:`auditfast.services.fabriciq_service`.
2. **Read-only despite a POST.** ``executeQueries`` is an HTTP POST, but it only
   ever runs DAX ``EVALUATE`` statements; the Power BI service rejects anything
   that would mutate a model. Nothing in this client writes.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit

from .errors import ProviderError

log = logging.getLogger("auditfast.powerbi")


def _retry_after_seconds(retry_after: str | None, attempt: int, cap: float = 30.0) -> float:
    """Seconds to wait before a retry, honoring the server's ``Retry-After``.

    Falls back to exponential backoff when the header is absent, and never waits
    longer than ``cap`` so a hostile value cannot stall the crawl.
    """
    try:
        if retry_after is not None:
            return min(float(retry_after), cap)
    except (TypeError, ValueError):
        pass
    return min(2.0 ** attempt, cap)


class PowerBIError(ProviderError):
    """A Power BI REST call failed in a way the caller must surface, not swallow.

    Carries the HTTP ``status`` and any Power BI error ``code`` so a tool can
    turn it into a structured, actionable message instead of a bare stack trace.
    """

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        self.status = status
        self.code = code
        super().__init__(message)


class PowerBIClient:
    """Reads Power BI artifacts and runs read-only DAX with a delegated token."""

    BASE = "https://api.powerbi.com/v1.0/myorg"

    def __init__(self, token: str, timeout: int = 60):
        import requests  # imported lazily so offline paths need no HTTP stack

        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )
        self._timeout = timeout
        self._groups_cache: list[dict] | None = None

    # -- transport -------------------------------------------------------------
    def _get(self, path: str) -> tuple[int | None, Any]:
        """GET a path, returning ``(status, body)``. ``None`` status = transport failure."""
        status, body, _retry_after = self._get_with_meta(path)
        return status, body

    def _get_with_meta(self, path: str) -> tuple[int | None, Any, str | None]:
        """GET a path, returning ``(status, body, retry_after)`` for throttle-aware retries."""
        try:
            response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
        except Exception as exc:  # network/DNS/TLS — treat as unknown, never as empty
            log.warning("GET %s transport error: %s", path, exc)
            return None, None, None
        retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
        try:
            return response.status_code, response.json(), retry_after
        except ValueError:
            return response.status_code, None, retry_after

    def _post(self, path: str, payload: dict) -> tuple[int | None, Any]:
        """POST JSON to a path, returning ``(status, body)``."""
        try:
            response = self._session.post(
                f"{self.BASE}{path}", json=payload, timeout=self._timeout
            )
        except Exception as exc:
            log.warning("POST %s transport error: %s", path, exc)
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _values(self, path: str) -> list[dict]:
        """GET a Power BI collection endpoint and return its ``value`` array."""
        return self._values_known(path)[0]

    def _values_known(self, path: str) -> tuple[list[dict], bool]:
        """GET a collection endpoint, returning ``(rows, readable)``.

        ``readable`` is what tells an *empty workspace* apart from a *forbidden
        or failed* listing. :meth:`_values` throws that distinction away, which is
        fine for the FabricIQ tools but not for an auditor: a check must report
        N/A when the listing could not be read and a real finding when the
        workspace genuinely holds none.
        """
        status, body = self._get(path)
        if status != 200 or not isinstance(body, dict):
            return [], False
        return list(body.get("value") or []), True

    # -- workspaces / items ----------------------------------------------------
    def list_groups(self) -> list[dict]:
        """List every workspace (group) the token can see, cached per client."""
        if self._groups_cache is None:
            self._groups_cache = self._values("/groups?$top=5000")
        return self._groups_cache

    def list_datasets(self, group_id: str) -> list[dict]:
        """List the semantic models (datasets) in one workspace."""
        return self._values(f"/groups/{group_id}/datasets")

    def list_reports(self, group_id: str) -> list[dict]:
        """List the reports in one workspace."""
        return self._values(f"/groups/{group_id}/reports")

    def list_reports_known(self, group_id: str) -> tuple[list[dict], bool]:
        """List the reports in one workspace, plus whether the listing was readable.

        Same call as :meth:`list_reports`; the second element distinguishes "this
        workspace has no report" from "the report list could not be read", which
        is what lets the report-reuse checks report N/A instead of a false
        finding. Each row carries ``datasetId`` — the report's semantic-model
        binding — except for paginated (RDL) reports, which bind to none.

        The personal **"My workspace"** is not a group, so ``/groups/{id}/reports``
        fails for it; when — and only when — ``group_id`` is not a group the token
        can see, its reports are read from the ``myorg`` root ``/reports`` instead.
        A *named* workspace whose group listing fails (permission/transient) is
        left unreadable, never silently swapped for the personal one.
        """
        rows, readable = self._values_known(f"/groups/{group_id}/reports")
        if readable:
            return rows, readable
        if group_id not in {g.get("id") for g in self.list_groups()}:
            return self._values_known("/reports")
        return rows, readable

    def get_report(self, report_id: str, group_id: str | None = None) -> dict | None:
        """Fetch one report's properties, or ``None`` if it is not accessible."""
        path = (
            f"/groups/{group_id}/reports/{report_id}"
            if group_id
            else f"/reports/{report_id}"
        )
        status, body = self._get(path)
        return body if status == 200 and isinstance(body, dict) else None

    def get_report_pages(self, report_id: str, group_id: str | None = None) -> list[dict]:
        """List a report's pages (name/displayName/order); [] if unavailable."""
        path = (
            f"/groups/{group_id}/reports/{report_id}/pages"
            if group_id
            else f"/reports/{report_id}/pages"
        )
        return self._values(path)

    def get_dataset(self, dataset_id: str, group_id: str | None = None) -> dict | None:
        """Fetch one semantic model's properties, or ``None`` if inaccessible."""
        path = (
            f"/groups/{group_id}/datasets/{dataset_id}"
            if group_id
            else f"/datasets/{dataset_id}"
        )
        status, body = self._get(path)
        return body if status == 200 and isinstance(body, dict) else None

    def dataset_last_refresh(
        self, dataset_id: str, group_id: str | None = None
    ) -> tuple[str | None, str]:
        """Return a semantic model's last refresh time and a failure classifier.

        Reads the Power BI refresh history (returned latest-first) and yields
        ``(timestamp, failure)`` where ``timestamp`` is the top entry's
        ``endTime`` (or ``startTime`` while a refresh is running). ``failure``
        follows the crawl convention: ``""`` read (``timestamp`` may still be
        ``None`` for a model that has genuinely never been refreshed);
        ``"forbidden"`` a 401/403 (wrong-audience or unlicensed token);
        ``"transient"`` a 404-on-every-form / throttling / 5xx / transport error.

        The workspace-scoped form is tried first; a personal ("My workspace")
        model 404s there, so the no-group form is used as a fallback. Keeping
        "could not read" distinct from "never refreshed" is what lets the caller
        report N/A instead of silently dropping the model.
        """
        paths = []
        if group_id:
            paths.append(f"/groups/{group_id}/datasets/{dataset_id}/refreshes?$top=1")
        paths.append(f"/datasets/{dataset_id}/refreshes?$top=1")
        failure = "transient"
        for path in paths:
            status, body = self._get(path)
            if status == 200 and isinstance(body, dict):
                rows = body.get("value") or []
                if rows and isinstance(rows[0], dict):
                    return rows[0].get("endTime") or rows[0].get("startTime"), ""
                return None, ""  # 200-empty: never refreshed — a real, readable answer
            if status in (401, 403):
                failure = "forbidden"  # token rejected; the other form will fare no better
        return None, failure

    def dataset_created_dates(self, group_id: str | None = None) -> dict[str, str]:
        """Map ``dataset id -> createdDate`` for a workspace's semantic models.

        ``GET /groups/{id}/datasets`` returns every model's ISO-8601
        ``createdDate`` in a single call — no admin or capacity needed, and it is
        present even for models that have never been refreshed. A personal
        ("My workspace") id 401s on the group form, so the no-group ``/datasets``
        form is used as a fallback. Returns ``{}`` when nothing could be read.
        """
        rows = self._values(f"/groups/{group_id}/datasets") if group_id else []
        if not rows:
            rows = self._values("/datasets")
        return {
            row["id"]: row["createdDate"]
            for row in rows
            if isinstance(row, dict) and row.get("id") and row.get("createdDate")
        }

    def dataset_refresh_schedule(
        self, dataset_id: str, group_id: str | None = None
    ) -> tuple[dict | None, str]:
        """Return a semantic model's *refresh schedule configuration* and a failure classifier.

        ``GET /groups/{gid}/datasets/{did}/refreshSchedule`` is an ordinary
        delegated read (``Dataset.Read.All``) — **no tenant-admin scope**, and the
        same shape and audience as :meth:`dataset_last_refresh`, which this client
        already calls. It returns the configured days/times, whether the schedule
        is enabled, and ``notifyOption``: ``MailOnFailure`` (the model's
        contacts/owner are mailed when a refresh fails) or ``NoNotification``.

        Returns ``(schedule, failure)`` following the crawl convention: ``""``
        read; ``"forbidden"`` a 401/403 (wrong-audience or unlicensed token);
        ``"transient"`` throttling / 5xx / transport error. A **404 is not a
        failure** — it is how the API says "this model has no refresh schedule"
        (a Direct Lake or push model), so it yields ``(None, "")`` and the caller
        records it as *no schedule* rather than as unreadable.

        A **transport failure is transient, not forbidden**, and the *last*
        attempt decides. A DNS blip or a reset connection mid-crawl looks nothing
        like a permission denial, but recording it as ``forbidden`` tells the
        knowledge base "this will never work with this token" and stops the next
        crawl from retrying. On a real crawl 7 DNS failures and 1 connection
        reset were filed as 410 forbidden reads for exactly this reason.

        Only the schedule *configuration* is read. No refresh rows, no history.
        """
        paths = []
        if group_id:
            paths.append(f"/groups/{group_id}/datasets/{dataset_id}/refreshSchedule")
        paths.append(f"/datasets/{dataset_id}/refreshSchedule")
        failure = "transient"
        reason = "no attempt completed"
        for path in paths:
            for attempt in range(3):
                status, body, retry_after = self._get_with_meta(path)
                if status == 200 and isinstance(body, dict):
                    return _refresh_schedule(body), ""
                if status in (400, 404):
                    return None, ""  # no schedule configured for this model - a real answer
                if status is None:
                    # Transport failure: DNS, reset, timeout. Retryable, and it says
                    # nothing about whether the token would have been accepted.
                    failure, reason = "transient", "transport error (DNS/reset/timeout)"
                elif status in (401, 403):
                    failure, reason = "forbidden", f"HTTP {status}"
                    break
                elif status == 429 or (status is not None and status >= 500):
                    failure, reason = "transient", f"HTTP {status}"
                else:
                    failure, reason = "transient", f"HTTP {status}"
                    break
                if attempt < 2:
                    time.sleep(_retry_after_seconds(retry_after, attempt))
        log.info("dataset %s refresh schedule unread (%s): %s",
                 dataset_id, failure, reason)
        return None, failure

    def execute_queries(
        self, dataset_id: str, dax_queries: list[str], group_id: str | None = None
    ) -> dict:
        """Run 1..N read-only DAX ``EVALUATE`` queries against a semantic model.

        Returns the raw Power BI ``executeQueries`` body
        (``{"results": [{"tables": [{"rows": [...]}]}]}``). Raises
        :class:`PowerBIError` on any non-200, with the service's own error code
        and message attached.
        """
        payload = {
            "queries": [{"query": q} for q in dax_queries],
            "serializerSettings": {"includeNulls": True},
        }
        path = (
            f"/groups/{group_id}/datasets/{dataset_id}/executeQueries"
            if group_id
            else f"/datasets/{dataset_id}/executeQueries"
        )
        status, body = self._post(path, payload)
        if status == 200 and isinstance(body, dict):
            return body
        raise PowerBIError(*_error_detail(status, body))

    # -- elevated audit reads -------------------------------------------------
    def tenant_settings(self) -> tuple[list[dict], bool]:
        """Return tenant settings, preserving unreadable versus genuinely empty."""
        status, body = self._get("/admin/tenantsettings")
        if status != 200 or not isinstance(body, dict):
            return [], False
        rows = body.get("tenantSettings", body.get("value", [])) or []
        return [row for row in rows if isinstance(row, dict)], True

    def activity_events(self, days_back: int = 14) -> tuple[list[dict], bool]:
        """Read a bounded activity-log window one UTC day at a time."""
        rows: list[dict] = []
        today = datetime.now(timezone.utc).date()
        for offset in range(max(1, min(int(days_back), 28))):
            day = today - timedelta(days=offset + 1)
            start = f"{day.isoformat()}T00:00:00.000Z"
            end = f"{day.isoformat()}T23:59:59.999Z"
            path = ("/admin/activityevents?startDateTime=" + quote(f"'{start}'")
                    + "&endDateTime=" + quote(f"'{end}'"))
            seen: set[str] = set()
            while path and path not in seen:
                seen.add(path)
                status, body = self._get(path)
                if status != 200 or not isinstance(body, dict):
                    return rows, False
                rows.extend(row for row in body.get("activityEventEntities", [])
                            if isinstance(row, dict))
                continuation = body.get("continuationUri")
                if not continuation:
                    break
                parsed = urlsplit(str(continuation))
                base_path = urlsplit(self.BASE).path
                relative_path = parsed.path.removeprefix(base_path)
                path = relative_path + (f"?{parsed.query}" if parsed.query else "")
        return rows, True

    def admin_scan(self, workspace_id: str) -> tuple[dict, bool]:
        """Run the existing read-only Scanner API client for one workspace."""
        from ..discovery.scanner import ScannerApiClient

        token = self._session.headers.get("Authorization", "").removeprefix("Bearer ")
        payload = ScannerApiClient(token, timeout=self._timeout).scan([workspace_id])
        return payload, bool(payload)

    def capacity_metrics(self) -> tuple[dict, bool]:
        """Find and query the Capacity Metrics model, adapting to its schema."""
        match = None
        group_id = None
        for group in self.list_groups():
            candidate_group = str(group.get("id") or "")
            for dataset in self.list_datasets(candidate_group):
                if "capacity metrics" in str(dataset.get("name") or "").lower():
                    match, group_id = dataset, candidate_group
                    break
            if match:
                break
        if not match:
            return {"model_found": False}, True

        dataset_id = str(match.get("id") or "")
        try:
            schema_body = self.execute_queries(dataset_id, ["EVALUATE INFO.VIEW.COLUMNS()"], group_id)
        except PowerBIError:
            return {}, False
        columns: dict[str, list[str]] = {}
        for row in _query_rows(schema_body):
            table = str(_row_value(row, "table") or "")
            column = str(_row_value(row, "name", "column") or "")
            if table and column:
                columns.setdefault(table, []).append(column)

        fact = _capacity_fact(columns)
        overload = _overload_fact(columns)
        result: dict[str, Any] = {
            "model_found": True,
            "model_id": dataset_id,
            "model_workspace_id": group_id,
            "peak_split": None,
            "top_consumers": [],
            "throttling_readable": overload is not None,
            "throttling_events": 0,
            "query_load": [],
        }
        if fact:
            table, cu, when, operation, item, workspace = fact
            if when:
                dax = f"EVALUATE SUMMARIZECOLUMNS({_dax_col(table, when)}, \"TotalCU\", SUM({_dax_col(table, cu)}))"
                try:
                    timed = _query_rows(self.execute_queries(dataset_id, [dax], group_id))
                except PowerBIError:
                    timed = []
                peak = off_peak = 0.0
                for row in timed:
                    hour = _hour(_row_value(row, when, "When"))
                    value = float(_row_value(row, "TotalCU") or 0)
                    if hour is not None and 8 <= hour < 18:
                        peak += value
                    else:
                        off_peak += value
                if timed:
                    result["peak_split"] = {"peak": peak, "off_peak": off_peak}
            keys = [name for name in (workspace, item, operation) if name]
            if keys:
                grouping = ", ".join(_dax_col(table, name) for name in dict.fromkeys(keys))
                dax = f"EVALUATE TOPN(20, SUMMARIZECOLUMNS({grouping}, \"TotalCU\", SUM({_dax_col(table, cu)})), [TotalCU], DESC)"
                try:
                    result["top_consumers"] = _query_rows(
                        self.execute_queries(dataset_id, [dax], group_id)
                    )
                except PowerBIError:
                    pass
            if operation:
                dax = f"EVALUATE SUMMARIZECOLUMNS({_dax_col(table, operation)}, \"TotalCU\", SUM({_dax_col(table, cu)}))"
                try:
                    operation_rows = _query_rows(self.execute_queries(dataset_id, [dax], group_id))
                except PowerBIError:
                    operation_rows = []
                words = ("sql", "warehouse", "dataset", "semantic", "model", "query", "refresh")
                result["query_load"] = [
                    row for row in operation_rows
                    if any(word in str(_row_value(row, operation)).lower() for word in words)
                ]
        if overload:
            table, column, when = overload
            key = _dax_col(table, when or column)
            dax = f"EVALUATE FILTER(SUMMARIZECOLUMNS({key}, \"Overload\", SUM({_dax_col(table, column)})), [Overload] > 0)"
            try:
                result["throttling_events"] = len(_query_rows(
                    self.execute_queries(dataset_id, [dax], group_id)
                ))
            except PowerBIError:
                result["throttling_readable"] = False
        return result, True

    # -- resolution helpers ----------------------------------------------------
    def find_report_group(self, report_id: str) -> tuple[str | None, dict | None]:
        """Locate which workspace a report lives in by scanning accessible groups."""
        target = report_id.lower()
        for group in self.list_groups():
            gid = group.get("id")
            for report in self.list_reports(gid):
                if str(report.get("id")).lower() == target:
                    return gid, report
        return None, None

    def find_dataset_group(self, dataset_id: str) -> tuple[str | None, dict | None]:
        """Locate which workspace a semantic model lives in by scanning groups."""
        target = dataset_id.lower()
        for group in self.list_groups():
            gid = group.get("id")
            for dataset in self.list_datasets(gid):
                if str(dataset.get("id")).lower() == target:
                    return gid, dataset
        return None, None


def _refresh_schedule(body: dict) -> dict:
    """Normalise a ``refreshSchedule`` payload into the shape checks read.

    The Power BI API returns the schedule either bare or wrapped in
    ``properties``/``value``, and spells the notification setting
    ``notifyOption``. Normalising here keeps every variation out of the check
    bodies, which must stay pure. ``notify_option`` is preserved verbatim so a
    future Fabric spelling is reported rather than silently mapped to "off".
    """
    raw = body.get("properties") if isinstance(body.get("properties"), dict) else body
    days = [str(d) for d in (raw.get("days") or []) if str(d).strip()]
    times = [str(t) for t in (raw.get("times") or []) if str(t).strip()]
    notify = str(raw.get("notifyOption") or raw.get("notifyOptions") or "").strip()
    return {
        "enabled": bool(raw.get("enabled", False)),
        "notify_option": notify,
        # A refresh failure reaches a human. Anything other than an explicit
        # "no notification" counts, so a new Fabric spelling is credited rather
        # than read as silence; a blank value means the API did not say.
        "notifies_on_failure": bool(notify) and notify.lower() != "nonotification",
        "days": days,
        "times": times,
        "local_time_zone_id": str(raw.get("localTimeZoneId") or ""),
    }


def _query_rows(body: dict) -> list[dict]:
    results = body.get("results") or []
    tables = results[0].get("tables", []) if results else []
    return [row for row in (tables[0].get("rows", []) if tables else []) if isinstance(row, dict)]


def _row_value(row: dict, *hints: str):
    for hint in hints:
        target = hint.lower()
        for key, value in row.items():
            leaf = str(key).rsplit("[", 1)[-1].rstrip("]").lower()
            if target == leaf or target in leaf:
                return value
    return None


def _pick(columns: list[str], *words: str) -> str | None:
    return next((column for word in words for column in columns
                 if word in column.lower()), None)


def _capacity_fact(schema: dict[str, list[str]]):
    for table, columns in schema.items():
        cu = _pick(columns, "cu (s)", "cu(s)", "cu ", "capacity unit")
        if cu:
            return (table, cu, _pick(columns, "hour", "date", "day", "time"),
                    _pick(columns, "operation"), _pick(columns, "item"),
                    _pick(columns, "workspace"))
    return None


def _overload_fact(schema: dict[str, list[str]]):
    for table, columns in schema.items():
        signal = _pick(columns, "throttl", "overload", "burndown", "rejection", "delay")
        if signal:
            return table, signal, _pick(columns, "hour", "date", "day", "time")
    return None


def _dax_col(table: str, column: str) -> str:
    return "'" + table.replace("'", "''") + "'[" + column.replace("]", "]]" ) + "]"


def _hour(value) -> int | None:
    if isinstance(value, (int, float)) and 0 <= int(value) <= 23:
        return int(value)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).hour
    except ValueError:
        return None


def _error_detail(status: int | None, body: Any) -> tuple[str, int | None, str | None]:
    """Turn a failed Power BI response into ``(message, status, code)``."""
    code: str | None = None
    message = f"Power BI request failed (HTTP {status})."
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message") or message
            details = error.get("pbi.error") or {}
            if isinstance(details, dict) and details.get("details"):
                extra = "; ".join(
                    str(d.get("detail", {}).get("value", ""))
                    for d in details["details"]
                    if isinstance(d, dict)
                ).strip("; ")
                if extra:
                    message = f"{message} — {extra}"
    if status == 401:
        message = (
            "Power BI rejected the token (HTTP 401). It must be issued for the "
            "audience https://analysis.windows.net/powerbi/api, not the Fabric API. "
            + message
        )
    return message, status, code
