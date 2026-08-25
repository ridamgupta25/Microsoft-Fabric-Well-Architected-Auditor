"""Per-object evidence, shaped by what the check actually reasons about.

The bulk of a workspace summary is raw column names, and the checks do not read
them: ``TB-STARSCHEMA`` counts key, numeric and descriptive columns, so sending
25 column names per table spends the budget on text nothing consumes. That is
what forced a 40-table sample, and a sample is the wrong answer - it lets the
other 497 tables through unexamined in both directions, the ones the rule wrongly
flagged and the ones it wrongly missed.

Sending the **derived facts** instead is roughly four times smaller, so every
object fits:

    dbo.DimCustomer   12 cols  keys=1  num=2   desc=9   rule=dimension

Each line also carries what the deterministic rule concluded, so a reader can see
where it disagrees - which is the finding worth having.

**Deriving is not always right.** It works for tables because the check reasons
about counts, so a count is the whole of what it needs. It does not work for
pipelines and notebooks: eleven pipeline checks read eleven different parts of
the same definition - activity ``type``, ``dependsOn`` conditions,
``typeProperties``, Script bodies, the pipeline's own parameters - and any field
a summary leaves out is a question the reader silently cannot answer. Those
families therefore send the artefact itself, pruned of empties.

That costs budget, and the budget is spent the same way throughout: **never by
dropping objects.** A large notebook gets a chunk to itself. A genuinely
enormous one is cut with a visible marker and an instruction to answer
``undetermined`` rather than assume the practice is absent. Truncating inside
one object is recoverable because the reader knows it happened; skipping objects
is not, which is the free pass that made a 40-table sample the wrong answer.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime

from ..core.check._notebook import markdown_sources, notebook_code
from ..core.check._pipeline import walk_activities
from ..core.check._tables import (
    column_type,
    columns,
    is_config_table_name,
    is_key_column,
    is_platform_table,
    normalise_column,
    normalise_table_name,
    related_columns,
    store_kind_of,
    store_of,
    table_roles,
)
from ..core.models import WorkspaceContext

#: Longest single string value kept inside a pipeline definition. Long enough
#: for a SQL statement or an expression, short enough that one embedded blob
#: cannot swallow the pipeline it sits in.
_MAX_STRING = 2000

#: Longest code or markdown body kept for one notebook. Beyond this the tail is
#: cut with a visible marker, never dropped silently - see :func:`_truncate`.
#: Truncating *within* an object is recoverable, because the reader can see it
#: happened and answer ``undetermined``. Dropping objects is not, which is why
#: nothing here samples.
_MAX_BODY = 40000

#: A dataflow/staging artefact: a 32-hex-digit name from a run id. Named after a
#: run rather than a thing, so ``is_platform_table`` - which matches known
#: platform *names* - cannot catch it.
_GENERATED_TABLE = re.compile(r"^[0-9a-f]{32}(?:_|$)", re.IGNORECASE)

#: Schemas Fabric owns. Their tables are bookkeeping, and on a real estate they
#: outnumber the solution tables - 57 of 67 objects in one job were platform
#: rows the reader would have had to label 'neither' one at a time.
_PLATFORM_SCHEMAS = ("queryinsights.", "sys.", "information_schema.")


def is_noise(name: str) -> bool:
    """True for an object that tells a reviewer nothing about the solution."""
    leaf = (name or "").split(".")[-1]
    lowered = (name or "").lower()
    return (
        is_platform_table(name)
        or bool(_GENERATED_TABLE.search(leaf))
        or any(part in lowered for part in _PLATFORM_SCHEMAS)
    )








def _truncate(text: str) -> str:
    """Cut an over-long body, saying so. Never silently.

    The marker matters as much as the cut: a reader who cannot see the tail must
    answer ``undetermined``, not conclude the practice is missing. Absent is a
    finding; unseen is not.
    """
    if len(text) <= _MAX_BODY:
        return text
    return text[:_MAX_BODY] + (
        f"\n\n... [TRUNCATED: {len(text) - _MAX_BODY} more characters. If the "
        f"answer to this check could be in the part you cannot see, label it "
        f"'undetermined' rather than assuming the practice is absent.]"
    )


def _render(definition: dict) -> str:
    """A pruned definition as indented JSON."""
    return json.dumps(definition, indent=1, ensure_ascii=False, default=str)


def _prune(value, depth: int = 0):
    """A definition with empty branches dropped and long strings cut.

    Pipeline JSON is mostly bookkeeping - nulls, empty lists, GUID references -
    and sending it raw spends most of the budget on text no check reads. What
    every pipeline check *does* read is the activity tree: types, names,
    ``dependsOn`` conditions and ``typeProperties``. Those all survive here;
    only empties and runaway string values are removed.
    """
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            pruned = _prune(val, depth + 1)
            if pruned not in (None, "", [], {}):
                out[key] = pruned
        return out
    if isinstance(value, list):
        return [p for p in (_prune(v, depth + 1) for v in value) if p not in (None, "", [], {})]
    if isinstance(value, str) and len(value) > _MAX_STRING:
        return value[:_MAX_STRING] + f"... [+{len(value) - _MAX_STRING} chars]"
    return value


def pipeline_definition(workspace: WorkspaceContext) -> list[dict]:
    """One record per pipeline: its definition, pruned but structurally intact.

    Eleven checks read this, and they read different parts of it - activity
    ``type`` for the notifiers, ``dependsOn`` conditions for the failure edge,
    ``typeProperties`` for the Copy sink settings, Script bodies for the reload
    SQL, the pipeline's own parameters for the load-mode switch. Rendering a
    summary would mean guessing which fields matter to all eleven, and a field
    left out is a question the reader silently cannot answer. The definition
    goes as-is instead, minus the empties.
    """
    pipelines = workspace.pipelines or {}
    records: list[dict] = []
    for name in sorted(pipelines):
        definition = pipelines[name] or {}
        acts = walk_activities(definition)
        if not acts and not definition:
            records.append({
                "id": name,
                "facts": "definition not readable",
                "rule_says": "",
            })
            continue
        types = sorted({str(a.get("type") or "?") for a in acts})
        header = (
            f"{len(acts)} activities ({', '.join(types)})\n"
            if acts else "no activities\n"
        )
        records.append({
            "id": name,
            "facts": header + _render(_prune(definition)),
            "rule_says": "",
        })
    return records


def notebook_code_evidence(workspace: WorkspaceContext) -> list[dict]:
    """One record per notebook: its code, plus the markdown that documents it.

    Comments are **kept**. The rules strip them - deliberately, so a comment
    describing a technique cannot be mistaken for the technique - but a reader
    judging intent needs them, and can tell a description from an implementation
    in a way a regex cannot. That difference is most of why these checks are
    advisory.
    """
    notebooks = workspace.notebooks or {}
    records: list[dict] = []
    for name in sorted(notebooks):
        definition = notebooks[name] or {}
        code = notebook_code(definition)
        markdown = markdown_sources(definition)
        if not code and not markdown:
            records.append({
                "id": name,
                "facts": "definition not readable",
                "rule_says": "",
            })
            continue

        parts = [f"{len(markdown)} markdown cell(s), {len(code)} chars of code"]
        if markdown:
            parts.append("--- markdown ---\n" + _truncate("\n\n".join(markdown)))
        parts.append("--- code ---\n" + _truncate(code) if code else "--- no code cells ---")
        records.append({
            "id": name,
            "facts": "\n".join(parts),
            "rule_says": "",
        })
    return records


def table_detail(workspace: WorkspaceContext) -> list[dict]:
    """One record per table: **every** column with its type, plus its store.

    ``table-shape`` caps column names at twelve, which is right for a check that
    reasons about the balance of column kinds. It is wrong for the eight checks
    that look for a *particular* column - an audit column, the SCD2 trio, a
    degenerate key - because a ``created_date`` sitting at position 13 would be
    invisible and the reader would report it absent. That is a false FAIL
    produced by truncation, and the same free pass a table sample gives, one
    level down.

    Also carries what those checks read and a shape line cannot express:

    * the **store** a table lives in - two checks group by it, and a workspace
      with one lakehouse is a different judgment from one with four;
    * the column **type** - a junk dimension needs low-cardinality columns, and
      ``rejection_reason varchar(500)`` is prose, not a flag;
    * whether a column appears in a **declared relationship**, which settles
      what a name can only suggest.
    """
    tables = workspace.tables or {}
    roles = table_roles(tables, workspace.semantic_models)
    related = related_columns(workspace.semantic_models or {})

    # `normalise_table_name` drops the store prefix, and `related_columns`
    # returns one workspace-wide set - so a single relationship on `badges`
    # marks Bronze.badges, Silver.badges and Gold.badges alike. On one estate
    # 47% of tables shared a base name with another, which is exactly the
    # duplicated-copy case where "which copy is actually served" is the
    # question. The marker is suppressed where it cannot distinguish them,
    # because a wrong marker is worse than none.
    ambiguous = Counter(normalise_table_name(n) for n in tables if not is_noise(n))

    records: list[dict] = []
    for name in sorted(tables):
        if is_noise(name):
            continue
        table = tables[name] or {}
        cols = columns(table)
        if not cols:
            records.append({
                "id": name,
                "facts": f"store={store_of(table) or '?'}; columns not readable",
                "rule_says": "",
            })
            continue

        norm_table = normalise_table_name(name)
        shared = ambiguous.get(norm_table, 0) > 1
        role = roles.get(name, "unknown")
        rendered = []
        for col in cols:
            col_name = col.get("name") or "?"
            kind = column_type(col) or "?"
            marks = []
            if is_key_column(col_name):
                marks.append("key")
            if not shared and (norm_table, normalise_column(col_name)) in related:
                marks.append("in-relationship")
            if col.get("is_masked") or col.get("masking_function") or col.get("data_mask"):
                # WS-DDM reads sys.columns.is_masked. Without it a reader would
                # have to assume, and assuming "unmasked" turns every
                # sensitive-looking column into a false finding.
                marks.append("MASKED")
            suffix = f" [{', '.join(marks)}]" if marks else ""
            rendered.append(f"{col_name}:{kind}{suffix}")

        store = store_of(table) or "?"
        kind = store_kind_of(table) or "?"
        note = (f"; NOTE: {ambiguous[norm_table]} tables share this base name, "
                f"so relationship markers are suppressed as they cannot say "
                f"which copy is joined" if shared else "")
        records.append({
            "id": name,
            "facts": (
                f"store={store} ({kind}); {len(cols)} columns{note}\n  "
                + "\n  ".join(rendered)
            ),
            # Self-describing on purpose. Eleven checks share this family, and a
            # bare "dimension" here was read three separate times as *this*
            # check's verdict - so a reader judging audit columns was handed a
            # star-schema answer. Saying what it is costs a few characters and
            # removes the confusion; the check's own conclusion is `rule_verdict`.
            "rule_says": f"star-schema role per the rule: {role}",
        })
    return records


def code_and_pipelines(workspace: WorkspaceContext) -> list[dict]:
    """Every notebook *and* every pipeline, in one population.

    ``WS-RUN-HISTORY-EXPORT`` asks whether the estate persists run outcomes
    anywhere, and either kind of artefact can be the place it happens. Sending
    only one would answer half the question and report the other half absent.
    Ids are prefixed because a notebook and a pipeline may share a name, and
    two objects with one id cannot both be labelled.
    """
    records = [
        {**record, "id": f"pipeline:{record['id']}"}
        for record in pipeline_definition(workspace)
    ]
    records += [
        {**record, "id": f"notebook:{record['id']}"}
        for record in notebook_code_evidence(workspace)
    ]
    return records


def _measure_expression(measure: dict) -> str:
    """A measure's DAX, tolerating the list form the definition sometimes uses."""
    expression = measure.get("expression")
    if isinstance(expression, list):
        return "".join(str(part) for part in expression)
    return str(expression or "")


