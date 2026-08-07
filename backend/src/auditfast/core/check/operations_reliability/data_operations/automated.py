"""Operations & Reliability · Data Operations — workspace ops hygiene.

Naming, source control, promotion gating, environment isolation, and the
deployment/test posture of the operational estate.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import executable_code
from auditfast.core.check._pipeline import script_sql, walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Environment-tier tokens that mark a promotable workspace. Only one is needed —
#: a lone UAT is fine; not every tier has to exist. Mapped to a canonical label
#: for the evidence text.
ENVIRONMENT_TIERS: dict[str, str] = {
    "DEV": "Dev", "DEVELOPMENT": "Dev",
    "SIT": "SIT",
    "QA": "QA",
    "TEST": "Test", "TST": "Test",
    "UAT": "UAT",
    "STG": "Staging", "STAGING": "Staging",
    "PREPROD": "Pre-Prod", "PREPRODUCTION": "Pre-Prod",
    "PROD": "Prod", "PRD": "Prod", "PRODUCTION": "Prod",
}

#: Name tokens that identify a reporting / semantic workspace by purpose.
REPORTING_MARKERS: frozenset[str] = frozenset(
    {"REPORT", "REPORTS", "REPORTING", "SEMANTIC", "DASHBOARD", "ANALYTICS"}
)

#: Item types that make a workspace a shared data / lakehouse workspace, where an
#: environment tier in the name is not expected.
STORAGE_ITEM_TYPES: frozenset[str] = frozenset(
    {"Lakehouse", "Warehouse", "SQLDatabase", "SQLEndpoint", "KQLDatabase", "Eventhouse"}
)

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")

#: A single word whose casing is internally consistent: all-lower, all-UPPER (or
#: digits), Title / PascalCase, or camelCase. A word matching none of these —
#: e.g. ``aRIGFHG`` — has letters mixed within it and is called out.
_CONSISTENT_WORD = re.compile(
    r"^(?:[a-z0-9]+|[A-Z0-9]+|(?:[A-Z][a-z0-9]+)+|[a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+)$"
)


def _name_tokens(name: str) -> set[str]:
    """Upper-cased alphanumeric tokens of a name (splits on spaces, ``_`` and ``-``)."""
    return {tok.upper() for tok in _TOKEN_SPLIT.split(name) if tok}


def _style_issues(name: str) -> str:
    """Describe any internal naming inconsistency, or ``""`` when the name is clean.

    Consistency-only — no single convention is mandated. Two signals:
    * separators: underscores used together with spaces or hyphens (mixed styles);
    * casing: a word that is not cleanly lower / UPPER / Title / camel / Pascal
      (e.g. ``aRIGFHG``), i.e. letters mixed within the word.
    """
    problems: list[str] = []
    if "_" in name and (" " in name or "-" in name):
        problems.append("mixes underscores with spaces/hyphens")
    irregular = [tok for tok in _TOKEN_SPLIT.split(name) if tok and not _CONSISTENT_WORD.match(tok)]
    if irregular:
        problems.append("irregular letter casing in " + ", ".join(f"'{t}'" for t in irregular))
    return "; ".join(problems)


@check(
    id="WS-NAME", ref="IMPL-24", title="Workspace name follows the organization naming convention (e.g., <Domain>-<Env>-<Project>) [WS-NAME]",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.LOW,
    requires=[Resource.WORKSPACE, Resource.ITEMS], required=False,
)
def naming_convention(ctx: CheckContext) -> Verdict:
    """The workspace name signals its promotion tier or purpose, in a consistent style.

    A promotable workspace carries an environment tier — Dev / UAT / Prod (only
    one is needed; a lone UAT is fine). A workspace with no tier is expected to be
    either a reporting workspace (its name should say so) or a shared data /
    lakehouse workspace (a tier is not required). On top of that the name must be
    internally consistent: one separator style and clean per-word casing — a name
    that mixes spaces with underscores, or garbles casing like ``aRIGFHG``, is
    called out even when its purpose is clear.
    """
    name = ctx.workspace.name
    tokens = _name_tokens(name)

    tier = next((ENVIRONMENT_TIERS[t] for t in tokens if t in ENVIRONMENT_TIERS), None)
    if tier:
        base, purpose = 3, f"environment tier '{tier}' present — follows the Dev/UAT/Prod promotion convention"
    elif tokens & REPORTING_MARKERS:
        base, purpose = 3, "reporting workspace — the name marks its purpose; an environment tier is not required"
    elif ctx.workspace.layer is Layer.REPORTING:
        base, purpose = 1, "tagged Reporting / Semantic but the name has no 'Reporting'/'Report' marker — add 'Reporting' to the name"
    elif ctx.workspace.layer is Layer.STORAGE or any(i.type in STORAGE_ITEM_TYPES for i in ctx.workspace.items):
        base, purpose = 3, "shared data / lakehouse workspace — an environment tier is not required"
    else:
        base, purpose = 0, "no environment tier (Dev/UAT/Prod) and no reporting or data-lakehouse marker — add a tier, or 'Reporting' if this is a reporting-only workspace"

    issues = _style_issues(name)
    if issues:
        # Purpose may be clear, but an inconsistent style is still called out.
        return graded(min(base, 1), f"'{name}': {purpose}. Inconsistent naming style — {issues}.")
    if base == 3:
        return binary(True, f"'{name}': {purpose}.")
    if base == 1:
        return graded(1, f"'{name}': {purpose}.")
    return binary(False, f"'{name}': {purpose}.")


@check(
    id="WS-GIT", ref="11.1.1", title="Git integration enabled for Fabric workspaces",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.GIT], required=True,
)
def git_connected(ctx: CheckContext) -> Verdict:
    """The workspace is connected to Git so its items are source-controlled."""
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable("Git connection state could not be read from Fabric")
    ok = ctx.workspace.git_connected
    return binary(ok, "Workspace is connected to Git" if ok
                  else "Workspace is not connected to Git")


@check(
    id="WS-DEPLOY", ref="11.2.1", title="Fabric Deployment Pipelines configured (Dev → QA → Prod) for all three layer workspaces",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE], required=True,
)
def deployment_pipeline(ctx: CheckContext) -> Verdict:
    """The workspace is assigned to a deployment pipeline gating promotion."""
    ok = ctx.workspace.deployment_pipeline
    return binary(ok, "Assigned to a deployment pipeline" if ok
                  else "No deployment pipeline assigned")


# =============================================================================
# 1.1.8 — single source of truth (no duplicate data stores)
# =============================================================================

#: Item types that *are* a data store. A store duplicated across domains or
#: layers is the defect this looks for. Deliberately excludes ``SQLEndpoint``
#: and ``SemanticModel``: Fabric auto-creates both alongside every Lakehouse and
#: Warehouse, so counting them would report a duplicate on every healthy estate.
DATA_STORE_TYPES: frozenset[str] = frozenset({
    "Lakehouse", "Warehouse", "SQLDatabase", "KQLDatabase", "Eventhouse",
    "Datamart", "MirroredDatabase", "MirroredWarehouse", "MirroredAzureDatabricksCatalog",
})

#: Tokens that describe the *container*, not the purpose it serves. Stripped so
#: ``LH_Sales`` and ``WH_SALES`` collapse onto the same purpose.
_STORE_NOISE_TOKENS: frozenset[str] = frozenset({
    "LH", "WH", "DB", "SQL", "KQL", "DW", "EH",
    "LAKE", "LAKEHOUSE", "WAREHOUSE", "DATABASE", "DATAMART", "MART",
    "EVENTHOUSE", "STORE", "DATA", "DATASTORE", "ONELAKE",
    "COPY", "CLONE", "BAK", "BACKUP", "OLD", "NEW", "TMP", "TEMP",
    "FINAL", "DRAFT", "ARCHIVE", "ARCHIVED", "V", "VER", "VERSION",
})
#: ``v2`` / ``V02`` / a bare ``2`` — a version or copy marker, never a purpose.
_VERSION_TOKEN = re.compile(r"^V?\d+$")


def _store_purpose(name: str) -> tuple[str, ...]:
    """The purpose a store name conveys, with container/version/env noise removed.

    Two stores that reduce to the same tuple serve the same purpose under two
    names — the "single source of truth" defect. Returns an empty tuple when
    nothing but noise is left, in which case the name says too little to judge.
    """
    tokens = [
        tok for tok in _name_tokens(name)
        if tok not in _STORE_NOISE_TOKENS
        and tok not in ENVIRONMENT_TIERS
        and not _VERSION_TOKEN.match(tok)
    ]
    return tuple(sorted(tokens))


@check(
    id="WS-SINGLE-SOURCE", ref="1.1.8",
    title="Single source of truth — no duplicate data stores serving the same purpose across domains or layers",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.ITEMS], required=True,
)
def single_source_of_truth(ctx: CheckContext) -> Verdict:
    """Each data store in the workspace serves a purpose no other store already serves.

    Compares Lakehouses, Warehouses, and databases by the *purpose* their names
    convey, after stripping container words (``LH``/``WH``/``Lakehouse``...),
    environment tiers, and version/copy markers (``v2``, ``_COPY``, ``_OLD``). A
    Lakehouse and a Warehouse that both reduce to ``SALES`` are two stores for
    one purpose — the classic second source of truth. Names that reduce to
    nothing but noise are excluded rather than guessed at.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    stores = [i for i in ctx.workspace.items if i.type in DATA_STORE_TYPES]
    if not stores:
        return not_applicable(
            "Workspace holds no Lakehouse, Warehouse, or database item, so there "
            "is no data store whose purpose could be duplicated"
        )

    by_purpose: dict[tuple[str, ...], list[str]] = {}
    unnamed = 0
    for item in stores:
        purpose = _store_purpose(item.display_name or item.id)
        if not purpose:
            unnamed += 1
            continue
        by_purpose.setdefault(purpose, []).append(item.display_name or item.id)

    judged = sum(len(names) for names in by_purpose.values())
    if not judged:
        return not_applicable(
            f"All {len(stores)} data store name(s) reduce to container/version "
            "words only, so no purpose can be compared"
        )

    clashes = {p: names for p, names in by_purpose.items() if len(names) > 1}
    duplicated = sum(len(names) for names in clashes.values())
    if not clashes:
        caveat = f" ({unnamed} name(s) too generic to compare)" if unnamed else ""
        return covered(judged, judged,
                       f"{judged} data store(s) each serve a distinct purpose{caveat}")

    detail = "; ".join(
        f"'{' '.join(purpose).lower()}' served by " + ", ".join(sorted(names))
        for purpose, names in sorted(clashes.items())[:3]
    )
    return covered(
        judged - duplicated, judged,
        f"{duplicated} of {judged} data store(s) share a purpose with another "
        f"store: {detail}",
    )


