"""Microsoft Fabric Well-Architected Auditing Platform.

Layers, outermost to innermost::

    api/  cli.py  mcp/    adapters — protocol translation only
    services/             orchestration; framework-free
    core/                 audit engine, checks, scoring; depends on nothing
    clients/              the read-only Fabric REST provider

Adapters may depend inward. ``core/`` depends on nothing outside itself, which
is what lets the REST API, the CLI, and the MCP server share one implementation.

Every audit reads the live tenant — there is no offline/demo mode in the
product. Deterministic test data lives entirely under ``tests/fixtures/`` and
is never imported by this package.
"""
__version__ = "0.4.0"
