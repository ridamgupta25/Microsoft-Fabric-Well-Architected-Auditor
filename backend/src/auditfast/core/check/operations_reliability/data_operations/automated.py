"""Operations & Reliability · Data Operations — workspace ops hygiene.

Naming, source control, promotion gating, environment isolation, and the
deployment/test posture of the operational estate.
"""
from __future__ import annotations

import json
import re

from auditfast.core.check._notebook import executable_code
from auditfast.core.check._pipeline import script_sql, walk_activities
from auditfast.core.check.helpers import Verdict, binary, covered, graded, not_applicable, note
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.LOW,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    requires=[Resource.WORKSPACE], required=True,
)
def deployment_pipeline(ctx: CheckContext) -> Verdict:
    """The workspace is assigned to a deployment pipeline gating promotion."""
    ok = ctx.workspace.deployment_pipeline
    return binary(ok, "Assigned to a deployment pipeline" if ok
                  else "No deployment pipeline assigned")


# =============================================================================
# 11.1.2 — every artifact type is actually covered by source control
# =============================================================================

#: Item types Fabric Git integration serialises into the connected repository.
#: A workspace can be Git-connected and still leave artifacts outside version
#: control, because only supported types are written to the repo.
GIT_TRACKED_TYPES: frozenset[str] = frozenset({
    "DataPipeline", "Notebook", "SemanticModel", "Report", "PaginatedReport",
    "Lakehouse", "Warehouse", "Environment", "SparkJobDefinition", "Dataflow",
    "KQLDatabase", "KQLQueryset", "KQLDashboard", "Eventhouse", "Eventstream",
    "MirroredDatabase", "Reflex", "SQLDatabase", "Datamart",
})

#: Types Fabric creates automatically alongside another item. They carry no
#: independent definition, so their absence from the repo is not a gap.
GIT_DERIVED_TYPES: frozenset[str] = frozenset({"SQLEndpoint"})