# =============================================================================
# 1.1.3 — environment isolation (no cross-environment dependencies)
# =============================================================================

#: One bounded-token matcher for every environment word, so ``DEV`` inside
#: ``DEVELOPMENT`` or ``DEVICE`` never matches.
_TIER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(sorted(ENVIRONMENT_TIERS, key=len, reverse=True))
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

#: The same tokens minus ``TEST``/``TST``. Inside a *definition* those words are
#: overwhelmingly benign — "Test connection", "Run unit tests", "testfile.csv" —
#: so treating them as an environment reference would manufacture findings, and
#: would contradict WS-UNIT-TESTS, which asks for a test step in the pipeline.
#: A workspace's *own* tier is still read from the full map above.
_FOREIGN_TIER_TOKENS: dict[str, str] = {
    token: tier for token, tier in ENVIRONMENT_TIERS.items() if token not in ("TEST", "TST")
}
_FOREIGN_TIER_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(sorted(_FOREIGN_TIER_TOKENS, key=len, reverse=True))
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _tiers_in(text: str) -> set[str]:
    """Canonical environment tiers named anywhere in ``text``."""
    return {ENVIRONMENT_TIERS[m.group(1).upper()] for m in _TIER_TOKEN_RE.finditer(text)}


def _foreign_tiers_in(text: str) -> set[str]:
    """Unambiguous environment tiers named in a definition (excludes "test")."""
    return {_FOREIGN_TIER_TOKENS[m.group(1).upper()]
            for m in _FOREIGN_TIER_RE.finditer(text)}


