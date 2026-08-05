"""Data Management & Quality · Reporting / Semantic — model structure checks.

Read from the semantic model's TMSL definition. The model is where the
fact-to-dimension wiring is declared explicitly, which makes it the one place
referential structure is machine-readable without querying the warehouse.
"""
from __future__ import annotations

import re

from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: Layers whose workspaces hold semantic models.
SEMANTIC_LAYERS = (Layer.REPORTING, Layer.STORAGE, Layer.MIXED)

#: A surrogate-key-shaped column name. Kimball convention is a ``_sk`` suffix;
#: ``_key`` / ``_id`` are the common variants. A relationship joining on one of
#: these is joining on a key rather than on a descriptive business attribute.
SURROGATE_KEY_RE = re.compile(r"(?:_sk|sk|_key|key|_id|id)$", re.IGNORECASE)


def _model(ctx: CheckContext) -> dict | None:
    """The parsed TMSL for the semantic model this check is running against."""
    return ctx.workspace.semantic_models.get(ctx.obj_name)


@check(
    id="SM-FK-SURROGATE", ref="5.4.1",
    title="Fact-dimension relationships join on surrogate keys",
    pillar=Pillar.DATA, scope=Scope.SEMANTIC_MODEL, severity=Severity.HIGH,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS], required=True,
)
def sm_fk_surrogate(ctx: CheckContext) -> Verdict:
    """Every relationship joins on a key column, and none is left inactive.

    This is the model-level half of referential integrity: it shows the facts
    are wired to their dimensions on surrogate keys rather than on a business
    attribute, and that the paths are live. It does **not** prove every FK value
    has a matching dimension row — that needs a query against the warehouse.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definition could not be read")

    relationships = model.get("relationships") or []
    if not relationships:
        if len(model.get("tables") or []) <= 1:
            return not_applicable("Single-table model — no relationships to define")
        return covered(0, 1, f"Model has {len(model.get('tables') or [])} tables but no "
                             f"relationships defined — facts are not wired to dimensions")

    on_keys, inactive = [], []
    for rel in relationships:
        from_col = str(rel.get("from_column") or "")
        if SURROGATE_KEY_RE.search(from_col):
            on_keys.append(rel)
        if not rel.get("is_active", True):
            inactive.append(rel)

    detail = (f"{len(on_keys)} of {len(relationships)} relationships join on a "
              f"key column")
    if inactive:
        names = ", ".join(sorted(str(r.get("name") or "?") for r in inactive))
        detail += f"; {len(inactive)} inactive ({names})"
    return covered(len(on_keys), len(relationships), detail)


@check(
    id="SM-BIDIRECTIONAL", ref="5.4.1",
    title="Relationships avoid ambiguous bidirectional filters",
    pillar=Pillar.DATA, scope=Scope.SEMANTIC_MODEL, severity=Severity.MEDIUM,
    layers=SEMANTIC_LAYERS,
    requires=[Resource.ITEMS, Resource.SEMANTIC_MODEL_DEFINITIONS], required=False,
)
def sm_bidirectional(ctx: CheckContext) -> Verdict:
    """Single-direction filters — bidirectional ones create ambiguous join paths.

    A both-directions filter on a star schema lets the engine resolve a query by
    more than one path, which produces results that are hard to explain and can
    silently double-count. They are occasionally justified; each one should be a
    deliberate decision.
    """
    model = _model(ctx)
    if model is None:
        return not_applicable("Semantic model definition could not be read")

    relationships = model.get("relationships") or []
    if not relationships:
        return not_applicable("Model defines no relationships")

    single = [r for r in relationships
              if "both" not in str(r.get("cross_filter") or "").lower()]
    both = [r for r in relationships if r not in single]
    detail = f"{len(single)} of {len(relationships)} relationships filter in one direction"
    if both:
        names = ", ".join(sorted(str(r.get("name") or "?") for r in both))
        detail += f"; bidirectional: {names}"
    return covered(len(single), len(relationships), detail)
