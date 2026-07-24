"""Domain core.

Pure, deterministic building blocks with no web/CLI dependencies:

* :mod:`.models`        - data models (``CheckResult``, ``Status``, pillars).
* :mod:`.scoring`       - coverage-to-score bands and roll-up aggregation.
* :mod:`.engine`        - runs the registered checks across workspaces.
* :mod:`.checks`        - the deterministic best-practice rule functions.

The read-only Fabric data clients live in :mod:`auditfast.clients`.
Everything here is framework-agnostic and unit-testable in isolation.
"""