def model_measures(workspace: WorkspaceContext) -> list[dict]:
    """One record per semantic model: its tables and every measure's DAX.

    ``R-DAX-VAR`` judges the quality of the DAX itself and ``WS-MONITOR-TREND``
    asks whether a date table and a time-intelligence measure exist together,
    so both need the expressions rather than a count of them.
    """
    models = workspace.semantic_models or {}
    records: list[dict] = []
    for name in sorted(models):
        model = models[name] or {}
        measures = model.get("measures") or []
        tables = model.get("tables") or []
        if not measures and not tables:
            records.append({
                "id": name,
                "facts": "definition not readable",
                "rule_says": "",
            })
            continue

        lines = [
            f"{len(tables)} table(s): {', '.join(str(t) for t in tables) or '(none)'}",
            f"{len(measures)} measure(s):",
        ]
        for measure in measures:
            dax = " ".join(_measure_expression(measure).split())
            lines.append(
                f"  [{measure.get('table', '?')}] {measure.get('name', '?')} = {dax}"
            )
        records.append({
            "id": name,
            "facts": _truncate("\n".join(lines)),
            "rule_says": "",
        })
    return records


def model_columns(workspace: WorkspaceContext) -> list[dict]:
    """One record per semantic model: every column's declared and source type.

    ``SM-COLUMN-SHAPE`` reasons about compression cost, which lives in the
    *source* type - ``uniqueidentifier``, ``varchar(max)``, ``datetime2`` -
    rather than the model's own coarse ``data_type``. Relationship-bound
    columns are marked, because a load-bearing join key is exempt.
    """
    models = workspace.semantic_models or {}
    records: list[dict] = []
    for name in sorted(models):
        model = models[name] or {}
        cols = model.get("columns") or []
        if not cols:
            records.append({
                "id": name,
                "facts": "no columns declared",
                "rule_says": "",
            })
            continue

        bound = set()
        for rel in model.get("relationships") or []:
            bound.add((str(rel.get("from_table")), str(rel.get("from_column"))))
            bound.add((str(rel.get("to_table")), str(rel.get("to_column"))))

        lines = [f"{len(cols)} column(s):"]
        for col in cols:
            table, col_name = str(col.get("table", "?")), str(col.get("name", "?"))
            marks = []
            if col.get("is_key"):
                marks.append("is_key")
            if (table, col_name) in bound:
                marks.append("in-relationship")
            if col.get("is_hidden"):
                marks.append("hidden")
            suffix = f" [{', '.join(marks)}]" if marks else ""
            lines.append(
                f"  {table}[{col_name}] {col.get('data_type', '?')}"
                f" / source={col.get('source_provider_type') or '?'}{suffix}"
            )
        records.append({
            "id": name,
            "facts": _truncate("\n".join(lines)),
            "rule_says": "",
        })
    return records


