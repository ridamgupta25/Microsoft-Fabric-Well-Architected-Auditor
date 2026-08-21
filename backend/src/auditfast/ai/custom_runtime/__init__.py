"""Custom-check runtime - isolated from ``core``.

Generated custom checks live and run here, **never** in :mod:`auditfast.core`.
They inherit :class:`~auditfast.ai.custom_runtime.base_check.BaseAuditCheck`, score
on a 0-100 float scale, and are executed by the hardened local runner - so nothing
here can touch the pinned deterministic 0-3 registry or its score.
"""
