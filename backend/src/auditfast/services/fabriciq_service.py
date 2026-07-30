"""FabricIQ tools, reimplemented natively and read-only.

Microsoft's hosted **FabricIQ** MCP server exposes six Power BI tools —
``DiscoverArtifacts``, ``ResolveReportIdFromUrl``, ``GetReportMetadata``,
``GetSemanticModelSchema``, ``ValueSearch`` and ``ExecuteQuery``. This module
re-creates their *deterministic, read-only data-plane* behaviour directly on top
of the Power BI REST API + DAX ``executeQueries`` endpoint, so the auditor owns
the implementation and never has to leave its read-only guarantee.

What is intentionally **not** reproduced: the hosted server's AI layer
(natural-language→DAX generation, verified answers, custom instructions). Those
are model-authored, non-deterministic features; this module returns the raw
schema and lets the caller do the reasoning. See
``fabric-skills/skills/fabriciq`` for the orchestration guidance that layers on
top of these primitives.

Token audience: every function except :func:`resolve_report_id_from_url` (which
is pure string parsing) needs a bearer token for
``https://analysis.windows.net/powerbi/api``. That is a *different* audience
from the Fabric token the audit path uses — passing the wrong one yields 401.

Each function accepts an optional ``client`` so tests can inject a fake Power BI
client and run entirely offline.
"""
from __future__ import annotations

import re
from typing import Any

from ..clients.powerbi import PowerBIClient, PowerBIError

# A canonical GUID, reused for URL parsing and validation.
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: How callers may spell the two supported artifact kinds.
_ARTIFACT_ALIASES = {
    "report": "Report",
    "reports": "Report",
    "semanticmodel": "SemanticModel",
    "semantic model": "SemanticModel",
    "semantic_model": "SemanticModel",
    "dataset": "SemanticModel",
    "datasets": "SemanticModel",
    "model": "SemanticModel",
}

MAX_RESULTS_CAP = 50
DEFAULT_MAX_ROWS = 250
MAX_ROWS_CAP = 1000
MAX_QUERIES = 4
DEFAULT_SCAN_WORKSPACES = 200


# -- 1. DiscoverArtifacts ------------------------------------------------------

def discover_artifacts(
    token: str,
    search_query: str,
    artifact_types: list[str] | None = None,
    max_results: int = MAX_RESULTS_CAP,
    *,
    client: PowerBIClient | None = None,
    max_workspaces: int = DEFAULT_SCAN_WORKSPACES,
) -> dict[str, Any]:
    """Search accessible workspaces for reports and semantic models by name.

    Mirrors ``DiscoverArtifacts(searchQuery, artifactTypes?, maxResults?)``.
    Reports are preferred over standalone semantic models, matching the FabricIQ
    guidance. Capped at 50 results and, for safety on large tenants, at
    ``max_workspaces`` scanned workspaces.
    """
    pbi = client or PowerBIClient(token)
    wanted = _normalize_types(artifact_types)
    limit = max(1, min(int(max_results or MAX_RESULTS_CAP), MAX_RESULTS_CAP))
    term = (search_query or "").strip().lower()

    matches: list[dict] = []
    groups = pbi.list_groups()
    scanned = 0
    hit_cap = False
    for group in groups:
        if scanned >= max_workspaces:
            hit_cap = True
            break
        scanned += 1
        gid, gname = group.get("id"), group.get("name")

        if "SemanticModel" in wanted:
            for dataset in pbi.list_datasets(gid):
                name = dataset.get("name") or ""
                if term and term not in name.lower():
                    continue
                matches.append({
                    "artifactType": "SemanticModel",
                    "id": dataset.get("id"),
                    "name": name,
                    "workspaceId": gid,
                    "workspaceName": gname,
                    "webUrl": dataset.get("webUrl"),
                    "configuredBy": dataset.get("configuredBy"),
                })
        if "Report" in wanted:
            for report in pbi.list_reports(gid):
                name = report.get("name") or ""
                if term and term not in name.lower():
                    continue
                matches.append({
                    "artifactType": "Report",
                    "id": report.get("id"),
                    "name": name,
                    "workspaceId": gid,
                    "workspaceName": gname,
                    "datasetId": report.get("datasetId"),
                    "webUrl": report.get("webUrl"),
                    "reportType": report.get("reportType"),
                })

    # Reports first (FabricIQ "prefer reports"), then alphabetical by name.
    matches.sort(key=lambda a: (0 if a["artifactType"] == "Report" else 1,
                                (a.get("name") or "").lower()))
    out = matches[:limit]
    return {
        "searchQuery": search_query,
        "count": len(out),
        "artifacts": out,
        "truncated": hit_cap or len(matches) > limit,
        "workspacesScanned": scanned,
    }


