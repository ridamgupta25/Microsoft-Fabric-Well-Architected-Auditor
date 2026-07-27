"""Microsoft Fabric Well-Architected Auditing Platform.

Layers, outermost to innermost::

    api/  cli.py  mcp/    adapters — protocol translation only
    services/             orchestration; framework-free
    core/                 audit engine, checks, scoring; depends on nothing
    clients/              read-only providers: Fabric (live), fixtures (mock)

Adapters may depend inward. ``core/`` depends on nothing outside itself, which
is what lets the REST API, the CLI, and the MCP server share one implementation.
"""
__version__ = "0.3.0"
