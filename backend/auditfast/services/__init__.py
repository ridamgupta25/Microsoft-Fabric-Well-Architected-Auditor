"""Application services.

The orchestration layer that both the CLI and the Flask API call into, so every
surface produces identical numbers:

* :mod:`.audit_service` - load config, build a client, run checks, aggregate,
  serialize.
* :mod:`.auth_service`  - read-only OAuth2 sign-in and in-memory session store.
"""