# -- 2. ResolveReportIdFromUrl -------------------------------------------------

def resolve_report_id_from_url(url: str) -> dict[str, Any]:
    """Parse a Power BI / Fabric report URL into its workspace and report GUIDs.

    Mirrors ``ResolveReportIdFromUrl(url)``. Pure string work — no network call.
    Recognises workspace URLs (``/groups/{id}/reports/{id}``), My-workspace URLs
    (``/reports/{id}``) and workspace-app URLs
    (``/groups/{id}/apps/{appId}/reports/{id}``), flagging the last because the
    path report id there is a per-app *instance* id, not the published GUID.
    """
    if not url or not isinstance(url, str):
        return {"error": "No URL was provided."}
    text = url.strip()

    app_id: str | None = None
    is_app_instance = False
    report_id: str | None = None

    app_match = re.search(rf"/apps/({_GUID})/reports/({_GUID})", text)
    if app_match:
        app_id, report_id, is_app_instance = app_match.group(1), app_match.group(2), True
    else:
        report_match = re.search(rf"/reports/({_GUID})", text)
        if report_match:
            report_id = report_match.group(1)

    workspace_id: str | None = None
    group_match = re.search(rf"/groups/({_GUID})", text)
    if group_match:
        workspace_id = group_match.group(1)

    if not report_id:
        return {"error": "Could not find a report GUID in the URL.", "url": url}

    result: dict[str, Any] = {
        "reportId": report_id,
        "workspaceId": workspace_id,
        "isAppInstance": is_app_instance,
    }
    if app_id:
        result["appId"] = app_id
    if is_app_instance:
        result["note"] = (
            "This is a workspace-app URL: the report id in the path is a per-app "
            "instance id, not the underlying published-report GUID. Call "
            "get_report_metadata to resolve the semantic model behind it."
        )
    return result


# -- 3. GetReportMetadata ------------------------------------------------------

def get_report_metadata(
    token: str,
    report_id: str,
    workspace_id: str | None = None,
    *,
    client: PowerBIClient | None = None,
) -> dict[str, Any]:
    """Return a report's properties, pages and underlying semantic model id.

    Mirrors ``GetReportMetadata(reportObjectId)``. The ``semanticModel`` field is
    the GUID to pass on to :func:`get_semantic_model_schema`,
    :func:`execute_query` and :func:`value_search`.

    The public REST API does not expose per-visual bindings or filters (those
    live in the PBIR definition), so this returns report-level properties plus
    the page inventory — enough to drive downstream schema and query calls.
    """
    pbi = client or PowerBIClient(token)
    resolved_group = workspace_id
    report = pbi.get_report(report_id, workspace_id)
    if report is None and workspace_id is None:
        resolved_group, report = pbi.find_report_group(report_id)
    if report is None:
        return {
            "error": f"Report {report_id!r} was not found or is not accessible.",
            "reportId": report_id,
        }

    pages = pbi.get_report_pages(report.get("id", report_id), resolved_group)
    return {
        "reportId": report.get("id"),
        "name": report.get("name"),
        "workspaceId": resolved_group,
        "semanticModel": report.get("datasetId"),
        "datasetId": report.get("datasetId"),
        "webUrl": report.get("webUrl"),
        "embedUrl": report.get("embedUrl"),
        "reportType": report.get("reportType"),
        "pageCount": len(pages),
        "pages": [
            {
                "name": p.get("name"),
                "displayName": p.get("displayName"),
                "order": p.get("order"),
            }
            for p in pages
        ],
        "note": (
            "Visual- and filter-level bindings require the PBIR report definition "
            "and are not exposed by the read-only REST API. Report properties, "
            "pages, and the underlying semantic model id are provided so schema "
            "and query tools can continue."
        ),
    }


# -- 4. GetSemanticModelSchema -------------------------------------------------

_SCHEMA_QUERIES = {
    "tables": "EVALUATE INFO.VIEW.TABLES()",
    "columns": "EVALUATE INFO.VIEW.COLUMNS()",
    "measures": "EVALUATE INFO.VIEW.MEASURES()",
    "relationships": "EVALUATE INFO.VIEW.RELATIONSHIPS()",
}


