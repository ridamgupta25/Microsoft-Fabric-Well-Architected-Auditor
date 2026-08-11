"""Timestamp parsing shared by the recency checks.

Two very different points ask the same mechanical question — *how long ago did
this item last run?* — and both read :attr:`auditfast.core.models.Item.last_run_utc`:

* ``WS-ORPHAN`` (ref 12.3.4) — nothing has run for months, so the item is waste.
* ``WS-GOLD-FRESHNESS`` (ref 5.4.7) — the serving items have not refreshed inside
  their SLA, so what is being served is stale.

The parsing rule is identical and belongs in one place, so a stamp that one check
can read is never a stamp the other silently drops. Underscore-prefixed, so the
check auto-loader skips it: this module holds parsing, not checks.

The rule itself: the Fabric job scheduler returns ISO-8601 with a ``Z`` suffix,
which :meth:`datetime.fromisoformat` does not accept before Python 3.11, and it
occasionally returns a stamp with no offset at all. Both are normalised to a
timezone-aware UTC datetime. A stamp that cannot be parsed returns ``None`` —
the *caller* decides what an unreadable stamp means, because the two checks
answer that differently and both answers are deliberate.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_stamp(value: object) -> datetime | None:
    """An ISO-8601 stamp as a timezone-aware UTC datetime, or ``None`` if unreadable.

    ``None``, an empty string and an unparseable value all return ``None``; a
    naive datetime string is assumed to be UTC, which is what every Fabric and
    Power BI timestamp this project reads actually is.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