@check(
    id="WS-GIT-COVERAGE", ref="11.1.2",
    title="All pipelines, notebooks, semantic models, and Warehouse artifacts source-controlled",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.GIT, Resource.ITEMS], required=True,
)
def git_covers_every_artifact(ctx: CheckContext) -> Verdict:
    """Source control reaches every artifact, not merely the workspace.

    Distinct from ``WS-GIT`` (11.1.1), which asks only whether a Git connection
    exists. A workspace can be connected and still leave artifacts unversioned,
    because Fabric serialises only the item types its Git integration supports —
    so this compares what the workspace *holds* against what the repository can
    actually receive.

    Auto-created items (a Lakehouse's SQL endpoint) are excluded: they have no
    independent definition, so their absence from the repo is not a gap. Whether
    the repository is *current* is not readable — Fabric reports the connection,
    not the sync state — and the evidence says so rather than implying it.
    """
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable("Git connection state could not be read from Fabric")
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    artifacts = [i for i in ctx.workspace.items if i.type not in GIT_DERIVED_TYPES]
    if not artifacts:
        return not_applicable(
            "Workspace holds no artifact that source control could cover"
        )

    if not ctx.workspace.git_connected:
        return binary(
            False,
            f"Workspace is not connected to Git, so none of its {len(artifacts)} "
            f"artifact(s) are source-controlled (WS-GIT / 11.1.1 covers the "
            f"connection itself)",
        )

    tracked = [i for i in artifacts if i.type in GIT_TRACKED_TYPES]
    untracked = sorted({i.type for i in artifacts if i.type not in GIT_TRACKED_TYPES})
    detail = (f"{len(tracked)} of {len(artifacts)} artifact(s) are of a type Fabric "
              f"Git integration serialises")
    if untracked:
        detail += f"; outside source control: {', '.join(untracked)}"
    detail += (". Fabric reports the connection, not the sync state, so whether the "
               "repository is up to date is not verified here.")
    return covered(len(tracked), len(artifacts), detail)


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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.ITEMS, Resource.ACTIVATOR_DEFINITIONS],
    required=True,
)
def activator_configured(ctx: CheckContext) -> Verdict:
    """The workspace has *some* event-driven alerting on its operational items.

    **Presence is not enough.** An Activator/Reflex item that was created but
    carries no rule triggers nothing, so the check reads the Activator's
    definition and credits it only when it holds a live rule. A pipeline ending
    in a Teams/Outlook activity, which Microsoft's "create alerts for pipeline
    runs" guidance recommends, is accepted as the documented alternative.

    **What it cannot determine.** Whether the rule's threshold is the *right* one,
    whether the alert routes to someone who acts on it, and whether
    *scheduled-job failure notifications* are switched on - that setting is not
    exposed on the items listing. An Activator whose definition could not be read
    is reported as *unverified* (N/A), never as a definite absence.
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

    # An Activator with a live rule is the strongest evidence. Read the parsed
    # rule counts from each Activator's definition.
    summaries = [ctx.workspace.activators.get(i.display_name or i.id) for i in activators]
    summaries = [s for s in summaries if s]
    active_rules = sum(s.get("active_rules", 0) for s in summaries)
    total_rules = sum(s.get("rules", 0) for s in summaries)
    if activators and active_rules:
        names = ", ".join(sorted(i.display_name or i.id for i in activators)[:3])
        return binary(True, f"{len(activators)} Data Activator item(s) with {active_rules} active "
                            f"rule(s) watching {len(sources)} operational item(s) ({names})")

    # No live Activator rule — a pipeline that ends in a Teams/Outlook
    # notification is the alternative Microsoft documents, and counts just as well.
    notifying = _pipelines_with_notification(ctx)
    if notifying:
        return binary(
            True,
            f"No live Data Activator rule, but {len(notifying)} pipeline(s) alert through a "
            f"Teams/Outlook/webhook activity ({', '.join(notifying[:3])}) - the "
            f"alternative Microsoft's pipeline-alerting guidance recommends"
        )

    # Activators exist but their rules could not be read — cannot judge the depth,
    # so this is unverified, not a definite gap.
    if activators and not ctx.workspace.has(Resource.ACTIVATOR_DEFINITIONS):
        names = ", ".join(sorted(i.display_name or i.id for i in activators)[:3])
        return not_applicable(
            f"{len(activators)} Data Activator item(s) present ({names}) but their rule "
            f"definitions could not be read (getDefinition needs Item.ReadWrite), and no "
            f"pipeline notification was found - trigger configuration is unverified"
        )

    # Activators exist and were readable but carry no live rule — the empty-item gap.
    if activators:
        names = ", ".join(sorted(i.display_name or i.id for i in activators)[:3])
        state = "every rule is paused" if total_rules else "no rules are configured"
        return binary(
            False,
            f"{len(activators)} Data Activator item(s) present ({names}) but {state}, and no "
            f"pipeline ends in a Teams/Outlook/webhook notification - {len(sources)} operational "
            f"item(s) have no live critical-event trigger"
        )

    return binary(
        False,
        f"No event-driven alerting found for {len(sources)} operational item(s): no "
        f"Data Activator item, and no pipeline ends in a Teams/Outlook/webhook "
        f"notification. Fabric's scheduled-job failure emails are not readable from "
        f"the items listing, so confirm those separately before treating this as a gap"
    )


def _pipelines_with_notification(ctx: CheckContext) -> list[str]:
    """Names of pipelines that end in a notification activity (empty when unreadable).

    The vocabulary is duplicated from ``PL-NOTIFY`` (ref 2.4.5) rather than
    imported: one check package importing another's private constants couples
    them, and the loader imports each leaf module independently.
    """
    if not ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        return []
    out: list[str] = []
    for name, definition in (ctx.workspace.pipelines or {}).items():
        for activity in walk_activities(definition):
            activity_type = activity.get("type")
            if activity_type in _ALERT_ACTIVITY_TYPES or (
                activity_type in _ALERT_CALL_TYPES
                and _ALERT_NAME_RE.search(activity.get("name", ""))
            ):
                out.append(name)
                break
    return sorted(out)


#: Activity types that notify a person directly.
_ALERT_ACTIVITY_TYPES = frozenset({
    "Teams", "Office365Outlook", "Outlook365", "SendEmail", "WebHook",
})
#: Generic call activities that only count as an alert when their *name* says so.
_ALERT_CALL_TYPES = frozenset({"Web", "WebActivity", "AzureFunctionActivity", "Function"})
_ALERT_NAME_RE = re.compile(r"notif|alert|email|teams", re.IGNORECASE)


# =============================================================================
# 11.1.4 — branching strategy
# =============================================================================

#: The integration branch a promoted (QA / Prod) workspace should track.
_TRUNK_BRANCH = re.compile(r"^(?:main|master|trunk)$", re.IGNORECASE)
#: A hardened, promotion-oriented branch — ``release``, ``release/2.1``, ``hotfix/x``.
_RELEASE_BRANCH = re.compile(r"^(?:release|releases|hotfix|rel)(?:[/-]|$)", re.IGNORECASE)
#: The shared integration branch of a git-flow style strategy. Teams commonly
#: qualify it with a repository/workspace name, such as ``DEV_FABRIC``.
_DEVELOP_BRANCH = re.compile(
    r"^(?:dev|develop|development|integration)(?:[/_-]|$)",
    re.IGNORECASE,
)
#: An isolated working branch.
_FEATURE_BRANCH = re.compile(r"^(?:feature|features|feat|bugfix|fix|users|user|topic)[/-]",
                             re.IGNORECASE)

#: Tiers that must not be built from an isolated working branch.
_PROMOTED_TIERS: frozenset[str] = frozenset({"QA", "UAT", "Staging", "Pre-Prod", "Prod"})


@check(
    id="WS-BRANCH", ref="11.1.4",
    title="Branching strategy defined (feature branches, main, release)",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
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
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.NOTEBOOK_DEFINITIONS, Resource.PIPELINE_DEFINITIONS], required=True,
)
def unit_tests_exist(ctx: CheckContext) -> Verdict:
    """A workspace that transforms data holds tests in proportion to that logic.

    A test asset is a notebook whose *name* marks it as a test, or whose
    executable code uses a testing framework (``unittest``, ``pytest``,
    ``chispa``, ``nutter``, Great Expectations, Deequ) or declares a ``test_``
    function / ``Test`` class. A pipeline activity named as a test counts too,
    since a test notebook is usually invoked from one.

    Scored as *coverage*, not presence: the point asks that critical
    transformation logic is tested, so nine test notebooks beside sixty untested
    transforms is a partial result. Deliberately not satisfied by a bare
    ``assert`` in load code: asserting a row count on production data is a
    data-quality gate, not a unit test of the transformation logic. Comments are
    stripped first, so a commented-out ``import pytest`` proves nothing.
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

    if not test_notebooks and not test_activities:
        return binary(False, f"{len(transforming)} transformation notebook(s) write tables, "
                             f"but no test notebook, test framework, or test activity was "
                             f"found anywhere in the workspace")

    # Coverage, not mere presence. The point asks that critical transformation
    # logic *is* tested, so a handful of test notebooks beside a large body of
    # untested transforms is a partial result, not a pass. One test asset is
    # credited per transformation notebook; the helper clamps the ratio, so a
    # workspace with more tests than transforms is simply fully covered.
    tests = len(test_notebooks) + len(test_activities)
    found = ", ".join(sorted(test_notebooks + test_activities)[:3])
    return covered(
        tests, len(transforming),
        f"{len(test_notebooks)} test notebook(s) and {len(test_activities)} test "
        f"activity(ies) against {len(transforming)} transformation notebook(s): {found}",
    )


