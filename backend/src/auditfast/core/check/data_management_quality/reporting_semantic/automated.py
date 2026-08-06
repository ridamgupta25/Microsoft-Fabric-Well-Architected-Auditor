"""Data Management & Quality · Reporting / Semantic — model structure checks.

Read from the semantic model's TMSL definition. The model is where the
fact-to-dimension wiring is declared explicitly, which makes it the one place
referential structure is machine-readable without querying the warehouse.
"""
from __future__ import annotations

import re
from typing import Any

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

SEMANTIC_LAYERS = (Layer.REPORTING, Layer.STORAGE, Layer.MIXED)

SURROGATE_KEY_RE = re.compile(r"(?:_sk|_key|_id)$", re.IGNORECASE)


def _model(ctx: CheckContext) -> dict[str, Any] | None:
    """Return parsed TMSL for the current semantic model object."""
    return (ctx.workspace.semantic_models or {}).get(ctx.obj_name)


def _relationships(model: dict[str, Any]) -> list[dict[str, Any]]:
    return model.get("relationships") or []


def _is_active(rel: dict[str, Any]) -> bool:
    """Normalize active flag variants."""
    return bool(rel.get("is_active", rel.get("isActive", True)))

def _norm_col(name: str) -> str:
    return re.sub(r"[\s\-\.]+", "_", (name or "").strip().lower())


def _query_results(ctx: CheckContext) -> dict[str, Any]:
    return getattr(ctx.workspace, "query_results", {}) or {}


_KEY_HINT_RE = re.compile(
    r"(?:^id$|_id$|^key$|_key$|^sk$|_sk$|code$|number$|num$|date$)",
    re.IGNORECASE,
)

@check(
    id="SM-FK-SURROGATE",
    ref="5.4.1",
    title="Fact-dimension relationships join on surrogate keys",
    pillar=Pillar.DATA,
    scope=Scope.SEMANTIC_MODEL,
    severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def sm_fk_surrogate(ctx: CheckContext) -> Verdict:
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definition could not be read")

    relationships = _relationships(model)
    if not relationships:
        table_count = len(model.get("tables") or [])
        if table_count <= 1:
            return not_applicable("Single-table model — no relationships to define")
        return covered(0, 1, f"Model has {table_count} tables but no relationships defined — facts are not wired to dimensions",)

    on_keys: list[dict[str, Any]] = []
    inactive_names: list[str] = []

    for rel in relationships:
        from_col = _norm_col(str(rel.get("from_column") or rel.get("fromColumn") or ""))
        to_col = _norm_col(str(rel.get("to_column") or rel.get("toColumn") or ""))

        if _KEY_HINT_RE.search(from_col) or _KEY_HINT_RE.search(to_col):
            on_keys.append(rel)

        if not _is_active(rel):
            inactive_names.append(str(rel.get("name") or rel.get("id") or "?"))

    detail = f"{len(on_keys)} of {len(relationships)} relationships join on a surrogate-key-shaped column"
    if inactive_names:
        detail += f"; {len(inactive_names)} inactive ({', '.join(sorted(inactive_names))})"

    return covered(len(on_keys), len(relationships), detail)


@check(
    id="SM-FK-RI-DATA",
    ref="5.4.1",
    title="Fact FK values resolve to dimension surrogate keys (no orphans)",
    pillar=Pillar.DATA,
    scope=Scope.WORKSPACE,
    severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS],
    required=True,
)
def sm_fk_ri_data(ctx: CheckContext) -> Verdict:
    rows = _query_results(ctx).get("5.4.1.ri") or []
    if not rows:
        return not_applicable("No Lakehouse/Warehouse SQL RI evidence found for 5.4.1")

    total = len(rows)
    passed = 0
    failing: list[str] = []

    for row in rows:
        orphan_count = int(row.get("orphan_count") or 0)
        rel_name = str(row.get("relationship") or "?")
        if orphan_count == 0:
            passed += 1
        else:
            failing.append(f"{rel_name} (orphans={orphan_count})")

    detail = f"{passed} of {total} FK relationships have zero orphans"
    if failing:
        detail += "; failing: " + ", ".join(failing[:10])
        if len(failing) > 10:
            detail += f" (+{len(failing) - 10} more)"

    return covered(passed, total, detail)