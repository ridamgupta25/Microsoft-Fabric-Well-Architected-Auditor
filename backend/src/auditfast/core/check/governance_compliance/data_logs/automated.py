"""Governance & Compliance · Data Logs — Warehouse auditing.

One point (7.4.6), promoted from a self-assessed question. The earlier triage
assumed Warehouse auditing was only visible through a tenant-admin audit API. It
is not: ``GET /v1/workspaces/{workspaceId}/warehouses/{warehouseId}/settings/sqlAudit``
returns the audit state, the configured action groups and the retention, and
needs the Audit permission on the Warehouse item — an ordinary delegated
permission, not tenant-admin.

Only the *configuration* is read. Audit rows (``sys.fn_get_audit_file_v2``) are
runtime data and never enter the knowledge base.
"""
from __future__ import annotations

from auditfast.core.check._audit import audit_enabled
from auditfast.core.check.helpers import Verdict, covered, not_applicable
from auditfast.core.check.registry import check
from auditfast.core.enums import Layer, Pillar, Resource, Scope, Severity
from auditfast.core.models import CheckContext

_LAYERS = (Layer.LOGS, Layer.MIXED)

#: How many Warehouse names the evidence spells out per category.
_MAX_NAMED = 3


@check(
    id="GOV-WH-AUDIT", ref="7.4.6",
    title="Warehouse-level auditing enabled for sensitive schemas (Finance) where supported",
    pillar=Pillar.DATA_GOVERNANCE, scope=Scope.WORKSPACE, severity=Severity.MEDIUM,
    layers=_LAYERS, requires=[Resource.ITEMS, Resource.WAREHOUSE_AUDIT], required=True,
)
def warehouse_auditing_enabled(ctx: CheckContext) -> Verdict:
    """Every Warehouse in this workspace has SQL auditing switched on.

    **What it can determine.** The share of Warehouses whose
    ``settings/sqlAudit`` state is Enabled, read per Warehouse over ordinary
    delegated Fabric REST (the Audit permission on the item, not tenant-admin).
    The evidence names the Warehouses left at Disabled and reports the configured
    retention where one is set.

    **What it cannot.** The setting is per *Warehouse*, not per schema, so it
    cannot say auditing covers the Finance schema specifically — a Warehouse
    holding sensitive schemas is judged as a whole, which is the granularity
    Fabric offers. It reads no audit rows, so it cannot confirm anything was
    actually written or that the output is retained where policy requires. A
    workspace with no Warehouse is N/A, and so is a Warehouse whose setting could
    not be read — "we could not ask" is never scored as "auditing is off".

    **Sibling.** ``GOV-FIN-CHANGE-AUDIT`` (ref 7.2.3) takes the same setting
    further and asks whether the configured action groups actually capture data
    modifications; a Warehouse with login-only auditing passes here and not
    there.
    """
    if not ctx.workspace.has(Resource.ITEMS):
        return not_applicable("Workspace items could not be read from Fabric")
    warehouses = [i for i in ctx.workspace.items if i.type == "Warehouse"]
    if not warehouses:
        return not_applicable(
            "This workspace holds no Warehouse, so there is no Warehouse-level "
            "auditing to enable"
        )
    if not ctx.workspace.has(Resource.WAREHOUSE_AUDIT):
        return not_applicable(
            f"The SQL audit setting could not be read for any of the {len(warehouses)} "
            f"Warehouse(s) — the Audit permission on the Warehouse item is required, so "
            f"whether auditing is on cannot be determined"
        )
    settings = ctx.workspace.warehouse_audit or {}
    if not settings:
        return not_applicable(
            f"No SQL audit setting was returned for the {len(warehouses)} Warehouse(s) "
            f"in this workspace"
        )

    enabled = sorted(name for name, value in settings.items() if audit_enabled(value))
    disabled = sorted(name for name in settings if name not in enabled)
    retentions = sorted({
        value.get("retention_days") for name, value in settings.items()
        if name in enabled and isinstance(value.get("retention_days"), int)
    })

    detail = f"{len(enabled)} of {len(settings)} Warehouse(s) have SQL auditing enabled"
    if enabled:
        detail += f": {', '.join(enabled[:_MAX_NAMED])}"
    if retentions:
        detail += f" (retention {', '.join(f'{d}d' for d in retentions)})"
    if disabled:
        detail += (f"; auditing is off on {len(disabled)} "
                   f"({', '.join(disabled[:_MAX_NAMED])})")
    if len(settings) < len(warehouses):
        detail += (f"; the setting could not be read for "
                   f"{len(warehouses) - len(settings)} further Warehouse(s), which are "
                   f"excluded rather than failed")
    detail += (". The setting is per Warehouse, not per schema, so Finance-schema "
               "coverage follows from the Warehouse that holds it.")
    return covered(len(enabled), len(settings), detail)
