"""Operations & Reliability · Data Operations — interactive (self-assessed) checks.

Points a machine cannot read from Fabric metadata — SLA targets/monitoring and
historical attainment, alerting, folder-based domain governance, CI/CD tests,
Git-host practice (the repo's files, history and branch policies, none of which
Fabric exposes — it exposes only the *connection*), deployment-rule
configuration, and cross-environment parity (a comparison *between* workspaces,
where each audit run judges one workspace at a time). The reviewer
self-assesses each during the audit and the chosen option's 0-3 score rolls into
the report, per Data Operations workspace — the Azure Well-Architected Review
model. Skipping records N/A and does not score.
"""
from __future__ import annotations

from auditfast.core.check.registry import questionnaire_check
from auditfast.core.enums import Layer, Pillar, Severity
from auditfast.core.models import CheckOption

_LAYERS = (Layer.OPERATIONS, Layer.MIXED)


def _options(partial_guidance: str, no_guidance: str) -> list[CheckOption]:
    """A standard three-point self-assessment: in place / partial / absent."""
    return [
        CheckOption("yes", "Yes — implemented consistently", 3, ""),
        CheckOption("partial", "Partially — some gaps remain", 1, partial_guidance),
        CheckOption("no", "No — not in place", 0, no_guidance),
    ]


questionnaire_check(
    id="WS-DOMAIN-FOLDERS", ref="1.1.4",
    title="Domain segregation via folders (Finance, Sales, etc.) is consistent and applied uniformly across Prep and Store workspaces",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are workspace folders used to segregate domains (Finance, Sales, …) consistently and uniformly across the Prep and Store workspaces?",
    options=_options(
        "Extend the same domain-folder taxonomy to every Prep and Store workspace so the structure is uniform.",
        "Adopt a consistent domain-folder structure (Finance, Sales, …) across all Prep and Store workspaces.",
    ),
)


questionnaire_check(
    id="PL-SLA-MONITORED", ref="9.4.2",
    title="Pipeline completion SLAs set and monitored",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Are completion SLAs defined for critical pipelines and actively monitored against actual run durations?",
    options=_options(
        "Define SLAs for the remaining critical pipelines and wire them into monitoring.",
        "Set completion SLAs for critical pipelines and monitor actual durations against them (e.g. via Data Activator or the Metadata DB).",
    ),
)


questionnaire_check(
    id="PL-SLA-ALERTS", ref="9.4.3",
    title="SLA breach triggers alerts (Data Activator, email, Teams)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Does an SLA breach automatically raise an alert (Data Activator, email, or Teams) to the owning team?",
    options=_options(
        "Extend SLA-breach alerting to all critical pipelines and confirm the alerts reach the owning team.",
        "Configure SLA-breach alerts via Data Activator, email, or Teams so late/failed runs notify the owning team.",
    ),
)


questionnaire_check(
    id="OPS-INTEGRATION-TESTS", ref="11.5.2",
    title="Integration tests validate end-to-end pipeline execution",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Do integration tests validate end-to-end pipeline execution (source → Gold) before release?",
    options=_options(
        "Broaden integration-test coverage to the remaining end-to-end pipeline paths.",
        "Add integration tests that run the pipelines end-to-end and assert the run succeeds before promotion.",
    ),
)


questionnaire_check(
    id="OPS-DATA-VALIDATION-TESTS", ref="11.5.3",
    title="Data validation tests run post-deployment (record counts, schema checks)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question="Do automated data-validation tests (record counts, schema checks) run after each deployment?",
    options=_options(
        "Extend post-deployment validation to all critical tables and schemas.",
        "Add post-deployment data-validation tests (record counts and schema checks) to the release process.",
    ),
)


# -- Git-host practice ---------------------------------------------------------
#
# Fabric exposes the *connection* to a repository (``WS-GIT`` ref 11.1.1 and
# ``WS-BRANCH`` ref 11.1.4 read it), never the repository itself: the Fabric REST
# API this tool calls returns the org/project/branch a workspace tracks, not the
# repo's files, its commit history, or its branch policies. Everything below is
# therefore a question, not a measurement.

#: Why the Git questions are asked rather than measured.
_WHY_GIT = (
    "self-assessed: Fabric exposes only the Git *connection*, not the "
    "repository's files, history, or branch policies"
)


questionnaire_check(
    id="OPS-GIT-IGNORE", ref="11.1.3",
    title="`.gitignore` / exclusion rules prevent sensitive data in the repo",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Does the repository carry `.gitignore` / exclusion rules that keep data files, secrets, "
        f"and local config out of source control? ({_WHY_GIT})"
    ),
    options=_options(
        "Extend the exclusion rules to the file types still slipping through, and scan the history "
        "for anything already committed.",
        "Add `.gitignore` / exclusion rules covering data extracts, credentials, and local config, "
        "and purge anything sensitive already in the history.",
    ),
)


