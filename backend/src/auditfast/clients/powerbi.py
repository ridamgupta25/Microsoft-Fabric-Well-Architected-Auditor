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
from typing import Any

from .errors import ProviderError

log = logging.getLogger("auditfast.powerbi")


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
        try:
            response = self._session.get(f"{self.BASE}{path}", timeout=self._timeout)
        except Exception as exc:  # network/DNS/TLS — treat as unknown, never as empty
            log.warning("GET %s transport error: %s", path, exc)
            return None, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

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
        status, body = self._get(path)
        if status != 200 or not isinstance(body, dict):
            return []
        return list(body.get("value") or [])

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