def _median_gap_hours(stamps: list[str]) -> float | None:
    """Median hours between consecutive runs, or ``None`` when unknowable.

    Computed **here, in code**. The reader is shown the number and decides only
    whether the item is monitoring data - it is never asked to do arithmetic
    over a date series, which is the mistake this whole design exists to avoid.
    """
    parsed = []
    for stamp in stamps or []:
        text = str(stamp).strip().replace("Z", "+00:00")
        try:
            parsed.append(datetime.fromisoformat(text))
        except ValueError:
            continue
    if len(parsed) < 2:
        return None
    parsed.sort()
    gaps = sorted(
        (later - earlier).total_seconds() / 3600
        for earlier, later in zip(parsed, parsed[1:], strict=False)
    )
    middle = len(gaps) // 2
    if len(gaps) % 2:
        return gaps[middle]
    return (gaps[middle - 1] + gaps[middle]) / 2


def workspace_items(workspace: WorkspaceContext) -> list[dict]:
    """One record per Fabric item: its type, last run, cadence, and consumers.

    ``WS-GOLD-FRESHNESS`` decides which items are *serving* items from name
    words like gold / curated / mart, and ``OPS-MONITOR-REFRESH`` decides which
    are *monitoring* items the same way. That selection is the judgment worth
    replacing - but a reader told to ignore the name needs something else to
    go on, and a type plus a timestamp is not it.

    So each record also carries **who reads it**: the reports bound to a
    semantic model. A model with a report on top is a serving surface whatever
    it is called, and a model nothing reads is not - which is the distinction
    the name-matching cannot make.

    Cadence is reported only where it means something. ``run_history`` is
    populated from the job scheduler, which semantic models do not use, so
    every model read "cadence not measurable (0 run(s) recorded)" - stated as
    an estate fact when it is an artefact of where the number comes from. A
    reader could only conclude those models had never run.
    """
    history = workspace.run_history or {}

    # Which reports read which model. The binding is by id; names are carried
    # so the evidence can say "read by X" rather than print a GUID.
    readers: dict[str, list[str]] = {}
    for report in workspace.reports or []:
        dataset = (report or {}).get("dataset_id") or ""
        if dataset:
            readers.setdefault(dataset, []).append(
                str((report or {}).get("name") or "(unnamed report)")
            )

    records: list[dict] = []
    for item in workspace.items or []:
        parts = [f"type={item.type}"]
        parts.append(f"last run (UTC) = {item.last_run_utc or 'no run recorded'}")

        runs = history.get(item.id) or []
        gap = _median_gap_hours(runs)
        if gap is not None:
            parts.append(
                f"median gap between runs = {gap:.1f} h over {len(runs)} run(s)"
            )
        elif runs:
            parts.append(f"cadence not measurable ({len(runs)} run(s) recorded)")

        bound = readers.get(item.id) or []
        if bound:
            shown = ", ".join(sorted(bound)[:5])
            more = f" (+{len(bound) - 5} more)" if len(bound) > 5 else ""
            parts.append(f"read by {len(bound)} report(s): {shown}{more}")
        elif item.type == "SemanticModel":
            parts.append("no report reads this model")

        records.append({
            "id": f"{item.type}:{item.display_name}",
            "facts": "; ".join(parts),
            "rule_says": "",
        })
    return records


