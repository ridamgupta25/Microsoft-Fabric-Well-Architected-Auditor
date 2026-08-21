"""Tests for WS-LAYER-OVERLOAD (ref 1.1.1) — mixed-workspace layer detection.

The check flags an untagged/mixed workspace that combines item types from more
than one layer (Prep / Store / Consumption / Logs), the "everything in one
workspace" anti-pattern. A workspace that resolves to a single layer role is
left to WS-LAYER-SEP and reports N/A here.
"""
from __future__ import annotations

from auditfast.core.check.data_management_quality.data_operations.automated import (
    layer_overload,
)
from auditfast.core.enums import Layer, Resource, Scope, Status
from auditfast.core.models import CheckContext, Item, WorkspaceContext


def _ctx(*item_types: str, layer: Layer = Layer.MIXED, name: str = "Mixed_WS") -> CheckContext:
    ws = WorkspaceContext(
        id=name,
        display_name=name,
        layer=layer,
        items=[
            Item(id=f"{name}-{i}", type=item_type, display_name=item_type)
            for i, item_type in enumerate(item_types)
        ],
    )
    return CheckContext(workspace=ws, settings={}, obj_name="", obj=None)


def test_single_concern_passes():
    verdict = layer_overload(_ctx("Notebook", "DataPipeline"))
    assert verdict.score == 3
    assert verdict.status is None or verdict.status is Status.PASS


def test_two_layers_are_partial():
    verdict = layer_overload(_ctx("Notebook", "Warehouse"))
    assert verdict.score == 1
    assert "mixes 2 layers" in verdict.evidence


def test_three_or_more_layers_fail():
    verdict = layer_overload(
        _ctx("Notebook", "Warehouse", "SemanticModel", "Eventhouse")
    )
    assert verdict.score == 0
    assert "mixes 4 layers" in verdict.evidence


def test_sql_endpoint_rides_with_store_not_an_extra_layer():
    verdict = layer_overload(_ctx("Warehouse", "SQLEndpoint", "Lakehouse"))
    assert verdict.score == 3


def test_tagged_single_layer_workspace_is_na():
    verdict = layer_overload(_ctx("Notebook", "Warehouse", layer=Layer.PREP))
    assert verdict.status is Status.NA
    assert verdict.scored is False


def test_name_inferred_layer_is_na():
    verdict = layer_overload(_ctx("Notebook", "Warehouse", name="Sales_DataPrep_Dev"))
    assert verdict.status is Status.NA
    assert verdict.scored is False


def test_unreadable_items_are_na():
    ws = WorkspaceContext(id="w", display_name="w", layer=Layer.MIXED,
                          unavailable={Resource.ITEMS})
    verdict = layer_overload(CheckContext(workspace=ws, settings={}, obj_name="", obj=None))
    assert verdict.status is Status.NA
    assert verdict.scored is False


def test_registered_as_workspace_scope_with_ref():
    from auditfast.core.check.registry import REGISTRY

    spec = REGISTRY.get("WS-LAYER-OVERLOAD")
    assert spec is not None
    assert spec.ref == "1.1.1"
    assert spec.scope is Scope.WORKSPACE
