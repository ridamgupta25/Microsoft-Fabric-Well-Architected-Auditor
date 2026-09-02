"""Data Management & Quality - Data Storage — cross-workspace (group) checks.

Compares the members of a project group (Dev -> UAT -> Prod) for warehouse
modelling practices that should hold in every environment. Registers into the
separate ``GROUP_REGISTRY`` via :func:`group_check`; N/A-not-FAIL when fewer than
two members can be read.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check import _xw
from auditfast.core.check._notebook import executable_code, layer_words_in, strip_sql_comments
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import group_check
from auditfast.core.enums import Pillar, Resource, Severity
from auditfast.core.models import GroupContext

#: An actual reconciliation *control*, not the mere word "reconcile": a count
#: comparison, a named count check, or a reconcile routine that is really called.
#: A bare mention in a variable name, string, or leftover token never qualifies.
_RECON_CONTROL = re.compile(
    r"assert(?![^\n]*\.is(?:Not)?Null\s*\()[^\n]*?\.count\s*\([^\n]*?(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"\.count\s*\(\s*\)\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:row|record|source|target|actual|expected|recon)_count\b\s*(?:==|!=|<=|>=|<|>)(?!\s*0\b)|"
    r"(?:==|!=|<=|>=|<|>)\s*(?:row|record|source|target|actual|expected|recon)_count\b|"
    r"\breconcile\w*\s*\(|\bcount_check\b|validate[^\n]*count|expect_table_row_count",
    re.IGNORECASE,
)

#: A rollup in SQL: it both groups and aggregates. Either alone is not a rollup —
#: ``GROUP BY`` without an aggregate is a DISTINCT, and ``SUM()`` without a
#: ``GROUP BY`` is a scalar.
_SQL_GROUP_BY = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
_SQL_AGGREGATION = re.compile(r"\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
#: Where a routine writes its result — the aggregate side of a derivation.
_SQL_WRITE_TARGET = re.compile(
    r"\bINSERT\s+INTO\s+([\w.\[\]\"]+)|\bINTO\s+([\w.\[\]\"]+)\s+FROM\b",
    re.IGNORECASE,
)
#: Where it reads from — the detail side.
_SQL_READ_SOURCE = re.compile(r"\b(?:FROM|JOIN)\s+([\w.\[\]\"]+)", re.IGNORECASE)

#: A rollup in Spark: a grouping call and an aggregation over it.
_NB_GROUPING = re.compile(r"\.(?:groupBy|groupby|rollup|cube)\s*\(", re.IGNORECASE)
_NB_AGGREGATION = re.compile(r"\.(?:agg|sum|count|avg|mean|min|max)\s*\(", re.IGNORECASE)
_NB_WRITE_TARGET = re.compile(
    r"""(?:\.saveAsTable\s*\(\s*|\.insertInto\s*\(\s*)(?:[rubf]{0,2})?["'`]([\w.]+)"""
    r"""|INSERT\s+(?:INTO|OVERWRITE(?:\s+TABLE)?)\s+([\w.]+)""",
    re.IGNORECASE,
)
#: The ``(?!...import)`` guard keeps Python's own ``from pyspark.sql import
#: functions`` out of the table list — without it every PySpark notebook appears
#: to read a detail table called ``sql``. The lookahead sits *before* the capture
#: so the engine cannot backtrack into a shorter name to satisfy it.
_NB_READ_SOURCE = re.compile(
    r"""spark\.(?:read\.)?table\s*\(\s*(?:[rubf]{0,2})?["'`]([\w.]+)"""
    r"""|\bFROM\s+(?![\w.]+\s+import\b)([\w.]+)""",
    re.IGNORECASE,
)

#: A comparison that can fail, and something that stops the run when it does.
_SQL_MISMATCH = re.compile(r"\b(?:if|where|having)\b[^;]*(?:<>|!=)", re.IGNORECASE)
_SQL_STOP = re.compile(r"\b(?:throw|raiserror)\b", re.IGNORECASE)

#: How many derivations one workspace may contribute. A rollup-heavy warehouse
#: could otherwise pair every table with every source it reads.
_MAX_DERIVATIONS = 200


def _bare(name) -> str:
    """The unqualified, lower-cased table name — ``Bronze_LH.SALES`` -> ``sales``."""
    cleaned = str(name or "").strip()
    for character in '[]"`':
        cleaned = cleaned.replace(character, "")
    return cleaned.split(".")[-1].strip().lower()


def _references(text: str, table: str) -> bool:
    """True when ``text`` names ``table`` as a whole identifier.

    A schema qualifier is transparent (``dbo.sales`` references ``sales``), but a
    longer name is not (``sales_archive`` does not reference ``sales``).
    """
    if not table:
        return False
    return bool(re.search(rf"(?<![\w]){re.escape(table)}(?![\w])", text, re.IGNORECASE))


def _model_derivations(ws) -> list[tuple[str, str, str]]:
    """Rollups the semantic model *declares* through ``alternateOf``.

    The strongest signal there is: Power BI records the base table and column an
    aggregation column summarises, so nothing is inferred.
    """
    found: list[tuple[str, str, str]] = []
    for model_name, model in (ws.semantic_models or {}).items():
        for aggregation in model.get("aggregations") or []:
            if not isinstance(aggregation, dict):
                continue
            aggregate = _bare(aggregation.get("table"))
            detail = _bare(aggregation.get("base_table"))
            if aggregate and detail and aggregate != detail:
                found.append((aggregate, detail, (
                    f"semantic model '{model_name}' declares '{aggregate}' as an "
                    f"aggregation of '{detail}'"
                )))
    return found


def _sql_derivations(ws) -> list[tuple[str, str, str]]:
    """Rollups computed in SQL: a routine that groups one table *into* another.

    Only a routine that **writes** its grouped result counts. A ``GROUP BY`` view
    is deliberately not a rollup here: a Fabric Warehouse view is computed at
    query time and is never materialised, so its totals are recalculated from the
    detail on every read and cannot drift from it. There is nothing to reconcile,
    and treating one as a rollup would fail an estate for a risk it cannot carry —
    on one real tenant every "finding" this check produced was a plain view.
    """
    found: list[tuple[str, str, str]] = []
    for routine in ws.sql_routines or []:
        sql = strip_sql_comments(str(routine.get("definition") or ""))
        if not (_SQL_GROUP_BY.search(sql) and _SQL_AGGREGATION.search(sql)):
            continue
        targets = {
            _bare(group)
            for match in _SQL_WRITE_TARGET.finditer(sql)
            for group in match.groups() if group
        }
        sources = {_bare(m.group(1)) for m in _SQL_READ_SOURCE.finditer(sql)}
        for aggregate in targets:
            for detail in sources - targets:
                if aggregate and detail:
                    found.append((aggregate, detail, (
                        f"{str(routine.get('type') or 'routine').lower()} "
                        f"'{routine.get('name')}' groups '{detail}' into '{aggregate}'"
                    )))
    return found


def _notebook_derivations(ws) -> list[tuple[str, str, str]]:
    """Rollups computed in a notebook: a grouped aggregation written to a table."""
    found: list[tuple[str, str, str]] = []
    for name, definition in (ws.notebooks or {}).items():
        code = executable_code(definition)
        if not (_NB_GROUPING.search(code) and _NB_AGGREGATION.search(code)):
            continue
        targets = {
            _bare(group)
            for match in _NB_WRITE_TARGET.finditer(code)
            for group in match.groups() if group
        }
        sources = {
            _bare(group)
            for match in _NB_READ_SOURCE.finditer(code)
            for group in match.groups() if group
        }
        for aggregate in targets:
            for detail in sources - targets:
                if aggregate and detail:
                    found.append((aggregate, detail, (
                        f"notebook '{name}' groups '{detail}' into '{aggregate}'"
                    )))
    return found


def _aggregate_derivations(ws) -> list[tuple[str, str, str]]:
    """Every detail-to-aggregate rollup this workspace *declares*, deduplicated.

    A rollup is only counted where the estate says so itself — a TMSL
    ``alternateOf``, or code that groups one table into another. Nothing is
    inferred from a table being *named* like a summary, which both missed real
    rollups (a finance estate calls them balances) and would have counted an
    imported source table that merely had the word in its name.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for aggregate, detail, how in (
        _model_derivations(ws) + _sql_derivations(ws) + _notebook_derivations(ws)
    ):
        if (aggregate, detail) in seen:
            continue
        seen.add((aggregate, detail))
        unique.append((aggregate, detail, how))
        if len(unique) >= _MAX_DERIVATIONS:
            break
    return unique


def _reconciles(ws, aggregate: str, detail: str) -> str:
    """Describe the control that proves this rollup did not lose data, or ``""``.

    The control must reference **both** tables of the derivation, total them, and
    act on a mismatch. Requiring the two real table names — rather than the words
    "detail" and "summary" appearing somewhere — is what makes this independent
    of naming convention.
    """
    for sql_object in (*(ws.sql_views or []), *(ws.sql_routines or [])):
        sql = strip_sql_comments(str(sql_object.get("definition") or ""))
        if (
            _references(sql, aggregate)
            and _references(sql, detail)
            and len(_SQL_AGGREGATION.findall(sql)) >= 2
            and _SQL_MISMATCH.search(sql)
            and _SQL_STOP.search(sql)
        ):
            return f"SQL '{sql_object.get('name')}' compares the totals and stops on a mismatch"

    for model_name, model in (ws.semantic_models or {}).items():
        for measure in model.get("measures") or []:
            expression = str(measure.get("expression") or "")
            if (
                _references(expression, aggregate)
                and _references(expression, detail)
                and len(_SQL_AGGREGATION.findall(expression)) >= 2
                and "-" in expression
            ):
                return (
                    f"measure '{measure.get('name')}' in '{model_name}' differences "
                    f"the two grains"
                )

    for name, definition in (ws.notebooks or {}).items():
        code = executable_code(definition)
        if (
            _references(code, aggregate)
            and _references(code, detail)
            and _RECON_CONTROL.search(code)
        ):
            return f"notebook '{name}' compares the two grains and fails on a mismatch"
    return ""


@group_check(
    id="XW-AGG-CONSIST", ref="5.4.3",
    title="Aggregate consistency: sum of detail records equals aggregate totals (no data loss in rollup)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH,
    requires=[
        Resource.TABLE_COLUMNS, Resource.SEMANTIC_MODEL_DEFINITIONS,
        Resource.NOTEBOOK_DEFINITIONS,
    ],
    required=False,
)
def aggregate_consistency(ctx: GroupContext) -> Verdict:
    """Every environment that builds a rollup verifies it against its detail.

    A rollup is found from what the estate **declares** — a semantic-model
    ``alternateOf`` aggregation, or SQL/Spark code that groups one table into
    another and writes the result — never from a table being *named* like a
    summary. Naming both missed real rollups (a finance estate calls them
    balances, not summaries) and risked counting an imported source table that
    happened to carry the word.

    An environment passes when at least one of its rollups is verified: something
    reads **both** tables of the derivation, totals them, and acts on a mismatch.

    An environment that builds no rollup is excluded, not failed — there is no
    aggregate to reconcile. N/A only when *no* environment builds one: a single
    environment with an unreconciled rollup is a real finding, not an
    unanswerable question.
    """
    verified: list[str] = []
    unverified: list[str] = []
    skipped: list[str] = []

    for member in ctx.members:
        ws = member.workspace
        if not (ws.has(Resource.TABLE_COLUMNS)
                or ws.has(Resource.SEMANTIC_MODEL_DEFINITIONS)
                or ws.has(Resource.NOTEBOOK_DEFINITIONS)):
            continue
        label = _xw.env_label(member)
        derivations = _aggregate_derivations(ws)
        if not derivations:
            skipped.append(f"{label} (builds no materialised aggregate rollup)")
            continue
        proof = ""
        for aggregate, detail, how in derivations:
            proof = _reconciles(ws, aggregate, detail)
            if proof:
                verified.append(f"{label} ({how}; {proof})")
                break
        if not proof:
            first = derivations[0][2]
            unverified.append(
                f"{label} ({len(derivations)} rollup(s), none reconciled — e.g. {first})"
            )

    excluded = (f"; {len(skipped)} environment(s) excluded with no rollup to "
                f"reconcile: {'; '.join(skipped)}") if skipped else ""

    judged = len(verified) + len(unverified)
    if judged == 0:
        return not_applicable(
            "no environment in this group builds a materialised detail-to-aggregate "
            f"rollup whose reconciliation could be judged{excluded}"
        )
    if not unverified:
        return covered(
            judged, judged,
            f"all {judged} environment(s) reconcile their rollups against the "
            f"detail: {'; '.join(verified)}{excluded}",
        )
    if judged == 1:
        return covered(
            0, 1,
            "the only environment that builds a rollup does not reconcile it "
            f"against the detail: {'; '.join(unverified)}{excluded}",
        )
    return covered(
        len(verified), judged,
        f"{len(verified)} of {judged} environment(s) reconcile a rollup against "
        f"its detail; not in {'; '.join(unverified)}{excluded}",
    )


#: A *data* write (not a DDL CREATE) and its target: what the flow produces.
_DATA_WRITE_TARGET = re.compile(
    r"""(?:\.saveAsTable\s*\(\s*|\.insertInto\s*\(\s*"""
    r"""|INSERT\s+(?:INTO|OVERWRITE(?:\s+TABLE)?)\s+)"""
    r"""(?:[rubf]{0,2})?["'`]?([\w.\[\]/-]+)""",
    re.IGNORECASE,
)
#: Target-name tokens that place a write in the Gold serving tier or a Warehouse.
_GOLD_TARGET_TOKENS = frozenset(
    {"gold", "serving", "serve", "presentation", "mart", "datamart",
     "aggregate", "aggregated", "consumption", "warehouse", "edw"}
)
#: Target-name tokens that mark a write as landing in a metadata/log registry,
#: not a data table — the tell of a DDL/metadata-bootstrap notebook.
_METADATA_TARGET = re.compile(
    r"\b(?:meta|metadata|loadlist|field_standards?|registry|catalog|_ddl"
    r"|log|audit|control|sequence_counter|validation|dictionary|lineage)\b",
    re.IGNORECASE,
)
#: A pipeline whose sink or target names the Gold serving tier / a Warehouse.
_PL_GOLD_SINK = re.compile(
    r"DataWarehouseSink|DataWarehouse\b|\bWarehouse\b|\bgold\b|\bmart\b|\bEDW\b",
    re.IGNORECASE,
)
_PL_SILVER_SOURCE = re.compile(r"\bsilver\b", re.IGNORECASE)
_PL_NAME_SILVER_TO_GOLD = re.compile(r"silver.{0,20}gold", re.IGNORECASE)
#: A pipeline record-count reconciliation control (compare source vs target).
_PL_RECON_CONTROL = re.compile(
    r"source[_ ]?count|target[_ ]?count|record[_ ]?count|reconcil"
    r"|count[_ ]?check|control[_ ]?total",
    re.IGNORECASE,
)


def _write_targets(code: str) -> list[str]:
    return [m.group(1) for m in _DATA_WRITE_TARGET.finditer(code)]


def _writes_gold_data(code: str) -> bool:
    """True when the code writes *data* to a Gold-tier / Warehouse target.

    A DDL ``CREATE TABLE`` is not a data write, and a target named for a metadata
    registry is not a gold data table, so neither counts here.
    """
    for target in _write_targets(code):
        tokens = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", target) if t}
        if tokens & _GOLD_TARGET_TOKENS:
            return True
    return False


def _is_metadata_only_notebook(code: str) -> bool:
    """True when the notebook only defines/loads metadata, not data.

    A DDL/metadata-bootstrap notebook (``nb_metadata_ddl_script``) creates schemas
    and a metadata/log/registry table and seeds it; it moves no Silver data to a
    Gold target, so it must not be mistaken for a Silver-to-Gold flow. It is
    metadata-only when it has no data write at all, or every data write targets a
    metadata/log/registry table.
    """
    targets = _write_targets(code)
    if not targets:
        return True
    return all(_METADATA_TARGET.search(target) for target in targets)


def _pipeline_is_silver_to_gold(name: str, definition: dict) -> bool:
    """True when a pipeline moves Silver data into a Gold-tier / Warehouse target."""
    if _PL_NAME_SILVER_TO_GOLD.search(name):
        return True
    blob = json.dumps(definition)
    return bool(_PL_SILVER_SOURCE.search(blob) and _PL_GOLD_SINK.search(blob))


def _silver_to_gold_flows(ws) -> tuple[list[str], list[str]]:
    """Return applicable and reconciled Silver-to-Gold flow names (nb + pipeline).

    A notebook flow reads Silver and writes *data* to a Gold-tier target, and is
    not a DDL/metadata notebook. A pipeline flow moves Silver into a Warehouse /
    Gold sink (e.g. the ``Silver To Gold`` pipeline). Each flow is then checked
    for a record-count reconciliation control.
    """
    applicable: list[str] = []
    reconciled: list[str] = []
    for name, definition in ws.notebooks.items():
        code = strip_sql_comments(executable_code(definition))
        if "silver" not in layer_words_in(code):
            continue
        if _is_metadata_only_notebook(code):
            continue
        if not _writes_gold_data(code):
            continue
        applicable.append(name)
        if _RECON_CONTROL.search(code):
            reconciled.append(name)
    for name, definition in ws.pipelines.items():
        if not _pipeline_is_silver_to_gold(name, definition):
            continue
        applicable.append(name)
        if _PL_RECON_CONTROL.search(json.dumps(definition)):
            reconciled.append(name)
    return applicable, reconciled


@group_check(
    id="XW-LAYER-RECON", ref="5.4.6",
    title="Cross-layer reconciliation: Gold record counts reconcile with Silver (accounting for aggregation)",
    pillar=Pillar.DATA_QUALITY, severity=Severity.HIGH,
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS], required=False,
)
def cross_layer_reconciliation(ctx: GroupContext) -> Verdict:
    """Detect reconciliation controls across a group's Silver-to-Gold flows.

    Silver-to-Gold flows are the notebooks that read Silver and write data to a
    Gold-tier target (DDL/metadata notebooks are excluded) and the pipelines that
    move Silver into a Warehouse / Gold sink. This is metadata-only: it inspects
    the definitions already in each snapshot and never queries table rows or
    business values.
    """
    readable = [member for member in ctx.members
                if member.workspace.has(Resource.NOTEBOOK_DEFINITIONS)]
    if len(readable) < 2:
        return not_applicable(
            "fewer than two workspaces had readable notebook definitions to compare"
        )

    applicable_total = 0
    reconciled_total = 0
    all_by_tier: list[tuple[str, list[str]]] = []
    missing_by_tier: list[tuple[str, list[str]]] = []
    for member in readable:
        flows, controlled = _silver_to_gold_flows(member.workspace)
        if not flows:
            continue
        controlled_set = set(controlled)
        tier = _xw.env_tier(member)
        applicable_total += len(flows)
        reconciled_total += len(controlled)
        all_by_tier.append((tier, flows))
        missing = [name for name in flows if name not in controlled_set]
        if missing:
            missing_by_tier.append((tier, missing))

    if applicable_total == 0:
        return not_applicable(
            "no Silver-to-Gold data flow (notebook write to a Gold-tier target, or "
            "a Silver-to-Warehouse pipeline) was found across the group"
        )

    def _grouped(pairs: list[tuple[str, list[str]]]) -> str:
        return "; ".join(
            f"**{tier}** — " + ", ".join(f"'{name}'" for name in names)
            for tier, names in pairs
        )

    if reconciled_total == applicable_total:
        return covered(
            applicable_total, applicable_total,
            "Gold record counts reconcile with Silver (accounting for aggregation) in "
            f"all {applicable_total} Silver-to-Gold flow(s): {_grouped(all_by_tier)}",
        )
    if reconciled_total == 0:
        return covered(
            0, applicable_total,
            f"None of the {applicable_total} pipelines/notebooks that load data from "
            "Silver into Gold check for row loss — they don't compare how many rows "
            "were read from Silver against how many were written to Gold, so if a load "
            f"silently dropped rows nobody would know. The {applicable_total} flow(s): "
            f"{_grouped(all_by_tier)}.",
        )
    return covered(
        reconciled_total, applicable_total,
        f"{reconciled_total} of {applicable_total} Silver-to-Gold flow(s) compare "
        "Silver source rows against Gold target rows; the rest do not check for row "
        f"loss: {_grouped(missing_by_tier)}.",
    )