def get_semantic_model_schema(
    token: str,
    artifact_id: str,
    workspace_id: str | None = None,
    *,
    client: PowerBIClient | None = None,
) -> dict[str, Any]:
    """Return a semantic model's tables, columns, measures and relationships.

    Mirrors ``GetSemanticModelSchema(artifactId)``. Built from the read-only
    ``INFO.VIEW.*`` DAX functions run through ``executeQueries`` — exactly the
    discovery path the ``semantic-model-consumption`` skill recommends.
    """
    pbi = client or PowerBIClient(token)
    group = workspace_id
    if group is None:
        group, _ = pbi.find_dataset_group(artifact_id)

    errors: dict[str, str] = {}

    def rows(kind: str) -> list[dict]:
        try:
            body = pbi.execute_queries(artifact_id, [_SCHEMA_QUERIES[kind]], group)
        except PowerBIError as exc:
            errors[kind] = str(exc)
            return []
        return [_clean_row(r) for r in _extract_rows(body)]

    tables: dict[str, dict] = {}

    def table_for(name: str) -> dict:
        return tables.setdefault(
            name, {"name": name, "columns": [], "measures": []}
        )

    for row in rows("tables"):
        name = row.get("Name") or row.get("Table")
        if name:
            table_for(name).update({
                "description": row.get("Description"),
                "isHidden": _as_bool(row.get("IsHidden")),
                "dataCategory": row.get("DataCategory"),
            })

    column_count = 0
    for row in rows("columns"):
        tname = row.get("Table")
        if not tname:
            continue
        table_for(tname)["columns"].append({
            "name": row.get("Name"),
            "dataType": row.get("DataType") or row.get("Type"),
            "dataCategory": row.get("DataCategory"),
            "isHidden": _as_bool(row.get("IsHidden")),
            "isKey": _as_bool(row.get("IsKey")),
            "formatString": row.get("FormatString"),
            "description": row.get("Description"),
        })
        column_count += 1

    measure_count = 0
    for row in rows("measures"):
        tname = row.get("Table")
        if not tname:
            continue
        table_for(tname)["measures"].append({
            "name": row.get("Name"),
            "expression": row.get("Expression"),
            "dataType": row.get("DataType") or row.get("Type"),
            "formatString": row.get("FormatString"),
            "displayFolder": row.get("DisplayFolder"),
            "isHidden": _as_bool(row.get("IsHidden")),
            "description": row.get("Description"),
        })
        measure_count += 1

    relationships = []
    for row in rows("relationships"):
        relationships.append({
            "fromTable": row.get("FromTable") or row.get("FromTableName"),
            "fromColumn": row.get("FromColumn") or row.get("FromColumnName"),
            "toTable": row.get("ToTable") or row.get("ToTableName"),
            "toColumn": row.get("ToColumn") or row.get("ToColumnName"),
            "isActive": _as_bool(row.get("IsActive")),
            "crossFilteringBehavior": row.get("CrossFilteringBehavior"),
            "cardinality": row.get("Cardinality"),
        })

    result: dict[str, Any] = {
        "artifactId": artifact_id,
        "workspaceId": group,
        "tableCount": len(tables),
        "columnCount": column_count,
        "measureCount": measure_count,
        "tables": list(tables.values()),
        "relationships": relationships,
    }
    if errors:
        result["errors"] = errors
    return result


# -- 5. ValueSearch ------------------------------------------------------------

def value_search(
    token: str,
    artifact_id: str,
    search_terms: str | list[str],
    workspace_id: str | None = None,
    max_columns: int = 25,
    max_rows: int = 20,
    *,
    client: PowerBIClient | None = None,
) -> dict[str, Any]:
    """Find which column(s) contain a value, and its exact spelling.

    Mirrors ``ValueSearch(artifactId, searchTerms, scope?)``. Scans the model's
    visible text columns (bounded by ``max_columns``) with case-insensitive DAX
    ``SEARCH`` so a downstream DAX filter can use the canonical value instead of
    guessing at capitalisation or spacing.
    """
    pbi = client or PowerBIClient(token)
    group = workspace_id
    if group is None:
        group, _ = pbi.find_dataset_group(artifact_id)

    terms = [search_terms] if isinstance(search_terms, str) else list(search_terms or [])
    terms = [t for t in terms if t and str(t).strip()]
    if not terms:
        return {"error": "No search terms were provided.", "artifactId": artifact_id}

    schema = get_semantic_model_schema(token, artifact_id, group, client=pbi)
    text_columns: list[tuple[str, str]] = []
    for table in schema.get("tables", []):
        for column in table.get("columns", []):
            data_type = (column.get("dataType") or "").lower()
            if data_type in {"string", "text"} and not column.get("isHidden"):
                text_columns.append((table["name"], column["name"]))
    text_columns = text_columns[:max_columns]

    matches: list[dict] = []
    hard_cap = max(1, int(max_rows)) * 2
    for table_name, column_name in text_columns:
        ref = _dax_ref(table_name, column_name)
        condition = " || ".join(
            f'SEARCH("{_dax_str(term)}", {ref}, 1, 0) > 0' for term in terms
        )
        dax = f"EVALUATE TOPN({max(1, int(max_rows))}, FILTER(VALUES({ref}), {condition}))"
        try:
            body = pbi.execute_queries(artifact_id, [dax], group)
        except PowerBIError:
            continue
        for row in _extract_rows(body):
            value = next(iter(row.values()), None)
            if value is not None:
                matches.append({"table": table_name, "column": column_name, "value": value})
        if len(matches) >= hard_cap:
            break

    return {
        "artifactId": artifact_id,
        "workspaceId": group,
        "searchTerms": terms,
        "matchCount": len(matches),
        "matches": matches[:hard_cap],
        "columnsScanned": len(text_columns),
    }


