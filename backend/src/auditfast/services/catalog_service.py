"""Questions about the check catalog itself — no tenant, no I/O, no audit run.

Every function here answers instantly from registered metadata. That is only
possible because a check declares its pillar, scope, layers, and requirements at
registration time; previously none of this was knowable without executing a full
audit.

This is also the most useful surface in mcp-inspector: you can browse the entire
rule library before pointing the tool at anything.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..core.checks.registry import REGISTRY, CheckRegistry
from ..core.enums import Layer, Pillar, Scope


def list_pillars() -> list[dict]:
    """The scored pillars, with how many checks each currently has."""
    return [
        {
            "name": pillar.value,
            "checks": len(REGISTRY.select(pillars=[pillar])),
        }
        for pillar in Pillar.scored()
    ]


def list_layers() -> list[dict]:
    """The layer roles a workspace can be tagged with."""
    return [
        {
            "name": layer.value,
            "checks": len(REGISTRY.select(layer=layer)),
        }
        for layer in Layer.assignable()
    ]


def list_scopes() -> list[str]:
    """The object kinds that have at least one check registered."""
    return [scope.value for scope in REGISTRY.scopes()]


def list_checks(
    pillar: str | None = None,
    layer: str | None = None,
    scope: str | None = None,
    registry: CheckRegistry = REGISTRY,
) -> list[dict]:
    """The catalog, optionally filtered. Arguments are the human-readable strings.

    An unrecognized ``pillar`` or ``scope`` yields an empty list rather than an
    error, so a caller exploring the API cannot break it with a typo.
    """
    pillars: Iterable[Pillar] | None = None
    if pillar:
        match = next((p for p in Pillar if p.value.lower() == pillar.lower()), None)
        if match is None:
            return []
        pillars = [match]

    scope_member = None
    if scope:
        scope_member = next((s for s in Scope if s.value.lower() == scope.lower()), None)
        if scope_member is None:
            return []

    specs = registry.select(
        pillars=pillars,
        scope=scope_member,
        layer=Layer.parse(layer) if layer else None,
    )
    return [spec.to_dict() for spec in sorted(specs, key=lambda s: s.id)]


def describe_check(check_id: str, registry: CheckRegistry = REGISTRY) -> dict | None:
    """Full metadata for one check, or ``None`` when the id is unknown."""
    spec = registry.get(check_id)
    return spec.to_dict() if spec else None


def catalog_summary(registry: CheckRegistry = REGISTRY) -> dict:
    """Counts by pillar and scope — the "what does this tool cover" answer."""
    return {
        "total": len(registry),
        "by_pillar": {
            pillar.value: len(registry.select(pillars=[pillar]))
            for pillar in Pillar.scored()
        },
        "by_scope": {
            scope.value: len(registry.select(scope=scope))
            for scope in registry.scopes()
        },
    }
