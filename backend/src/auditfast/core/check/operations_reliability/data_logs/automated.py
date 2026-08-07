"""Operations & Reliability · Data Logs — the telemetry store and its queries.

Two points about the observability layer, both readable from the item inventory
plus the workspace's Git connection: telemetry lands in a store built for it
(Eventhouse / KQL database) rather than only in a batch store, and the saved KQL
that operators actually run has a home and a version history.

No API exposes a KQL queryset's *text*, so these judge the presence and the
source-control posture of the assets, never the content of a query.
"""
from __future__ import annotations

from auditfast.core.check.helpers import Verdict, binary, graded, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

#: A store designed for high-volume, high-ingest telemetry. An Eventhouse is the
#: container; a KQLDatabase is what queries actually run against. Either one
#: present means the real-time store exists.
EVENTHOUSE_TYPES: frozenset[str] = frozenset({"Eventhouse", "KQLDatabase"})

#: Items that *stream* data into the workspace. Their presence is the readable
#: signal that real-time telemetry is arriving here — the "where appropriate"
#: half of the point — without guessing at volume, which no API reports.
STREAMING_SOURCE_TYPES: frozenset[str] = frozenset({"Eventstream"})

#: Batch stores. Telemetry landing only here is the defect the point describes:
#: a high-volume/real-time feed forced through a batch-oriented store.
BATCH_STORE_TYPES: frozenset[str] = frozenset({"Lakehouse", "Warehouse", "SQLDatabase"})

#: Saved KQL an operator opens during an investigation. A queryset is the
#: canonical form; a real-time dashboard carries its queries inline and counts
#: as the same practice.
KQL_QUERY_ASSET_TYPES: frozenset[str] = frozenset({"KQLQueryset", "KQLDashboard"})


def _named(items, types: frozenset[str]) -> list[str]:
    """Display names of the items whose type is in ``types``, sorted."""
    return sorted(i.display_name or i.id for i in items if i.type in types)


@check(
    id="WS-EVENTHOUSE-TELEMETRY", ref="10.3.1",
    title="Eventhouse/KQL DB used for high-volume or real-time telemetry where appropriate",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.ITEMS], required=True,
)
def eventhouse_for_telemetry(ctx: CheckContext) -> Verdict:
    """Telemetry in the logging workspace lands in an Eventhouse / KQL database.

    "High-volume" is not readable from any Fabric API, so the *appropriateness*
    half is judged from what the workspace holds: an Eventstream means real-time
    telemetry is genuinely arriving, and an Eventhouse or KQL database means
    there is a store built to absorb and query it.

    A workspace whose only stores are batch (Lakehouse / Warehouse) scores in the
    middle rather than failing — a log workspace may legitimately be batch-only —
    and a workspace with neither a store nor a streaming source is N/A, because
    there is no telemetry here to place.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    items = ctx.workspace.items
    eventhouses = _named(items, EVENTHOUSE_TYPES)
    streams = _named(items, STREAMING_SOURCE_TYPES)
    batch = _named(items, BATCH_STORE_TYPES)

    if eventhouses:
        detail = f"Eventhouse/KQL store present: {', '.join(eventhouses)}"
        if streams:
            detail += f"; fed by {len(streams)} streaming source(s)"
        return binary(True, detail)

    if streams:
        return binary(
            False,
            f"{len(streams)} streaming source(s) ({', '.join(streams)}) but no Eventhouse "
            f"or KQL database — real-time telemetry has no store built for it",
        )

    if batch:
        return graded(
            1,
            f"No Eventhouse or KQL database; telemetry is held only in {len(batch)} batch "
            f"store(s) ({', '.join(batch)}) — adequate for low volume, not for high-volume "
            f"or real-time feeds",
        )

    return not_applicable(
        "Workspace holds no telemetry store and no streaming source, so there is "
        "no telemetry here to place"
    )


@check(
    id="WS-KQL-QUERIES", ref="10.3.2",
    title="KQL queries exist for common operational investigations and are version-controlled",
    pillar=Pillar.OPERATIONS, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=(Layer.LOGS,), requires=[Resource.ITEMS, Resource.GIT], required=True,
)
def kql_queries_version_controlled(ctx: CheckContext) -> Verdict:
    """Saved KQL for investigations exists as a workspace item and is under Git.

    Two halves, both readable. *Exist*: a KQL queryset or a real-time dashboard
    is a saved, shareable query — an investigation run from someone's browser tab
    leaves nothing behind. *Version-controlled*: the workspace is Git-connected,
    so those querysets have a history and a review path.

    Judged only where there is something to query: a workspace with no Eventhouse
    or KQL database is N/A rather than a failure. The *content* of a queryset is
    never inspected — no API returns it — so "covers the common investigations"
    is out of reach and deliberately not guessed at.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")

    items = ctx.workspace.items
    stores = _named(items, EVENTHOUSE_TYPES)
    if not stores:
        return not_applicable(
            "Workspace holds no Eventhouse or KQL database, so there is nothing "
            "for a saved KQL query to investigate"
        )
    if not ctx.workspace.has(Resource.GIT):
        return not_applicable(
            f"{len(stores)} Eventhouse/KQL store(s) present, but the Git connection state "
            f"could not be read, so version control cannot be judged"
        )

    assets = _named(items, KQL_QUERY_ASSET_TYPES)
    versioned = ctx.workspace.git_connected
    version_note = "Git-connected" if versioned else "not Git-connected"

    if assets and versioned:
        return graded(
            3,
            f"{len(assets)} saved KQL asset(s) ({', '.join(assets)}) against "
            f"{len(stores)} store(s); workspace is {version_note}",
        )
    if assets:
        return graded(
            1,
            f"{len(assets)} saved KQL asset(s) ({', '.join(assets)}) exist, but the "
            f"workspace is {version_note} — the queries have no history or review path",
        )
    if versioned:
        return graded(
            0,
            f"{len(stores)} Eventhouse/KQL store(s) but no saved KQL queryset or real-time "
            f"dashboard — investigations leave nothing reusable behind (workspace is "
            f"{version_note})",
        )
    return graded(
        0,
        f"{len(stores)} Eventhouse/KQL store(s) with no saved KQL queryset or real-time "
        f"dashboard, and the workspace is {version_note}",
    )
