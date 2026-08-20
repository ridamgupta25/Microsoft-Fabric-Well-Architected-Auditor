"""The cross-workspace cross-domain-lineage check (ref 8.1.5).

Unit-tests ``XW-LINEAGE-CROSSDOMAIN`` directly: it scores whether every group
member keeps its cross-domain dependencies *identifiable* (a named shortcut or a
path that names its workspace/item) rather than opaque (a bare GUID or a raw
external URL). Fewer than two readable members ⇒ N/A, never a low score.
"""
from __future__ import annotations

from auditfast.core.check.governance_compliance.data_operations.group import (
    lineage_crossdomain_consistent,
)
from auditfast.core.enums import Layer, Resource, Status
from auditfast.core.models import GroupContext, GroupMemberContext, WorkspaceContext

_GUID = "0123abcd-4567-89ab-cdef-0123456789ab"


def _ws(
    ws_id: str,
    *,
    pipelines: dict | None = None,
    notebooks: dict | None = None,
    shortcuts: dict | None = None,
    readable: bool = True,
) -> WorkspaceContext:
    ctx = WorkspaceContext(id=ws_id, display_name=ws_id, layer=Layer.OPERATIONS)
    ctx.pipelines = pipelines or {}
    ctx.notebooks = notebooks or {}
    ctx.shortcuts = shortcuts or {}
    if not readable:
        ctx.unavailable.update(
            {Resource.PIPELINE_DEFINITIONS, Resource.NOTEBOOK_DEFINITIONS, Resource.SHORTCUTS}
        )
    return ctx


def _group(*members: tuple[WorkspaceContext, int]) -> GroupContext:
    return GroupContext(
        name="Sales",
        members=tuple(
            GroupMemberContext(ws, level, Layer.OPERATIONS) for ws, level in members
        ),
        settings={},
    )


def test_identifiable_dependencies_in_every_environment_pass():
    named_shortcut = {"Gold": [{"name": "sales", "target_type": "OneLake"}]}
    dev = _ws("ws-dev", shortcuts=named_shortcut)
    prod = _ws("ws-prod", shortcuts=dict(named_shortcut))
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3
    assert verdict.scored is True


def test_opaque_shortcut_in_one_environment_lowers_the_score():
    dev = _ws("ws-dev", shortcuts={"Gold": [{"name": "sales", "target_type": "OneLake"}]})
    # Prod's shortcut has no name/target type — undocumented dependency.
    prod = _ws("ws-prod", shortcuts={"Gold": [{"path": "/x"}]})
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3


def test_bare_guid_onelake_path_is_opaque():
    dev = _ws("ws-dev", pipelines={"PL": {"note": "clean"}})
    prod = _ws("ws-prod", pipelines={
        "PL": {"src": f"abfss://{_GUID}@x.onelake.dfs.core.windows.net/{_GUID}/Tables/t"},
    })
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3


def test_named_onelake_path_is_identifiable():
    path = "abfss://SalesDomain@x.onelake.dfs.core.windows.net/GoldLH/Tables/t"
    dev = _ws("ws-dev", pipelines={"PL": {"src": path}})
    prod = _ws("ws-prod", pipelines={"PL": {"src": path}})
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score == 3


def test_external_storage_url_is_opaque():
    dev = _ws("ws-dev", notebooks={"NB": {"cells": []}})
    prod = _ws("ws-prod", notebooks={
        "NB": {"cells": [{"cell_type": "code", "source": ["df = spark.read.parquet('s3://bucket/x')"]}]},
    })
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.score is not None and verdict.score < 3


def test_fewer_than_two_readable_members_is_na():
    dev = _ws("ws-dev", shortcuts={"Gold": [{"name": "s", "target_type": "OneLake"}]})
    prod = _ws("ws-prod", readable=False)
    verdict = lineage_crossdomain_consistent(_group((dev, 1), (prod, 10)))
    assert verdict.status is Status.NA
    assert verdict.scored is False