# -- 6. ExecuteQuery -----------------------------------------------------------

def execute_query(
    token: str,
    artifact_id: str,
    dax_queries: str | list[str],
    max_rows: int = DEFAULT_MAX_ROWS,
    workspace_id: str | None = None,
    *,
    client: PowerBIClient | None = None,
) -> dict[str, Any]:
    """Run 1–4 read-only DAX ``EVALUATE`` queries against a semantic model.

    Mirrors ``ExecuteQuery(artifactId, daxQueries, maxRows?)``. Enforces exactly
    one ``EVALUATE`` per query and the 4-query / 1,000-row FabricIQ limits, then
    truncates each result to ``max_rows`` (default 250) client-side, flagging any
    result that had more rows.
    """
    pbi = client or PowerBIClient(token)
    queries = [dax_queries] if isinstance(dax_queries, str) else list(dax_queries or [])
    queries = [q for q in queries if q and q.strip()]
    if not queries:
        return {"error": "No DAX queries were provided.", "artifactId": artifact_id}
    if len(queries) > MAX_QUERIES:
        return {
            "error": f"At most {MAX_QUERIES} DAX queries are allowed per call.",
            "artifactId": artifact_id,
        }
    for query in queries:
        evaluate_count = len(re.findall(r"(?i)\bEVALUATE\b", query))
        if evaluate_count != 1:
            return {
                "error": (
                    f"Each query must contain exactly one EVALUATE statement "
                    f"(found {evaluate_count})."
                ),
                "query": query,
            }

    limit = max(1, min(int(max_rows or DEFAULT_MAX_ROWS), MAX_ROWS_CAP))
    group = workspace_id
    if group is None:
        group, _ = pbi.find_dataset_group(artifact_id)

    try:
        body = pbi.execute_queries(artifact_id, queries, group)
    except PowerBIError as exc:
        return {
            "error": str(exc),
            "status": exc.status,
            "code": exc.code,
            "artifactId": artifact_id,
        }

    results = []
    for result in body.get("results") or []:
        result_tables = result.get("tables") or []
        rows = (result_tables[0].get("rows") if result_tables else []) or []
        results.append({
            "rowCount": min(len(rows), limit),
            "truncated": len(rows) > limit,
            "rows": rows[:limit],
        })
    return {
        "artifactId": artifact_id,
        "workspaceId": group,
        "queryCount": len(queries),
        "results": results,
    }


# -- shared helpers ------------------------------------------------------------

def _normalize_types(artifact_types: list[str] | None) -> set[str]:
    """Resolve caller-supplied type names to ``{"Report", "SemanticModel"}``."""
    if not artifact_types:
        return {"Report", "SemanticModel"}
    resolved = set()
    for raw in artifact_types:
        key = str(raw).strip().lower()
        resolved.add(_ARTIFACT_ALIASES.get(key, str(raw)))
    resolved &= {"Report", "SemanticModel"}
    return resolved or {"Report", "SemanticModel"}


def _extract_rows(body: dict) -> list[dict]:
    """Pull the first result table's rows out of an executeQueries body."""
    results = body.get("results") or []
    if not results:
        return []
    tables = results[0].get("tables") or []
    if not tables:
        return []
    return tables[0].get("rows") or []


def _clean_row(row: dict) -> dict:
    """Strip ``Table[Column]`` / ``[Column]`` framing off executeQueries keys."""
    return {_clean_key(key): value for key, value in row.items()}


def _clean_key(key: str) -> str:
    """``'Foo'[Bar]`` -> ``Bar``; ``[Name]`` -> ``Name``; else unchanged."""
    match = re.search(r"\[([^\[\]]+)\]\s*$", key)
    return match.group(1) if match else key


def _as_bool(value: Any) -> Any:
    """Normalise the truthy variants executeQueries returns for boolean columns."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return value


def _dax_ref(table: str, column: str) -> str:
    """Build a safe ``'Table'[Column]`` reference, escaping quotes/brackets."""
    safe_table = table.replace("'", "''")
    safe_column = column.replace("]", "]]")
    return f"'{safe_table}'[{safe_column}]"


def _dax_str(value: str) -> str:
    """Escape a DAX string literal (double any embedded quote)."""
    return str(value).replace('"', '""')
