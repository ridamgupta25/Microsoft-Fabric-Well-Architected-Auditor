"""Operations & Reliability · Data Operations — workspace ops hygiene.

Naming, source control, and promotion gating for the operational estate.
"""
from __future__ import annotations

import re

from auditfast.core.check.helpers import Verdict, binary, graded, not_applicable
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