@check(
    id="WS-ENV-ISOLATION", ref="1.1.3",
    title="Environment isolation enforced (Dev / QA / Prod workspaces have no shared mutable artifacts or cross-env dependencies)",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.WORKSPACE, Resource.PIPELINE_DEFINITIONS], required=True,
)
def environment_isolation(ctx: CheckContext) -> Verdict:
    """No pipeline in this workspace names an environment other than its own.

    The workspace declares its tier in its name (Dev / QA / UAT / Prod ...). Its
    pipelines are then read whole — activity names, paths, connection strings,
    expressions — for a *different* tier's name. A Prod pipeline that reaches
    into a ``_DEV`` path is a cross-environment dependency: promoting it changes
    behaviour, and a Dev change can move Prod data.

    ``test``/``tst`` are excluded from the definition scan: inside a pipeline
    those words almost always mean "test connection" or "run the tests", not the
    Test environment. Only names decide at all, because a workspace reference in
    a definition is a GUID that cannot be resolved to a workspace from a
    single-workspace crawl. A workspace whose own name declares no tier is N/A,
    not a failure.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable("Pipeline definitions could not be read from Fabric")

    own_tiers = _tiers_in(ctx.workspace.name)
    if not own_tiers:
        return not_applicable(
            f"Workspace name '{ctx.workspace.name}' declares no environment tier "
            "(Dev/QA/UAT/Prod...), so a cross-environment reference cannot be identified"
        )

    pipelines = ctx.workspace.pipelines
    if not pipelines:
        return not_applicable(
            "Workspace has no pipeline definitions to inspect for cross-environment references"
        )

    offenders: dict[str, set[str]] = {}
    for name, definition in pipelines.items():
        foreign = _foreign_tiers_in(json.dumps(definition)) - own_tiers
        if foreign:
            offenders[name] = foreign

    own = "/".join(sorted(own_tiers))
    if not offenders:
        return covered(len(pipelines), len(pipelines),
                       f"All {len(pipelines)} pipeline(s) in this {own} workspace "
                       f"reference only {own} resources")

    detail = "; ".join(
        f"'{name}' names {'/'.join(sorted(tiers))}"
        for name, tiers in sorted(offenders.items())[:3]
    )
    return covered(
        len(pipelines) - len(offenders), len(pipelines),
        f"{len(offenders)} of {len(pipelines)} pipeline(s) in this {own} workspace "
        f"reference another environment: {detail}",
    )


# =============================================================================
# 10.5.1 — Data Activator triggers for critical events
# =============================================================================

#: Fabric item types for Data Activator. The REST API reports ``Reflex``;
#: ``Activator`` is accepted for forward compatibility with the newer name.
ACTIVATOR_TYPES: frozenset[str] = frozenset({"Reflex", "Activator"})

#: Items that actually run, refresh, or stream — the things a critical event can
#: be raised about. A workspace with none of them has nothing to trigger on.
EVENT_SOURCE_TYPES: frozenset[str] = frozenset({
    "DataPipeline", "Notebook", "Dataflow", "SparkJobDefinition",
    "SemanticModel", "Eventstream", "Eventhouse", "KQLDatabase",
    "Lakehouse", "Warehouse", "MirroredDatabase",
})


@check(
    id="WS-ACTIVATOR", ref="10.5.1",
    title="Data Activator (or equivalent) triggers configured for critical events",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.ITEMS], required=True,
)
def activator_configured(ctx: CheckContext) -> Verdict:
    """An operational workspace owns a Data Activator item to react to critical events.

    Item-inventory level, and deliberately distinct from ``PL-NOTIFY`` (2.4.5),
    which asks whether one *pipeline* ends in a notification activity. This asks
    whether the workspace has event-driven alerting at all — a Reflex/Activator
    item watching its data and jobs. The rule counts items, so it is unaffected
    by whether a pipeline definition could be read.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    sources = [i for i in ctx.workspace.items if i.type in EVENT_SOURCE_TYPES]
    activators = [i for i in ctx.workspace.items if i.type in ACTIVATOR_TYPES]
    if not sources and not activators:
        return not_applicable(
            "Workspace holds no pipeline, notebook, dataset, or stream, so there "
            "is no critical event for an Activator to trigger on"
        )
    if activators:
        names = ", ".join(sorted(i.display_name or i.id for i in activators)[:3])
        return binary(True, f"{len(activators)} Data Activator item(s) present ({names}) "
                            f"watching {len(sources)} operational item(s)")
    return binary(False, f"No Data Activator (Reflex) item in a workspace with "
                         f"{len(sources)} operational item(s) — critical events raise no trigger")