questionnaire_check(
    id="OPS-GIT-COMMIT-MSG", ref="11.1.5",
    title="Commit messages are descriptive and linked to work items",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Do commit messages describe the change and reference the work item or ticket that asked "
        f"for it? ({_WHY_GIT})"
    ),
    options=_options(
        "Agree a commit-message convention and apply it consistently, including the work-item "
        "reference.",
        "Adopt a commit-message convention that describes the change and links the work item, and "
        "enforce it in review.",
    ),
)


questionnaire_check(
    id="OPS-GIT-PR-REVIEW", ref="11.1.6",
    title="Pull request reviews required before merge to main",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Is a reviewed pull request required before anything merges to main - enforced by branch "
        f"policy rather than by convention? ({_WHY_GIT})"
    ),
    options=_options(
        "Turn the convention into an enforced branch policy so main cannot be written to directly.",
        "Protect main with a branch policy that requires a reviewed pull request before merge.",
    ),
)


questionnaire_check(
    id="OPS-GIT-MIN-REVIEWERS", ref="11.1.7",
    title="Minimum reviewer count enforced via branch policies",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Does the branch policy set a minimum number of approvers (and exclude the author) rather "
        f"than accepting a single self-approval? ({_WHY_GIT})"
    ),
    options=_options(
        "Raise the minimum approver count on the protected branches that do not yet set one, and "
        "exclude the author from counting toward it.",
        "Configure a minimum reviewer count in the branch policy for main and release branches.",
    ),
)


questionnaire_check(
    id="OPS-WH-SCHEMA-SCM", ref="11.4.1",
    title="Gold Warehouse schema changes are source-controlled (SQL project / DACPAC or equivalent)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Are Gold Warehouse schema changes authored as source-controlled artefacts (SQL project, "
        f"DACPAC, or migration scripts) rather than applied ad-hoc in the portal? ({_WHY_GIT})"
    ),
    options=_options(
        "Bring the remaining objects under the SQL project / migration scripts so no schema change "
        "reaches production outside source control.",
        "Move Warehouse schema into a source-controlled SQL project (or migration scripts) and "
        "deploy it from the pipeline instead of editing in place.",
    ),
)


# -- Cross-environment posture -------------------------------------------------
#
# The engine judges one workspace at a time, so a *comparison across* Dev / QA /
# Prod is outside what any single check sees; and deployment rules live in the
# Deployment Pipelines API this tool does not call.

questionnaire_check(
    id="OPS-DEPLOY-RULES", ref="11.2.2",
    title="Deployment rules configured for environment-specific parameters (connections, paths, capacity)",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Are deployment rules configured so environment-specific values - connections, lakehouse "
        "paths, capacity - are rebound on promotion instead of hand-edited afterwards? "
        "(self-assessed: deployment-rule configuration sits behind a Deployment Pipelines API this "
        "tool does not call)"
    ),
    options=_options(
        "Add deployment rules for the parameters still corrected by hand after a promotion.",
        "Configure deployment rules for every environment-specific connection, path, and capacity "
        "binding so promotion needs no manual edit.",
    ),
)


questionnaire_check(
    id="OPS-ENV-PARITY", ref="11.3.4",
    title='Environment parity maintained, no "works on dev" surprises',
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Do Dev, QA and Prod match closely enough in structure, settings and capacity that a "
        'change working in Dev behaves the same in Prod - no "works on dev" surprises? '
        "(self-assessed: this compares workspaces against each other, and each audit run judges "
        "one workspace at a time)"
    ),
    options=_options(
        "Reconcile the known differences between environments and record the ones that must remain "
        "(e.g. capacity size) so they are expected rather than surprising.",
        "Bring the environments into parity - same item structure, settings and configuration "
        'shape - so promotion stops producing "works on dev" failures.',
    ),
)


# -- SLA reporting over time ---------------------------------------------------
#
# Narrower than the sibling questions ``PL-SLA-MONITORED`` (ref 9.4.2, is an SLA
# set and watched) and ``PL-SLA-ALERTS`` (ref 9.4.3, does a breach alert): this
# one asks whether compliance is *retained and reported over time*, which needs
# run history beyond Fabric's own retention window.

questionnaire_check(
    id="OPS-SLA-HISTORY", ref="9.4.4",
    title="Historical SLA compliance tracked and reported",
    pillar=Pillar.RELIABILITY, severity=Severity.MEDIUM, layers=_LAYERS,
    question=(
        "Is SLA compliance retained over time and reported (e.g. a monthly attainment figure per "
        "critical pipeline), not just alerted on in the moment? (self-assessed: historical run "
        "outcomes come from the capacity-metrics and monitoring admin APIs this tool does not call)"
    ),
    options=_options(
        "Retain the SLA outcomes you already collect and turn them into a periodic attainment "
        "report covering every critical pipeline.",
        "Persist per-run SLA outcomes to a durable store and report attainment over time, so trends "
        "and repeat offenders are visible.",
    ),
)