# =============================================================================
# 9.2.4 — critical Gold data has a secondary copy or export
# =============================================================================

#: Stores whose content is "Gold" in the sense the point means — the modelled,
#: consumer-facing data whose loss would be felt immediately.
_GOLD_STORE_TYPES: frozenset[str] = frozenset({"Warehouse", "Lakehouse", "SQLDatabase"})

#: Fabric's own replication: a mirrored database/warehouse *is* a second,
#: continuously maintained copy.
_MIRROR_TYPES: frozenset[str] = frozenset(
    {"MirroredDatabase", "MirroredWarehouse", "MirroredAzureDatabricksCatalog"}
)

#: Name tokens that mark a store as the Gold/curated layer.
_GOLD_NAME_TOKENS: frozenset[str] = frozenset({"GOLD", "CURATED", "PRESENTATION", "MART", "DW", "EDW"})

#: A Copy sink that writes *outside* OneLake — a file drop, a blob container, an
#: object store. These are the sink/store-settings type names Fabric emits.
_EXTERNAL_SINK_TYPE = re.compile(
    r"AzureBlobStorage|AzureBlobFS|AzureDataLakeStore|AmazonS3|GoogleCloudStorage|"
    r"FileServer|Sftp|Ftp|OracleCloudStorage|AzureFileStorage|"
    r"(?:DelimitedText|Parquet|Json|Avro|Orc|Binary)(?:Sink|WriteSettings)",
    re.IGNORECASE,
)
#: Sinks that stay inside the workspace — matched first so a Lakehouse Parquet
#: write is not mistaken for an export.
_INTERNAL_SINK_TYPE = re.compile(r"Lakehouse|Warehouse|DataWarehouse", re.IGNORECASE)


