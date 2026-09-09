"""Elevated-access checks — one sub-package per :class:`AdminCategory`.

These read data an ordinary reviewer cannot see: workspace role assignments,
connections, gateways, tenant settings, capacity metrics. They register into
``ADMIN_REGISTRY`` via ``@admin_check`` rather than the standard ``REGISTRY``,
so a standard audit can never select them and its score is unaffected by who
signed in.

Layout — the leaf module must be named ``admin.py`` to be auto-imported::

    elevated/tenant/admin.py       AdminCategory.TENANT
    elevated/capacity/admin.py     AdminCategory.CAPACITY
    elevated/workspace/admin.py    AdminCategory.WORKSPACE

A category with no registered checks is not an error: it reports a count of
zero and is offered as unavailable in the selection screen.
"""