# =============================================================================
# 11.1.4 — branching strategy
# =============================================================================

#: The integration branch a promoted (QA / Prod) workspace should track.
_TRUNK_BRANCH = re.compile(r"^(?:main|master|trunk)$", re.IGNORECASE)
#: A hardened, promotion-oriented branch — ``release``, ``release/2.1``, ``hotfix/x``.
_RELEASE_BRANCH = re.compile(r"^(?:release|releases|hotfix|rel)(?:[/-]|$)", re.IGNORECASE)
#: The shared integration branch of a git-flow style strategy.
_DEVELOP_BRANCH = re.compile(r"^(?:dev|develop|development|integration)$", re.IGNORECASE)
#: An isolated working branch.
_FEATURE_BRANCH = re.compile(r"^(?:feature|features|feat|bugfix|fix|users|user|topic)[/-]",
                             re.IGNORECASE)

#: Tiers that must not be built from an isolated working branch.
_PROMOTED_TIERS: frozenset[str] = frozenset({"QA", "UAT", "Staging", "Pre-Prod", "Prod"})


@check(
    id="WS-BRANCH", ref="11.1.4",
    title="Branching strategy defined (feature branches, main, release)",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.GIT], required=True,
)
def branching_strategy(ctx: CheckContext) -> Verdict:
    """The workspace tracks a branch that names its role in a defined strategy.

    Read from the Git connection Fabric already reports — the branch this
    workspace is bound to. A named strategy shows up in the branch name:
    ``main``/``master`` (integration), ``release/*`` or ``hotfix/*``
    (promotion), ``develop`` (shared integration), ``feature/*`` (isolated
    work). The workspace's own environment tier then has to agree with it: a
    Prod workspace built from ``feature/anmol-wip`` has no promotion gate at
    all, while a Dev workspace bound directly to ``main`` has no feature
    isolation.

    Branch *policies* — PR reviews, minimum reviewers — live in the Git provider,
    not in Fabric, and are out of this check's reach.
    """
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable("Git connection state could not be read from Fabric")
    if not ctx.workspace.git_connected:
        return not_applicable(
            "Workspace is not connected to Git, so it tracks no branch "
            "(WS-GIT / 11.1.1 covers the connection itself)"
        )
    branch = str(ctx.workspace.git_details.get("branch") or "").strip()
    if not branch:
        return not_applicable(
            "Workspace is Git-connected but Fabric reported no branch name, so the "
            "branching strategy cannot be read"
        )

    if _TRUNK_BRANCH.match(branch):
        role = "trunk"
    elif _RELEASE_BRANCH.match(branch):
        role = "release"
    elif _DEVELOP_BRANCH.match(branch):
        role = "develop"
    elif _FEATURE_BRANCH.match(branch):
        role = "feature"
    else:
        role = ""

    tiers = _tiers_in(ctx.workspace.name)
    promoted = bool(tiers & _PROMOTED_TIERS)
    development = "Dev" in tiers

    if not role:
        return graded(0, f"Branch '{branch}' matches no branching-strategy convention "
                         f"(main/master, release/*, develop, feature/*) — the workspace "
                         f"is built from an ad-hoc branch")
    if promoted and role == "feature":
        return graded(0, f"{'/'.join(sorted(tiers))} workspace is built from feature "
                         f"branch '{branch}' — a promoted environment must track "
                         f"main/master or release/*")
    if promoted and role == "develop":
        return graded(1, f"{'/'.join(sorted(tiers))} workspace tracks integration branch "
                         f"'{branch}' rather than main/master or release/*")
    if development and role == "trunk":
        return graded(2, f"Dev workspace tracks trunk branch '{branch}' directly — the "
                         f"branch role is defined, but feature-branch isolation is not in use")
    where = f"{'/'.join(sorted(tiers))} workspace" if tiers else "Workspace"
    return graded(3, f"{where} tracks '{branch}', a {role} branch of a defined "
                     f"branching strategy")