def _copy_sink_types(definition: dict) -> set[str]:
    """The sink type names used by the Copy activities of one pipeline."""
    found: set[str] = set()
    for activity in walk_activities(definition):
        if activity.get("type") != "Copy":
            continue
        sink = (activity.get("typeProperties") or {}).get("sink")
        if not isinstance(sink, dict):
            continue
        for value in (sink.get("type"), (sink.get("storeSettings") or {}).get("type")
                      if isinstance(sink.get("storeSettings"), dict) else None,
                      (sink.get("formatSettings") or {}).get("type")
                      if isinstance(sink.get("formatSettings"), dict) else None):
            if isinstance(value, str) and value:
                found.add(value)
    return found


@check(
    id="WS-GOLD-SECONDARY-COPY", ref="9.2.4",
    title="Critical Gold-layer data has a secondary copy or export mechanism",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.HIGH,
    layers=(Layer.OPERATIONS,),
    requires=[Resource.ITEMS, Resource.PIPELINE_DEFINITIONS, Resource.SHORTCUTS],
    required=True,
)
def gold_data_has_a_secondary_copy(ctx: CheckContext) -> Verdict:
    """Something other than the primary store holds, or can reproduce, the Gold data.

    Severity is High rather than the checklist's Medium: the whole point is the
    scenario where the primary store is gone, and a workspace that fails this has
    no answer to it at all.

    **What it can determine.** Three readable mechanisms, from data already
    crawled. A **mirrored** database/warehouse item is a maintained second copy.
    A pipeline **Copy activity whose sink is external** to OneLake (blob / ADLS /
    S3 / file share, or a file-format sink written through external store
    settings) is an export. A **shortcut to external storage** means the data
    also exists — or is referenced — outside this workspace.

    **What it cannot.** Confirm the copy is current, complete, restorable, or
    that anyone has ever tested restoring it; and it cannot see platform-level
    backup (OneLake soft delete, capacity-level recovery), which is not exposed
    per workspace. So a pass means "an export mechanism exists", never "the
    recovery plan works". A workspace holding no Gold-shaped store is N/A.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    items = ctx.workspace.items
    stores = [i for i in items if i.type in _GOLD_STORE_TYPES]
    mirrors = sorted({i.display_name or i.id for i in items if i.type in _MIRROR_TYPES})
    if not stores and not mirrors:
        return not_applicable(
            "Workspace holds no Warehouse, Lakehouse or SQL database, so there is no "
            "Gold-layer data here to copy or export"
        )

    gold = sorted({
        i.display_name or i.id for i in stores
        if i.type == "Warehouse" or _name_tokens(i.display_name or "") & _GOLD_NAME_TOKENS
    })
    described = (f"{len(gold)} Gold/Warehouse store(s) ({', '.join(gold[:3])})" if gold
                 else f"{len(stores)} store(s)")

    mechanisms: list[str] = []
    exporter_count = 0
    if mirrors:
        mechanisms.append(f"mirrored item(s): {', '.join(mirrors[:3])}")

    if ctx.workspace.has(Resource.PIPELINE_DEFINITIONS):
        exporters: list[str] = []
        for name, definition in (ctx.workspace.pipelines or {}).items():
            sink_types = _copy_sink_types(definition)
            external = sorted({
                t for t in sink_types
                if _EXTERNAL_SINK_TYPE.search(t) and not _INTERNAL_SINK_TYPE.search(t)
            })
            if external:
                exporters.append(f"{name} -> {'/'.join(external[:2])}")
        if exporters:
            exporter_count = len(exporters)
            mechanisms.append(f"{len(exporters)} pipeline(s) copy to external storage: "
                              f"{'; '.join(sorted(exporters)[:2])}")

    if ctx.workspace.has(Resource.SHORTCUTS):
        external_shortcuts = sorted({
            str(s.get("name") or "")
            for entries in (ctx.workspace.shortcuts or {}).values()
            for s in entries
            if isinstance(s, dict)
            and (s.get("target_type") or "").strip().lower() not in ("", "onelake")
        })
        if external_shortcuts:
            mechanisms.append(f"{len(external_shortcuts)} shortcut(s) to storage outside "
                              f"this workspace: {', '.join(external_shortcuts[:3])}")

    if mechanisms:
        # Presence of a mechanism somewhere is not per-store coverage: two mirrored
        # databases do not protect eighteen unrelated Warehouses. Matching a mirror
        # to its source is NOT readable — no API reports what a mirror mirrors — so
        # this counts mechanisms against Gold stores rather than pretending to pair
        # them. External shortcuts are deliberately excluded from the count: a
        # workspace can hold hundreds that reference inbound data and protect
        # nothing, so they are reported as context only.
        protective = len(mirrors) + exporter_count
        if gold and protective < len(gold):
            return covered(
                protective, len(gold),
                f"{described}: {protective} export/copy mechanism(s) for {len(gold)} "
                f"Gold/Warehouse store(s) — {'; '.join(mechanisms)}. Which store each "
                f"mechanism protects is not readable, so this counts mechanisms, not "
                f"verified per-store coverage",
            )
        return binary(True, f"{described} have a secondary copy/export mechanism — "
                            f"{'; '.join(mechanisms)}")
    unread = sorted(
        resource.value for resource in (Resource.PIPELINE_DEFINITIONS, Resource.SHORTCUTS)
        if not ctx.workspace.has(resource)
    )
    if unread:
        return not_applicable(
            f"{described} present, but {', '.join(unread)} could not be read, so an export "
            f"mechanism cannot be ruled out"
        )
    return binary(False, f"{described} with no mirrored item, no pipeline export to storage "
                         f"outside OneLake, and no external shortcut — the Gold data exists "
                         f"in exactly one place")


# =============================================================================
# 11.3.1 — Dev / QA / Prod separation, seen from inside one workspace
# =============================================================================


@check(
    id="WS-TIER-DECLARED", ref="11.3.1",
    title="Separate workspaces for Dev, QA, and Production per layer",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.WORKSPACE],
    required=False,
)
def environment_tier_is_declared(ctx: CheckContext) -> Verdict:
    """Reports the tier this workspace declares — the estate-wide question is not scorable here.

    **Why this is unscored (``note``), on purpose.** The point counts *nine
    workspaces*: three layers x three tiers. The engine judges one workspace at a
    time and each audit run targets only the workspaces the reviewer selected, so
    no check body can see whether the other eight exist. Scoring the readable
    fragment would be worse than useless: a correctly-named ``…_PROD``
    workspace would score 3 in an estate that has no Dev at all, and a shared
    Gold Lakehouse — which legitimately carries no tier — would score 0. Both
    verdicts would be confidently wrong, so this check reports the fact and
    leaves the roll-up alone.

    **What it reports.** The environment tier this workspace's name declares
    (Dev / SIT / QA / Test / UAT / Staging / Pre-Prod / Prod), the layer it is
    tagged with, and whether it is assigned to a deployment pipeline — the three
    facts a reviewer needs to assemble the estate-wide picture across the
    workspaces in the report.

    **Related, and genuinely different.** ``WS-NAME`` (IMPL-24) scores naming
    style; ``WS-ENV-ISOLATION`` (1.1.3) scores whether this workspace's pipelines
    reach into another tier; ``WS-DEPLOY`` (11.2.1) scores deployment-pipeline
    assignment. None of them reports the tier itself, which is what the estate
    view needs. The gated roadmap entry ``R-11-3-1`` carries the same ref and
    records what a tenant-admin API would unlock.
    """
    name = ctx.workspace.name
    tokens = _name_tokens(name)
    tier = next((ENVIRONMENT_TIERS[t] for t in sorted(tokens) if t in ENVIRONMENT_TIERS), None)
    layer = ctx.workspace.layer.value
    promotion = ("assigned to a deployment pipeline" if ctx.workspace.deployment_pipeline
                 else "not assigned to a deployment pipeline")
    if tier:
        return note(f"'{name}' declares environment tier '{tier}' (layer: {layer}; "
                    f"{promotion}). Whether a Dev/QA/Prod set exists for this layer can "
                    f"only be judged across workspaces, not from inside one")
    return note(f"'{name}' declares no environment tier in its name (layer: {layer}; "
                f"{promotion}), so a Dev/QA/Prod separation is not expressible from the "
                f"name alone — legitimate for a shared store, a gap for a promotable "
                f"workspace")


# =============================================================================
# 1.1.5 — medallion architecture (Bronze -> Silver -> Gold)
# =============================================================================

#: Name tokens that place a store in a medallion tier, mapped to the tier. Each
#: tier lists the words teams actually use for it, so a ``LH_Raw_Landing`` reads
#: as Bronze and a ``WH_Presentation`` as Gold. Deliberately conservative: a word
#: that means something else as often as it means a tier (``STAGE``, ``FINAL``)
#: is left out rather than guessed at.
MEDALLION_TOKENS: dict[str, str] = {
    "BRONZE": "Bronze", "RAW": "Bronze", "LANDING": "Bronze", "INGEST": "Bronze",
    "INGESTION": "Bronze", "SOURCE": "Bronze",
    "SILVER": "Silver", "CLEANSED": "Silver", "CLEAN": "Silver",
    "CONFORMED": "Silver", "REFINED": "Silver", "ENRICHED": "Silver",
    "GOLD": "Gold", "CURATED": "Gold", "MART": "Gold", "DATAMART": "Gold",
    "PRESENTATION": "Gold", "SERVING": "Gold", "SEMANTIC": "Gold",
}

#: The tiers in the order the architecture flows.
MEDALLION_ORDER: tuple[str, ...] = ("Bronze", "Silver", "Gold")

#: The store type the checklist point asks each tier to be built on. Bronze and
#: Silver are file/Delta workloads (a Lakehouse); Gold is the modelled serving
#: layer the point names as a Warehouse.
MEDALLION_EXPECTED_TYPE: dict[str, str] = {
    "Bronze": "Lakehouse", "Silver": "Lakehouse", "Gold": "Warehouse",
}


def _medallion_tiers(name: str) -> set[str]:
    """Medallion tiers a store or workspace name declares (empty when none)."""
    return {MEDALLION_TOKENS[tok] for tok in _name_tokens(name) if tok in MEDALLION_TOKENS}


@check(
    id="WS-MEDALLION", ref="1.1.5",
    title="Medallion architecture properly implemented (Bronze Lakehouse -> Silver Lakehouse -> Gold Warehouse) with clear layer boundaries",
    pillar=Pillar.RELIABILITY, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.OPERATIONS,), requires=[Resource.WORKSPACE, Resource.ITEMS], required=True,
)
def medallion_architecture(ctx: CheckContext) -> Verdict:
    """The data stores name their medallion tier, and each tier sits on the right store type.

    Two readable facts, both from the item inventory:

    * **the boundary is declared** — the Lakehouses / Warehouses carry a tier
      word in their names (Bronze / Raw / Landing, Silver / Cleansed /
      Conformed, Gold / Curated / Mart / Presentation), so a reader can tell
      which layer a store belongs to. The workspace's own name counts too: an
      estate that puts each tier in its own workspace declares the boundary
      there rather than in the store name;
    * **the tier sits on the store type the point asks for** — Bronze and
      Silver on a Lakehouse, Gold on a Warehouse. A "Gold" Lakehouse is called
      out by name, because the point specifically asks for a Gold Warehouse.

    **Be honest about the signal: names are all there is.** Nothing in the item
    metadata records which store a pipeline writes to, so a perfectly layered
    estate whose stores are called ``LH_One`` and ``LH_Two`` reads here as
    undeclared, and a store called ``Gold`` that in fact holds raw extracts
    reads as Gold. This scores whether the architecture is *expressed* — which
    is what "clear layer boundaries" asks for — not whether data physically
    flows Bronze to Silver to Gold. The evidence says so.

    Reuses the store vocabulary of ``WS-SINGLE-SOURCE`` (ref 1.1.8) — same
    module, same ``DATA_STORE_TYPES`` and token splitter, no cross-pillar
    import. N/A when the workspace holds no data store: a prep or reporting
    workspace has no medallion tier to declare.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    stores = [i for i in ctx.workspace.items if i.type in DATA_STORE_TYPES]
    if not stores:
        return not_applicable(
            "Workspace holds no Lakehouse, Warehouse, or database item, so it "
            "implements no medallion storage tier"
        )

    by_tier: dict[str, list[tuple[str, str]]] = {}
    for item in stores:
        name = item.display_name or item.id
        for tier in _medallion_tiers(name):
            by_tier.setdefault(tier, []).append((name, item.type))

    workspace_tiers = _medallion_tiers(ctx.workspace.name)
    declared = sorted(set(by_tier) | workspace_tiers,
                      key=lambda tier: MEDALLION_ORDER.index(tier))

    if not declared:
        return graded(
            0,
            f"None of the {len(stores)} data store(s) — nor the workspace name — "
            f"names a medallion tier (Bronze/Raw, Silver/Cleansed, Gold/Curated), "
            f"so the layer boundaries are not expressed anywhere a reader can see "
            f"them. Names are the only readable signal for layer intent.",
        )

    misplaced = [
        f"'{name}' is the {tier} tier on a {item_type}, not a "
        f"{MEDALLION_EXPECTED_TYPE[tier]}"
        for tier, entries in by_tier.items()
        for name, item_type in entries
        if item_type != MEDALLION_EXPECTED_TYPE[tier]
        and item_type in {"Lakehouse", "Warehouse"}
    ]

    where = ", ".join(
        f"{tier}: " + (", ".join(sorted(n for n, _ in by_tier.get(tier, [])))
                       or "declared by the workspace name")
        for tier in declared
    )
    caveat = (" Names are the only readable signal for layer intent — no item "
              "metadata records which store a pipeline writes to.")

    if len(declared) < len(MEDALLION_ORDER):
        missing = [t for t in MEDALLION_ORDER if t not in declared]
        return graded(
            1 if len(declared) == 1 else 2,
            f"{len(declared)} of 3 medallion tier(s) are named here ({where}); "
            f"no store or workspace name declares {', '.join(missing)}. A tier held "
            f"in another workspace is not visible from inside this one.{caveat}",
        )

    if misplaced:
        return graded(
            2,
            f"All three medallion tiers are named ({where}), but the progression "
            f"does not land on the store types the standard asks for: "
            f"{'; '.join(sorted(misplaced)[:3])}.{caveat}",
        )
    return graded(
        3,
        f"Bronze -> Silver -> Gold are all named and each sits on the expected "
        f"store type ({where}).{caveat}",
    )
