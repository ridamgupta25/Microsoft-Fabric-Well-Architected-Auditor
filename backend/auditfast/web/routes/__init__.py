"""Flask blueprints that make up the JSON API.

Each module registers one blueprint and is kept thin - it only parses the
request, calls a service, and serializes the result:

* :mod:`.config_routes`    - GET /api/config
* :mod:`.auth_routes`      - POST /api/auth/*
* :mod:`.workspace_routes` - workspace listing + diagnostics
* :mod:`.audit_routes`     - POST /api/run
* :mod:`.download_routes`  - GET /api/download/<kind>
"""