# =============================================================================
# 11.4.2 — warehouse deployment is automated and environment-parameterized
# =============================================================================

#: Schema/deployment T-SQL — the statements that *change the warehouse*, as
#: opposed to the DML a load runs.
_WAREHOUSE_DDL = re.compile(
    r"\b(?:CREATE|ALTER|DROP)\s+(?:OR\s+ALTER\s+)?"
    r"(?:TABLE|VIEW|PROC|PROCEDURE|FUNCTION|SCHEMA|INDEX|DATABASE)\b",
    re.IGNORECASE,
)
#: A pipeline expression — the only way a Fabric pipeline injects an
#: environment-specific value into a script instead of baking it in.
_PIPELINE_EXPRESSION = re.compile(
    r"@\{|@pipeline\s*\(|@variables\s*\(|@parameters\s*\(|@activity\s*\(|"
    r"@dataset\s*\(|@linkedService\s*\(|@item\s*\(",
    re.IGNORECASE,
)


@check(
    id="WS-WH-DEPLOY", ref="11.4.2",
    title="Warehouse deployments are automated and environment-parameterized (not manual T-SQL in Prod)",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.WORKSPACE, Resource.ITEMS, Resource.GIT,
              Resource.PIPELINE_DEFINITIONS], required=True,
)
def warehouse_deployment_automated(ctx: CheckContext) -> Verdict:
    """Warehouse schema changes ship through automation, parameterized per environment.

    Two halves, both readable from Fabric:

    * *automated* — the workspace is Git-connected or assigned to a deployment
      pipeline, so a change has a path other than someone opening a query editor
      against Prod;
    * *parameterized* — the schema T-SQL that pipelines do run (``CREATE`` /
      ``ALTER`` / ``DROP`` of a table, view, or procedure, read from Script
      activities) resolves its environment-specific values from pipeline
      expressions rather than literals.

    A warehouse with no in-pipeline DDL is judged on automation alone. Stored
    procedures defined inside the Warehouse are not readable through any API, so
    their absence is never counted against the workspace.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [i for i in ctx.workspace.items if i.type == "Warehouse"]
    if not warehouses:
        return not_applicable("Workspace holds no Warehouse item")
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return not_applicable(
            f"{len(warehouses)} Warehouse item(s) present, but pipeline definitions "
            "could not be read, so deployment T-SQL cannot be inspected"
        )

    git_ok = ctx.workspace.has(Resource.GIT) and ctx.workspace.git_connected
    deploy_ok = ctx.workspace.deployment_pipeline
    automated = git_ok or deploy_ok
    if git_ok and deploy_ok:
        how = "Git source control and a deployment pipeline"
    elif git_ok:
        how = "Git source control"
    else:
        how = "a deployment pipeline"

    ddl_pipelines: list[str] = []
    parameterized: list[str] = []
    for name, definition in ctx.workspace.pipelines.items():
        sql = script_sql(definition)
        if not sql or not _WAREHOUSE_DDL.search(sql):
            continue
        ddl_pipelines.append(name)
        declared = bool((definition.get("properties") or {}).get("parameters"))
        if declared or _PIPELINE_EXPRESSION.search(sql):
            parameterized.append(name)

    if not ddl_pipelines:
        return binary(
            automated,
            f"No schema T-SQL runs from a pipeline; warehouse changes ship through {how}"
            if automated else
            "No schema T-SQL runs from a pipeline and the workspace has neither Git "
            "nor a deployment pipeline — warehouse changes can only be applied by hand",
        )

    n_ddl, n_param = len(ddl_pipelines), len(parameterized)
    detail = (f"{n_param} of {n_ddl} pipeline(s) running warehouse DDL are "
              f"environment-parameterized")
    if automated and n_param == n_ddl:
        return graded(3, f"Warehouse changes ship through {how}; {detail}")
    if automated and n_param:
        return graded(2, f"Warehouse changes ship through {how}, but only {detail}")
    if automated:
        return graded(1, f"Warehouse changes ship through {how}, but none of the "
                         f"{n_ddl} pipeline(s) running DDL parameterize it — the same "
                         f"literal T-SQL is applied in every environment")
    if n_param == n_ddl:
        return graded(1, f"{detail}, but the workspace has neither Git nor a deployment "
                         f"pipeline, so nothing gates how that DDL reaches Prod")
    return graded(0, f"No Git and no deployment pipeline, and only {detail} — warehouse "
                     f"schema changes are effectively manual T-SQL")


# =============================================================================
# 11.4.5 — semantic model deployment is versioned and pipeline-orchestrated
# =============================================================================

#: Activity types that refresh a semantic model from a pipeline.
SEMANTIC_REFRESH_TYPES: frozenset[str] = frozenset({
    "PBISemanticModelRefresh", "SemanticModelRefresh", "DatasetRefresh", "RefreshDataset",
})


@check(
    id="WS-SM-DEPLOY", ref="11.4.5",
    title="Semantic model deployment is versioned and part of the Data Consumption pipeline",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.WORKSPACE, Resource.ITEMS, Resource.GIT,
              Resource.PIPELINE_DEFINITIONS], required=True,
)
def semantic_model_deployment(ctx: CheckContext) -> Verdict:
    """Semantic models are source-controlled and refreshed by an orchestrated pipeline.

    *Versioned* is Git connection or deployment-pipeline assignment — the model's
    definition has a history and a promotion path rather than being edited in
    place. *Part of the pipeline* is a semantic-model refresh activity in one of
    the workspace's pipelines, so the model is rebuilt as a step of the data
    flow instead of on an independent schedule.

    When the workspace holds no pipelines at all, the refresh is assumed to be
    orchestrated from the workspace that owns the flow and only versioning is
    judged — a reporting workspace is not marked down for a pipeline that
    correctly lives elsewhere.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    models = [i for i in ctx.workspace.items if i.type == "SemanticModel"]
    if not models:
        return not_applicable("Workspace holds no semantic model")
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable(
            f"{len(models)} semantic model(s) present, but the Git connection state "
            "could not be read, so versioning cannot be judged"
        )

    versioned = ctx.workspace.git_connected or ctx.workspace.deployment_pipeline
    version_note = ("Git-connected" if ctx.workspace.git_connected
                    else "assigned to a deployment pipeline" if ctx.workspace.deployment_pipeline
                    else "neither Git-connected nor assigned to a deployment pipeline")

    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS) or not ctx.workspace.pipelines:
        return binary(
            versioned,
            f"{len(models)} semantic model(s); workspace is {version_note}. No pipeline "
            f"in this workspace, so refresh orchestration is assumed to live with the "
            f"flow that owns it and is not judged here",
        )

    orchestrating = [
        name for name, definition in ctx.workspace.pipelines.items()
        if any(a.get("type") in SEMANTIC_REFRESH_TYPES for a in walk_activities(definition))
    ]
    total = len(ctx.workspace.pipelines)
    if versioned and orchestrating:
        return graded(3, f"{len(models)} semantic model(s): workspace is {version_note} "
                         f"and {len(orchestrating)} of {total} pipeline(s) refresh a "
                         f"semantic model as a pipeline step")
    if versioned:
        return graded(2, f"{len(models)} semantic model(s): workspace is {version_note}, "
                         f"but none of its {total} pipeline(s) refresh a semantic model — "
                         f"deployment is versioned but not part of the data pipeline")
    if orchestrating:
        return graded(1, f"{len(models)} semantic model(s): {len(orchestrating)} of {total} "
                         f"pipeline(s) refresh a semantic model, but the workspace is "
                         f"{version_note} — the model's definition is not versioned")
    return graded(0, f"{len(models)} semantic model(s): workspace is {version_note} and no "
                     f"pipeline refreshes a semantic model — deployment is neither "
                     f"versioned nor orchestrated")


