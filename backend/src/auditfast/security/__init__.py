"""Security / authentication helpers.

* :mod:`.device_flow` - MSAL device-code sign-in used by the CLI ``--live`` mode.

All flows request read-only scopes only; no secrets are ever stored.
"""
