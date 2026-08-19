"""Draft a new deterministic check from a plain-language checklist point.

When the intake pipeline finds a point is *not* already covered, this module
turns it into a **proposal**: an inferred pillar/scope/severity, the data the
check would need, a ready-to-edit ``@check`` code skeleton, and a remediation
stub. The proposal is design-time scaffolding for a human (or Copilot, driven by
``.github/agents/checklist-author.agent.md``) to finish, review, and promote via
the normal roadmap->automated flow.

Crucially, a proposal is **never** registered automatically: nothing here calls
``registry.register``. That keeps the live registry — and therefore the pinned
score and check count — unchanged. Determinism is preserved because a proposed
check only ever runs after a human writes and merges the real evaluator.

Inference is deterministic keyword mapping. When ``settings.ai_enabled`` is on,
:mod:`.orchestrator` can enrich the rationale and remediation prose, but the
structural fields (id, scope, requires) always come from this pure logic so the
scaffold is reproducible with or without a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.enums import Pillar, Resource, Scope, Severity

_TOKEN = re.compile(r"[a-z0-9]+")

# -- keyword inference maps ----------------------------------------------------
# First map to fire wins for scope; pillars accumulate a score and the top wins.

_SCOPE_KEYWORDS: dict[Scope, frozenset[str]] = {
    Scope.NOTEBOOK: frozenset(
        {"notebook", "spark", "pyspark", "cell", "dataframe", "optimize", "vorder",
         "vacuum", "delta", "partition", "cache", "broadcast", "udf", "shuffle"}
    ),
    Scope.PIPELINE: frozenset(
        {"pipeline", "activity", "copy", "orchestration", "trigger", "retry",
         "dataflow", "ingestion", "schedule", "dependency"}
    ),
}

_PILLAR_KEYWORDS: dict[Pillar, frozenset[str]] = {
    Pillar.SECURITY_ACCESS: frozenset(
        {"security", "rls", "cls", "permission", "permissions", "access", "encryption",
         "secret", "secrets", "sensitivity", "private", "endpoint", "firewall",
         "identity", "guest", "admin", "mfa", "token", "credential", "keyvault"}
    ),
    Pillar.DATA_GOVERNANCE: frozenset(
        {"governance", "compliance", "catalog", "lineage", "label", "labels",
         "retention", "policy", "audit", "classification", "ownership", "endorsement",
         "certified", "purview", "gdpr", "regulatory"}
    ),
    Pillar.RELIABILITY: frozenset(
        {"operations", "reliability", "git", "ci", "cd", "deployment", "retry",
         "monitor", "monitoring", "alert", "alerts", "backup", "recovery", "schedule",
         "orchestration", "failure", "dr", "resilience", "availability", "sla"}
    ),
    Pillar.DATA_PROCESSING: frozenset(
        {"performance", "capacity", "optimize", "partition", "partitioning", "cache",
         "delta", "vorder", "compaction", "throughput", "latency", "spark",
         "concurrency", "skew", "shuffle", "index", "indexing", "directlake"}
    ),
    Pillar.COST_MANAGEMENT: frozenset(
        {"cost", "sku", "autoscale", "pause", "budget", "billing", "consumption",
         "spend", "finops", "idle", "orphan", "orphaned", "utilization", "rightsizing"}
    ),
    Pillar.DATA_QUALITY: frozenset(
        {"data", "quality", "schema", "null", "nulls", "duplicate", "duplicates",
         "validation", "freshness", "completeness", "accuracy", "table", "column",
         "constraint", "dedup", "medallion", "bronze", "silver", "gold", "lakehouse"}
    ),
}

_SCOPE_REQUIRES: dict[Scope, tuple[Resource, ...]] = {
    Scope.WORKSPACE: (Resource.WORKSPACE, Resource.ITEMS),
    Scope.PIPELINE: (Resource.ITEMS, Resource.PIPELINE_DEFINITIONS),
    Scope.NOTEBOOK: (Resource.ITEMS, Resource.NOTEBOOK_DEFINITIONS),
}

_SCOPE_PREFIX: dict[Scope, str] = {
    Scope.WORKSPACE: "WS",
    Scope.PIPELINE: "PL",
    Scope.NOTEBOOK: "NB",
}


@dataclass(frozen=True, slots=True)
class CheckProposal:
    """A draft check awaiting a human evaluator and review."""

    point: str
    suggested_id: str
    pillar: Pillar
    scope: Scope
    severity: Severity
    requires: tuple[Resource, ...]
    title: str
    rationale: str
    code_skeleton: str
    remediation_stub: str

    def to_dict(self) -> dict:
        return {
            "point": self.point,
            "suggested_id": self.suggested_id,
            "suggested_ref": "TBD — assign the next checklist ref for this pillar",
            "pillar": self.pillar.value,
            "scope": self.scope.value,
            "severity": self.severity.value,
            "requires": [r.value for r in self.requires],
            "title": self.title,
            "rationale": self.rationale,
            "code_skeleton": self.code_skeleton,
            "remediation_stub": self.remediation_stub,
        }


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def infer_scope(tokens: set[str]) -> Scope:
    for scope, words in _SCOPE_KEYWORDS.items():
        if tokens & words:
            return scope
    return Scope.WORKSPACE


def infer_pillar(tokens: set[str]) -> Pillar:
    best, best_score = Pillar.DATA_QUALITY, 0
    for pillar, words in _PILLAR_KEYWORDS.items():
        score = len(tokens & words)
        if score > best_score:
            best, best_score = pillar, score
    return best


def _slug_id(prefix: str, tokens: list[str]) -> str:
    salient = [t for t in tokens if len(t) > 2][:3] or ["custom"]
    return (prefix + "-" + "-".join(salient)).upper()[:24]


def _title(point: str) -> str:
    trimmed = point.strip().rstrip(".")
    return (trimmed[:1].upper() + trimmed[1:]) if trimmed else "Proposed check"


def _skeleton(proposal_id: str, pillar: Pillar, scope: Scope,
              severity: Severity, requires: tuple[Resource, ...], title: str) -> str:
    requires_src = ", ".join(f"Resource.{r.name}" for r in requires)
    pillar_src = f"Pillar.{pillar.name}"
    scope_src = f"Scope.{scope.name}"
    sev_src = f"Severity.{severity.name}"
    obj_hint = {
        Scope.WORKSPACE: "ws = ctx.workspace",
        Scope.PIPELINE: "definition = ctx.obj  # parsed pipeline JSON",
        Scope.NOTEBOOK: "code = notebook_code(ctx.obj)  # from ._notebook import notebook_code",
    }[scope]
    return (
        f'@check(id="{proposal_id}", ref="TBD", title="{title}",\n'
        f"       pillar={pillar_src}, scope={scope_src}, severity={sev_src},\n"
        f"       requires=[{requires_src}])\n"
        f"def {proposal_id.lower().replace('-', '_')}(ctx: CheckContext) -> Verdict:\n"
        f'    """{title}."""\n'
        f"    {obj_hint}\n"
        f"    # TODO: read the fetched data and decide.\n"
        f"    # Report not_applicable(...) when the data needed was unavailable —\n"
        f"    # never FAIL on missing data.\n"
        f'    ok = False  # replace with the real condition\n'
        f'    return binary(ok, "evidence describing what was observed")\n'
    )


def draft_proposal(point: str) -> CheckProposal:
    """Build a deterministic draft check for an uncovered point."""
    tokens = _tokens(point)
    token_set = set(tokens)
    scope = infer_scope(token_set)
    pillar = infer_pillar(token_set)
    severity = Severity.HIGH if pillar is Pillar.SECURITY_ACCESS else Severity.MEDIUM
    requires = _SCOPE_REQUIRES[scope]
    proposal_id = _slug_id(_SCOPE_PREFIX[scope], tokens)
    title = _title(point)
    rationale = (
        f"Inferred {pillar.value} / {scope.value} from the wording. Adjust the "
        f"pillar, scope, and required data before implementing the evaluator."
    )
    remediation_stub = (
        f"Document how to bring a {scope.value} into compliance with: {title}. "
        f"Keep it concrete and actionable; this text is shown on every finding."
    )
    return CheckProposal(
        point=point,
        suggested_id=proposal_id,
        pillar=pillar,
        scope=scope,
        severity=severity,
        requires=requires,
        title=title,
        rationale=rationale,
        code_skeleton=_skeleton(proposal_id, pillar, scope, severity, requires, title),
        remediation_stub=remediation_stub,
    )