# =============================================================================
# 11.5.1 — unit tests for critical transformation logic
# =============================================================================

#: The word "test" standing alone or opening a CamelCase name — ``TestSalesLoad``,
#: ``NB_unit_test``, and ``Run Unit Tests`` match; ``Latest_Load``, ``Contest``,
#: and ``Tested_Rows`` do not. The case-insensitivity is scoped to the word so
#: the trailing ``[A-Z0-9]`` boundary stays case-*sensitive* — that boundary is
#: what separates ``TestSales`` from ``Tested``.
_TEST_NAME_RE = re.compile(
    r"(?:^|[_\-\s.])(?i:unit[_\-\s]?)?(?i:tests?|testing)(?=$|[_\-\s.]|[A-Z0-9])"
)
#: A real testing framework, or a test function/class declaration. Matched
#: against *executable* code only, so a commented-out import proves nothing.
_TEST_FRAMEWORK_RE = re.compile(
    r"\bimport\s+unittest\b|\bfrom\s+unittest\b|unittest\.TestCase|unittest\.main\s*\(|"
    r"\bimport\s+pytest\b|\bfrom\s+pytest\b|@pytest\.|pytest\.main\s*\(|"
    r"\bimport\s+nutter\b|\bfrom\s+nutter\b|"
    r"\bfrom\s+chispa\b|\bimport\s+chispa\b|assert_df_equality|assertDataFrameEqual|"
    r"great_expectations|\bimport\s+soda\b|\bimport\s+deequ\b|pydeequ|"
    r"^\s*def\s+test_\w+\s*\(|^\s*class\s+Test\w*\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
#: The notebook actually transforms data — i.e. it has logic worth unit testing.
_TRANSFORM_WRITE_RE = re.compile(
    r"\.write\b|saveAsTable\s*\(|\bINSERT\s+INTO\b|\bMERGE\s+INTO\b|"
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\b|\.saveAsTable\b",
    re.IGNORECASE,
)


@check(
    id="WS-UNIT-TESTS", ref="11.5.1",
    title="Unit tests exist for critical transformation logic",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS], required=True,
)
def unit_tests_exist(ctx: CheckContext) -> Verdict:
    """A workspace that transforms data also holds something that tests the transform.

    A test asset is a notebook whose *name* marks it as a test, or whose
    executable code uses a testing framework (``unittest``, ``pytest``,
    ``chispa``, ``nutter``, Great Expectations, Deequ) or declares a ``test_``
    function / ``Test`` class. A pipeline activity named as a test counts too,
    since a test notebook is usually invoked from one.

    Deliberately not satisfied by a bare ``assert`` in load code: asserting a row
    count on production data is a data-quality gate, not a unit test of the
    transformation logic. Comments are stripped first, so a commented-out
    ``import pytest`` proves nothing.
    """
    if not ctx.workspace.has(Resource.NOTEBOOK_DEFINITIONS):
        return not_applicable("Notebook definitions could not be read from Fabric")

    notebooks = ctx.workspace.notebooks
    if not notebooks:
        return not_applicable(
            "Workspace has no notebook definitions, so there is no transformation "
            "logic here to unit test"
        )

    transforming: list[str] = []
    test_notebooks: list[str] = []
    for name, definition in notebooks.items():
        code = executable_code(definition)
        if _TEST_NAME_RE.search(name) or _TEST_FRAMEWORK_RE.search(code):
            test_notebooks.append(name)
        elif _TRANSFORM_WRITE_RE.search(code):
            transforming.append(name)

    test_activities: list[str] = []
    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        for pipeline_name, definition in ctx.workspace.pipelines.items():
            for activity in walk_activities(definition):
                if _TEST_NAME_RE.search(str(activity.get("name") or "")):
                    test_activities.append(f"{pipeline_name}/{activity.get('name')}")

    if not transforming and not test_notebooks:
        return not_applicable(
            f"None of the {len(notebooks)} notebook(s) writes a table, so the "
            "workspace holds no transformation logic to unit test"
        )

    if test_notebooks or test_activities:
        found = ", ".join(sorted(test_notebooks + test_activities)[:3])
        return binary(True, f"{len(test_notebooks)} test notebook(s) and "
                            f"{len(test_activities)} test activity(ies) cover "
                            f"{len(transforming)} transformation notebook(s): {found}")
    return binary(False, f"{len(transforming)} transformation notebook(s) write tables, "
                         f"but no test notebook, test framework, or test activity was "
                         f"found anywhere in the workspace")