def workspace_roles(workspace: WorkspaceContext) -> list[dict]:
    """One record per workspace role assignment, with the config tables in view.

    ``WS-METADATA-WRITE`` asks whether only framework identities can write to
    the metadata store. Who holds a role is structured data the rule reads
    correctly; *whether a metadata store exists at all* is decided from a
    name vocabulary that includes the word 'job', so a fact table of job
    applications makes the rule believe there is one.

    The reader therefore needs both in one place: it settles the metadata-store
    question first, and labels every assignment 'undetermined' when there is no
    metadata store to protect.
    """
    tables = workspace.tables or {}
    config_like = sorted(
        name for name in tables
        if not is_noise(name) and is_config_table_name(name)
    )
    header = (
        "config-shaped tables in this workspace: "
        + (", ".join(config_like) if config_like else "(none matched by name)")
    )

    records: list[dict] = []
    for role in workspace.role_assignments or []:
        records.append({
            "id": f"{role.role}:{role.display_name or role.principal_id or '?'}",
            "facts": (
                f"role={role.role}; principal_type={role.principal_type}; "
                f"name={role.display_name or '(unnamed)'}\n{header}"
            ),
            "rule_says": "individual" if role.is_individual else "non-personal",
        })
    return records


#: ``evidence family -> builder``. A guide names one of these; the job export
#: calls it once per workspace and chunks the result.
BUILDERS = {
    "table-detail": table_detail,
    "pipeline-definition": pipeline_definition,
    "notebook-code": notebook_code_evidence,
    "code-and-pipelines": code_and_pipelines,
    "model-measures": model_measures,
    "model-columns": model_columns,
    "workspace-items": workspace_items,
    "workspace-roles": workspace_roles,
}


def build(family: str, workspace: WorkspaceContext) -> list[dict]:
    """Every object a check must judge, as ``{id, facts, rule_says}`` records."""
    builder = BUILDERS.get(family)
    return builder(workspace) if builder else []
